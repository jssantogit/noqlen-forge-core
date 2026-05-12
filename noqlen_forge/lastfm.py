from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .audio import audio_files, get_tag, read_track
from .config import APP_SLUG, get_config_value, load_config
from .lastfm_filter import TagContext, clean_existing_lastfm_tags, filter_tags, normalize_tag

LASTFM_ROOT = "https://ws.audioscrobbler.com/2.0/"
CACHE_ROOT = Path.home() / ".cache" / APP_SLUG / "lastfm"
TAG_SEPARATOR = "; "
_LAST_REQUEST = 0.0

NOISE_TAGS = {
    "seen live",
    "fav",
    "favs",
    "favorites",
    "favorite",
    "favourite",
    "loved",
    "my top tracks",
    "under 2000 listeners",
    "spotify",
    "youtube",
    "local files",
    "maris song",
    "one time flamengo",
    "hit",
    "vocal",
    "00s",
    "10s",
    "20s",
}

RELATED_ARTIST_NOISE = {"aespa", "ive", "blackpink", "twice", "bts", "le sserafim"}

TAG_NORMALIZATIONS = {
    "kpop": "K-pop",
    "k-pop": "K-pop",
    "k-rnb": "K-R&B",
    "k-r&b": "K-R&B",
    "korean rnb": "Korean R&B",
    "korean r&b": "Korean R&B",
    "korean": "Korean",
    "girl group": "Girl Group",
    "girl groups": "Girl Group",
    "rnb": "R&B",
    "r&b": "R&B",
    "contemporary rnb": "Contemporary R&B",
    "contemporary r&b": "Contemporary R&B",
    "alternative rnb": "Alternative R&B",
    "alternative r&b": "Alternative R&B",
    "uk garage": "UK Garage",
    "drum and bass": "Drum n Bass",
    "drum n bass": "Drum n Bass",
    "jersey club": "Jersey Club",
    "baltimore club": "Baltimore Club",
    "funk carioca": "Funk Carioca",
    "dance-pop": "Dance-pop",
    "future bass": "Future Bass",
    "future house": "Future House",
    "bedroom pop": "Bedroom Pop",
    "hip hop soul": "Hip Hop Soul",
    "neo-soul": "Neo-Soul",
    "neo soul": "Neo-Soul",
    "synth-pop": "Synth-pop",
    "technical death metal": "Technical Death Metal",
    "progressive death metal": "Progressive Death Metal",
    "neoclassical metal": "Neoclassical Metal",
}

ALLOWLIST_TAGS = {
    "K-pop",
    "Pop",
    "Dance-pop",
    "Synth-pop",
    "R&B",
    "Contemporary R&B",
    "Alternative R&B",
    "K-R&B",
    "Korean R&B",
    "UK Garage",
    "2-step",
    "Jersey Club",
    "Baltimore Club",
    "Funk Carioca",
    "Drum n Bass",
    "Footwork",
    "Future Bass",
    "Future House",
    "Future Garage",
    "Bedroom Pop",
    "Hip Hop Soul",
    "Neo-Soul",
    "Outsider House",
    "Technical Death Metal",
    "Death Metal",
    "Progressive Death Metal",
    "Progressive Metal",
    "Neoclassical Metal",
    "Metal",
    "Brutal Death Metal",
    "Melodic Death Metal",
    "Jazz Fusion",
    "Progressive Rock",
    "Melancholic",
    "Energetic",
    "Aggressive",
    "Dreamy",
    "Chill",
    "Atmospheric",
    "Dark",
    "Happy",
    "Sad",
}
ALLOWLIST_LOWER = {tag.lower(): tag for tag in ALLOWLIST_TAGS}
DESCRIPTOR_TAGS = {"catchy", "korean"}
MBID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


@dataclass(slots=True)
class LastfmPlan:
    path: Path
    tags: list[str]
    source: str = ""
    confidence: str = ""
    raw_tags: list[str] = field(default_factory=list)
    removed: list[tuple[str, str]] = field(default_factory=list)
    debug_lines: list[str] = field(default_factory=list)
    existing: str = ""
    skipped: str = ""
    warning: str = ""


@dataclass(slots=True)
class LastfmFetchResult:
    tags: list[dict[str, Any]]
    source: str = ""
    confidence: str = ""
    debug_lines: list[str] = field(default_factory=list)


def get_lastfm_api_key() -> str:
    env_value = os.environ.get("LASTFM_API_KEY", "").strip()
    if env_value:
        return env_value
    return str(get_config_value(load_config(), "apis", "lastfm_api_key", "")).strip()


def fetch_track_top_tags(artist: str, title: str, mbid: str | None = None, debug: bool = False) -> list[dict[str, Any]]:
    tags, _debug = fetch_track_top_tags_debug(artist, title, mbid=mbid, debug=debug)
    return tags


def fetch_track_top_tags_debug(artist: str, title: str, mbid: str | None = None, debug: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    api_key = get_lastfm_api_key()
    if not api_key:
        return [], []
    debug_lines: list[str] = []
    clean_mbid = mbid.strip() if mbid else ""
    valid_mbid = clean_mbid if _valid_mbid(clean_mbid) else ""
    if debug:
        debug_lines.append(f"artist={artist or 'unknown'}")
        debug_lines.append(f"title={title or 'unknown'}")
        if valid_mbid:
            debug_lines.append(f"mbid={valid_mbid}")
        elif clean_mbid:
            debug_lines.append("mbid=invalid/skipped")
    if artist and title:
        params = {"artist": artist, "track": title, "api_key": api_key}
        data = _fetch_cached("track", _normalize_cache_part(artist), _normalize_cache_part(title), params=params, debug=debug)
        debug_lines.extend(_debug_response(data, params, debug))
        tags = _extract_tags(data)
        if tags:
            return tags, debug_lines
    if valid_mbid:
        params = {"mbid": valid_mbid, "api_key": api_key}
        data = _fetch_cached("mbid", valid_mbid.lower(), params=params, debug=debug)
        debug_lines.extend(_debug_response(data, params, debug))
        return _extract_tags(data), debug_lines
    return [], debug_lines


def fetch_best_lastfm_tags_debug(artist: str, title: str, album: str = "", mbid: str | None = None, debug: bool = False, allow_fallback: bool = True, min_count: int = 3, max_tags: int = 10) -> LastfmFetchResult:
    api_key = get_lastfm_api_key()
    if not api_key:
        return LastfmFetchResult(tags=[])
    debug_lines: list[str] = []
    clean_artist = artist.strip()
    clean_title = title.strip()
    clean_album = album.strip()
    clean_mbid = mbid.strip() if mbid else ""
    valid_mbid = clean_mbid if _valid_mbid(clean_mbid) else ""
    attempts: list[tuple[str, str, dict[str, str], tuple[str, ...]]] = []
    if _known(clean_artist) and clean_title:
        attempts.append(("track", "high", {"method": "track.getTopTags", "artist": clean_artist, "track": clean_title, "api_key": api_key}, ("track", _normalize_cache_part(clean_artist), _normalize_cache_part(clean_title))))
    if valid_mbid:
        attempts.append(("track", "high", {"method": "track.getTopTags", "mbid": valid_mbid, "api_key": api_key}, ("mbid", valid_mbid.lower())))
    if allow_fallback and _known(clean_artist) and _known(clean_album):
        attempts.append(("album", "high", {"method": "album.getTopTags", "artist": clean_artist, "album": clean_album, "api_key": api_key}, ("album", _normalize_cache_part(clean_artist), _normalize_cache_part(clean_album))))
    if allow_fallback and _known(clean_artist):
        attempts.append(("artist", "medium", {"method": "artist.getTopTags", "artist": clean_artist, "api_key": api_key}, ("artist", _normalize_cache_part(clean_artist))))
    if debug:
        debug_lines.append(f"artist={clean_artist or 'unknown'}")
        debug_lines.append(f"title={clean_title or 'unknown'}")
        debug_lines.append(f"album={clean_album or 'unknown'}")
        if valid_mbid:
            debug_lines.append(f"mbid={valid_mbid}")
        elif clean_mbid:
            debug_lines.append("mbid=invalid/skipped")
    for source, confidence, params, cache_parts in attempts:
        data = _fetch_cached(*cache_parts, params=params, debug=debug)
        debug_lines.extend(_debug_response(data, params, debug))
        raw_tags = _extract_tags(data)
        context = TagContext(artist=clean_artist, album=clean_album, title=clean_title, source=source)
        filtered = filter_tags(raw_tags, context=context, min_count=min_count, max_tags=max_tags).kept
        if filtered:
            return LastfmFetchResult(tags=raw_tags, source=source, confidence=confidence, debug_lines=debug_lines)
    return LastfmFetchResult(tags=[], debug_lines=debug_lines)


def normalize_lastfm_tag(tag: str) -> str:
    return normalize_tag(tag)


def filter_lastfm_tags(tags: list[dict[str, Any]], min_count: int = 3, max_tags: int = 10, artist: str = "") -> list[str]:
    filtered, _removed = filter_lastfm_tags_with_report(tags, min_count=min_count, max_tags=max_tags, artist=artist)
    return filtered


def filter_lastfm_tags_with_report(tags: list[dict[str, Any]], min_count: int = 3, max_tags: int = 10, artist: str = "") -> tuple[list[str], list[tuple[str, str]]]:
    result = filter_tags(tags, context=TagContext(artist=artist), min_count=min_count, max_tags=max_tags)
    return result.kept, [(decision.original, decision.reason) for decision in result.removed]


def analyze_lastfm_tags(path: Path, apply: bool = False, force: bool = False, min_count: int = 3, max_tags: int = 10, debug: bool = False, raw: bool = False, allow_fallback: bool = True) -> tuple[int, str]:
    files = audio_files(path)
    if not files:
        return 1, "No supported audio files found"
    if not get_lastfm_api_key():
        return 0, "Last.fm: skipped, LASTFM_API_KEY not set."
    plans: list[LastfmPlan] = []
    for file_path in files:
        track = read_track(file_path)
        context = TagContext(artist=track.artist, albumartist=track.albumartist, album=track.album, title=track.title)
        existing = clean_existing_lastfm_tags(get_tag(track, "lastfm_tags"), context)
        if existing and not force:
            plans.append(LastfmPlan(path=file_path, tags=[], existing=existing, skipped=f"skipped existing LASTFM_TAGS={existing}"))
            continue
        artist = track.artist or track.albumartist
        mbid = _first(get_tag(track, "mb_track_id"))
        try:
            result = fetch_best_lastfm_tags_debug(artist, track.title, album=track.album, mbid=mbid, debug=debug, allow_fallback=allow_fallback, min_count=min_count, max_tags=max_tags)
            raw_tags, debug_lines = result.tags, result.debug_lines
            context.source = result.source
            filtered_result = filter_tags(raw_tags, context=context, min_count=min_count, max_tags=max_tags)
            filtered = filtered_result.kept
            removed = [(decision.original, decision.reason) for decision in filtered_result.removed]
        except Exception as exc:
            plans.append(LastfmPlan(path=file_path, tags=[], warning=f"Last.fm: failed for {file_path}: {exc}"))
            continue
        plans.append(LastfmPlan(path=file_path, tags=filtered, source=result.source, confidence=result.confidence, raw_tags=[str(item.get("name", "")).strip() for item in raw_tags if item.get("name")], removed=removed, debug_lines=debug_lines))
        if apply and filtered:
            write_lastfm_tags(file_path, filtered)
    return 0, summarize_lastfm_tags(plans, apply=apply, debug=debug, raw=raw)


def write_lastfm_tags(path: Path, tags: list[str]) -> None:
    value = TAG_SEPARATOR.join(normalize_lastfm_tags(tags))
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _write_mp3_lastfm_tags(path, value)
        return
    audio = MutagenFile(path, easy=False)
    if audio is None:
        return
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        _write_mp4_lastfm_tags(audio, value)
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        audio["LASTFM_TAGS"] = [value]
    audio.save()


def normalize_lastfm_tags(values: list[str]) -> list[str]:
    value = clean_existing_lastfm_tags(values, TagContext())
    return [value] if value else []


def summarize_lastfm_tags(plans: list[LastfmPlan], apply: bool, debug: bool = False, raw: bool = False) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    lines = [f"{mode}: Last.fm tags"]
    for plan in plans:
        if plan.warning:
            lines.append(f"- {plan.path}: {plan.warning}")
        elif plan.skipped:
            lines.append(f"- {plan.path}: {plan.skipped}")
        elif plan.tags:
            value = TAG_SEPARATOR.join(plan.tags)
            action = f"wrote LASTFM_TAGS={value}" if apply else "would write"
            source = f" source={plan.source}" if plan.source else ""
            confidence = f" confidence={plan.confidence}" if plan.confidence else ""
            lines.append(f"- {plan.path}: tags={value}{source}{confidence} action={action}")
        else:
            lines.append(f"- {plan.path}: no reliable Last.fm tags found")
        if raw and plan.raw_tags:
            lines.append(f"  raw={TAG_SEPARATOR.join(plan.raw_tags)}")
        if debug:
            for line in plan.debug_lines:
                lines.append(f"  debug: {line}")
            for tag, reason in plan.removed:
                source = f" source={plan.source}" if plan.source else ""
                confidence = f" confidence={plan.confidence}" if plan.confidence else ""
                lines.append(f"  removed: \"{tag}\" reason={reason}{source}{confidence}")
    return "\n".join(lines)


def _write_mp3_lastfm_tags(path: Path, value: str) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("TXXX:LASTFM_TAGS")
    tags.add(TXXX(encoding=3, desc="LASTFM_TAGS", text=[value]))
    tags.save(path)


def _write_mp4_lastfm_tags(audio: MP4, value: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    audio.tags["----:com.apple.iTunes:LASTFM_TAGS"] = [MP4FreeForm(value.encode("utf-8"))]


def _fetch_cached(kind: str, *parts: str, params: dict[str, str], debug: bool = False) -> dict[str, Any]:
    path = _cache_path(kind, *parts)
    cached = _read_cache(path)
    if cached is not None:
        return cached
    data = _request_lastfm(params, debug=debug)
    if not data.get("_http_status") and not data.get("_http_error"):
        _write_cache(path, data)
    return data


def _request_lastfm(params: dict[str, str], debug: bool = False) -> dict[str, Any]:
    global _LAST_REQUEST
    url = _lastfm_url(params)
    elapsed = time.monotonic() - _LAST_REQUEST
    if _LAST_REQUEST and elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                _LAST_REQUEST = time.monotonic()
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            _LAST_REQUEST = time.monotonic()
            body = exc.read().decode("utf-8", "replace")
            return {"_http_status": exc.code, "_http_body": body, "_debug_url": _lastfm_url(params, include_api_key=False)}
        except (OSError, URLError) as exc:
            if attempt == 2:
                return {"_http_error": str(exc), "_debug_url": _lastfm_url(params, include_api_key=False)}
            time.sleep(0.5 * (attempt + 1))
    return {}


def _lastfm_url(params: dict[str, str], include_api_key: bool = True) -> str:
    query = {"method": "track.getTopTags", "format": "json", **params}
    if not include_api_key:
        query.pop("api_key", None)
    return f"{LASTFM_ROOT}?{urllib.parse.urlencode(query)}"


def _extract_tags(data: dict[str, Any]) -> list[dict[str, Any]]:
    if data.get("_http_status") or data.get("_http_error"):
        return []
    tag_data = ((data or {}).get("toptags") or {}).get("tag") or []
    if isinstance(tag_data, dict):
        tag_data = [tag_data]
    return [item for item in tag_data if isinstance(item, dict)]


def _cache_path(*parts: str) -> Path:
    raw = "\0".join(parts).encode("utf-8")
    return CACHE_ROOT / f"{hashlib.sha256(raw).hexdigest()}.json"


def _read_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)


def _normalize_cache_part(value: str) -> str:
    return normalize_lastfm_tag(value).lower()


def _tag_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first(values: list[str]) -> str:
    return values[0] if values else ""


def _valid_mbid(value: str) -> bool:
    return bool(value and MBID_RE.fullmatch(value.strip()))


def _known(value: str) -> bool:
    return bool(value and value.strip().lower() != "unknown")


def _lastfm_tag_rank(name: str) -> int:
    return 1 if name.lower() in DESCRIPTOR_TAGS else 0


def _debug_response(data: dict[str, Any], params: dict[str, str], debug: bool) -> list[str]:
    if not debug:
        return []
    lines = [f"url={_lastfm_url(params, include_api_key=False)}"]
    if data.get("_http_status"):
        lines.append(f"http_status={data['_http_status']}")
        if data.get("_http_body"):
            lines.append(f"http_body={data['_http_body']}")
    elif data.get("_http_error"):
        lines.append(f"http_error={data['_http_error']}")
    return lines


def _lastfm_noise_reason(raw_name: str, name: str, artist_lower: str) -> str:
    lowered = name.lower()
    raw_lower = raw_name.lower()
    if lowered in NOISE_TAGS or raw_lower in NOISE_TAGS:
        return "noise tag"
    if re.fullmatch(r"19\d\d|20[0-2]\d|203[0-5]", raw_lower):
        return "isolated year"
    if re.fullmatch(r"(?:\d{2}|\d{4})s", raw_lower):
        return "decade tag"
    if len(raw_name.split()) > 4 and lowered not in ALLOWLIST_LOWER:
        return "long personal phrase"
    if artist_lower and lowered == artist_lower:
        return "artist name tag"
    if raw_lower in RELATED_ARTIST_NOISE and raw_lower != artist_lower:
        return "unrelated artist tag"
    return ""
