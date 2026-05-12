from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .audio import Track, get_tag
from .config import APP_USER_AGENT
from .provider_common import compare_ratio, confidence_allows, match_confidence, normalize_album_title, normalize_artist_name, normalize_track_title, safe_debug_url

MAX_IMAGE_BYTES = 10 * 1024 * 1024
COVER_ART_ARCHIVE_URL = "https://coverartarchive.org/release"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
USER_AGENT = f"{APP_USER_AGENT} (Noqlen Forge Core metadata tool)"
LOCAL_COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg", "folder.png", "front.jpg", "front.jpeg", "front.png")


@dataclass(slots=True)
class ImageInfo:
    data: bytes
    mime: str
    extension: str
    width: int | None = None
    height: int | None = None


@dataclass(slots=True)
class CoverResult:
    data: bytes
    mime: str
    source: str
    provider: str
    width: int | None = None
    height: int | None = None
    size_bytes: int = 0
    confidence: str = "medium"
    match_reason: str = ""
    external_url: str | None = None
    release_id: str | None = None
    album_id: str | None = None


@dataclass(slots=True)
class ProviderAttempt:
    provider: str
    status: str
    message: str
    debug: list[str] = field(default_factory=list)
    result: CoverResult | None = None


@dataclass(slots=True)
class CoverProvider:
    name: str
    enabled: bool = True
    requires_api_key: bool = False
    supports_album: bool = True
    supports_track: bool = True

    def fetch(self, tracks: list[Track], target_dir: Path, prefer_front: bool = True, max_size_mb: int = 10, debug: bool = False) -> ProviderAttempt:
        raise NotImplementedError


class LocalCoverProvider(CoverProvider):
    def __init__(self) -> None:
        super().__init__(name="local", requires_api_key=False)

    def fetch(self, tracks: list[Track], target_dir: Path, prefer_front: bool = True, max_size_mb: int = 10, debug: bool = False) -> ProviderAttempt:
        for name in LOCAL_COVER_NAMES:
            path = target_dir / name
            if not path.is_file():
                continue
            info = validate_image_bytes(path.read_bytes(), max_bytes=max_size_mb * 1024 * 1024)
            if info is None:
                return ProviderAttempt(self.name, "WARN", f"{name}: invalid image bytes")
            return ProviderAttempt(self.name, "OK", f"local cover {name}", result=cover_result(info, provider=self.name, source=f"local:{name}", confidence="high", match_reason="local folder cover"))
        return ProviderAttempt(self.name, "SKIP", "no local cover file")


class MusicBrainzCoverProvider(CoverProvider):
    def __init__(self) -> None:
        super().__init__(name="musicbrainz", requires_api_key=False, supports_track=False)

    def fetch(self, tracks: list[Track], target_dir: Path, prefer_front: bool = True, max_size_mb: int = 10, debug: bool = False) -> ProviderAttempt:
        release_id = common_release_id(tracks)
        if not release_id:
            return ProviderAttempt(self.name, "SKIP", "MusicBrainz Album Id missing")
        debug_lines: list[str] = []
        url = f"{COVER_ART_ARCHIVE_URL}/{urllib.parse.quote(release_id)}"
        if debug:
            debug_lines.extend([f"musicbrainz url: {url}", f"release_id: {release_id}"])
        try:
            payload = get_bytes(url, accept="application/json", max_bytes=1024 * 1024)
            data = json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return ProviderAttempt(self.name, "WARN", f"HTTP {exc.code}", debug_lines)
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"response rejected: {exc}", debug_lines)
        images = data.get("images") if isinstance(data, dict) else []
        if debug:
            debug_lines.append(f"candidates: {len(images or [])}")
        for image in preferred_images(images or [], prefer_front=prefer_front):
            image_url = image_url_from_row(image)
            if not image_url:
                continue
            if debug:
                debug_lines.append(f"image url: {safe_debug_url(image_url)}")
            try:
                info = validate_image_bytes(get_bytes(image_url, accept="image/*", max_bytes=max_size_mb * 1024 * 1024), max_bytes=max_size_mb * 1024 * 1024)
            except Exception as exc:
                debug_lines.append(f"image rejected: {exc}") if debug else None
                continue
            if info is None:
                debug_lines.append("image rejected: invalid image bytes") if debug else None
                continue
            return ProviderAttempt(self.name, "OK", "release MBID match", debug_lines, cover_result(info, provider=self.name, source="Cover Art Archive", confidence="high", match_reason="release MBID match", external_url=image_url, release_id=release_id, album_id=release_id))
        return ProviderAttempt(self.name, "WARN", "no valid front cover image", debug_lines)


class ITunesCoverProvider(CoverProvider):
    def __init__(self) -> None:
        super().__init__(name="itunes", requires_api_key=False)

    def fetch(self, tracks: list[Track], target_dir: Path, prefer_front: bool = True, max_size_mb: int = 10, debug: bool = False) -> ProviderAttempt:
        artist, album, title = common_artist_album_title(tracks)
        term = " ".join(part for part in (artist, album or title) if part)
        if not artist or not (album or title):
            return ProviderAttempt(self.name, "SKIP", "missing artist and album/title")
        params = {"term": term, "entity": "album" if album else "song", "limit": "10"}
        url = f"{ITUNES_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        debug_lines = [f"itunes url: {safe_debug_url(url)}"] if debug else []
        try:
            rows = json.loads(get_bytes(url, accept="application/json", max_bytes=1024 * 1024).decode("utf-8")).get("results", [])
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"response rejected: {exc}", debug_lines)
        best = best_itunes_match(rows, artist, album, title)
        if best is None:
            return ProviderAttempt(self.name, "WARN", "no metadata match", debug_lines)
        confidence, reason, row = best
        artwork = str(row.get("artworkUrl100") or "")
        if not artwork:
            return ProviderAttempt(self.name, "WARN", "matched result has no artwork", debug_lines)
        artwork = upscale_itunes_artwork(artwork)
        try:
            info = validate_image_bytes(get_bytes(artwork, accept="image/*", max_bytes=max_size_mb * 1024 * 1024), max_bytes=max_size_mb * 1024 * 1024)
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"image rejected: {exc}", debug_lines)
        if info is None:
            return ProviderAttempt(self.name, "WARN", "invalid image bytes", debug_lines)
        return ProviderAttempt(self.name, "OK", f"{confidence} confidence", debug_lines, cover_result(info, provider=self.name, source="iTunes Search", confidence=confidence, match_reason=reason, external_url=artwork, album_id=str(row.get("collectionId") or "") or None))


class DeezerCoverProvider(CoverProvider):
    def __init__(self) -> None:
        super().__init__(name="deezer", requires_api_key=False)

    def fetch(self, tracks: list[Track], target_dir: Path, prefer_front: bool = True, max_size_mb: int = 10, debug: bool = False) -> ProviderAttempt:
        artist, album, title = common_artist_album_title(tracks)
        term = " ".join(part for part in (artist, album or title) if part)
        if not artist or not (album or title):
            return ProviderAttempt(self.name, "SKIP", "missing artist and album/title")
        url = f"{DEEZER_SEARCH_URL}?{urllib.parse.urlencode({'q': term, 'limit': '10'})}"
        debug_lines = [f"deezer url: {safe_debug_url(url)}"] if debug else []
        try:
            rows = json.loads(get_bytes(url, accept="application/json", max_bytes=1024 * 1024).decode("utf-8")).get("data", [])
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"response rejected: {exc}", debug_lines)
        best = best_deezer_match(rows, artist, album, title)
        if best is None:
            return ProviderAttempt(self.name, "WARN", "no metadata match", debug_lines)
        confidence, reason, row = best
        album_row = row.get("album") or {}
        artwork = str(album_row.get("cover_xl") or album_row.get("cover_big") or album_row.get("cover_medium") or "")
        if not artwork:
            return ProviderAttempt(self.name, "WARN", "matched result has no artwork", debug_lines)
        try:
            info = validate_image_bytes(get_bytes(artwork, accept="image/*", max_bytes=max_size_mb * 1024 * 1024), max_bytes=max_size_mb * 1024 * 1024)
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"image rejected: {exc}", debug_lines)
        if info is None:
            return ProviderAttempt(self.name, "WARN", "invalid image bytes", debug_lines)
        return ProviderAttempt(self.name, "OK", f"{confidence} confidence", debug_lines, cover_result(info, provider=self.name, source="Deezer", confidence=confidence, match_reason=reason, external_url=artwork, album_id=str(album_row.get("id") or "") or None))


class SpotifyCoverProvider(CoverProvider):
    def __init__(self) -> None:
        super().__init__(name="spotify", enabled=False, requires_api_key=True)

    def fetch(self, tracks: list[Track], target_dir: Path, prefer_front: bool = True, max_size_mb: int = 10, debug: bool = False) -> ProviderAttempt:
        return ProviderAttempt(self.name, "SKIP", "spotify provider not implemented; credentials required")


PROVIDERS = {provider.name: provider for provider in (LocalCoverProvider(), MusicBrainzCoverProvider(), ITunesCoverProvider(), DeezerCoverProvider(), SpotifyCoverProvider())}


def fetch_cover_with_providers(tracks: list[Track], target_dir: Path, sources: list[str], min_confidence: str = "medium", prefer_front: bool = True, max_size_mb: int = 10, debug: bool = False) -> tuple[CoverResult | None, list[ProviderAttempt]]:
    attempts: list[ProviderAttempt] = []
    for source in sources:
        provider = PROVIDERS.get(source)
        if provider is None:
            attempts.append(ProviderAttempt(source, "SKIP", "unknown provider"))
            continue
        if not provider.enabled:
            attempts.append(ProviderAttempt(source, "SKIP", "provider disabled"))
            continue
        attempt = provider.fetch(tracks, target_dir, prefer_front=prefer_front, max_size_mb=max_size_mb, debug=debug)
        attempts.append(attempt)
        if attempt.result is None:
            continue
        if confidence_allows(attempt.result.confidence, min_confidence):
            return attempt.result, attempts
        attempts[-1].status = "WARN"
        attempts[-1].message = f"confidence {attempt.result.confidence} below minimum {min_confidence}"
    return None, attempts


def validate_image_bytes(data: bytes, max_bytes: int = MAX_IMAGE_BYTES) -> ImageInfo | None:
    if not data or len(data) > max_bytes:
        return None
    stripped = data[:256].lstrip().lower()
    if stripped.startswith((b"<html", b"<!doctype html", b"<?xml")) or b"<html" in stripped:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        width, height = jpeg_dimensions(data)
        return ImageInfo(data=data, mime="image/jpeg", extension="jpg", width=width, height=height)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = png_dimensions(data)
        return ImageInfo(data=data, mime="image/png", extension="png", width=width, height=height)
    return None


def cover_result(info: ImageInfo, provider: str, source: str, confidence: str, match_reason: str, external_url: str | None = None, release_id: str | None = None, album_id: str | None = None) -> CoverResult:
    return CoverResult(data=info.data, mime=info.mime, source=source, provider=provider, width=info.width, height=info.height, size_bytes=len(info.data), confidence=confidence, match_reason=match_reason, external_url=external_url, release_id=release_id, album_id=album_id)


def common_release_id(tracks: list[Track]) -> str:
    values = [value for track in tracks for value in get_tag(track, "mb_album_id")]
    return max(set(values), key=values.count) if values else ""


def common_artist_album_title(tracks: list[Track]) -> tuple[str, str, str]:
    return common_value(tracks, "albumartist") or common_value(tracks, "artist"), common_value(tracks, "album"), common_value(tracks, "title")


def common_value(tracks: list[Track], attr: str) -> str:
    values = [getattr(track, attr, "") for track in tracks if getattr(track, attr, "")]
    return max(set(values), key=values.count) if values else ""


def best_itunes_match(rows: list[dict], artist: str, album: str, title: str) -> tuple[str, str, dict] | None:
    best: tuple[float, str, str, dict] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_artist = str(row.get("artistName") or "")
        row_album = str(row.get("collectionName") or "")
        row_title = str(row.get("trackName") or "")
        confidence, reason = match_confidence(
            compare_ratio(normalize_artist_name(artist), normalize_artist_name(row_artist)),
            compare_ratio(normalize_album_title(album), normalize_album_title(row_album)),
            compare_ratio(normalize_track_title(title), normalize_track_title(row_title)),
            title_only=not album,
        )
        score = {"high": 3, "medium": 2, "low": 1}[confidence]
        if best is None or score > best[0]:
            best = (score, confidence, reason, row)
    return (best[1], best[2], best[3]) if best else None


def best_deezer_match(rows: list[dict], artist: str, album: str, title: str) -> tuple[str, str, dict] | None:
    best: tuple[float, str, str, dict] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_artist = str((row.get("artist") or {}).get("name") or "")
        row_album = str((row.get("album") or {}).get("title") or "")
        row_title = str(row.get("title") or "")
        confidence, reason = match_confidence(compare_ratio(artist, row_artist), compare_ratio(album, row_album), compare_ratio(title, row_title), title_only=not album)
        score = {"high": 3, "medium": 2, "low": 1}[confidence]
        if best is None or score > best[0]:
            best = (score, confidence, reason, row)
    return (best[1], best[2], best[3]) if best else None


def preferred_images(images: list[dict], prefer_front: bool = True) -> list[dict]:
    if not prefer_front:
        return images
    front = [image for image in images if image.get("front") or "Front" in (image.get("types") or [])]
    return front + [image for image in images if image not in front]


def image_url_from_row(image: dict) -> str:
    thumbnails = image.get("thumbnails") or {}
    return str(image.get("image") or thumbnails.get("large") or thumbnails.get("small") or "")


def upscale_itunes_artwork(url: str) -> str:
    return url.replace("100x100bb", "1000x1000bb") if "100x100bb" in url else url


def get_bytes(url: str, accept: str, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(max_bytes + 1)


def png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None, None


def jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        length = int.from_bytes(data[index + 2:index + 4], "big")
        if marker in {0xC0, 0xC2} and index + 8 < len(data):
            return int.from_bytes(data[index + 7:index + 9], "big"), int.from_bytes(data[index + 5:index + 7], "big")
        if length < 2:
            break
        index += 2 + length
    return None, None
