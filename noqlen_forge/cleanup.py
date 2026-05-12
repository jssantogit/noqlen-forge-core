from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import unicodedata

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TBPM, TXXX, UFID
from mutagen.mp4 import MP4FreeForm

from .audio import BAD_VALUES, TAG_ALIASES, Track, get_tag, is_valid_bpm, is_valid_percent
from .lastfm_filter import TagContext, clean_existing_lastfm_tags
from .mood import normalize_moods

EMPTY_TAG_NAMES = {"album artist credit", "artist credit", "musicbrainz album comment"}
BAD_EDITION_CODECS = {"flac": "FLAC", "mp3": "MP3", "aac": "AAC", "alac": "ALAC", "wav": "WAV", "aiff": "AIFF", "ogg": "OGG", "vorbis": "VORBIS", "opus": "OPUS", "wma": "WMA"}
BAD_TECHNICAL_EDITIONS = {"12", "16bit", "24bit", "3313rpm", "331rpm", "33rpm", "441khz", "45rpm", "48khz", "96khz", "cd", "file", "lp", "stereo", "vinyl"}


@dataclass(slots=True)
class CleanupPlan:
    path: Path
    remove: set[str] = field(default_factory=set)
    remove_values: dict[str, list[str]] = field(default_factory=dict)
    set_values: dict[str, list[str]] = field(default_factory=dict)
    before_values: dict[str, list[str]] = field(default_factory=dict)


def plan_cleanup(tracks: list[Track], release_date: str = "") -> list[CleanupPlan]:
    majority_label = _majority_label(tracks)
    plans: list[CleanupPlan] = []
    for track in tracks:
        plan = CleanupPlan(path=track.path)
        for key, values in track.tags.items():
            lowered = key.lower()
            alias_name = lowered.rsplit(":", 1)[-1] if lowered.startswith("----:com.apple.itunes:") else lowered
            logical = TAG_ALIASES.get(lowered) or TAG_ALIASES.get(alias_name) or lowered
            if lowered in EMPTY_TAG_NAMES:
                plan.remove.add(key)
            bad_values = [value for value in values if value.strip() in BAD_VALUES]
            if bad_values:
                plan.remove_values.setdefault(key, []).extend(bad_values)
            if values and all(value.strip() in BAD_VALUES for value in values):
                plan.remove.add(key)
            if logical == "bpm" and any(not is_valid_bpm(value) for value in values):
                plan.remove_values.setdefault(key, []).extend(value for value in values if not is_valid_bpm(value))
                if all(not is_valid_bpm(value) for value in values):
                    plan.remove.add(key)
            if logical in {"energy", "danceability"} and any(not is_valid_percent(value) for value in values):
                plan.remove_values.setdefault(key, []).extend(value for value in values if not is_valid_percent(value))
                if all(not is_valid_percent(value) for value in values):
                    plan.remove.add(key)
            if logical == "edition":
                bad_editions = [value for value in values if _is_bad_technical_edition(value, track)]
                if bad_editions:
                    plan.remove_values.setdefault(key, []).extend(bad_editions)
                    if all(_is_bad_technical_edition(value, track) for value in values):
                        plan.remove.add(key)
            if lowered in {"tcmp", "compilation"} and any(value.strip() in {"0", "0.0", ""} for value in values):
                plan.remove.add(key)
            if lowered == "ufid:http://musicbrainz.org" and not any(value.strip() for value in values):
                plan.remove.add(key)
        style = get_tag(track, "style")
        if style:
            normalized = normalize_style(style)
            if normalized != style:
                plan.set_values["STYLE"] = normalized
        lastfm_tags = get_tag(track, "lastfm_tags")
        if lastfm_tags:
            context = TagContext(artist=track.artist, albumartist=track.albumartist, album=track.album, title=track.title)
            cleaned = clean_existing_lastfm_tags(lastfm_tags, context)
            if cleaned:
                normalized = [cleaned]
                if normalized != lastfm_tags:
                    plan.set_values["LASTFM_TAGS"] = normalized
                    plan.before_values["LASTFM_TAGS"] = lastfm_tags
            else:
                plan.remove.add("lastfm_tags")
                plan.remove_values["lastfm_tags"] = lastfm_tags
        mood = get_tag(track, "mood")
        if mood:
            normalized_mood = ["; ".join(normalize_moods(mood))]
            if normalized_mood != mood:
                plan.set_values["MOOD"] = normalized_mood
        if majority_label and not get_tag(track, "label"):
            plan.set_values["LABEL"] = [majority_label]
        if release_date and not get_tag(track, "originaldate"):
            plan.set_values["ORIGINALDATE"] = [release_date]
        if plan.remove or plan.set_values:
            plans.append(plan)
    return plans


def apply_cleanup(plans: list[CleanupPlan], apply: bool) -> None:
    if not apply:
        return
    for plan in plans:
        audio = MutagenFile(plan.path, easy=False)
        if audio is None or audio.tags is None:
            continue
        suffix = plan.path.suffix.lower()
        if suffix == ".mp3":
            _apply_mp3(plan)
        else:
            for key in plan.remove:
                for actual in _matching_keys(audio.tags, key):
                    del audio.tags[actual]
            for key, values in plan.set_values.items():
                if suffix in {".m4a", ".mp4", ".aac"} and key.upper() in MP4_FREEFORM_KEYS:
                    audio.tags[MP4_FREEFORM_KEYS[key.upper()]] = [MP4FreeForm(value.encode("utf-8")) for value in values]
                else:
                    audio.tags[key] = values
            audio.save()


def summarize_cleanup(plans: list[CleanupPlan], apply: bool, verbose: bool = False, repaired_fields_by_path: dict[Path, set[str]] | None = None) -> str:
    remove_heading = "removed" if apply else "would remove"
    write_heading = "wrote" if apply else "would write"
    if not plans:
        return f"{remove_heading}:\n- nothing\n{write_heading}:\n- nothing"
    lines = [f"{remove_heading}:"]
    repaired_fields_by_path = repaired_fields_by_path or {}
    remove_lines = _verbose_remove_lines(plans, repaired_fields_by_path) if verbose else _grouped_remove_lines(plans, repaired_fields_by_path)
    lines.extend(remove_lines or ["- nothing"])
    lines.append(f"{write_heading}:")
    write_lines: list[str] = []
    for plan in plans:
        for key, values in sorted(plan.set_values.items()):
            before = plan.before_values.get(key)
            if before:
                write_lines.append(f"- {plan.path}: {key}: {'; '.join(before)} -> {'; '.join(values)}")
            else:
                write_lines.append(f"- {plan.path}: {key}={'; '.join(values)}")
    lines.extend(write_lines or ["- nothing"])
    return "\n".join(lines)


def _verbose_remove_lines(plans: list[CleanupPlan], repaired_fields_by_path: dict[Path, set[str]]) -> list[str]:
    remove_lines: list[str] = []
    for plan in plans:
        for key in sorted(plan.remove):
            for issue in _removal_issues(key, plan.remove_values.get(key), repaired_fields_by_path.get(plan.path, set())):
                remove_lines.append(f"- {plan.path}: {issue}")
    return remove_lines


def _grouped_remove_lines(plans: list[CleanupPlan], repaired_fields_by_path: dict[Path, set[str]]) -> list[str]:
    groups: dict[str, set[Path]] = {}
    order: dict[str, int] = {}
    for plan in plans:
        for key in sorted(plan.remove):
            for issue in _removal_issues(key, plan.remove_values.get(key), repaired_fields_by_path.get(plan.path, set())):
                if issue not in groups:
                    groups[issue] = set()
                    order[issue] = len(order)
                groups[issue].add(plan.path)
    return [f"- {_file_count(len(paths))}: {issue}" for issue, paths in sorted(groups.items(), key=lambda item: (-len(item[1]), order[item[0]]))]


def _removal_issues(key: str, values: list[str] | None, repaired_fields: set[str] | None = None) -> list[str]:
    repaired_fields = repaired_fields or set()
    logical = _logical_cleanup_key(key)
    suffix = ", will be repaired by MusicBrainz" if logical in repaired_fields else ""
    if not values:
        return [f"empty {logical}{suffix}"]
    return [f"empty {logical}{suffix}" if value == "" else f"{key}={value}" for value in values]


def _logical_cleanup_key(key: str) -> str:
    lowered = key.lower()
    alias_name = lowered.rsplit(":", 1)[-1] if lowered.startswith("----:com.apple.itunes:") else lowered
    return TAG_ALIASES.get(lowered) or TAG_ALIASES.get(alias_name) or lowered


def _file_count(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def normalize_style(values: list[str]) -> list[str]:
    parts: list[str] = []
    for value in values:
        for part in value.replace(",", ";").split(";"):
            clean = part.strip()
            if clean and clean not in parts:
                parts.append(clean)
    return ["; ".join(parts)] if parts else []


def _is_bad_technical_edition(value: str, track: Track) -> bool:
    normalized = _normalize_descriptor(value)
    if normalized in BAD_TECHNICAL_EDITIONS:
        return True
    codec = BAD_EDITION_CODECS.get(normalized)
    return bool(codec and _local_codec(track) != codec)


def _normalize_descriptor(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    return "".join(character for character in normalized if character.isalnum())


def _local_codec(track: Track) -> str:
    value = track.format.lower().strip(".")
    if value == "flac":
        return "FLAC"
    if value == "mp3":
        return "MP3"
    if value in {"m4a", "mp4", "aac"}:
        return "AAC"
    if value == "opus":
        return "OPUS"
    if value == "ogg":
        return "OGG"
    if value == "wma":
        return "WMA"
    if value == "wav":
        return "WAV"
    if value == "aiff":
        return "AIFF"
    if value == "alac":
        return "ALAC"
    return value.upper()


def _majority_label(tracks: list[Track]) -> str:
    labels = [label for track in tracks for label in get_tag(track, "label")]
    if not labels:
        return ""
    label, count = Counter(labels).most_common(1)[0]
    return label if count > len(tracks) / 2 else ""


def _apply_mp3(plan: CleanupPlan) -> None:
    tags = ID3(plan.path)
    for key in plan.remove:
        _delete_id3_key(tags, key)
    for key, values in plan.set_values.items():
        if key.upper() == "TBPM":
            tags.delall("TBPM")
            tags.add(TBPM(encoding=3, text=values))
        else:
            tags.delall(f"TXXX:{key}")
            tags.add(TXXX(encoding=3, desc=key, text=values))
    tags.save(plan.path)


def _delete_id3_key(tags: ID3, key: str) -> None:
    lowered = key.lower()
    for frame_id in ID3_FRAME_ALIASES.get(lowered, []):
        tags.delall(frame_id)
    if lowered in {"mb_track_id", "ufid:http://musicbrainz.org"}:
        tags.delall("UFID:http://musicbrainz.org")
    tags.delall(f"TXXX:{key}")
    tags.delall(key)
    for actual in list(tags.keys()):
        actual_lower = actual.lower()
        txxx_desc = actual_lower.removeprefix("txxx:") if actual_lower.startswith("txxx:") else actual_lower
        if actual_lower == lowered or txxx_desc == lowered or TAG_ALIASES.get(actual_lower) == lowered or TAG_ALIASES.get(txxx_desc) == lowered:
            del tags[actual]


def _matching_keys(tags, key: str) -> list[str]:
    lowered = key.lower()
    matches: list[str] = []
    for actual in tags.keys():
        actual_lower = actual.lower()
        freeform_name = actual_lower.rsplit(":", 1)[-1] if actual_lower.startswith("----:com.apple.itunes:") else actual_lower
        if actual_lower == lowered or TAG_ALIASES.get(actual_lower) == lowered or TAG_ALIASES.get(freeform_name) == lowered:
            matches.append(actual)
    return matches


ID3_FRAME_ALIASES = {
    "title": ["TIT2"],
    "artist": ["TPE1"],
    "albumartist": ["TPE2"],
    "album": ["TALB"],
    "date": ["TDRC"],
    "originaldate": ["TDOR", "TXXX:ORIGINALDATE", "TXXX:Original Date", "TXXX:ORIGINAL YEAR"],
    "genre": ["TCON"],
    "bpm": ["TBPM"],
    "compilation": ["TCMP", "TXXX:TCMP"],
    "label": ["TXXX:LABEL", "TXXX:publisher", "TXXX:PUBLISHER"],
    "style": ["TXXX:STYLE"],
    "energy": ["TXXX:ENERGY"],
    "danceability": ["TXXX:DANCEABILITY"],
    "lastfm_tags": ["TXXX:LASTFM_TAGS"],
    "mood": ["TXXX:MOOD"],
    "mb_album_id": ["TXXX:MusicBrainz Album Id"],
    "mb_track_id": ["TXXX:MusicBrainz Track Id"],
    "mb_release_group_id": ["TXXX:MusicBrainz Release Group Id"],
}

MP4_FREEFORM_KEYS = {
    "LABEL": "----:com.apple.iTunes:LABEL",
    "STYLE": "----:com.apple.iTunes:STYLE",
    "ORIGINALDATE": "----:com.apple.iTunes:ORIGINALDATE",
    "LASTFM_TAGS": "----:com.apple.iTunes:LASTFM_TAGS",
    "MOOD": "----:com.apple.iTunes:MOOD",
}
