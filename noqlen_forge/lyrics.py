from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, USLT
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .audio import Track, audio_files, get_tag, read_tracks
from .lyrics_providers import PROVIDERS as LYRICS_PROVIDERS
from .lyrics_providers import ProviderAttempt
from .lyrics_providers import LyricsResult as ProviderLyricsResult
from .provider_common import confidence_allows
from .config import APP_USER_AGENT

LRC_TIMESTAMP_RE = re.compile(r"\[(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")
LRC_METADATA_RE = re.compile(r"\[[a-zA-Z][a-zA-Z0-9_\-]*:[^\]]*\]")
LRC_BRACKET_RE = re.compile(r"\[[^\]]+\]")
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"


USER_AGENT = f"{APP_USER_AGENT} (Noqlen Forge Core metadata tool)"
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
PLACEHOLDER_TEXTS = {
    "instrumental",
    "no lyrics",
    "no lyrics.",
    "lyrics not found",
    "lyrics not found.",
    "not found",
    "n/a",
    "none",
    "null",
    "false",
}


@dataclass(slots=True)
class LyricsResult:
    text: str
    synced: bool
    source: str
    confidence: str = "high"
    provider: str = "local"
    language: str | None = None
    duration: float | None = None
    match_reason: str = ""
    external_id: str | None = None
    instrumental: bool = False
    text_hash: str = ""
    selection_reason: str = ""
    sidecar_path: str = ""


@dataclass(slots=True)
class LyricsStats:
    tracks: list[Track]
    embedded_existing: int = 0
    sidecar_existing: int = 0
    fetched: int = 0
    embedded_written: int = 0
    sidecar_written: int = 0
    missing: int = 0
    skipped: int = 0
    synced_found: int = 0
    sidecar_lrc_after: int = 0
    lyrics_after: int = 0
    provider_warnings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    selection_warnings: list[str] = field(default_factory=list)
    debug_lines: list[str] = field(default_factory=list)
    provider_attempts: dict[Path, list[ProviderAttempt]] = field(default_factory=dict)
    per_file: dict[Path, LyricsResult] = field(default_factory=dict)
    selections: dict[Path, LyricsSelectionResult] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.tracks)

    @property
    def status(self) -> str:
        if self.errors:
            return "FAIL"
        if self.conflicts:
            return "REVIEW"
        return "OK" if self.total and self.lyrics_after == self.total else "WARN"


@dataclass(slots=True)
class LyricsSelectionConfig:
    prefer_synced: bool = True
    allow_unsynced: bool = True
    prefer_local: bool = True
    prefer_existing: bool = True
    overwrite_existing: bool = False
    min_confidence: str = "medium"
    review_on_conflict: bool = True
    review_on_existing_mismatch: bool = True
    allow_instrumental: bool = False
    allow_empty: bool = False
    fallback_on_instrumental: bool = False
    synced_bonus: int = 10
    local_bonus: int = 8
    existing_bonus: int = 12
    duration_tolerance_seconds: float = 3.0
    conflict_similarity_threshold: float = 0.75


@dataclass(slots=True)
class LyricsSelectionResult:
    selected: LyricsResult | None = None
    skipped: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "WARN"
    reason: str = ""
    existing_kept: bool = False


def is_lrc(text: str) -> bool:
    return bool(LRC_TIMESTAMP_RE.search(text or ""))


def classify_lyrics_text(text: str, *, synced_hint: bool = False) -> tuple[str, list[str]]:
    clean = normalize_lyrics_text(text)
    warnings: list[str] = []
    textual = normalized_lyrics_for_compare(clean)
    if not clean:
        return "empty", warnings
    lowered = textual.casefold().strip(" .!\t\n")
    if lowered in PLACEHOLDER_TEXTS:
        if lowered == "instrumental":
            return "instrumental", warnings
        return "placeholder", warnings
    if is_lrc(clean):
        return "synced_valid", warnings
    if synced_hint or LRC_BRACKET_RE.search(clean):
        warnings.append("invalid LRC timestamps")
        return "synced_invalid", warnings
    if len(textual) < 8:
        warnings.append("lyrics too short")
        return "too_short", warnings
    return "plain_text", warnings


def strip_lrc_timestamps(text: str) -> str:
    lines = [LRC_METADATA_RE.sub("", LRC_TIMESTAMP_RE.sub("", line)).strip() for line in (text or "").splitlines()]
    return normalize_lyrics_text("\n".join(lines))


def normalize_lyrics_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").strip().split("\n"))


def normalized_lyrics_for_compare(text: str) -> str:
    stripped = strip_lrc_timestamps(text)
    stripped = LRC_BRACKET_RE.sub("", stripped)
    lines = [" ".join(line.split()) for line in stripped.splitlines()]
    return normalize_lyrics_text("\n".join(line for line in lines if line)).casefold()


def lyrics_text_hash(text: str) -> str:
    return sha256(normalized_lyrics_for_compare(text).encode("utf-8")).hexdigest()


def lyrics_similarity(left: str, right: str) -> float:
    left_norm = normalized_lyrics_for_compare(left)
    right_norm = normalized_lyrics_for_compare(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def lyrics_diverge(left: str, right: str, threshold: float = 0.75) -> bool:
    if lyrics_text_hash(left) == lyrics_text_hash(right):
        return False
    return lyrics_similarity(left, right) < threshold


def has_embedded_lyrics(file: Path) -> bool:
    return bool(read_embedded_lyrics(file))


def read_embedded_lyrics(file: Path) -> str:
    suffix = file.suffix.lower()
    try:
        if suffix == ".mp3":
            tags = ID3(file)
            for frame in tags.getall("USLT"):
                text = normalize_lyrics_text(frame.text)
                if text:
                    return text
            return ""
        audio = MutagenFile(file, easy=False)
    except (ID3NoHeaderError, Exception):
        return ""
    if audio is None or audio.tags is None:
        return ""
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        return _first_text(audio.tags.get("\xa9lyr"))
    if isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        return _first_text(audio.tags.get("LYRICS") or audio.tags.get("lyrics"))
    return ""


def write_embedded_lyrics(file: Path, text: str, synced: bool = False, force: bool = False) -> None:
    if has_embedded_lyrics(file) and not force:
        return
    clean = strip_lrc_timestamps(text) if synced else normalize_lyrics_text(text)
    if not clean:
        raise ValueError("empty lyrics")
    suffix = file.suffix.lower()
    if suffix == ".mp3":
        _write_mp3_lyrics(file, clean)
        return
    audio = MutagenFile(file, easy=False)
    if audio is None:
        raise ValueError("unsupported or unreadable audio file")
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        _write_mp4_lyrics(audio, clean)
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        _write_vorbis_lyrics(audio, clean)
    else:
        raise ValueError("unsupported lyrics format")
    audio.save()


def find_sidecar_lyrics(file: Path) -> LyricsResult | None:
    for path in sidecar_candidates(file):
        if not path.is_file():
            continue
        try:
            text = normalize_lyrics_text(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            text = normalize_lyrics_text(path.read_text(encoding="utf-8", errors="ignore"))
        if text:
            return LyricsResult(text=text, synced=is_lrc(text) or path.suffix.lower() == ".lrc", source=f"sidecar:{path.name}", confidence="high", provider="local", match_reason="local sidecar lyrics")
    return None


def sidecar_candidates(file: Path) -> list[Path]:
    return [file.with_suffix(".lrc"), file.with_suffix(".txt"), file.parent / "track.lrc", file.parent / "track.txt"]


def lyrics_sidecar_path(file: Path, synced: bool) -> Path:
    return file.with_suffix(".lrc" if synced else ".txt")


def save_sidecar_lyrics(file: Path, result: LyricsResult, force: bool = False) -> Path:
    path = lyrics_sidecar_path(file, result.synced)
    if path.exists() and not force:
        return path
    path.write_text(normalize_lyrics_text(result.text) + "\n", encoding="utf-8")
    return path


def select_best_lyrics_candidate(query: Track, candidates: list[LyricsResult], existing_lyrics: str = "", config: LyricsSelectionConfig | dict | None = None) -> LyricsSelectionResult:
    selection_config = _selection_config(config)
    existing_kind, existing_warnings = classify_lyrics_text(existing_lyrics)
    existing_valid = existing_kind in {"synced_valid", "plain_text", "instrumental"} or (selection_config.allow_empty and existing_kind == "empty")
    result = LyricsSelectionResult(warnings=list(existing_warnings))
    if existing_lyrics and existing_valid and selection_config.prefer_existing and not selection_config.overwrite_existing:
        result.status = "SKIP"
        result.existing_kept = True
        result.reason = "existing lyrics preserved"
        return result

    valid: list[LyricsResult] = []
    for candidate in candidates:
        kind, warnings = classify_lyrics_text(candidate.text, synced_hint=candidate.synced)
        result.warnings.extend(f"{candidate.provider}: {warning}" for warning in warnings)
        candidate.text_hash = lyrics_text_hash(candidate.text)
        if kind == "synced_valid":
            candidate.synced = True
        elif kind == "plain_text":
            candidate.synced = False
        elif kind == "instrumental":
            candidate.instrumental = True
            if not (selection_config.allow_instrumental or selection_config.fallback_on_instrumental):
                result.skipped.append(f"{candidate.provider}: instrumental lyrics disabled")
                continue
        elif kind == "empty" and selection_config.allow_empty:
            pass
        else:
            result.skipped.append(f"{candidate.provider}: {kind.replace('_', ' ')}")
            continue
        if candidate.synced is False and not selection_config.allow_unsynced:
            result.skipped.append(f"{candidate.provider}: unsynced lyrics disabled")
            continue
        if not confidence_allows(candidate.confidence, selection_config.min_confidence):
            result.skipped.append(f"{candidate.provider}: confidence {candidate.confidence} below minimum {selection_config.min_confidence}")
            continue
        if query.duration is not None and candidate.duration is not None and abs(query.duration - candidate.duration) > selection_config.duration_tolerance_seconds:
            result.warnings.append(f"{candidate.provider}: duration differs by {abs(query.duration - candidate.duration):.1f}s")
        valid.append(candidate)

    if not valid:
        result.status = "WARN" if result.warnings or result.skipped else "SKIP"
        result.reason = "no selectable lyrics candidate"
        return result

    high = [candidate for candidate in valid if candidate.confidence == "high"]
    conflict_pairs: list[tuple[LyricsResult, LyricsResult]] = []
    for index, left in enumerate(high):
        for right in high[index + 1:]:
            if left.text_hash == right.text_hash:
                continue
            if lyrics_similarity(left.text, right.text) < selection_config.conflict_similarity_threshold:
                conflict_pairs.append((left, right))
    if conflict_pairs and selection_config.review_on_conflict:
        for left, right in conflict_pairs:
            result.conflicts.append(f"{query.path.name}: conflicting high-confidence lyrics from {left.provider} and {right.provider}")
        result.status = "REVIEW"
        result.reason = "high-confidence lyrics conflict"
        return result

    selected = sorted(valid, key=lambda candidate: _candidate_score(candidate, selection_config), reverse=True)[0]
    if existing_lyrics and existing_valid and selection_config.overwrite_existing and selection_config.review_on_existing_mismatch:
        if lyrics_diverge(existing_lyrics, selected.text, selection_config.conflict_similarity_threshold):
            result.conflicts.append(f"{query.path.name}: existing lyrics differ from {selected.provider} candidate")
            result.status = "REVIEW"
            result.reason = "existing lyrics mismatch"
            return result
    selected.selection_reason = _selection_reason(selected, selection_config)
    result.selected = selected
    result.status = "OK"
    result.reason = selected.selection_reason
    return result


def fetch_lrclib_lyrics(track: Track, prefer_synced: bool = True, timeout: float = 10, debug: bool = False) -> tuple[LyricsResult | None, list[str]]:
    attempt = LYRICS_PROVIDERS["lrclib"].fetch(track, prefer_synced=prefer_synced, debug=debug)
    if attempt.result is None:
        return None, attempt.debug
    return _lyrics_result_from_provider(attempt.result), attempt.debug


def lyrics_path(path: Path, apply: bool = False, force: bool = False, embed_lyrics: bool = True, save_lrc: bool = True, save_txt: bool = False, prefer_synced: bool = True, allow_unsynced: bool = True, sources: list[str] | None = None, min_confidence: str = "medium", verbose: bool = False, debug: bool = False, config: dict | None = None, prefer_local: bool | None = None, allow_instrumental: bool | None = None, allow_empty: bool | None = None) -> tuple[int, str]:
    tracks = read_tracks(path)
    if not tracks:
        return 1, "No supported audio files found"
    stats = process_lyrics(tracks, apply=apply, force=force, embed_lyrics=embed_lyrics, save_lrc=save_lrc, save_txt=save_txt, prefer_synced=prefer_synced, allow_unsynced=allow_unsynced, sources=sources, min_confidence=min_confidence, debug=debug, config=config, prefer_local=prefer_local, allow_instrumental=allow_instrumental, allow_empty=allow_empty)
    return (1 if stats.status == "FAIL" else 0), render_lyrics_result(stats, apply=apply, force=force, embed_lyrics=embed_lyrics, save_lrc=save_lrc, save_txt=save_txt, verbose=verbose, debug=debug)


def process_lyrics(tracks: list[Track], apply: bool = False, force: bool = False, embed_lyrics: bool = True, save_lrc: bool = True, save_txt: bool = False, prefer_synced: bool = True, allow_unsynced: bool = True, sources: list[str] | None = None, min_confidence: str = "medium", debug: bool = False, config: dict | None = None, prefer_local: bool | None = None, allow_instrumental: bool | None = None, allow_empty: bool | None = None) -> LyricsStats:
    enabled_sources = list(sources or ["local", "lrclib"])
    local_source_enabled = any(source in enabled_sources for source in ("local", "sidecar", "embedded"))
    stats = LyricsStats(tracks=tracks)
    stats.embedded_existing = sum(1 for track in tracks if has_embedded_lyrics(track.path) or get_tag(track, "lyrics"))
    local_results: dict[Path, LyricsResult] = {}
    for track in tracks:
        sidecar = find_sidecar_lyrics(track.path) if local_source_enabled else None
        if sidecar:
            stats.sidecar_existing += 1
            local_results[track.path] = sidecar
    for track in tracks:
        existing = has_embedded_lyrics(track.path) or bool(get_tag(track, "lyrics"))
        existing_text = read_embedded_lyrics(track.path) or "\n".join(get_tag(track, "lyrics"))
        candidates: list[LyricsResult] = []
        attempts: list[ProviderAttempt] = []
        for source in enabled_sources:
            result = None
            if source == "embedded":
                embedded_text = read_embedded_lyrics(track.path) or "\n".join(get_tag(track, "lyrics"))
                if not embedded_text:
                    attempts.append(ProviderAttempt("embedded", "SKIP", "no embedded lyrics"))
                    continue
                result = LyricsResult(embedded_text, is_lrc(embedded_text), "embedded", provider="embedded", confidence="high", match_reason="embedded lyrics")
                attempts.append(ProviderAttempt("embedded", "OK", "embedded lyrics", result=_provider_result_from_lyrics(result)))
            elif source in {"local", "sidecar"}:
                local = local_results.get(track.path)
                if local is None:
                    attempts.append(ProviderAttempt(source, "SKIP", "no embedded or sidecar lyrics" if source == "local" else "no sidecar lyrics"))
                    continue
                if source == "sidecar":
                    local.provider = "sidecar"
                attempts.append(ProviderAttempt(source, "OK", local.source, result=_provider_result_from_lyrics(local)))
                result = local
            elif source == "lrclib":
                fetched, debug_lines = fetch_lrclib_lyrics(track, prefer_synced=prefer_synced, debug=debug)
                stats.debug_lines.extend(debug_lines)
                if fetched is None:
                    attempts.append(ProviderAttempt("lrclib", "WARN", "no lyrics from lrclib", debug_lines))
                    continue
                attempts.append(ProviderAttempt("lrclib", "OK", f"{'synced' if fetched.synced else 'plain'} {fetched.confidence} confidence", debug_lines, _provider_result_from_lyrics(fetched)))
                result = fetched
                stats.fetched += 1
            else:
                provider = LYRICS_PROVIDERS.get(source)
                if provider is None:
                    attempts.append(ProviderAttempt(source, "SKIP", "unknown provider"))
                elif not provider.enabled_for(config):
                    attempts.append(ProviderAttempt(source, "SKIP", "provider disabled"))
                else:
                    attempt = _fetch_provider(provider, track, prefer_synced=prefer_synced, debug=debug, config=config)
                    attempts.append(attempt)
                    result = _lyrics_result_from_provider(attempt.result) if attempt.result else None
            if result is not None:
                candidates.append(result)
        selection = select_best_lyrics_candidate(track, candidates, existing_text, _lyrics_selection_config(config, prefer_synced=prefer_synced, allow_unsynced=allow_unsynced, min_confidence=min_confidence, force=force, prefer_local=prefer_local, allow_instrumental=allow_instrumental, allow_empty=allow_empty))
        result = selection.selected
        stats.selections[track.path] = selection
        stats.selection_warnings.extend(f"{track.path.name}: {warning}" for warning in selection.warnings)
        stats.conflicts.extend(selection.conflicts)
        for skipped in selection.skipped:
            provider = skipped.split(":", 1)[0]
            for attempt in attempts:
                if attempt.provider == provider and attempt.status == "OK":
                    attempt.status = "WARN"
                    attempt.message = skipped.split(":", 1)[1].strip() if ":" in skipped else skipped
                    break
        stats.provider_attempts[track.path] = attempts
        stats.provider_warnings.extend(f"{track.path.name}: {attempt.provider}: {attempt.message}" for attempt in attempts if attempt.status == "WARN")
        if result is None:
            if existing or selection.existing_kept:
                stats.skipped += 1
            else:
                stats.missing += 1
            continue
        stats.per_file[track.path] = result
        if result.synced:
            stats.synced_found += 1
        if existing and not force:
            stats.skipped += 1
        elif apply and embed_lyrics:
            try:
                write_embedded_lyrics(track.path, result.text, synced=result.synced, force=force)
                stats.embedded_written += 1
            except Exception as exc:
                stats.errors.append(f"{track.path.name}: {exc}")
        if apply and ((result.synced and save_lrc) or (not result.synced and save_txt)):
            try:
                before = lyrics_sidecar_path(track.path, result.synced).exists()
                save_sidecar_lyrics(track.path, result, force=force)
                if force or not before:
                    stats.sidecar_written += 1
            except Exception as exc:
                stats.errors.append(f"{track.path.name} sidecar: {exc}")
    stats.lyrics_after = sum(1 for track in tracks if has_embedded_lyrics(track.path) or get_tag(track, "lyrics")) if apply else stats.embedded_existing
    stats.sidecar_lrc_after = sum(1 for track in tracks if track.path.with_suffix(".lrc").is_file())
    stats.warnings.extend(stats.provider_warnings)
    if stats.total and (stats.lyrics_after + len([p for p in stats.per_file if not apply])) < stats.total:
        stats.warnings.append(f"Lyrics missing: {stats.total - max(stats.lyrics_after, len(stats.per_file))}/{stats.total}")
    return stats


def render_lyrics_result(stats: LyricsStats, apply: bool, force: bool = False, embed_lyrics: bool = True, save_lrc: bool = True, save_txt: bool = False, verbose: bool = False, debug: bool = False) -> str:
    total = stats.total
    artist = _common_track_value(stats.tracks, "albumartist") or _common_track_value(stats.tracks, "artist")
    album = _common_track_value(stats.tracks, "album")
    lines: list[str] = []
    if artist or album:
        lines.append(f"Album: {artist} - {album}" if artist and album else f"Album: {album or artist}")
    lines.append(f"Files: {total}")
    lines.append(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")
    lines.append("")
    lines.append(_stage_line(1, "Detect lyrics", "OK", f"embedded {stats.embedded_existing}/{total}, sidecar {stats.sidecar_existing}/{total}"))
    found = len(stats.per_file)
    if found:
        synced = sum(1 for result in stats.per_file.values() if result.synced)
        unsynced = found - synced
        parts = []
        if synced:
            parts.append(f"synced {synced}/{total}")
        if unsynced:
            parts.append(f"unsynced {unsynced}/{total}")
        provider = _primary_provider(stats)
        provider_part = f"{provider} " if provider else ""
        lines.append(_stage_line(2, "Providers", "REVIEW" if stats.conflicts else "OK", provider_part + ", ".join(parts)))
    else:
        status = "WARN" if stats.provider_warnings or stats.missing else "SKIP"
        lines.append(_stage_line(2, "Providers", status, "no lyrics found" if status == "WARN" else "embedded lyrics already present"))
    if not embed_lyrics:
        lines.append(_stage_line(3, "Embed lyrics", "SKIP", "embedding disabled"))
    elif apply:
        status = "OK" if stats.embedded_written or stats.skipped else "SKIP"
        summary = f"embedded {stats.embedded_written}/{total}"
        if stats.skipped:
            summary += f", skipped {stats.skipped}/{total}"
        lines.append(_stage_line(3, "Embed lyrics", status if not stats.errors else "FAIL", summary))
    else:
        write_count = sum(1 for track in stats.tracks if track.path in stats.per_file and (force or not has_embedded_lyrics(track.path)))
        lines.append(_stage_line(3, "Embed lyrics", "DRY", f"would write embedded {write_count}/{total}"))
    sidecar_targets = sum(1 for result in stats.per_file.values() if (result.synced and save_lrc) or (not result.synced and save_txt))
    sidecar_write_targets = sum(1 for path, result in stats.per_file.items() if ((result.synced and save_lrc) or (not result.synced and save_txt)) and (force or not lyrics_sidecar_path(path, result.synced).exists()))
    if apply:
        action = "saved"
        count = stats.sidecar_written
        status = "OK" if stats.sidecar_written else "SKIP"
    else:
        action = "would save"
        count = sidecar_write_targets
        status = "DRY" if sidecar_write_targets else "SKIP"
    sidecar_label = "lrc" if save_lrc else "txt" if save_txt else "sidecar"
    if sidecar_targets and count == 0:
        sidecar_summary = f"existing {sidecar_label} {stats.sidecar_lrc_after}/{total}"
    else:
        sidecar_summary = f"{action} {sidecar_label} {count}/{total}" if sidecar_targets else "sidecar disabled or unavailable"
    lines.append(_stage_line(4, "Save sidecar", status, sidecar_summary))
    if verbose and stats.per_file:
        lines.append("")
        lines.append("Sources:")
        for path, result in sorted(stats.per_file.items()):
            kind = "synced" if result.synced else "unsynced"
            lines.append(f"- {path}: provider={result.provider}, source={result.source}, type={kind}, confidence={result.confidence}, chars={len(result.text)}, reason={result.selection_reason or result.match_reason or 'matched'}")
    if debug and stats.debug_lines:
        lines.append("")
        lines.append("Debug:")
        lines.extend(f"- {line}" for line in stats.debug_lines)
    if (verbose or debug) and stats.provider_attempts:
        lines.append("")
        lines.append("Provider Attempts:")
        for path, attempts in sorted(stats.provider_attempts.items()):
            for attempt in attempts:
                lines.append(f"- {path.name}: {attempt.provider} {attempt.status} {attempt.message}")
    warnings = [warning for warning in [*stats.warnings, *stats.selection_warnings] if warning]
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    if stats.errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in stats.errors)
    if stats.conflicts:
        lines.append("")
        lines.append("Conflicts:")
        lines.extend(f"- {conflict}" for conflict in stats.conflicts)
    final_lyrics = stats.lyrics_after if apply else stats.embedded_existing
    final_synced = synced_lyrics_count(stats.tracks)
    final_lrc = stats.sidecar_lrc_after if save_lrc else "skipped"
    lines.append("")
    lines.append("Final:")
    selected_synced = sum(1 for result in stats.per_file.values() if result.synced)
    selected_unsynced = len(stats.per_file) - selected_synced
    existing_kept = sum(1 for selection in stats.selections.values() if selection.existing_kept)
    lines.append(f"Selected synced: {selected_synced}")
    lines.append(f"Selected unsynced: {selected_unsynced}")
    lines.append(f"Existing kept: {existing_kept}")
    lines.append(f"Lyrics: {final_lyrics}/{total} embedded")
    lines.append(f"Synced Lyrics: {final_synced}/{total}")
    provider = _primary_provider(stats)
    confidence = _primary_confidence(stats)
    if provider:
        lines.append(f"Provider: {provider}")
    if confidence:
        lines.append(f"Confidence: {confidence}")
    lines.append(f"Sidecar LRC: {final_lrc if isinstance(final_lrc, str) else f'{final_lrc}/{total}'}")
    lines.append(f"Conflicts: {len(stats.conflicts)}")
    lines.append(f"Skipped: {stats.skipped}")
    lines.append(f"Status: {stats.status}")
    return "\n".join(lines)


def lyrics_count(tracks: list[Track]) -> int:
    return sum(1 for track in tracks if get_tag(track, "lyrics") or has_embedded_lyrics(track.path))


def synced_lyrics_count(tracks: list[Track]) -> int:
    return sum(1 for track in tracks if _has_synced_sidecar_or_tag(track))


def sidecar_lrc_status(tracks: list[Track], save_lrc: bool = True) -> str:
    if not save_lrc:
        return "skipped"
    total = len(tracks)
    count = sum(1 for track in tracks if track.path.with_suffix(".lrc").is_file())
    return f"{count}/{total}"


def _has_synced_sidecar_or_tag(track: Track) -> bool:
    lrc = track.path.with_suffix(".lrc")
    if lrc.is_file():
        return True
    values = get_tag(track, "synced_lyrics")
    return any(is_lrc(value) or value.strip() for value in values)


def _write_mp3_lyrics(path: Path, text: str) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("USLT")
    tags.add(USLT(encoding=3, lang="und", desc="", text=text))
    tags.save(path)


def _write_mp4_lyrics(audio: MP4, text: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    audio.tags["\xa9lyr"] = [text]


def _write_vorbis_lyrics(audio: FLAC | OggVorbis | OggOpus, text: str) -> None:
    audio["LYRICS"] = [text]


def _first_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        for item in value:
            text = normalize_lyrics_text(str(item))
            if text:
                return text
        return ""
    return normalize_lyrics_text(str(value))


def _common_track_value(tracks: list[Track], attr: str) -> str:
    values = [getattr(track, attr, "") for track in tracks if getattr(track, attr, "")]
    return max(set(values), key=values.count) if values else ""


def _lyrics_result_from_provider(result: ProviderLyricsResult | None) -> LyricsResult | None:
    if result is None:
        return None
    return LyricsResult(text=result.text, synced=result.synced, source=result.source, confidence=result.confidence, provider=result.provider, language=result.language, duration=result.duration, match_reason=result.match_reason, external_id=result.external_id, instrumental=result.instrumental)


def _fetch_provider(provider, track: Track, prefer_synced: bool, debug: bool, config: dict | None) -> ProviderAttempt:
    try:
        return provider.fetch(track, prefer_synced=prefer_synced, debug=debug, config=config)
    except TypeError:
        return provider.fetch(track, prefer_synced=prefer_synced, debug=debug)


def _provider_result_from_lyrics(result: LyricsResult) -> ProviderLyricsResult:
    return ProviderLyricsResult(text=result.text, synced=result.synced, source=result.source, provider=result.provider, confidence=result.confidence, language=result.language, duration=result.duration, match_reason=result.match_reason, external_id=result.external_id)


def _lyrics_selection_config(config: dict | None, *, prefer_synced: bool, allow_unsynced: bool, min_confidence: str, force: bool, prefer_local: bool | None, allow_instrumental: bool | None, allow_empty: bool | None) -> LyricsSelectionConfig:
    lyrics = (config or {}).get("lyrics", {}) if isinstance(config, dict) else {}
    selection = lyrics.get("selection", {}) if isinstance(lyrics, dict) else {}
    if not isinstance(selection, dict):
        selection = {}
    return LyricsSelectionConfig(
        prefer_synced=prefer_synced,
        allow_unsynced=allow_unsynced,
        prefer_local=bool(prefer_local if prefer_local is not None else lyrics.get("prefer_local", True)),
        prefer_existing=bool(lyrics.get("prefer_existing", True)),
        overwrite_existing=force or bool(lyrics.get("overwrite_existing", lyrics.get("overwrite", False))),
        min_confidence=min_confidence,
        review_on_conflict=bool(lyrics.get("review_on_conflict", True)),
        review_on_existing_mismatch=bool(lyrics.get("review_on_existing_mismatch", True)),
        allow_instrumental=bool(allow_instrumental if allow_instrumental is not None else lyrics.get("allow_instrumental", False)),
        allow_empty=bool(allow_empty if allow_empty is not None else lyrics.get("allow_empty", False)),
        fallback_on_instrumental=bool(lyrics.get("fallback_on_instrumental", False)),
        synced_bonus=int(selection.get("synced_bonus", 10)),
        local_bonus=int(selection.get("local_bonus", 8)),
        existing_bonus=int(selection.get("existing_bonus", 12)),
        duration_tolerance_seconds=float(selection.get("duration_tolerance_seconds", 3.0)),
        conflict_similarity_threshold=float(selection.get("conflict_similarity_threshold", 0.75)),
    )


def _selection_config(config: LyricsSelectionConfig | dict | None) -> LyricsSelectionConfig:
    if isinstance(config, LyricsSelectionConfig):
        return config
    if isinstance(config, dict):
        return _lyrics_selection_config({"lyrics": config.get("lyrics", config)}, prefer_synced=bool(config.get("prefer_synced", True)), allow_unsynced=bool(config.get("allow_unsynced", True)), min_confidence=str(config.get("min_confidence", "medium")), force=bool(config.get("overwrite_existing", config.get("force", False))), prefer_local=config.get("prefer_local"), allow_instrumental=config.get("allow_instrumental"), allow_empty=config.get("allow_empty"))
    return LyricsSelectionConfig()


def _candidate_score(candidate: LyricsResult, config: LyricsSelectionConfig) -> int:
    score = CONFIDENCE_RANK.get(candidate.confidence, 0) * 100
    if config.prefer_synced and candidate.synced:
        score += config.synced_bonus
    if not config.prefer_synced and not candidate.synced:
        score += config.synced_bonus
    if config.prefer_local and candidate.provider in {"local", "embedded", "sidecar"}:
        score += config.local_bonus
    if candidate.source == "embedded":
        score += config.existing_bonus
    return score


def _selection_reason(candidate: LyricsResult, config: LyricsSelectionConfig) -> str:
    parts = ["selected", "synced" if candidate.synced else "unsynced", candidate.confidence]
    if config.prefer_local and candidate.provider in {"local", "embedded", "sidecar"}:
        parts.append("local")
    if config.prefer_synced and candidate.synced:
        parts.append("preferred synced")
    elif config.prefer_synced and not candidate.synced:
        parts.append("higher confidence fallback")
    return ", ".join(parts)


def _primary_provider(stats: LyricsStats) -> str:
    values = [result.provider for result in stats.per_file.values() if result.provider]
    return max(set(values), key=values.count) if values else ""


def _primary_confidence(stats: LyricsStats) -> str:
    values = [result.confidence for result in stats.per_file.values() if result.confidence]
    return max(set(values), key=values.count) if values else ""


def _stage_line(index: int, name: str, status: str, summary: str) -> str:
    return f"[{index}/4] {name:<22} {status:<6} {summary}".rstrip()
