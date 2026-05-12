from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TDOR, TXXX, UFID
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .audio import Track, get_tag, mb_album_ids
from .scoring import release_track_rows


@dataclass(slots=True)
class WritePlan:
    path: Path
    changes: dict[str, str]


def plan_musicbrainz_writes(tracks: list[Track], release: dict, force: bool = False) -> list[WritePlan]:
    existing = mb_album_ids(tracks)
    if existing and not force:
        return []
    release_tracks = release_track_rows(release)
    album_artist_id = _album_artist_id(release)
    original_date = _original_date(release)
    label = _label(release)
    plans: list[WritePlan] = []
    for index, track in enumerate(sorted(tracks, key=lambda t: t.tracknumber or 999)):
        mb_track = release_tracks[index] if index < len(release_tracks) else {}
        changes = {
            "MusicBrainz Album Id": release.get("id", ""),
            "MusicBrainz Release Group Id": (release.get("release-group") or {}).get("id", ""),
            "MusicBrainz Track Id": mb_track.get("recording_id", ""),
            "MusicBrainz Release Track Id": mb_track.get("id", ""),
            "MusicBrainz Album Artist Id": album_artist_id,
        }
        if original_date and not get_tag(track, "originaldate"):
            changes["Original Date"] = original_date
        if label and not get_tag(track, "label"):
            changes["Label"] = label
        plans.append(WritePlan(path=track.path, changes={key: value for key, value in changes.items() if value}))
    return plans


def plan_partial_musicbrainz_repair(tracks: list[Track], release: dict) -> list[WritePlan]:
    release_tracks = release_track_rows(release)
    release_id = release.get("id", "")
    release_group_id = (release.get("release-group") or {}).get("id", "")
    album_artist_id = _album_artist_id(release)
    original_date = _original_date(release)
    label = _label(release)
    plans: list[WritePlan] = []
    for index, track in enumerate(sorted(tracks, key=lambda t: t.tracknumber or 999)):
        mb_track = release_tracks[index] if index < len(release_tracks) else {}
        changes: dict[str, str] = {}
        if release_id and not get_tag(track, "mb_album_id"):
            changes["MusicBrainz Album Id"] = release_id
        if release_group_id and not get_tag(track, "mb_release_group_id"):
            changes["MusicBrainz Release Group Id"] = release_group_id
        if mb_track.get("recording_id") and not get_tag(track, "mb_track_id"):
            changes["MusicBrainz Track Id"] = mb_track["recording_id"]
        if mb_track.get("id") and not get_tag(track, "mb_release_track_id"):
            changes["MusicBrainz Release Track Id"] = mb_track["id"]
        if album_artist_id and not get_tag(track, "mb_album_artist_id"):
            changes["MusicBrainz Album Artist Id"] = album_artist_id
        if original_date and not get_tag(track, "originaldate"):
            changes["Original Date"] = original_date
        if label and not get_tag(track, "label"):
            changes["Label"] = label
        if changes:
            plans.append(WritePlan(path=track.path, changes=changes))
    return plans


def apply_musicbrainz_writes(plans: list[WritePlan], apply: bool) -> list[str]:
    if not apply:
        return []
    errors: list[str] = []
    for plan in plans:
        suffix = plan.path.suffix.lower()
        if suffix == ".mp3":
            _write_mp3(plan.path, plan.changes)
            mp3_errors = _verify_mp3(plan.path, plan.changes)
            if mp3_errors:
                errors.extend(mp3_errors)
            continue
        audio = MutagenFile(plan.path, easy=False)
        if audio is None:
            errors.append(f"{plan.path}: unsupported or unreadable audio file")
            continue
        elif isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
            _write_mp4(audio, plan.changes)
        elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
            _write_vorbis(audio, plan.changes)
        audio.save()
    return errors


def summarize_plans(plans: list[WritePlan], apply: bool, verbose: bool = False) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    if not plans:
        return f"{mode}: no MusicBrainz writes planned"
    if not apply and not verbose:
        counts: dict[str, int] = {}
        for plan in plans:
            for field in plan.changes:
                counts[field] = counts.get(field, 0) + 1
        lines = ["DRY-RUN: MusicBrainz apply"]
        for field in sorted(counts):
            count = counts[field]
            files = "file" if count == 1 else "files"
            lines.append(f"- {count} {files}: would write {field}")
        return "\n".join(lines)
    lines = [f"{mode}: {len(plans)} files"]
    for plan in plans:
        fields = ", ".join(sorted(plan.changes))
        lines.append(f"{plan.path}: {fields}")
    return "\n".join(lines)


def summarize_partial_repair(plans: list[WritePlan], apply: bool) -> str:
    lines = ["MusicBrainz partial repair:"]
    if not plans:
        lines.append("- no missing MusicBrainz fields found")
        return "\n".join(lines)
    action = "wrote" if apply else "would write"
    for plan in plans:
        for key, value in plan.changes.items():
            field = key.removeprefix("MusicBrainz ")
            lines.append(f"- {plan.path}: {action} {field}={value}")
    return "\n".join(lines)


def _write_mp3(path: Path, changes: dict[str, str]) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for key, value in changes.items():
        if key == "Original Date":
            tags.delall("TDOR")
            tags.add(TDOR(encoding=3, text=[value]))
            tags.delall("TXXX:ORIGINALDATE")
            tags.add(TXXX(encoding=3, desc="ORIGINALDATE", text=[value]))
            tags.delall("TXXX:Original Date")
            tags.add(TXXX(encoding=3, desc="Original Date", text=[value]))
            continue
        tags.delall(f"TXXX:{key}")
        tags.add(TXXX(encoding=3, desc=key, text=[value]))
    recording_id = changes.get("MusicBrainz Track Id")
    if recording_id:
        tags.delall("UFID:http://musicbrainz.org")
        tags.add(UFID(owner="http://musicbrainz.org", data=recording_id.encode("utf-8")))
    tags.save(path)


def _verify_mp3(path: Path, changes: dict[str, str]) -> list[str]:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return [f"{path}: ID3 tags were not saved"]
    errors: list[str] = []
    for key, value in changes.items():
        if key == "Original Date":
            if not _has_original_date(tags, value):
                errors.append(f"{path}: missing TDOR/TXXX:ORIGINALDATE after save")
            continue
        if not _has_txxx(tags, key, value):
            errors.append(f"{path}: missing TXXX:{key} after save")
    recording_id = changes.get("MusicBrainz Track Id")
    if recording_id and not _has_musicbrainz_ufid(tags, recording_id):
        errors.append(f"{path}: missing UFID:http://musicbrainz.org after save")
    return errors


def _has_txxx(tags: ID3, desc: str, value: str) -> bool:
    for frame in tags.getall(f"TXXX:{desc}"):
        if value in [str(item) for item in frame.text]:
            return True
    return False


def _has_musicbrainz_ufid(tags: ID3, recording_id: str) -> bool:
    expected = recording_id.encode("utf-8")
    for frame in tags.getall("UFID:http://musicbrainz.org"):
        if frame.data == expected:
            return True
    return False


def _has_original_date(tags: ID3, value: str) -> bool:
    for frame in tags.getall("TDOR"):
        if value in [str(item) for item in frame.text]:
            return True
    return _has_txxx(tags, "ORIGINALDATE", value) or _has_txxx(tags, "Original Date", value)


def _write_mp4(audio: MP4, changes: dict[str, str]) -> None:
    if audio.tags is None:
        audio.add_tags()
    for key, value in changes.items():
        if key == "Original Date":
            audio.tags["----:com.apple.iTunes:ORIGINALDATE"] = [MP4FreeForm(value.encode("utf-8"))]
            audio.tags["----:com.apple.iTunes:Original Date"] = [MP4FreeForm(value.encode("utf-8"))]
            audio.tags["\xa9day"] = [value]
            continue
        audio.tags[f"----:com.apple.iTunes:{key}"] = [MP4FreeForm(value.encode("utf-8"))]


def _write_vorbis(audio, changes: dict[str, str]) -> None:
    mapping = {
        "MusicBrainz Album Id": "MUSICBRAINZ_ALBUMID",
        "MusicBrainz Release Group Id": "MUSICBRAINZ_RELEASEGROUPID",
        "MusicBrainz Track Id": "MUSICBRAINZ_TRACKID",
        "MusicBrainz Release Track Id": "MUSICBRAINZ_RELEASETRACKID",
        "MusicBrainz Album Artist Id": "MUSICBRAINZ_ALBUMARTISTID",
        "ACOUSTID_ID": "ACOUSTID_ID",
        "ACOUSTID_FINGERPRINT": "ACOUSTID_FINGERPRINT",
        "Original Date": "ORIGINALDATE",
        "Date": "DATE",
        "Genre": "GENRE",
        "Style": "STYLE",
        "Label": "LABEL",
        "Catalog Number": "CATALOGNUMBER",
        "Barcode": "BARCODE",
        "Release Country": "RELEASECOUNTRY",
        "Media": "MEDIA",
        "Release Type": "RELEASETYPE",
        "ISRC": "ISRC",
    }
    for key, value in changes.items():
        audio[mapping.get(key, key.upper().replace(" ", ""))] = [value]


def _album_artist_id(release: dict) -> str:
    for credit in release.get("artist-credit") or []:
        if isinstance(credit, dict) and isinstance(credit.get("artist"), dict):
            return credit["artist"].get("id", "")
    return ""


def _original_date(release: dict) -> str:
    release_group_date = (release.get("release-group") or {}).get("first-release-date", "")
    value = release_group_date or release.get("date", "")
    clean = str(value).strip()
    if clean == "0000":
        return ""
    return clean


def _label(release: dict) -> str:
    labels: list[str] = []
    for info in release.get("label-info") or []:
        label = info.get("label") or {}
        name = str(label.get("name", "")).strip()
        if name and name.lower() not in {existing.lower() for existing in labels}:
            labels.append(name)
    return "; ".join(labels)
