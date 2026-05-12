from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError, SYLT, TXXX, UFID, USLT

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".opus"}
BAD_VALUES = {"", "0000", "0", "0.0"}


@dataclass(slots=True)
class Track:
    path: Path
    format: str
    album: str = ""
    albumartist: str = ""
    artist: str = ""
    title: str = ""
    tracknumber: int | None = None
    date: str = ""
    duration: float | None = None
    tags: dict[str, list[str]] = field(default_factory=dict)


def is_audio_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def audio_files(target: Path) -> list[Path]:
    if is_audio_file(target):
        return [target]
    if not target.is_dir():
        return []
    return sorted(path for path in target.rglob("*") if is_audio_file(path))


def target_kind(target: Path) -> str:
    if is_audio_file(target):
        return "single"
    files = audio_files(target)
    if not files:
        return "empty"
    if len(files) == 1:
        return "single"
    parent_dirs = {path.parent for path in files}
    return "album" if len(parent_dirs) == 1 else "artist"


def read_tracks(target: Path) -> list[Track]:
    return [read_track(path) for path in audio_files(target)]


def read_track(path: Path) -> Track:
    audio = _open_audio(path)
    tags = _read_tags(audio)
    return Track(
        path=path,
        format=path.suffix.lower().lstrip("."),
        album=_first(tags, "album"),
        albumartist=_first(tags, "albumartist", "album artist", "albumartistsort"),
        artist=_first(tags, "artist"),
        title=_first(tags, "title"),
        tracknumber=_track_number(_first(tags, "tracknumber", "trkn")),
        date=_first(tags, "date", "year", "originaldate"),
        duration=getattr(getattr(audio, "info", None), "length", None),
        tags=tags,
    )


def _open_audio(path: Path) -> Any:
    try:
        return MutagenFile(path, easy=False)
    except MutagenError:
        if path.suffix.lower() == ".mp3":
            try:
                return _ID3OnlyAudio(ID3(path))
            except ID3NoHeaderError:
                return None
        raise


@dataclass(slots=True)
class _ID3OnlyAudio:
    tags: ID3
    info: None = None


def mb_album_ids(tracks: list[Track]) -> set[str]:
    return {value for track in tracks for value in get_tag(track, "mb_album_id") if value}


def get_tag(track: Track, logical_name: str) -> list[str]:
    keys = LOGICAL_TAG_KEYS[logical_name]
    values: list[str] = []
    for key in keys:
        values.extend(track.tags.get(key.lower(), []))
    values = list(dict.fromkeys(values))
    if logical_name == "bpm":
        return [value for value in values if is_valid_bpm(value)]
    if logical_name == "key":
        return [value for value in values if is_valid_key(value)]
    if logical_name in {"energy", "danceability"}:
        return [value for value in values if is_valid_percent(value)]
    return [value for value in values if value not in BAD_VALUES]


def is_valid_bpm(value: str | int | float) -> bool:
    try:
        bpm = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(bpm) and 40 <= bpm <= 240


def is_valid_key(value: str) -> bool:
    return bool(re.fullmatch(r"[A-G](?:#|b)? (?:Major|Minor)", value.strip()))


def is_valid_percent(value: str | int | float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0 < number <= 100


def _read_tags(audio: Any) -> dict[str, list[str]]:
    if audio is None or audio.tags is None:
        return {}
    tags: dict[str, list[str]] = {}
    for key, value in audio.tags.items():
        for normalized_key, normalized_value in _tag_values(key, value):
            tags.setdefault(normalized_key.lower(), []).append(normalized_value)
            if normalized_key.lower() != key.lower() and (normalized_value.strip() in BAD_VALUES or (normalized_key.lower() == "bpm" and not is_valid_bpm(normalized_value))):
                tags.setdefault(key.lower(), []).append(normalized_value)
    return tags


def _tag_values(key: str, value: Any) -> list[tuple[str, str]]:
    if key == "trkn" and value:
        first = value[0]
        if isinstance(first, tuple) and first:
            return [("tracknumber", str(first[0]))]
    mapped_key = _logical_key(key)
    if isinstance(value, USLT):
        return [("lyrics", str(value.text))]
    if isinstance(value, SYLT):
        return [("synced_lyrics", str(value.text))]
    if isinstance(value, TXXX):
        mapped_desc = _logical_key(value.desc)
        return [(mapped_desc, str(item)) for item in value.text]
    if isinstance(value, UFID):
        owner = value.owner.decode("utf-8", "ignore") if isinstance(value.owner, bytes) else value.owner
        data = value.data.decode("utf-8", "ignore") if isinstance(value.data, bytes) else str(value.data)
        ufid_key = f"UFID:{owner}"
        return [(_logical_key(ufid_key), data)]
    if key.startswith("----:com.apple.iTunes:"):
        return [(mapped_key, _decode_mp4_freeform(item)) for item in value]
    if isinstance(value, list):
        return [(mapped_key, str(item)) for item in value]
    text = getattr(value, "text", None)
    if isinstance(text, list):
        return [(mapped_key, str(item)) for item in text]
    return [(mapped_key, str(value))]


def _decode_mp4_freeform(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return str(value)


def _logical_key(key: str) -> str:
    lowered = key.lower()
    if lowered.startswith("----:com.apple.itunes:"):
        name = lowered.rsplit(":", 1)[-1]
        return TAG_ALIASES.get(name, key)
    return TAG_ALIASES.get(lowered, key)


def _first(tags: dict[str, list[str]], *keys: str) -> str:
    for key in keys:
        values = tags.get(key.lower(), [])
        if values:
            return values[0]
    return ""


def _track_number(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value.split("/", 1)[0])
    except ValueError:
        return None


LOGICAL_TAG_KEYS = {
    "mb_album_id": [
        "mb_album_id",
        "MUSICBRAINZ_ALBUMID",
        "MusicBrainz Album Id",
        "----:com.apple.iTunes:MusicBrainz Album Id",
    ],
    "mb_release_group_id": [
        "mb_release_group_id",
        "MUSICBRAINZ_RELEASEGROUPID",
        "MusicBrainz Release Group Id",
        "----:com.apple.iTunes:MusicBrainz Release Group Id",
    ],
    "mb_track_id": [
        "mb_track_id",
        "MUSICBRAINZ_TRACKID",
        "MusicBrainz Track Id",
        "UFID:http://musicbrainz.org",
        "----:com.apple.iTunes:MusicBrainz Track Id",
    ],
    "mb_release_track_id": [
        "mb_release_track_id",
        "MUSICBRAINZ_RELEASETRACKID",
        "MusicBrainz Release Track Id",
        "----:com.apple.iTunes:MusicBrainz Release Track Id",
    ],
    "mb_album_artist_id": [
        "mb_album_artist_id",
        "MUSICBRAINZ_ALBUMARTISTID",
        "MusicBrainz Album Artist Id",
        "----:com.apple.iTunes:MusicBrainz Album Artist Id",
    ],
    "label": ["LABEL", "organization", "publisher", "----:com.apple.iTunes:LABEL", "----:com.apple.iTunes:publisher"],
    "genre": ["GENRE", "genre", "----:com.apple.iTunes:GENRE", "\xa9gen"],
    "style": ["STYLE", "----:com.apple.iTunes:STYLE"],
    "catalog_number": ["catalog_number", "CATALOGNUMBER", "Catalog Number", "CatalogNumber", "CATALOG NUMBER", "----:com.apple.iTunes:CATALOGNUMBER", "----:com.apple.iTunes:Catalog Number"],
    "barcode": ["barcode", "BARCODE", "Barcode", "----:com.apple.iTunes:BARCODE"],
    "country": ["country", "RELEASECOUNTRY", "COUNTRY", "Release Country", "----:com.apple.iTunes:RELEASECOUNTRY", "----:com.apple.iTunes:COUNTRY"],
    "media": ["media", "MEDIA", "FORMAT", "Media", "Format", "----:com.apple.iTunes:MEDIA", "----:com.apple.iTunes:FORMAT"],
    "audio_codec": ["audio_codec", "AUDIOCODEC", "Audio Codec", "Media", "FORMAT", "----:com.apple.iTunes:AUDIOCODEC", "----:com.apple.iTunes:MEDIA", "----:com.apple.iTunes:FORMAT"],
    "release_format": ["release_format", "RELEASEFORMAT", "Release Format", "----:com.apple.iTunes:RELEASEFORMAT"],
    "edition": ["edition", "EDITION", "Edition", "----:com.apple.iTunes:EDITION"],
    "release_type": ["release_type", "RELEASETYPE", "ALBUMTYPE", "Release Type", "Album Type", "----:com.apple.iTunes:RELEASETYPE", "----:com.apple.iTunes:ALBUMTYPE"],
    "isrc": ["ISRC", "----:com.apple.iTunes:ISRC"],
    "date": ["DATE", "Date", "YEAR", "year", "\xa9day"],
    "originaldate": ["ORIGINALDATE", "Original Date", "ORIGINAL YEAR", "originalyear", "----:com.apple.iTunes:ORIGINALDATE"],
    "tracktotal": ["TRACKTOTAL", "Track Total", "tracktotal", "totaltracks", "----:com.apple.iTunes:Track Total"],
    "disc": ["DISCNUMBER", "Disc Number", "discnumber", "disk", "disknumber", "----:com.apple.iTunes:Disc Number"],
    "disctotal": ["DISCTOTAL", "Disc Total", "disctotal", "totaldiscs", "----:com.apple.iTunes:Disc Total"],
    "explicit": ["EXPLICIT", "Explicit", "explicit", "----:com.apple.iTunes:Explicit"],
    "deezer_album_id": ["DEEZER_ALBUM_ID", "Deezer Album Id", "----:com.apple.iTunes:DEEZER_ALBUM_ID"],
    "deezer_track_id": ["DEEZER_TRACK_ID", "Deezer Track Id", "----:com.apple.iTunes:DEEZER_TRACK_ID"],
    "itunes_collection_id": ["ITUNES_COLLECTION_ID", "iTunes Collection Id", "----:com.apple.iTunes:ITUNES_COLLECTION_ID"],
    "itunes_track_id": ["ITUNES_TRACK_ID", "iTunes Track Id", "----:com.apple.iTunes:ITUNES_TRACK_ID"],
    "acoustid_id": ["ACOUSTID_ID", "AcoustID Id", "Acoustid Id", "----:com.apple.iTunes:ACOUSTID_ID"],
    "acoustid_fingerprint": ["ACOUSTID_FINGERPRINT", "AcoustID Fingerprint", "Acoustid Fingerprint", "----:com.apple.iTunes:ACOUSTID_FINGERPRINT"],
    "bpm": ["bpm", "BPM", "TBPM", "tmpo", "----:com.apple.iTunes:BPM"],
    "key": ["key", "KEY", "initialkey", "INITIALKEY", "TKEY", "----:com.apple.iTunes:KEY", "----:com.apple.iTunes:INITIALKEY"],
    "energy": ["energy", "ENERGY", "----:com.apple.iTunes:ENERGY"],
    "danceability": ["danceability", "DANCEABILITY", "----:com.apple.iTunes:DANCEABILITY"],
    "replaygain_track_gain": ["replaygain_track_gain", "REPLAYGAIN_TRACK_GAIN", "ReplayGain Track Gain", "----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN"],
    "replaygain_track_peak": ["replaygain_track_peak", "REPLAYGAIN_TRACK_PEAK", "ReplayGain Track Peak", "----:com.apple.iTunes:REPLAYGAIN_TRACK_PEAK"],
    "replaygain_album_gain": ["replaygain_album_gain", "REPLAYGAIN_ALBUM_GAIN", "ReplayGain Album Gain", "----:com.apple.iTunes:REPLAYGAIN_ALBUM_GAIN"],
    "replaygain_album_peak": ["replaygain_album_peak", "REPLAYGAIN_ALBUM_PEAK", "ReplayGain Album Peak", "----:com.apple.iTunes:REPLAYGAIN_ALBUM_PEAK"],
    "loudness": ["loudness", "LOUDNESS", "LUFS", "----:com.apple.iTunes:LOUDNESS", "----:com.apple.iTunes:LUFS"],
    "lastfm_tags": ["lastfm_tags", "LASTFM_TAGS", "----:com.apple.iTunes:LASTFM_TAGS"],
    "mood": ["mood", "MOOD", "----:com.apple.iTunes:MOOD"],
    "cover": ["cover", "COVER"],
    "lyrics": ["lyrics", "LYRICS", "USLT", "\xa9lyr"],
    "synced_lyrics": ["synced_lyrics", "SYNCEDLYRICS", "SYLT", "LRC"],
}

TAG_ALIASES = {
    "\xa9nam": "title",
    "title": "title",
    "\xa9art": "artist",
    "artist": "artist",
    "aart": "albumartist",
    "albumartist": "albumartist",
    "album artist": "albumartist",
    "\xa9alb": "album",
    "album": "album",
    "\xa9day": "date",
    "\xa9gen": "genre",
    "tit2": "title",
    "tpe1": "artist",
    "tpe2": "albumartist",
    "talb": "album",
    "tdrc": "date",
    "tdor": "originaldate",
    "tcon": "genre",
    "tbpm": "bpm",
    "tmpo": "bpm",
    "bpm": "bpm",
    "tkey": "key",
    "key": "key",
    "initialkey": "key",
    "energy": "energy",
    "danceability": "danceability",
    "replaygain_track_gain": "replaygain_track_gain",
    "replaygain track gain": "replaygain_track_gain",
    "replaygain_track_peak": "replaygain_track_peak",
    "replaygain track peak": "replaygain_track_peak",
    "replaygain_album_gain": "replaygain_album_gain",
    "replaygain album gain": "replaygain_album_gain",
    "replaygain_album_peak": "replaygain_album_peak",
    "replaygain album peak": "replaygain_album_peak",
    "loudness": "loudness",
    "lufs": "loudness",
    "lastfm_tags": "lastfm_tags",
    "mood": "mood",
    "uslt": "lyrics",
    "\xa9lyr": "lyrics",
    "lyrics": "lyrics",
    "syncedlyrics": "synced_lyrics",
    "sylt": "synced_lyrics",
    "lrc": "synced_lyrics",
    "tcmp": "compilation",
    "label": "label",
    "publisher": "label",
    "style": "style",
    "catalognumber": "catalog_number",
    "catalog number": "catalog_number",
    "barcode": "barcode",
    "releasecountry": "country",
    "release country": "country",
    "country": "country",
    "media": "media",
    "format": "media",
    "audiocodec": "audio_codec",
    "audio codec": "audio_codec",
    "releaseformat": "release_format",
    "release format": "release_format",
    "edition": "edition",
    "releasetype": "release_type",
    "release type": "release_type",
    "albumtype": "release_type",
    "album type": "release_type",
    "isrc": "isrc",
    "originaldate": "originaldate",
    "original date": "originaldate",
    "original year": "originaldate",
    "originalyear": "originaldate",
    "discnumber": "disc",
    "disc number": "disc",
    "disknumber": "disc",
    "disk": "disc",
    "musicbrainz_albumid": "mb_album_id",
    "musicbrainz album id": "mb_album_id",
    "musicbrainz_releasegroupid": "mb_release_group_id",
    "musicbrainz release group id": "mb_release_group_id",
    "musicbrainz_trackid": "mb_track_id",
    "musicbrainz track id": "mb_track_id",
    "musicbrainz releasetrackid": "mb_release_track_id",
    "musicbrainz release track id": "mb_release_track_id",
    "musicbrainz_albumartistid": "mb_album_artist_id",
    "musicbrainz album artist id": "mb_album_artist_id",
    "acoustid_id": "acoustid_id",
    "acoustid id": "acoustid_id",
    "acoustid_fingerprint": "acoustid_fingerprint",
    "acoustid fingerprint": "acoustid_fingerprint",
    "ufid:http://musicbrainz.org": "mb_track_id",
}
