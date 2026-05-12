from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .audio import BAD_VALUES, Track, get_tag, is_valid_bpm, is_valid_key, is_valid_percent, read_tracks
from .config import get_config_value, load_config
from .cover import cover_count, local_cover_status
from .lyrics import lyrics_count, sidecar_lrc_status, synced_lyrics_count
from .output import format_warning


@dataclass(slots=True)
class AuditResult:
    tracks: list[Track]
    bad_fields: list[str]

    @property
    def status(self) -> str:
        if not self.tracks or self.bad_fields:
            return "REVIEW"
        total = len(self.tracks)
        essential_tags = ("mb_album_id", "mb_track_id", "mb_release_group_id")
        if not all(_filled(self.tracks, field) == total for field in essential_tags):
            return "REVIEW"
        if _unique_count(self.tracks, "mb_album_id") > 1 or _unique_count(self.tracks, "mb_release_group_id") > 1:
            return "REVIEW"
        if not all(track.album and track.title and (track.artist or track.albumartist) for track in self.tracks):
            return "REVIEW"
        rich = ("label", "style", "originaldate", "bpm", "key", "energy", "danceability", "lastfm_tags", "mood", "replaygain_track_gain", "replaygain_album_gain", "loudness")
        if not all(_filled(self.tracks, field) == total for field in rich):
            return "WARN"
        if cover_count(self.tracks) < total:
            return "WARN"
        if lyrics_count(self.tracks) < total:
            return "WARN"
        config = load_config()
        if bool(get_config_value(config, "lyrics", "save_lrc", True)) and synced_lyrics_count(self.tracks) and sidecar_lrc_status(self.tracks, save_lrc=True) != f"{total}/{total}":
            return "WARN"
        return "OK"


def audit_path(path: Path) -> AuditResult:
    tracks = read_tracks(path)
    return AuditResult(tracks=tracks, bad_fields=find_bad_fields(tracks))


def render_audit(result: AuditResult, verbose: bool = False, advanced: bool = False) -> str:
    tracks = result.tracks
    total = len(tracks)
    lines = ["Noqlen Forge audit", f"Target: {_target_label(tracks)}", f"Files: {total}", "Mode: READ-ONLY", ""]
    lines.append(f"Album: {_most_common(track.album for track in tracks)}")
    lines.append(f"Album Artist: {_most_common(track.albumartist for track in tracks)}")
    lines.append(f"Artist: {_most_common(track.artist for track in tracks)}")
    lines.append("")
    lines.append("Required:")
    lines.append(f"MB Album Id: {_filled(tracks, 'mb_album_id')}/{total}")
    lines.append(f"MB Track Id: {_filled(tracks, 'mb_track_id')}/{total}")
    lines.append(f"Release Group Id: {_filled(tracks, 'mb_release_group_id')}/{total}")
    lines.extend(_render_bad_fields(result.bad_fields, verbose=verbose))
    lines.append("")
    lines.append("Enrichment:")
    lines.append(f"Label: {_filled(tracks, 'label')}/{total}")
    lines.append(f"Style: {_filled(tracks, 'style')}/{total}")
    lines.append(f"Original Date: {_filled(tracks, 'originaldate')}/{total}")
    lines.append(f"BPM: {_filled(tracks, 'bpm')}/{total}")
    lines.append(f"Key: {_filled(tracks, 'key')}/{total}")
    lines.append(f"Energy: {_filled(tracks, 'energy')}/{total}")
    lines.append(f"Danceability: {_filled(tracks, 'danceability')}/{total}")
    lines.append(f"Last.fm Tags: {_filled(tracks, 'lastfm_tags')}/{total}")
    lines.append(f"Mood: {_filled(tracks, 'mood')}/{total}")
    lines.append(f"ReplayGain Track: {_replaygain_track_count(tracks)}/{total}")
    lines.append(f"ReplayGain Album: {_replaygain_album_count(tracks)}/{total}")
    lines.append(f"Loudness: {_filled(tracks, 'loudness')}/{total}")
    lines.append(f"Cover: {cover_count(tracks)}/{total}")
    save_folder_cover = bool(get_config_value(load_config(), "cover", "save_folder_cover", False))
    lines.append(f"Folder Cover: {local_cover_status(tracks, save_folder_cover=save_folder_cover)}")
    config = load_config()
    lines.append(f"Lyrics: {lyrics_count(tracks)}/{total}")
    lines.append(f"Synced Lyrics: {synced_lyrics_count(tracks)}/{total}")
    save_lrc = bool(get_config_value(config, "lyrics", "save_lrc", True))
    lines.append(f"Sidecar LRC: {sidecar_lrc_status(tracks, save_lrc=save_lrc)}")
    if advanced or bool(get_config_value(config, "audit", "show_catalog_fields", False)):
        lines.append("")
        lines.append("Identification:")
        lines.append(f"Fingerprint: {_filled(tracks, 'acoustid_fingerprint')}/{total}")
        lines.append(f"AcoustID: {_filled(tracks, 'acoustid_id')}/{total}")
        lines.append(f"MB Recording Id: {_filled(tracks, 'mb_track_id')}/{total}")
        lines.append(f"MB Album Id: {_filled(tracks, 'mb_album_id')}/{total}")
        lines.append("")
        lines.append("Catalog:")
        lines.append(f"Label: {_filled(tracks, 'label')}/{total}")
        lines.append(f"Catalog Number: {_filled(tracks, 'catalog_number')}/{total}")
        lines.append(f"Barcode: {_filled(tracks, 'barcode')}/{total}")
        lines.append(f"Country: {_filled(tracks, 'country')}/{total}")
        lines.append(f"Media: {_filled(tracks, 'media')}/{total}")
        lines.append(f"Release Type: {_filled(tracks, 'release_type')}/{total}")
        lines.append(f"ISRC: {_filled(tracks, 'isrc')}/{total}")
        lines.append("")
        lines.append("Audio:")
        lines.append(f"Track Gain: {_filled(tracks, 'replaygain_track_gain')}/{total}")
        lines.append(f"Track Peak: {_filled(tracks, 'replaygain_track_peak')}/{total}")
        lines.append(f"Album Gain: {_filled(tracks, 'replaygain_album_gain')}/{total}")
        lines.append(f"Album Peak: {_filled(tracks, 'replaygain_album_peak')}/{total}")
        lines.append(f"LUFS: {_filled(tracks, 'loudness')}/{total}")
    warnings = _warnings(tracks, total)
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(_render_warning(warning) for warning in warnings)
    next_action = _status_next_action(result.status)
    if next_action:
        lines.append("")
        lines.append(f"Next: {next_action}")
    if verbose:
        lines.extend(_render_track_details(tracks))
    lines.append("")
    lines.append(f"Status: {result.status}")
    return "\n".join(lines)


def render_final_audit(result: AuditResult, verbose: bool = False, advanced: bool = False) -> str:
    lines = ["Final audit:"]
    lines.extend(render_audit(result, verbose=verbose, advanced=advanced).splitlines()[5:])
    return "\n".join(lines)


def find_bad_fields(tracks: list[Track]) -> list[str]:
    bad: list[str] = []
    for track in tracks:
        for key, values in track.tags.items():
            for value in values:
                if value.strip() in BAD_VALUES or (key.lower() in {"tbpm", "bpm", "tmpo"} and not is_valid_bpm(value)) or (key.lower() in {"tkey", "key", "initialkey"} and not is_valid_key(value)) or (key.lower() in {"energy", "danceability"} and not is_valid_percent(value)):
                    bad.append(f"{track.path.name}:{key}={value}")
    return bad


def _render_bad_fields(bad_fields: list[str], verbose: bool) -> list[str]:
    if not bad_fields:
        return ["Bad fields: none"]
    if verbose:
        return ["Bad fields: " + ", ".join(bad_fields)]
    lines = ["Bad fields:"]
    lines.extend(f"- {_file_count(len(files))}: {issue}" for issue, files in _group_bad_fields(bad_fields))
    lines.append("Use --verbose to show per-file details.")
    return lines


def _warnings(tracks: list[Track], total: int) -> list[str]:
    warnings: list[str] = []
    optional = (
        ("Style", "style"),
        ("Original Date", "originaldate"),
        ("BPM", "bpm"),
        ("Key", "key"),
        ("Energy", "energy"),
        ("Danceability", "danceability"),
        ("Last.fm Tags", "lastfm_tags"),
        ("Mood", "mood"),
        ("ReplayGain Track", "replaygain_track_gain"),
        ("ReplayGain Album", "replaygain_album_gain"),
        ("Loudness", "loudness"),
        ("Fingerprint", "acoustid_fingerprint"),
        ("AcoustID", "acoustid_id"),
    )
    for label, field in optional:
        count = _filled(tracks, field)
        if total and count < total:
            warnings.append(f"{label} missing: {total - count}/{total}")
    covers = cover_count(tracks)
    if total and covers < total:
        warnings.append(f"Cover missing: {total - covers}/{total}")
    lyric_count = lyrics_count(tracks)
    if total and lyric_count < total:
        warnings.append(f"Lyrics missing: {total - lyric_count}/{total}")
    save_lrc = bool(get_config_value(load_config(), "lyrics", "save_lrc", True))
    synced_count = synced_lyrics_count(tracks)
    sidecar_status = sidecar_lrc_status(tracks, save_lrc=save_lrc)
    if total and save_lrc and synced_count and sidecar_status != f"{total}/{total}":
        warnings.append(f"Sidecar LRC missing: {sidecar_status}")
    return warnings


def _render_warning(warning: str) -> str:
    return format_warning(warning, next_action=_warning_next_action(warning), sanitize=False)


def _warning_next_action(warning: str) -> str:
    label = warning.split(":", 1)[0].removesuffix(" missing")
    if label in {"MB Album Id", "MB Track Id", "Release Group Id", "Fingerprint", "AcoustID"}:
        return "run `noqlen-forge enrich PATH --full` and review MusicBrainz/AcoustID identity before applying."
    if label in {"Lyrics", "Sidecar LRC"}:
        return "run `noqlen-forge lyrics PATH` or keep lyrics intentionally missing."
    if label == "Cover":
        return "run `noqlen-forge cover PATH` or keep cover intentionally missing."
    if label in {"ReplayGain Track", "ReplayGain Album", "Loudness"}:
        return "run `noqlen-forge replaygain PATH` or `noqlen-forge enrich PATH --full --replaygain`."
    if label == "Key":
        return "enable native key detection (`auto` or `portable_basic`) or leave Key empty."
    return ""


def _status_next_action(status: str) -> str:
    if status == "REVIEW":
        return "resolve required identity/bad-field issues before applying write-capable workflows."
    if status == "WARN":
        return "optional metadata is incomplete; enrich only the fields you want to maintain."
    return ""


def _render_track_details(tracks: list[Track]) -> list[str]:
    if not tracks:
        return []
    lines = ["", "Files:"]
    for track in tracks:
        lines.append(f"- {track.path}")
    return lines


def _group_bad_fields(bad_fields: list[str]) -> list[tuple[str, set[str]]]:
    groups: dict[str, set[str]] = {}
    order: dict[str, int] = {}
    for bad_field in bad_fields:
        file_name, issue = _split_bad_field(bad_field)
        if issue not in groups:
            groups[issue] = set()
            order[issue] = len(order)
        groups[issue].add(file_name)
    return sorted(groups.items(), key=lambda item: (-len(item[1]), order[item[0]]))


def _split_bad_field(bad_field: str) -> tuple[str, str]:
    file_name, raw_issue = bad_field.split(":", 1) if ":" in bad_field else ("unknown", bad_field)
    key, value = raw_issue.rsplit("=", 1) if "=" in raw_issue else (raw_issue, "")
    issue = f"empty {key}" if value == "" else raw_issue
    return file_name, issue


def _file_count(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _filled(tracks: list[Track], field: str) -> int:
    return sum(1 for track in tracks if get_tag(track, field))


def _replaygain_track_count(tracks: list[Track]) -> int:
    return sum(1 for track in tracks if get_tag(track, "replaygain_track_gain") and get_tag(track, "replaygain_track_peak"))


def _replaygain_album_count(tracks: list[Track]) -> int:
    return sum(1 for track in tracks if get_tag(track, "replaygain_album_gain") and get_tag(track, "replaygain_album_peak"))


def _unique_count(tracks: list[Track], field: str) -> int:
    return len({value for track in tracks for value in get_tag(track, field)})


def _most_common(values) -> str:
    clean = [value for value in values if value]
    if not clean:
        return "unknown"
    return Counter(clean).most_common(1)[0][0]


def _target_label(tracks: list[Track]) -> str:
    if not tracks:
        return "no audio files"
    parents = {track.path.parent for track in tracks}
    if len(parents) == 1:
        return str(next(iter(parents)))
    return "multiple folders"
