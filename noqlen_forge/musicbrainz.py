from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .audio import Track
from .cache import cache_key, read_json, write_json
from .config import APP_USER_AGENT

BASE_URL = "https://musicbrainz.org/ws/2"


USER_AGENT = f"{APP_USER_AGENT} (Noqlen Forge Core metadata tool)"
RETRY_STATUSES = {429, 500, 502, 503, 504}
_last_request = 0.0


def search_releases(tracks: list[Track]) -> list[dict]:
    artist = _first(track.albumartist or track.artist for track in tracks)
    album = _first(track.album for track in tracks)
    if not artist or not album:
        return []
    releases: list[dict] = []
    seen: set[str] = set()
    for query in _release_queries(tracks, artist, album):
        for release in _search_release_query(query):
            release_id = release.get("id")
            if release_id and release_id not in seen:
                seen.add(release_id)
                releases.append(release)
    return releases


def _search_release_query(query: str) -> list[dict]:
    cache_path = cache_key("search", query)
    cached = read_json(cache_path)
    if cached is not None:
        return cached.get("releases", [])
    url = f"{BASE_URL}/release/?" + urllib.parse.urlencode({"query": query, "fmt": "json", "limit": "10"})
    data = _get_json(url)
    write_json(cache_path, data)
    return data.get("releases", [])


def _release_queries(tracks: list[Track], artist: str, album: str) -> list[str]:
    queries = [f'artist:"{artist}" AND release:"{album}"']
    if len(tracks) == 1:
        track = tracks[0]
        albumartist = track.albumartist or artist
        titles = _single_title_candidates(track)
        if len(titles) > 1:
            queries.append(f'artist:"{albumartist}" AND release:"{titles[1]}"')
        if track.title and track.artist:
            queries.append(f'artist:"{track.artist}" AND release:"{track.title}"')
            queries.append(f'artist:"{track.artist}" AND recording:"{track.title}"')
    return _dedupe_strings(queries)


def _single_title_candidates(track: Track) -> list[str]:
    candidates = [track.album, _strip_single_suffix(track.album), track.title]
    folder = track.path.parent.name
    artist_prefixes = [track.albumartist, track.artist]
    for artist in artist_prefixes:
        if artist and folder.lower().startswith(f"{artist.lower()} - "):
            folder = folder[len(artist) + 3 :]
            break
    candidates.append(_strip_single_suffix(folder))
    return _dedupe_strings(value.strip() for value in candidates if value and value.strip())


def _strip_single_suffix(value: str) -> str:
    clean = value.strip()
    suffixes = (" - Single", "- Single", " Single", "(Single)", "[Single]", " - EP", "(EP)")
    lowered = clean.lower()
    for suffix in suffixes:
        if lowered.endswith(suffix.lower()):
            return clean[: -len(suffix)].strip()
    return clean


def _dedupe_strings(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def get_release(release_id: str) -> dict:
    cache_path = cache_key("release", release_id)
    cached = read_json(cache_path)
    if cached is not None:
        return cached
    inc = "artists+artist-credits+recordings+release-groups+labels+media"
    url = f"{BASE_URL}/release/{release_id}?" + urllib.parse.urlencode({"inc": inc, "fmt": "json"})
    data = _get_json(url)
    write_json(cache_path, data)
    return data


def hydrate_releases(releases: list[dict]) -> list[dict]:
    hydrated: list[dict] = []
    for release in releases:
        release_id = release.get("id")
        if release_id:
            hydrated.append(get_release(release_id))
    return hydrated


def _get_json(url: str) -> Any:
    last_error: Exception | None = None
    for attempt in range(5):
        _rate_limit()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUSES:
                raise
        except (urllib.error.URLError, ConnectionResetError, TimeoutError) as exc:
            last_error = exc
        time.sleep(min(8, 2**attempt))
    raise RuntimeError(f"MusicBrainz request failed after retries: {last_error}")


def _rate_limit() -> None:
    global _last_request
    now = time.monotonic()
    wait = 1.0 - (now - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _first(values) -> str:
    for value in values:
        if value:
            return value
    return ""
