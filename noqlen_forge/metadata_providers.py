from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .audio import Track, get_tag, read_tracks, target_kind
from .config import APP_USER_AGENT, get_config_value, load_config
from .provider_common import compare_ratio, confidence_allows, normalize_album_title, normalize_artist_name, normalize_track_title, safe_debug_url
from .writers import WritePlan, apply_musicbrainz_writes

DISCOGS_SEARCH_URL = "https://api.discogs.com/database/search"
DISCOGS_RELEASE_URL = "https://api.discogs.com/releases"
DEEZER_SEARCH_ALBUM_URL = "https://api.deezer.com/search/album"
DEEZER_SEARCH_TRACK_URL = "https://api.deezer.com/search/track"
DEEZER_ALBUM_URL = "https://api.deezer.com/album"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
ACOUSTID_LOOKUP_URL = "https://api.acoustid.org/v2/lookup"


USER_AGENT = f"{APP_USER_AGENT} (Noqlen Forge Core metadata tool)"
CONFIDENCE_MIN_SCORE = {"high": 85, "medium": 70, "low": 0}
FIELD_AUTHORITY = {
    "mb_album_id": ["musicbrainz"],
    "mb_release_group_id": ["musicbrainz"],
    "mb_track_id": ["musicbrainz", "acoustid"],
    "mb_release_track_id": ["musicbrainz", "acoustid"],
    "acoustid_id": ["acoustid"],
    "acoustid_fingerprint": ["acoustid"],
    "genre": ["discogs", "beatport", "deezer", "itunes", "lastfm"],
    "style": ["discogs", "beatport", "deezer", "itunes", "lastfm"],
    "label": ["discogs", "musicbrainz", "deezer", "itunes"],
    "catalog_number": ["discogs", "musicbrainz", "deezer", "itunes"],
    "barcode": ["discogs", "musicbrainz", "deezer", "itunes"],
    "country": ["discogs", "musicbrainz", "deezer", "itunes"],
    "media": ["discogs", "musicbrainz", "deezer", "itunes"],
    "audio_codec": ["discogs"],
    "release_format": ["discogs", "musicbrainz", "deezer", "itunes"],
    "edition": ["discogs", "musicbrainz", "deezer", "itunes"],
    "release_type": ["discogs", "musicbrainz", "deezer", "itunes"],
    "date": ["musicbrainz", "discogs", "itunes", "deezer"],
    "originaldate": ["musicbrainz", "discogs", "itunes", "deezer"],
    "cover": ["coverartarchive", "local", "itunes", "deezer", "discogs"],
    "tracktotal": ["local", "musicbrainz", "deezer", "itunes"],
    "disctotal": ["local", "musicbrainz", "itunes", "deezer"],
    "explicit": ["itunes", "deezer"],
    "deezer_album_id": ["deezer"],
    "deezer_track_id": ["deezer"],
    "itunes_collection_id": ["itunes"],
    "itunes_track_id": ["itunes"],
    "lyrics": ["local", "lrclib"],
    "audio_features": ["local", "beatport", "spotify"],
}
PROVIDER_ROLES = {"musicbrainz": "identity", "acoustid": "identifier", "discogs": "catalog", "deezer": "fallback", "itunes": "fallback", "beatport": "specialized", "lastfm": "community"}
FIELD_TAG_NAMES = {
    "mb_album_id": "MusicBrainz Album Id",
    "mb_release_group_id": "MusicBrainz Release Group Id",
    "mb_track_id": "MusicBrainz Track Id",
    "mb_release_track_id": "MusicBrainz Release Track Id",
    "mb_album_artist_id": "MusicBrainz Album Artist Id",
    "genre": "Genre",
    "style": "Style",
    "label": "Label",
    "catalog_number": "Catalog Number",
    "barcode": "Barcode",
    "country": "Release Country",
    "media": "Media",
    "audio_codec": "Audio Codec",
    "release_format": "Release Format",
    "edition": "Edition",
    "release_type": "Release Type",
    "date": "Date",
    "originaldate": "Original Date",
    "isrc": "ISRC",
    "tracktotal": "Track Total",
    "disctotal": "Disc Total",
    "explicit": "Explicit",
    "deezer_album_id": "DEEZER_ALBUM_ID",
    "deezer_track_id": "DEEZER_TRACK_ID",
    "itunes_collection_id": "ITUNES_COLLECTION_ID",
    "itunes_track_id": "ITUNES_TRACK_ID",
    "acoustid_id": "ACOUSTID_ID",
    "acoustid_fingerprint": "ACOUSTID_FINGERPRINT",
}
IDENTITY_FIELDS = {"mb_album_id", "mb_release_group_id", "mb_track_id", "mb_release_track_id", "mb_album_artist_id"}
DISCOGS_EDITION_FIELDS = {"barcode", "catalog_number", "country", "media", "audio_codec", "edition"}
DISCOGS_AMBIGUOUS_SAFE_FIELDS = ("genre", "style", "release_type", "release_format", "label")
DISCOGS_AUDIO_CODECS = {"flac": "FLAC", "mp3": "MP3", "aac": "AAC", "alac": "ALAC", "wav": "WAV", "aiff": "AIFF", "ogg": "OGG", "vorbis": "VORBIS", "opus": "OPUS", "wma": "WMA"}
DISCOGS_RELEASE_TYPES = {"album", "single", "ep", "compilation", "mini-album"}
DISCOGS_EDITION_DESCRIPTIONS = {
    "anniversary edition",
    "bonus tracks",
    "box set",
    "deluxe edition",
    "digipak",
    "expanded edition",
    "japanese edition",
    "limited edition",
    "reissue",
    "remastered",
    "special edition",
    "tour edition",
}
DISCOGS_TECHNICAL_DESCRIPTIONS = {
    "12",
    "16bit",
    "24bit",
    "3313rpm",
    "331rpm",
    "33rpm",
    "441khz",
    "45rpm",
    "48khz",
    "96khz",
    "cd",
    "file",
    "lp",
    "stereo",
    "vinyl",
}


@dataclass(slots=True)
class MetadataContext:
    path: Path
    target_type: str
    artist: str = ""
    albumartist: str = ""
    album: str = ""
    title: str = ""
    tracks: list[Track] = field(default_factory=list)
    mb_album_id: str = ""
    mb_release_group_id: str = ""
    barcode: str = ""
    catalog_number: str = ""
    label: str = ""
    existing_tags: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class MetadataCandidate:
    provider: str
    source_id: str
    source_url: str | None = None
    confidence: str = "low"
    score: int = 0
    match_reason: str = ""
    album: str = ""
    albumartist: str = ""
    artist: str = ""
    title: str = ""
    date: str = ""
    originaldate: str = ""
    label: str = ""
    genre: str = ""
    style: str = ""
    country: str = ""
    barcode: str = ""
    catalog_number: str = ""
    media: str = ""
    audio_codec: str = ""
    release_format: str = ""
    edition: str = ""
    release_type: str = ""
    release_status: str = ""
    mb_album_id: str = ""
    mb_release_group_id: str = ""
    mb_track_id: str = ""
    mb_release_track_id: str = ""
    mb_album_artist_id: str = ""
    tracklist: list[dict[str, str]] = field(default_factory=list)
    isrcs: dict[int, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    tracktotal: str = ""
    disctotal: str = ""
    explicit: str = ""
    deezer_album_id: str = ""
    deezer_track_id: str = ""
    itunes_collection_id: str = ""
    itunes_track_id: str = ""
    acoustid_id: str = ""
    acoustid_fingerprint: str = ""


@dataclass(slots=True)
class FieldDecision:
    field: str
    current_value: str
    candidate_value: str
    provider: str
    confidence: str
    action: str
    reason: str


@dataclass(slots=True)
class ProviderAttempt:
    provider: str
    status: str
    message: str
    candidates: list[MetadataCandidate] = field(default_factory=list)
    debug: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProviderSelection:
    active: list[str]
    skipped: list[tuple[str, str]] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class MetadataProvider:
    name: str
    enabled: bool = True
    requires_api_key: bool = False
    supported_targets: tuple[str, ...] = ("album", "single", "track")

    def fetch_album(self, context: MetadataContext, debug: bool = False) -> ProviderAttempt:
        raise NotImplementedError

    def fetch_track(self, context: MetadataContext, debug: bool = False) -> ProviderAttempt:
        return self.fetch_album(context, debug=debug)


class MusicBrainzMetadataProvider(MetadataProvider):
    def __init__(self) -> None:
        super().__init__("musicbrainz", enabled=True, requires_api_key=False)

    def fetch_album(self, context: MetadataContext, debug: bool = False) -> ProviderAttempt:
        if context.mb_album_id and context.mb_release_group_id:
            candidate = MetadataCandidate(provider=self.name, source_id=context.mb_album_id, confidence="high", score=100, match_reason="identity already present", album=context.album, albumartist=context.albumartist, artist=context.artist, title=context.title)
            return ProviderAttempt(self.name, "OK", "identity complete", [candidate])
        return ProviderAttempt(self.name, "WARN", "identity incomplete; use apply-mbid/enrich for MBIDs")


class DiscogsMetadataProvider(MetadataProvider):
    def __init__(self, token: str = "", enabled: bool = True, field_config: dict[str, Any] | None = None, release_id: str = "", candidate_index: int | None = None) -> None:
        super().__init__("discogs", enabled=enabled, requires_api_key=True)
        self.token = token
        self.field_config = field_config or {}
        self.release_id = str(release_id or "").strip()
        self.candidate_index = candidate_index

    def fetch_album(self, context: MetadataContext, debug: bool = False) -> ProviderAttempt:
        if not self.enabled:
            return ProviderAttempt(self.name, "SKIP", "provider disabled")
        if not self.token:
            return ProviderAttempt(self.name, "SKIP", "DISCOGS_TOKEN not set")
        artist = context.albumartist or context.artist
        album = context.album or context.title
        if not artist or not album:
            return ProviderAttempt(self.name, "SKIP", "missing artist and album/title")
        debug_lines: list[str] = []
        if self.release_id:
            candidate = self._fetch_release(self.release_id, context, debug_lines, debug)
            if candidate is None:
                return ProviderAttempt(self.name, "WARN", f"manual Discogs release {self.release_id} not found", debug=debug_lines)
            if candidate.score < 70:
                return ProviderAttempt(self.name, "REVIEW", f"manual Discogs release validation weak: score={candidate.score}", [candidate], debug_lines)
            return ProviderAttempt(self.name, "OK", f"manual Discogs release selected: {candidate.source_id} score={candidate.score}", [candidate], debug_lines)
        rows: list[dict[str, Any]] = []
        search_params = {"artist": artist, "release_title": album, "type": "release", "per_page": "10"}
        if context.barcode:
            search_params["barcode"] = _digits(context.barcode)
        if context.catalog_number:
            search_params["catno"] = context.catalog_number
        url = f"{DISCOGS_SEARCH_URL}?{urllib.parse.urlencode(search_params)}"
        if debug:
            debug_lines.append(f"discogs search url: {safe_debug_url(url)}")
        try:
            rows = fetch_discogs_json(url, self.token).get("results", [])
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"search rejected: {exc}", debug=debug_lines)
        candidates: list[MetadataCandidate] = []
        for row in rows[:10]:
            release_id = str(row.get("id") or "")
            if not release_id:
                continue
            candidate = self._fetch_release(release_id, context, debug_lines, debug)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda item: item.score, reverse=True)
        if debug:
            debug_lines.append(f"raw summary: results={len(rows)} hydrated={len(candidates)}")
            debug_lines.extend(f"score {item.source_id}: {item.score} {item.confidence} {item.match_reason}" for item in candidates[:5])
        if not candidates:
            return ProviderAttempt(self.name, "WARN", "no metadata match", debug=debug_lines)
        if self.candidate_index is not None:
            if self.candidate_index < 1 or self.candidate_index > len(candidates):
                return ProviderAttempt(self.name, "REVIEW", f"Discogs candidate index {self.candidate_index} out of range", candidates, debug_lines)
            selected = candidates[self.candidate_index - 1]
            selected.extra["manual_selection"] = True
            ordered = [selected] + [candidate for index, candidate in enumerate(candidates, start=1) if index != self.candidate_index]
            if selected.score < 70:
                return ProviderAttempt(self.name, "REVIEW", f"manual Discogs candidate validation weak: score={selected.score}", ordered, debug_lines)
            return ProviderAttempt(self.name, "OK", f"manual Discogs candidate {self.candidate_index} selected: {selected.source_id} score={selected.score}", ordered, debug_lines)
        if len(candidates) > 1 and candidates[0].score - candidates[1].score < 5:
            count = len(_ambiguous_discogs_candidates(candidates))
            return ProviderAttempt(self.name, "REVIEW", f"Discogs REVIEW ambiguous editions: {count} equally strong matches. Use --verbose to inspect candidates and --discogs-release-id ID to choose one.", candidates, debug_lines)
        return ProviderAttempt(self.name, "OK", f"candidate score={candidates[0].score}", candidates, debug_lines)

    def _fetch_release(self, release_id: str, context: MetadataContext, debug_lines: list[str], debug: bool) -> MetadataCandidate | None:
        release_url = f"{DISCOGS_RELEASE_URL}/{urllib.parse.quote(release_id)}"
        if debug:
            debug_lines.append(f"discogs release url: {safe_debug_url(release_url)}")
        try:
            payload = fetch_discogs_json(release_url, self.token)
        except Exception as exc:
            debug_lines.append(f"release {release_id} rejected: {exc}") if debug else None
            return None
        candidate = candidate_from_discogs_release(payload, context)
        candidate.extra["field_config"] = self.field_config
        if self.release_id:
            candidate.extra["manual_selection"] = True
        return candidate


class DeezerMetadataProvider(MetadataProvider):
    def __init__(self, enabled: bool = True, field_config: dict[str, Any] | None = None) -> None:
        super().__init__("deezer", enabled=enabled, requires_api_key=False)
        self.field_config = field_config or {}

    def fetch_album(self, context: MetadataContext, debug: bool = False) -> ProviderAttempt:
        if not self.enabled:
            return ProviderAttempt(self.name, "SKIP", "provider disabled")
        artist = context.albumartist or context.artist
        album = context.album or context.title
        if not artist or not album:
            return ProviderAttempt(self.name, "SKIP", "missing artist and album/title")
        debug_lines: list[str] = []
        params = {"q": f'artist:"{artist}" album:"{album}"', "limit": "10"}
        url = f"{DEEZER_SEARCH_ALBUM_URL}?{urllib.parse.urlencode(params)}"
        if debug:
            debug_lines.append(f"deezer search url: {safe_debug_url(url)}")
        try:
            rows = fetch_json(url).get("data", [])
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"search skipped: {exc}", debug=debug_lines)
        candidates: list[MetadataCandidate] = []
        for row in rows[:10]:
            album_id = str(row.get("id") or "")
            if not album_id:
                continue
            candidate = self._fetch_album_id(album_id, context, debug_lines, debug)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda item: item.score, reverse=True)
        if debug:
            debug_lines.append(f"raw summary: results={len(rows)} hydrated={len(candidates)}")
            debug_lines.extend(f"score {item.source_id}: {item.score} {item.confidence} {item.match_reason}" for item in candidates[:5])
        if not candidates:
            return ProviderAttempt(self.name, "WARN", "no metadata match", debug=debug_lines)
        return ProviderAttempt(self.name, "OK", f"candidate score={candidates[0].score}", candidates, debug_lines)

    def fetch_track(self, context: MetadataContext, debug: bool = False) -> ProviderAttempt:
        if context.album:
            album_attempt = self.fetch_album(context, debug=debug)
            if album_attempt.candidates:
                return album_attempt
        artist = context.artist or context.albumartist
        title = context.title or context.album
        if not artist or not title:
            return ProviderAttempt(self.name, "SKIP", "missing artist and title")
        debug_lines: list[str] = []
        params = {"q": f'artist:"{artist}" track:"{title}"', "limit": "10"}
        url = f"{DEEZER_SEARCH_TRACK_URL}?{urllib.parse.urlencode(params)}"
        if debug:
            debug_lines.append(f"deezer search url: {safe_debug_url(url)}")
        try:
            rows = fetch_json(url).get("data", [])
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"search skipped: {exc}", debug=debug_lines)
        candidates = [candidate_from_deezer_track(row, context, self.field_config) for row in rows[:10]]
        candidates = [candidate for candidate in candidates if candidate.source_id]
        candidates.sort(key=lambda item: item.score, reverse=True)
        if debug:
            debug_lines.append(f"raw summary: results={len(rows)} hydrated={len(candidates)}")
            debug_lines.extend(f"score {item.source_id}: {item.score} {item.confidence} {item.match_reason}" for item in candidates[:5])
        if not candidates:
            return ProviderAttempt(self.name, "WARN", "no metadata match", debug=debug_lines)
        return ProviderAttempt(self.name, "OK", f"candidate score={candidates[0].score}", candidates, debug_lines)

    def _fetch_album_id(self, album_id: str, context: MetadataContext, debug_lines: list[str], debug: bool) -> MetadataCandidate | None:
        url = f"{DEEZER_ALBUM_URL}/{urllib.parse.quote(album_id)}"
        if debug:
            debug_lines.append(f"deezer album url: {safe_debug_url(url)}")
        try:
            return candidate_from_deezer_album(fetch_json(url), context, self.field_config)
        except Exception as exc:
            debug_lines.append(f"album {album_id} rejected: {exc}") if debug else None
            return None


class ITunesMetadataProvider(MetadataProvider):
    def __init__(self, enabled: bool = True, storefront: str = "us", field_config: dict[str, Any] | None = None) -> None:
        super().__init__("itunes", enabled=enabled, requires_api_key=False)
        self.storefront = (storefront or "us").lower()
        self.field_config = field_config or {}

    def fetch_album(self, context: MetadataContext, debug: bool = False) -> ProviderAttempt:
        if not self.enabled:
            return ProviderAttempt(self.name, "SKIP", "provider disabled")
        artist = context.albumartist or context.artist
        album = context.album or context.title
        if not artist or not album:
            return ProviderAttempt(self.name, "SKIP", "missing artist and album/title")
        debug_lines: list[str] = []
        params = {"term": f"{artist} {album}", "media": "music", "entity": "album", "country": self.storefront, "limit": "10"}
        url = f"{ITUNES_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        if debug:
            debug_lines.append(f"itunes search url: {safe_debug_url(url)}")
        try:
            rows = fetch_json(url).get("results", [])
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"search skipped: {exc}", debug=debug_lines)
        candidates: list[MetadataCandidate] = []
        for row in rows[:10]:
            collection_id = str(row.get("collectionId") or "")
            if not collection_id:
                continue
            candidate = self._lookup_collection(collection_id, row, context, debug_lines, debug)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda item: item.score, reverse=True)
        if debug:
            debug_lines.append(f"raw summary: results={len(rows)} hydrated={len(candidates)} storefront={self.storefront}")
            debug_lines.extend(f"score {item.source_id}: {item.score} {item.confidence} {item.match_reason}" for item in candidates[:5])
        if not candidates:
            return ProviderAttempt(self.name, "WARN", "no metadata match", debug=debug_lines)
        return ProviderAttempt(self.name, "OK", f"candidate score={candidates[0].score} storefront={self.storefront}", candidates, debug_lines)

    def fetch_track(self, context: MetadataContext, debug: bool = False) -> ProviderAttempt:
        if context.album:
            album_attempt = self.fetch_album(context, debug=debug)
            if album_attempt.candidates:
                return album_attempt
        artist = context.artist or context.albumartist
        title = context.title or context.album
        if not artist or not title:
            return ProviderAttempt(self.name, "SKIP", "missing artist and title")
        debug_lines: list[str] = []
        params = {"term": f"{artist} {title}", "media": "music", "entity": "song", "country": self.storefront, "limit": "10"}
        url = f"{ITUNES_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        if debug:
            debug_lines.append(f"itunes search url: {safe_debug_url(url)}")
        try:
            rows = fetch_json(url).get("results", [])
        except Exception as exc:
            return ProviderAttempt(self.name, "WARN", f"search skipped: {exc}", debug=debug_lines)
        candidates = [candidate_from_itunes_song(row, context, self.field_config, self.storefront) for row in rows[:10]]
        candidates = [candidate for candidate in candidates if candidate.source_id]
        candidates.sort(key=lambda item: item.score, reverse=True)
        if not candidates:
            return ProviderAttempt(self.name, "WARN", "no metadata match", debug=debug_lines)
        return ProviderAttempt(self.name, "OK", f"candidate score={candidates[0].score} storefront={self.storefront}", candidates, debug_lines)

    def _lookup_collection(self, collection_id: str, album_row: dict[str, Any], context: MetadataContext, debug_lines: list[str], debug: bool) -> MetadataCandidate | None:
        params = {"id": collection_id, "entity": "song", "country": self.storefront}
        url = f"{ITUNES_LOOKUP_URL}?{urllib.parse.urlencode(params)}"
        if debug:
            debug_lines.append(f"itunes lookup url: {safe_debug_url(url)}")
        try:
            payload = fetch_json(url)
        except Exception as exc:
            debug_lines.append(f"collection {collection_id} rejected: {exc}") if debug else None
            return None
        return candidate_from_itunes_collection(album_row, payload.get("results", []), context, self.field_config, self.storefront)


class AcoustIDMetadataProvider(MetadataProvider):
    def __init__(self, api_key: str = "", enabled: bool = True, field_config: dict[str, Any] | None = None, fpcalc: str = "fpcalc") -> None:
        super().__init__("acoustid", enabled=enabled, requires_api_key=False)
        self.api_key = api_key
        self.field_config = field_config or {}
        self.fpcalc = fpcalc or "fpcalc"
        self.min_score = float(self.field_config.get("min_score", 0.80) or 0.80)
        self.max_candidates = int(self.field_config.get("max_candidates", 5) or 5)

    def fetch_album(self, context: MetadataContext, debug: bool = False) -> ProviderAttempt:
        if not self.enabled:
            return ProviderAttempt(self.name, "SKIP", "provider disabled")
        debug_lines: list[str] = []
        results: list[dict[str, Any]] = []
        fpcalc_path = shutil.which(self.fpcalc) or (self.fpcalc if Path(self.fpcalc).exists() else "")
        if not fpcalc_path:
            return ProviderAttempt(self.name, "WARN", f"fpcalc not found; install chromaprint or set [tools].fpcalc")
        for track in context.tracks:
            fingerprint = _existing_tag(track, "acoustid_fingerprint")
            duration = track.duration or 0.0
            generated = False
            if not fingerprint:
                try:
                    fp = run_fpcalc(track.path, fpcalc_path, debug_lines=debug_lines, debug=debug)
                except RuntimeError as exc:
                    results.append({"track": track, "status": "WARN", "message": str(exc)})
                    continue
                fingerprint = fp.get("fingerprint", "")
                duration = float(fp.get("duration") or duration or 0.0)
                generated = True
            row: dict[str, Any] = {"track": track, "fingerprint": fingerprint, "duration": duration, "generated": generated, "status": "OK"}
            if not self.api_key:
                row["status"] = "SKIP"
                row["message"] = "ACOUSTID_KEY not set; lookup skipped"
                results.append(row)
                continue
            try:
                lookup = lookup_acoustid(fingerprint, duration, self.api_key, self.max_candidates, debug_lines=debug_lines, debug=debug)
            except Exception as exc:
                row["status"] = "WARN"
                row["message"] = f"AcoustID lookup failed: {exc}"
                results.append(row)
                continue
            row.update(select_acoustid_match(track, lookup, self.min_score))
            results.append(row)
        candidate = candidate_from_acoustid_results(context, results, self.field_config)
        generated_count = sum(1 for row in results if row.get("fingerprint"))
        matched_count = sum(1 for row in results if row.get("acoustid_id"))
        if not self.api_key:
            status = "WARN" if generated_count else "WARN"
            message = f"fingerprints {generated_count}/{len(context.tracks)}; AcoustID lookup skipped: ACOUSTID_KEY not set"
        elif matched_count == len(context.tracks) and candidate.confidence != "low":
            status = "OK"
            message = f"matches {matched_count}/{len(context.tracks)}"
        elif matched_count:
            status = "REVIEW"
            message = f"matches {matched_count}/{len(context.tracks)}; review required"
        else:
            status = "WARN"
            message = f"matches 0/{len(context.tracks)}"
        return ProviderAttempt(self.name, status, message, [candidate] if candidate else [], debug_lines)


PROVIDER_CLASSES = {"musicbrainz": MusicBrainzMetadataProvider, "acoustid": AcoustIDMetadataProvider, "discogs": DiscogsMetadataProvider, "deezer": DeezerMetadataProvider, "itunes": ITunesMetadataProvider}


def metadata_path(path: Path, apply: bool = False, force: bool = False, providers: list[str] | None = None, min_confidence: str = "medium", verbose: bool = False, debug: bool = False, config: dict[str, Any] | None = None, allow_more_providers: bool = False, discogs_release_id: str = "", candidate_index: int | None = None, itunes_storefront: str = "") -> tuple[int, str]:
    tracks = read_tracks(path)
    if not tracks:
        return 1, "No supported audio files found"
    config = config or load_config()
    context = build_context(path, tracks)
    selection = resolve_metadata_providers(config, providers=providers, allow_more_providers=allow_more_providers)
    min_confidence = min_confidence or str(_provider_section(config).get("min_confidence", "medium"))
    attempts = fetch_metadata_with_providers(context, selection.active, config=config, debug=debug, discogs_release_id=discogs_release_id, candidate_index=candidate_index, itunes_storefront=itunes_storefront)
    selected = _select_candidate(attempts, min_confidence)
    if selected is None:
        selected = _select_acoustid_fingerprint_candidate(attempts)
    decisions = merge_candidate(context, selected, min_confidence=min_confidence, force=force) if selected else merge_ambiguous_discogs_common_fields(context, attempts, min_confidence=min_confidence, force=force)
    plans = acoustid_plans_from_candidate(tracks, selected, force=force) if selected and selected.provider == "acoustid" else plans_from_decisions(tracks, decisions)
    manual_discogs_selection_required = _manual_discogs_selection_required(attempts)
    errors = apply_musicbrainz_writes(plans, apply=apply and not manual_discogs_selection_required)
    if errors:
        return 1, "Metadata write verification failed:\n" + "\n".join(f"- {error}" for error in errors)
    status = metadata_status(attempts, decisions, selected)
    return (0 if status != "REVIEW" else 1), render_metadata_output(context, attempts, decisions, apply=apply, status=status, verbose=verbose, debug=debug, selection=selection, manual_discogs_selection_required=manual_discogs_selection_required)


def resolve_metadata_providers(config: dict[str, Any], providers: list[str] | None = None, allow_more_providers: bool = False) -> ProviderSelection:
    section = _provider_section(config)
    if not bool(section.get("enabled", True)) and providers is None:
        return ProviderSelection([], [(source, "metadata providers disabled") for source in list(section.get("sources", []))], {})
    requested = list(providers) if providers is not None else list(section.get("sources", ["musicbrainz", "discogs"]))
    max_active = int(section.get("max_active", 2) or 0)
    allow_more = allow_more_providers or bool(section.get("allow_more_providers", False))
    active: list[str] = []
    skipped: list[tuple[str, str]] = []
    roles: dict[str, str] = {}
    seen: set[str] = set()
    for source in requested:
        if source in seen:
            continue
        seen.add(source)
        specific = _provider_specific(config, source)
        role = str(specific.get("role", PROVIDER_ROLES.get(source, "fallback")))
        roles[source] = role
        if providers is None and not bool(specific.get("enabled", source in {"musicbrainz", "discogs"})):
            skipped.append((source, "disabled by config"))
            continue
        counted_active = sum(1 for item in active if roles.get(item, PROVIDER_ROLES.get(item, "fallback")) != "identifier")
        if role != "identifier" and max_active > 0 and counted_active >= max_active and not allow_more:
            skipped.append((source, "over max_active limit"))
            continue
        active.append(source)
    return ProviderSelection(active, skipped, roles)


def fetch_metadata_with_providers(context: MetadataContext, sources: list[str], config: dict[str, Any] | None = None, debug: bool = False, discogs_release_id: str = "", candidate_index: int | None = None, itunes_storefront: str = "") -> list[ProviderAttempt]:
    config = config or load_config()
    attempts: list[ProviderAttempt] = []
    for source in sources:
        provider = build_provider(source, config, discogs_release_id=discogs_release_id, candidate_index=candidate_index, itunes_storefront=itunes_storefront)
        if provider is None:
            attempts.append(ProviderAttempt(source, "SKIP", "unknown provider"))
            continue
        if context.target_type not in provider.supported_targets:
            attempts.append(ProviderAttempt(source, "SKIP", f"target {context.target_type} unsupported"))
            continue
        attempt = provider.fetch_track(context, debug=debug) if context.target_type in {"single", "track"} else provider.fetch_album(context, debug=debug)
        attempts.append(attempt)
    return attempts


def build_provider(source: str, config: dict[str, Any], discogs_release_id: str = "", candidate_index: int | None = None, itunes_storefront: str = "") -> MetadataProvider | None:
    if source == "musicbrainz":
        enabled = bool(_provider_specific(config, "musicbrainz").get("enabled", True))
        provider = MusicBrainzMetadataProvider()
        provider.enabled = enabled
        return provider
    if source == "discogs":
        section = _provider_specific(config, "discogs")
        enabled = bool(section.get("enabled", True))
        return DiscogsMetadataProvider(token=discogs_token(config), enabled=enabled, field_config=section, release_id=discogs_release_id, candidate_index=candidate_index)
    if source == "acoustid":
        section = _provider_specific(config, "acoustid")
        enabled = bool(section.get("enabled", True))
        return AcoustIDMetadataProvider(api_key=acoustid_api_key(config), enabled=enabled, field_config=section, fpcalc=str(get_config_value(config, "tools", "fpcalc", "fpcalc") or "fpcalc"))
    if source == "deezer":
        section = _provider_specific(config, "deezer")
        return DeezerMetadataProvider(enabled=True, field_config=section)
    if source == "itunes":
        section = _provider_specific(config, "itunes")
        storefront = itunes_storefront or str(section.get("storefront") or get_config_value(config, "apis", "itunes_storefront", "us") or "us")
        return ITunesMetadataProvider(enabled=True, storefront=storefront, field_config=section)
    return None


def build_context(path: Path, tracks: list[Track]) -> MetadataContext:
    first = tracks[0]
    tags: dict[str, list[str]] = {}
    for track in tracks:
        for key, values in track.tags.items():
            tags.setdefault(key, [])
            for value in values:
                if value not in tags[key]:
                    tags[key].append(value)
    return MetadataContext(
        path=path,
        target_type=target_kind(path),
        artist=first.artist,
        albumartist=first.albumartist,
        album=first.album,
        title=first.title,
        tracks=tracks,
        mb_album_id=_first_logical(tracks, "mb_album_id"),
        mb_release_group_id=_first_logical(tracks, "mb_release_group_id"),
        barcode=_first_logical(tracks, "barcode"),
        catalog_number=_first_logical(tracks, "catalog_number"),
        label=_first_logical(tracks, "label"),
        existing_tags=tags,
    )


def merge_candidate(context: MetadataContext, candidate: MetadataCandidate | None, min_confidence: str = "medium", force: bool = False) -> list[FieldDecision]:
    if candidate is None:
        return []
    decisions: list[FieldDecision] = []
    for field in _candidate_fields(candidate):
        value = normalize_candidate_value(field, str(getattr(candidate, field) or ""))
        if not value:
            continue
        current = _first_logical(context.tracks, field)
        if not confidence_allows(candidate.confidence, min_confidence):
            decisions.append(FieldDecision(field, current, value, candidate.provider, candidate.confidence, "review", f"confidence {candidate.confidence} below minimum {min_confidence}"))
        elif not current:
            decisions.append(FieldDecision(field, current, value, candidate.provider, candidate.confidence, "write", "empty field"))
        elif _norm_value(current) == _norm_value(value):
            decisions.append(FieldDecision(field, current, value, candidate.provider, candidate.confidence, "skip", "same value"))
        elif force:
            decisions.append(FieldDecision(field, current, value, candidate.provider, candidate.confidence, "write", "forced overwrite"))
        elif provider_has_authority(candidate.provider, field):
            decisions.append(FieldDecision(field, current, value, candidate.provider, candidate.confidence, "review", "conflict with existing value"))
        else:
            decisions.append(FieldDecision(field, current, value, candidate.provider, candidate.confidence, "skip", "existing value kept"))
    for field in IDENTITY_FIELDS:
        value = str(getattr(candidate, field, "") or "")
        if value and candidate.provider not in {"musicbrainz", "acoustid"}:
            decisions.append(FieldDecision(field, _first_logical(context.tracks, field), value, candidate.provider, candidate.confidence, "skip", "MusicBrainz identity fields are authoritative"))
    return decisions


def merge_ambiguous_discogs_common_fields(context: MetadataContext, attempts: list[ProviderAttempt], min_confidence: str = "medium", force: bool = False) -> list[FieldDecision]:
    attempt = next((item for item in attempts if item.provider == "discogs" and item.status == "REVIEW"), None)
    if attempt is None:
        return []
    candidates = _ambiguous_discogs_candidates(attempt.candidates)
    if len(candidates) < 2:
        return []
    common = MetadataCandidate(provider="discogs", source_id="ambiguous-common", confidence="high", score=candidates[0].score, match_reason="common fields across ambiguous Discogs editions")
    common.extra["field_config"] = candidates[0].extra.get("field_config", {}) if isinstance(candidates[0].extra.get("field_config", {}), dict) else {}
    for field in DISCOGS_AMBIGUOUS_SAFE_FIELDS:
        values = [normalize_candidate_value(field, str(getattr(candidate, field) or "")) for candidate in candidates]
        if values and values[0] and all(_norm_value(value) == _norm_value(values[0]) for value in values):
            setattr(common, field, values[0])
    return merge_candidate(context, common, min_confidence=min_confidence, force=force)


def plans_from_decisions(tracks: list[Track], decisions: list[FieldDecision]) -> list[WritePlan]:
    changes = {FIELD_TAG_NAMES[decision.field]: decision.candidate_value for decision in decisions if decision.action == "write" and decision.field in FIELD_TAG_NAMES}
    if not changes:
        return []
    return [WritePlan(track.path, dict(changes)) for track in tracks]


def acoustid_plans_from_candidate(tracks: list[Track], candidate: MetadataCandidate | None, force: bool = False, force_acoustid: bool = False, force_identity: bool = False) -> list[WritePlan]:
    if candidate is None or candidate.provider != "acoustid":
        return []
    rows = candidate.extra.get("per_file_changes", [])
    if not isinstance(rows, list):
        return []
    plans: list[WritePlan] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        track = row.get("track")
        if not isinstance(track, Track):
            continue
        changes: dict[str, str] = {}
        for field, tag_name in FIELD_TAG_NAMES.items():
            value = str(row.get(field) or "")
            if not value:
                continue
            current = _existing_tag(track, field)
            is_acoustid_field = field in {"acoustid_id", "acoustid_fingerprint"}
            is_identity_field = field in {"mb_track_id", "mb_release_track_id", "mb_album_id", "mb_release_group_id"}
            allow_overwrite = force or (is_acoustid_field and force_acoustid) or (is_identity_field and force_identity and candidate.confidence in {"medium", "high"})
            if current and _norm_value(current) != _norm_value(value) and not allow_overwrite:
                continue
            if current and _norm_value(current) == _norm_value(value):
                continue
            changes[tag_name] = value
        if changes:
            plans.append(WritePlan(track.path, changes))
    return plans


def _candidate_fields(candidate: MetadataCandidate) -> tuple[str, ...]:
    if candidate.provider == "acoustid":
        return ("acoustid_id", "acoustid_fingerprint", "mb_track_id", "mb_release_track_id", "mb_album_id", "mb_release_group_id")
    fields = ["genre", "style", "label", "catalog_number", "barcode", "country", "media", "audio_codec", "release_format", "release_type", "edition"]
    if candidate.provider in {"deezer", "itunes"}:
        enabled = candidate.extra.get("field_config", {}) if isinstance(candidate.extra.get("field_config", {}), dict) else {}
        safe = []
        if bool(enabled.get("use_for_genre", True)):
            safe.append("genre")
        if bool(enabled.get("use_for_date", True)):
            safe.extend(["date", "originaldate"])
        if bool(enabled.get("use_for_tracklist", True)):
            safe.extend(["tracktotal", "disctotal"])
        if candidate.provider == "itunes" and bool(enabled.get("use_for_explicit", True)):
            safe.append("explicit")
        if candidate.provider == "deezer":
            safe.append("explicit")
        safe.extend(["deezer_album_id", "deezer_track_id", "itunes_collection_id", "itunes_track_id"])
        return tuple(safe)
    if candidate.provider != "discogs":
        fields.extend(["date", "originaldate"])
        return tuple(fields)
    enabled = candidate.extra.get("field_config", {}) if isinstance(candidate.extra.get("field_config", {}), dict) else {}
    mapping = {
        "genre": "use_for_genre",
        "style": "use_for_style",
        "label": "use_for_label",
        "catalog_number": "use_for_catalog_number",
        "barcode": "use_for_barcode",
        "country": "use_for_country",
        "media": "use_for_format",
        "audio_codec": "use_for_format",
        "release_format": "use_for_format",
        "release_type": "use_for_release_type",
        "edition": "use_for_format",
    }
    return tuple(field for field in fields if bool(enabled.get(mapping[field], True)))


def metadata_status(attempts: list[ProviderAttempt], decisions: list[FieldDecision], candidate: MetadataCandidate | None) -> str:
    if any(attempt.status == "REVIEW" for attempt in attempts):
        return "REVIEW"
    if candidate and candidate.provider == "acoustid" and candidate.extra.get("conflicts"):
        return "REVIEW"
    if any(decision.action == "review" for decision in decisions):
        return "REVIEW"
    if candidate is None:
        return "WARN"
    if candidate.provider == "acoustid" and not candidate.extra.get("match_count") and not any(decision.action == "write" for decision in decisions):
        return "WARN"
    return "OK"


def render_metadata_output(context: MetadataContext, attempts: list[ProviderAttempt], decisions: list[FieldDecision], apply: bool, status: str, verbose: bool = False, debug: bool = False, selection: ProviderSelection | None = None, manual_discogs_selection_required: bool = False) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    lines = [f"Album: {(context.albumartist or context.artist or 'unknown')} - {(context.album or context.title or 'unknown')}", f"Files: {len(context.tracks)}", f"Mode: {mode}", ""]
    if selection is not None:
        lines.append("Metadata providers:")
        if selection.active:
            for source in selection.active:
                lines.append(f"- {source}: {selection.roles.get(source, PROVIDER_ROLES.get(source, 'fallback'))}")
        else:
            lines.append("- none")
        if selection.skipped:
            lines.append("")
            lines.append("Skipped providers:")
            for source, reason in selection.skipped:
                lines.append(f"- {source}: {reason}")
        lines.append("")
    step = 1
    provider_steps = sum(4 if attempt.provider == "acoustid" else 2 if attempt.provider in {"discogs", "deezer", "itunes"} else 1 for attempt in attempts)
    total_steps = provider_steps if attempts and all(attempt.provider == "acoustid" for attempt in attempts) else provider_steps + 2
    for attempt in attempts:
        if attempt.provider == "acoustid":
            candidate = attempt.candidates[0] if attempt.candidates else None
            fingerprint_count = int(candidate.extra.get("fingerprint_count", 0)) if candidate else 0
            match_count = int(candidate.extra.get("match_count", 0)) if candidate else 0
            resolve_status = "OK" if candidate and candidate.mb_track_id and not candidate.extra.get("conflicts") else "REVIEW" if candidate and candidate.extra.get("conflicts") else attempt.status
            resolve_message = "recording match" if candidate and candidate.mb_track_id else "recordings found, album ambiguous" if match_count else "no recording match"
            write_ids = sum(1 for decision in decisions if decision.action == "write" and decision.field in {"mb_track_id", "acoustid_id", "acoustid_fingerprint"})
            lines.append(f"[{step}/{total_steps}] Fingerprint         {'OK' if fingerprint_count else attempt.status}     {fingerprint_count}/{len(context.tracks)} generated")
            step += 1
            lines.append(f"[{step}/{total_steps}] AcoustID lookup     {attempt.status}     {attempt.message}")
            step += 1
            lines.append(f"[{step}/{total_steps}] MusicBrainz resolve {resolve_status}     {resolve_message}")
            step += 1
            apply_status = "APPLY" if apply and write_ids else "DRY" if not apply and write_ids else "SKIP"
            apply_action = "would write" if not apply and write_ids else "wrote" if apply and write_ids else "no identity fields selected"
            suffix = f"identity fields {write_ids}" if write_ids else ""
            lines.append(f"[{step}/{total_steps}] Apply identity      {apply_status:<5}  {apply_action} {suffix}".rstrip())
            step += 1
            continue
        if attempt.provider in {"discogs", "deezer", "itunes"}:
            label = "Discogs" if attempt.provider == "discogs" else "Deezer" if attempt.provider == "deezer" else "iTunes"
            lines.append(f"[{step}/{total_steps}] {label} search       {attempt.status}     {attempt.message}")
            step += 1
            release = attempt.candidates[0].source_id if attempt.candidates else "none"
            fetch_status = "OK" if attempt.candidates else attempt.status
            noun = "release" if attempt.provider == "discogs" else "album/track" if attempt.provider == "deezer" else "collection"
            lines.append(f"[{step}/{total_steps}] Fetch metadata      {fetch_status}     {noun}={release}")
            step += 1
            continue
        lines.append(f"[{step}/{total_steps}] {attempt.provider.title()} metadata     {attempt.status}     {attempt.message}")
        step += 1
    if attempts and all(attempt.provider == "acoustid" for attempt in attempts):
        planned = [decision for decision in decisions if decision.action == "write"]
        if planned:
            lines.append("")
            lines.append("Planned:" if not apply else "Applied:")
            for decision in planned:
                lines.append(f"- {FIELD_TAG_NAMES.get(decision.field, decision.field)}: {_display_candidate_value(decision.field, decision.candidate_value)}")
        candidate = attempts[0].candidates[0] if attempts[0].candidates else None
        conflicts = candidate.extra.get("conflicts", []) if candidate else []
        if conflicts:
            lines.append("")
            lines.append("Warnings:")
            for conflict in conflicts:
                lines.append(f"- Existing identity conflicts with AcoustID, review: {conflict}")
        if verbose or debug:
            lines.append("")
            lines.append("Candidates:")
            for attempt in attempts:
                for index, candidate in enumerate(attempt.candidates[:5], start=1):
                    lines.extend(_render_candidate_details(index, candidate))
        if verbose:
            lines.append("Decisions:")
            for decision in decisions:
                lines.append(f"- {decision.field}: {decision.action} ({decision.reason})")
        if debug:
            lines.append("")
            lines.append("Debug:")
            for attempt in attempts:
                for line in attempt.debug:
                    lines.append(f"- {line}")
        fingerprint_count = int(candidate.extra.get("fingerprint_count", 0)) if candidate else 0
        match_count = int(candidate.extra.get("match_count", 0)) if candidate else 0
        album_count = len(context.tracks) if candidate and candidate.extra.get("album_consistent") else 0
        lines.append("")
        lines.append(f"Fingerprint: {fingerprint_count}/{len(context.tracks)}")
        lines.append(f"AcoustID: {match_count}/{len(context.tracks)}")
        lines.append(f"Recording IDs: {match_count}/{len(context.tracks)}")
        lines.append(f"Album IDs: {album_count}/{len(context.tracks)}")
        lines.append(f"Status: {status}")
        return "\n".join(lines)
    write_count = sum(1 for decision in decisions if decision.action == "write")
    if status == "REVIEW" and _manual_discogs_selection_required(attempts) and write_count == 0:
        merge_status = "REVIEW"
        merge_message = "manual edition selection required"
    elif write_count > 0:
        merge_status = "OK"
        merge_message = f"{write_count} fields selected"
    else:
        merge_status = "SKIP"
        merge_message = "no safe fields selected"
    lines.append(f"[{step}/{total_steps}] Merge fields        {merge_status}     {merge_message}")
    if _manual_discogs_selection_required(attempts) and write_count == 0:
        lines.append("Reason: ambiguous edition-specific metadata; use --discogs-release-id or --candidate.")
    step += 1
    action = "wrote" if apply else "would write"
    fields = "/".join(decision.field.replace("_", " ") for decision in decisions if decision.action == "write") or "nothing"
    if apply and manual_discogs_selection_required:
        lines.append(f"[{step}/{total_steps}] Apply metadata      SKIP manual Discogs release selection required")
    elif write_count == 0:
        lines.append(f"[{step}/{total_steps}] Apply metadata      SKIP no metadata writes planned")
    else:
        lines.append(f"[{step}/{total_steps}] Apply metadata      {'APPLY' if apply else 'DRY'}    {action} {fields}")
    planned = [decision for decision in decisions if decision.action == "write"]
    if planned:
        lines.append("")
        lines.append("Planned:" if not apply else "Applied:")
        for decision in planned:
            lines.append(f"- {FIELD_TAG_NAMES.get(decision.field, decision.field)}: {_display_candidate_value(decision.field, decision.candidate_value)}")
    warnings = [decision for decision in decisions if decision.action in {"review", "skip"} and decision.reason not in {"same value"}]
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for decision in warnings:
            lines.append(f"- Existing {FIELD_TAG_NAMES.get(decision.field, decision.field)} differs from {decision.provider}, {decision.action}: {decision.reason}")
    if verbose or debug:
        lines.append("")
        lines.append("Candidates:")
        for attempt in attempts:
            for index, candidate in enumerate(attempt.candidates[:5], start=1):
                lines.extend(_render_candidate_details(index, candidate))
    if verbose:
        lines.append("Decisions:")
        for decision in decisions:
            lines.append(f"- {decision.field}: {decision.action} ({decision.reason})")
    if debug:
        lines.append("")
        lines.append("Debug:")
        for attempt in attempts:
            lines.extend(f"- {line}" for line in attempt.debug)
    lines.append("")
    lines.append(f"Final:\nMetadata provider status: {status}\nStatus: {status}")
    return "\n".join(lines)


def candidate_from_discogs_release(release: dict[str, Any], context: MetadataContext) -> MetadataCandidate:
    artists = "; ".join(_artist_name(row) for row in release.get("artists") or [] if _artist_name(row))
    labels, catalog_numbers = _labels_and_catalog(release)
    formats, release_formats, descriptions = _formats(release)
    identifiers = _identifiers(release)
    tracklist = _tracklist(release)
    barcode = _identifier_value(identifiers, "Barcode")
    release_type, edition = _discogs_release_descriptions(descriptions)
    audio_codec, format_decision = _discogs_audio_codec_decision(descriptions, context.tracks)
    candidate = MetadataCandidate(
        provider="discogs",
        source_id=str(release.get("id") or ""),
        source_url=str(release.get("uri") or "") or (f"https://www.discogs.com/release/{release.get('id')}" if release.get("id") else None),
        album=str(release.get("title") or ""),
        albumartist=artists,
        artist=artists,
        date=str(release.get("released") or release.get("year") or ""),
        originaldate=str(release.get("released") or release.get("year") or ""),
        label="; ".join(labels),
        genre=_join_limited(release.get("genres") or [], 3),
        style=_join_limited(_title_values(release.get("styles") or []), 8),
        country=str(release.get("country") or ""),
        barcode=barcode,
        catalog_number="; ".join(catalog_numbers),
        audio_codec=audio_codec,
        release_format="; ".join(release_formats),
        edition=edition,
        release_type=release_type,
        release_status=str(release.get("status") or ""),
        tracklist=tracklist,
        isrcs=_isrcs(release),
        extra={"identifiers": identifiers, "format_descriptions": descriptions, "discogs_formats": formats, "local_format": _local_format_label(context.tracks), "format_decision": format_decision},
    )
    candidate.score, reasons = score_discogs_candidate(candidate, context)
    candidate.confidence = "high" if candidate.score >= 85 else "medium" if candidate.score >= 70 else "low"
    candidate.match_reason = "; ".join(reasons) if reasons else "metadata match too weak"
    return candidate


def score_discogs_candidate(candidate: MetadataCandidate, context: MetadataContext) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    album_ratio = compare_ratio(normalize_album_title(context.album or context.title), normalize_album_title(candidate.album))
    artist_ratio = compare_ratio(normalize_artist_name(context.albumartist or context.artist), normalize_artist_name(candidate.albumartist or candidate.artist))
    score += round(album_ratio * 30)
    score += round(artist_ratio * 25)
    if album_ratio >= 0.95:
        reasons.append("album title exact")
    if artist_ratio >= 0.92:
        reasons.append("artist match")
    if context.barcode and candidate.barcode and _digits(context.barcode) == _digits(candidate.barcode):
        score += 30
        reasons.append("barcode exact")
    if context.catalog_number and candidate.catalog_number and _catalog_matches(context.catalog_number, candidate.catalog_number):
        score += 25
        reasons.append("catalog number exact")
    if context.label and candidate.label:
        label_ratio = compare_ratio(context.label, candidate.label)
        if label_ratio >= 0.9:
            score += 8
            reasons.append("label match")
        elif label_ratio >= 0.75:
            score += 4
            reasons.append("label close")
    if context.tracks and candidate.tracklist:
        if len(context.tracks) == len(candidate.tracklist):
            score += 15
            reasons.append("track count match")
        else:
            score -= min(15, abs(len(context.tracks) - len(candidate.tracklist)) * 3)
        title_scores = []
        for local, remote in zip(sorted(context.tracks, key=lambda item: item.tracknumber or 999), candidate.tracklist):
            title_scores.append(compare_ratio(normalize_track_title(local.title), normalize_track_title(remote.get("title", ""))))
        if title_scores:
            average = sum(title_scores) / len(title_scores)
            score += round(average * 15)
            if average >= 0.9:
                reasons.append("track titles match")
    local_year = _year(context.tracks[0].date if context.tracks else "")
    candidate_year = _year(candidate.date)
    if local_year and candidate_year:
        delta = abs(local_year - candidate_year)
        if delta == 0:
            score += 10
            reasons.append("year match")
        elif delta <= 1:
            score += 5
            reasons.append("year close")
    return max(0, min(100, score)), reasons


def candidate_from_deezer_album(album: dict[str, Any], context: MetadataContext, field_config: dict[str, Any] | None = None) -> MetadataCandidate:
    artist = str((album.get("artist") or {}).get("name") or "")
    tracks = (album.get("tracks") or {}).get("data") or []
    tracklist = [{"position": str(row.get("track_position") or index), "title": str(row.get("title") or ""), "duration": str(row.get("duration") or "")} for index, row in enumerate(tracks, start=1) if row.get("title")]
    candidate = MetadataCandidate(
        provider="deezer",
        source_id=str(album.get("id") or ""),
        source_url=str(album.get("link") or "") or None,
        album=str(album.get("title") or ""),
        albumartist=artist,
        artist=artist,
        date=str(album.get("release_date") or ""),
        originaldate=str(album.get("release_date") or ""),
        genre=_deezer_genre(album),
        tracklist=tracklist,
        tracktotal=str(album.get("nb_tracks") or (len(tracklist) if tracklist else "")),
        explicit=_explicit_value(album.get("explicit_lyrics")),
        deezer_album_id=str(album.get("id") or ""),
        extra={"cover_url": album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium") or album.get("cover"), "field_config": field_config or {}},
    )
    candidate.score, reasons = score_digital_candidate(candidate, context)
    candidate.confidence = _digital_confidence(candidate.score, candidate, context)
    candidate.match_reason = "; ".join(reasons) if reasons else "metadata match too weak"
    return candidate


def candidate_from_deezer_track(track: dict[str, Any], context: MetadataContext, field_config: dict[str, Any] | None = None) -> MetadataCandidate:
    album = track.get("album") or {}
    artist = str((track.get("artist") or {}).get("name") or "")
    candidate = MetadataCandidate(
        provider="deezer",
        source_id=str(track.get("id") or ""),
        source_url=str(track.get("link") or "") or None,
        album=str(album.get("title") or ""),
        albumartist=artist,
        artist=artist,
        title=str(track.get("title") or ""),
        date=str(track.get("release_date") or ""),
        originaldate=str(track.get("release_date") or ""),
        tracklist=[{"position": str(track.get("track_position") or "1"), "title": str(track.get("title") or ""), "duration": str(track.get("duration") or "")}],
        tracktotal="1",
        explicit=_explicit_value(track.get("explicit_lyrics")),
        deezer_album_id=str(album.get("id") or ""),
        deezer_track_id=str(track.get("id") or ""),
        extra={"cover_url": album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium") or album.get("cover"), "field_config": field_config or {}},
    )
    candidate.score, reasons = score_digital_candidate(candidate, context)
    candidate.confidence = _digital_confidence(candidate.score, candidate, context)
    candidate.match_reason = "; ".join(reasons) if reasons else "metadata match too weak"
    return candidate


def candidate_from_itunes_collection(album_row: dict[str, Any], results: list[dict[str, Any]], context: MetadataContext, field_config: dict[str, Any] | None = None, storefront: str = "us") -> MetadataCandidate:
    songs = [row for row in results if row.get("wrapperType") == "track" or row.get("kind") == "song"]
    album = next((row for row in results if row.get("wrapperType") == "collection"), album_row)
    tracklist = [{"position": str(row.get("trackNumber") or index), "title": str(row.get("trackName") or ""), "duration": str(round(float(row.get("trackTimeMillis") or 0) / 1000)) if row.get("trackTimeMillis") else "", "disc": str(row.get("discNumber") or "")} for index, row in enumerate(songs, start=1) if row.get("trackName")]
    candidate = MetadataCandidate(
        provider="itunes",
        source_id=str(album.get("collectionId") or album_row.get("collectionId") or ""),
        source_url=str(album.get("collectionViewUrl") or album_row.get("collectionViewUrl") or "") or None,
        album=str(album.get("collectionName") or album_row.get("collectionName") or ""),
        albumartist=str(album.get("collectionArtistName") or album.get("artistName") or album_row.get("artistName") or ""),
        artist=str(album.get("artistName") or album_row.get("artistName") or ""),
        date=_date_prefix(str(album.get("releaseDate") or album_row.get("releaseDate") or "")),
        originaldate=_date_prefix(str(album.get("releaseDate") or album_row.get("releaseDate") or "")),
        genre=str(album.get("primaryGenreName") or album_row.get("primaryGenreName") or ""),
        tracklist=tracklist,
        tracktotal=str(album.get("trackCount") or album_row.get("trackCount") or (len(tracklist) if tracklist else "")),
        disctotal=str(max([int(row.get("discCount") or 0) for row in songs] or [0]) or ""),
        explicit=_itunes_explicit(album.get("collectionExplicitness") or album_row.get("collectionExplicitness")),
        itunes_collection_id=str(album.get("collectionId") or album_row.get("collectionId") or ""),
        extra={"cover_url": _itunes_artwork(album.get("artworkUrl100") or album_row.get("artworkUrl100") or ""), "storefront": storefront, "store_country": album.get("country") or album_row.get("country"), "field_config": field_config or {}},
    )
    candidate.score, reasons = score_digital_candidate(candidate, context)
    candidate.confidence = _digital_confidence(candidate.score, candidate, context)
    candidate.match_reason = "; ".join(reasons) if reasons else "metadata match too weak"
    return candidate


def candidate_from_itunes_song(song: dict[str, Any], context: MetadataContext, field_config: dict[str, Any] | None = None, storefront: str = "us") -> MetadataCandidate:
    candidate = MetadataCandidate(
        provider="itunes",
        source_id=str(song.get("trackId") or ""),
        source_url=str(song.get("trackViewUrl") or "") or None,
        album=str(song.get("collectionName") or ""),
        albumartist=str(song.get("collectionArtistName") or song.get("artistName") or ""),
        artist=str(song.get("artistName") or ""),
        title=str(song.get("trackName") or ""),
        date=_date_prefix(str(song.get("releaseDate") or "")),
        originaldate=_date_prefix(str(song.get("releaseDate") or "")),
        genre=str(song.get("primaryGenreName") or ""),
        tracklist=[{"position": str(song.get("trackNumber") or "1"), "title": str(song.get("trackName") or ""), "duration": str(round(float(song.get("trackTimeMillis") or 0) / 1000)) if song.get("trackTimeMillis") else ""}],
        tracktotal=str(song.get("trackCount") or "1"),
        disctotal=str(song.get("discCount") or ""),
        explicit=_itunes_explicit(song.get("trackExplicitness")),
        itunes_collection_id=str(song.get("collectionId") or ""),
        itunes_track_id=str(song.get("trackId") or ""),
        extra={"cover_url": _itunes_artwork(song.get("artworkUrl100") or ""), "storefront": storefront, "store_country": song.get("country"), "field_config": field_config or {}},
    )
    candidate.score, reasons = score_digital_candidate(candidate, context)
    candidate.confidence = _digital_confidence(candidate.score, candidate, context)
    candidate.match_reason = "; ".join(reasons) if reasons else "metadata match too weak"
    return candidate


def score_digital_candidate(candidate: MetadataCandidate, context: MetadataContext) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    local_title = context.album or context.title
    candidate_title = candidate.album or candidate.title
    title_ratio = compare_ratio(normalize_album_title(local_title), normalize_album_title(candidate_title))
    artist_ratio = compare_ratio(normalize_artist_name(context.albumartist or context.artist), normalize_artist_name(candidate.albumartist or candidate.artist))
    score += round(title_ratio * 35)
    score += round(artist_ratio * 30)
    if title_ratio >= 0.95:
        reasons.append("title exact")
    elif title_ratio >= 0.85:
        reasons.append("title strong")
    if artist_ratio >= 0.92:
        reasons.append("artist match")
    if context.tracks and candidate.tracklist:
        if len(context.tracks) == len(candidate.tracklist):
            score += 15
            reasons.append("track count match")
        else:
            score -= min(15, abs(len(context.tracks) - len(candidate.tracklist)) * 4)
        title_scores = [compare_ratio(normalize_track_title(local.title), normalize_track_title(remote.get("title", ""))) for local, remote in zip(sorted(context.tracks, key=lambda item: item.tracknumber or 999), candidate.tracklist)]
        if title_scores and sum(title_scores) / len(title_scores) >= 0.9:
            score += 10
            reasons.append("track titles match")
        duration_score = _duration_score(context.tracks, candidate.tracklist)
        if duration_score:
            score += duration_score
            reasons.append("duration close")
    local_year = _year(context.tracks[0].date if context.tracks else "")
    candidate_year = _year(candidate.date)
    if local_year and candidate_year:
        delta = abs(local_year - candidate_year)
        if delta == 0:
            score += 10
            reasons.append("year match")
        elif delta <= 1:
            score += 5
            reasons.append("year close")
    return max(0, min(100, score)), reasons


def _digital_confidence(score: int, candidate: MetadataCandidate, context: MetadataContext) -> str:
    if score >= 85 and (not context.tracks or not candidate.tracklist or len(context.tracks) == len(candidate.tracklist)):
        return "high"
    if score >= 70:
        return "medium"
    return "low"


def discogs_token(config: dict[str, Any]) -> str:
    value = os.environ.get("DISCOGS_TOKEN")
    if value:
        return value.strip()
    api_value = str(get_config_value(config, "apis", "discogs_token", "") or "").strip()
    if api_value:
        return api_value
    return str(_provider_specific(config, "discogs").get("token", "") or "").strip()


def acoustid_api_key(config: dict[str, Any]) -> str:
    for name in ("ACOUSTID_KEY", "ACOUSTID_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return str(get_config_value(config, "apis", "acoustid_api_key", "") or "").strip()


def run_fpcalc(path: Path, fpcalc: str = "fpcalc", debug_lines: list[str] | None = None, debug: bool = False) -> dict[str, Any]:
    command = [fpcalc, "-json", str(path)]
    if debug and debug_lines is not None:
        debug_lines.append("fpcalc command: " + " ".join(command))
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    except OSError as exc:
        raise RuntimeError(f"fpcalc failed: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"fpcalc failed: {message}") from exc
    output = completed.stdout.strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        payload = parse_fpcalc_output(output)
    fingerprint = str(payload.get("fingerprint") or payload.get("FINGERPRINT") or "").strip()
    duration = payload.get("duration") or payload.get("DURATION") or 0
    if not fingerprint:
        raise RuntimeError("fpcalc did not return a fingerprint")
    return {"fingerprint": fingerprint, "duration": float(duration or 0)}


def parse_fpcalc_output(output: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def lookup_acoustid(fingerprint: str, duration: float, api_key: str, max_candidates: int = 5, debug_lines: list[str] | None = None, debug: bool = False) -> dict[str, Any]:
    params = {
        "client": api_key,
        "duration": str(int(round(duration))),
        "fingerprint": fingerprint,
        "meta": "recordings+releases+releasegroups",
    }
    url = f"{ACOUSTID_LOOKUP_URL}?{urllib.parse.urlencode(params)}"
    if debug and debug_lines is not None:
        masked = safe_debug_url(url)
        if len(masked) > 500:
            masked = masked[:500] + "..."
        debug_lines.append(f"acoustid lookup url: {masked}")
    payload = fetch_json(url)
    results = payload.get("results") or []
    if isinstance(results, list):
        payload["results"] = results[:max_candidates]
    return payload


def select_acoustid_match(track: Track, payload: dict[str, Any], min_score: float = 0.80) -> dict[str, Any]:
    best: dict[str, Any] = {}
    for result in payload.get("results") or []:
        score = float(result.get("score") or 0)
        if score < min_score:
            continue
        for recording in result.get("recordings") or []:
            row = acoustid_recording_candidate(track, result, recording, score)
            if row and int(row.get("score", 0)) > int(best.get("score", -1)):
                best = row
    return best or {"score": 0, "confidence": "low", "message": "no AcoustID result above minimum score"}


def acoustid_recording_candidate(track: Track, result: dict[str, Any], recording: dict[str, Any], acoustid_score: float) -> dict[str, Any]:
    title = str(recording.get("title") or "")
    artist = _artist_credit_name(recording.get("artists") or recording.get("artist-credit") or [])
    duration_ms = recording.get("duration")
    duration_delta = None
    if duration_ms and track.duration:
        duration_delta = abs(float(duration_ms) / 1000.0 - float(track.duration))
    title_ratio = compare_ratio(track.title, title) if track.title and title else 0.0
    artist_ratio = compare_ratio(track.artist or track.albumartist, artist) if (track.artist or track.albumartist) and artist else 0.0
    score = int(round(acoustid_score * 100))
    reasons = [f"acoustid score={acoustid_score:.2f}"]
    if duration_delta is not None:
        if duration_delta <= 3:
            score += 5
            reasons.append("duration close")
        elif duration_delta > 8:
            score -= 20
            reasons.append("duration mismatch")
    if title_ratio >= 0.90:
        score += 5
        reasons.append("title match")
    if artist_ratio >= 0.86:
        score += 5
        reasons.append("artist match")
    score = max(0, min(100, score))
    if acoustid_score >= 0.90 and (duration_delta is None or duration_delta <= 3) and (not track.title or title_ratio >= 0.80) and (not (track.artist or track.albumartist) or artist_ratio >= 0.70):
        confidence = "high"
    elif acoustid_score >= 0.80 and (duration_delta is None or duration_delta <= 8):
        confidence = "medium"
    else:
        confidence = "low"
    release_id, release_group_id, release_track_id = _recording_release_ids(recording)
    return {
        "acoustid_id": str(result.get("id") or ""),
        "mb_track_id": str(recording.get("id") or ""),
        "mb_album_id": release_id,
        "mb_release_group_id": release_group_id,
        "mb_release_track_id": release_track_id,
        "title": title,
        "artist": artist,
        "score": score,
        "confidence": confidence,
        "match_reason": "; ".join(reasons),
    }


def candidate_from_acoustid_results(context: MetadataContext, rows: list[dict[str, Any]], field_config: dict[str, Any]) -> MetadataCandidate | None:
    if not rows:
        return None
    write_fingerprint = bool(field_config.get("write_fingerprint", True))
    write_acoustid = bool(field_config.get("write_acoustid", True))
    use_identity = bool(field_config.get("use_for_identity", True))
    per_file: list[dict[str, Any]] = []
    for row in rows:
        track = row.get("track")
        if not isinstance(track, Track):
            continue
        changes: dict[str, Any] = {"track": track}
        if write_fingerprint and row.get("fingerprint"):
            changes["acoustid_fingerprint"] = row.get("fingerprint")
        if write_acoustid and row.get("acoustid_id") and row.get("confidence") != "low":
            changes["acoustid_id"] = row.get("acoustid_id")
        if use_identity and row.get("confidence") != "low":
            if row.get("mb_track_id"):
                changes["mb_track_id"] = row.get("mb_track_id")
            if row.get("mb_release_track_id"):
                changes["mb_release_track_id"] = row.get("mb_release_track_id")
        per_file.append(changes)
    matched = [row for row in rows if row.get("mb_track_id") and row.get("confidence") in {"medium", "high"}]
    release_ids = {str(row.get("mb_album_id") or "") for row in matched if row.get("mb_album_id")}
    release_group_ids = {str(row.get("mb_release_group_id") or "") for row in matched if row.get("mb_release_group_id")}
    album_consistent = len(context.tracks) > 1 and len(matched) == len(context.tracks) and len(release_ids) == 1
    if album_consistent:
        release_id = next(iter(release_ids))
        release_group_id = next(iter(release_group_ids), "")
        for changes in per_file:
            changes["mb_album_id"] = release_id
            if release_group_id:
                changes["mb_release_group_id"] = release_group_id
    conflicts: list[str] = []
    for changes in per_file:
        track = changes.get("track")
        if not isinstance(track, Track):
            continue
        for field in ("mb_track_id", "mb_release_track_id", "mb_album_id", "mb_release_group_id", "acoustid_id", "acoustid_fingerprint"):
            value = str(changes.get(field) or "")
            current = _existing_tag(track, field)
            if value and current and _norm_value(current) != _norm_value(value):
                conflicts.append(f"{track.path}: {field}")
    best = max(rows, key=lambda row: int(row.get("score") or 0), default={})
    confidence = str(best.get("confidence") or ("medium" if any(row.get("fingerprint") for row in rows) else "low"))
    candidate = MetadataCandidate(
        provider="acoustid",
        source_id=str(best.get("acoustid_id") or "fingerprint"),
        confidence=confidence,
        score=int(best.get("score") or 0),
        match_reason=str(best.get("match_reason") or "Chromaprint fingerprint"),
        acoustid_id=str(best.get("acoustid_id") or ""),
        acoustid_fingerprint=str(best.get("fingerprint") or ""),
        mb_track_id=str(best.get("mb_track_id") or ""),
        mb_release_track_id=str(best.get("mb_release_track_id") or ""),
        mb_album_id=next(iter(release_ids), "") if album_consistent else "",
        mb_release_group_id=next(iter(release_group_ids), "") if album_consistent else "",
        title=str(best.get("title") or context.title),
        artist=str(best.get("artist") or context.artist),
    )
    candidate.extra["per_file_changes"] = per_file
    candidate.extra["fingerprint_count"] = sum(1 for row in rows if row.get("fingerprint"))
    candidate.extra["match_count"] = len(matched)
    candidate.extra["album_consistent"] = album_consistent
    candidate.extra["decisions"] = rows
    candidate.extra["conflicts"] = conflicts
    return candidate


def provider_has_authority(provider: str, field: str) -> bool:
    order = FIELD_AUTHORITY.get(field, [])
    return bool(order and order[0] == provider)


def normalize_candidate_value(field: str, value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" ;")
    if field == "style":
        parts = [part.strip().title() for part in value.split(";") if part.strip()]
        return "; ".join(dict.fromkeys(parts))
    if field == "genre":
        parts = [part.strip().title() for part in value.split(";") if part.strip()]
        return "; ".join(dict.fromkeys(parts[:3]))
    if field == "barcode":
        digits = _digits(value)
        return digits or value
    return value


def fetch_discogs_json(url: str, token: str, timeout: int = 15) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT, "Authorization": f"Discogs token={token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("HTTP 429 rate limited") from exc
        raise RuntimeError(f"HTTP {exc.code}") from exc


def fetch_json(url: str, timeout: int = 12) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc


def _select_candidate(attempts: list[ProviderAttempt], min_confidence: str) -> MetadataCandidate | None:
    candidates = [candidate for attempt in attempts if attempt.status == "OK" for candidate in attempt.candidates]
    candidates = [candidate for candidate in candidates if candidate.provider != "musicbrainz" and confidence_allows(candidate.confidence, min_confidence)]
    return max(candidates, key=lambda item: item.score, default=None)


def _select_acoustid_fingerprint_candidate(attempts: list[ProviderAttempt]) -> MetadataCandidate | None:
    for attempt in attempts:
        if attempt.provider == "acoustid" and attempt.candidates:
            return attempt.candidates[0]
    return None


def _ambiguous_discogs_candidates(candidates: list[MetadataCandidate]) -> list[MetadataCandidate]:
    if len(candidates) < 2:
        return []
    top_score = candidates[0].score
    if top_score < 85:
        return []
    return [candidate for candidate in candidates if top_score - candidate.score < 5 and candidate.confidence == "high"]


def _manual_discogs_selection_required(attempts: list[ProviderAttempt]) -> bool:
    for attempt in attempts:
        if attempt.provider != "discogs" or attempt.status != "REVIEW":
            continue
        if _ambiguous_discogs_candidates(attempt.candidates):
            return not any(bool(candidate.extra.get("manual_selection")) for candidate in attempt.candidates)
    return False


def _render_candidate_details(index: int, candidate: MetadataCandidate) -> list[str]:
    descriptions = candidate.extra.get("format_descriptions", [])
    if isinstance(descriptions, list):
        description_items = [str(item) for item in descriptions if str(item).strip()]
        description_value = "; ".join(str(item) for item in descriptions if str(item).strip())
    else:
        description_items = []
        description_value = ""
    discogs_formats = candidate.extra.get("discogs_formats", [])
    if isinstance(discogs_formats, list):
        discogs_format_value = ", ".join(str(item) for item in [*discogs_formats, *description_items] if str(item).strip())
    else:
        discogs_format_value = ""
    return [
        f"{index}. {candidate.provider}:{candidate.source_id}",
        f"   title: {candidate.album or candidate.title or 'unknown'}",
        f"   artist: {candidate.albumartist or candidate.artist or 'unknown'}",
        f"   year/released: {candidate.date or 'unknown'}",
        f"   country: {candidate.country or 'unknown'}",
        f"   label: {candidate.label or 'unknown'}",
        f"   catalog number: {candidate.catalog_number or 'unknown'}",
        f"   barcode: {candidate.barcode or 'unknown'}",
        f"   Discogs format: {discogs_format_value or candidate.release_format or 'unknown'}",
        f"   Local format: {candidate.extra.get('local_format') or 'unknown'}",
        f"   Decision: {candidate.extra.get('format_decision') or 'unknown'}",
        f"   release format: {candidate.release_format or 'unknown'}",
        f"   audio codec: {candidate.audio_codec or 'unknown'}",
        f"   descriptions: {description_value or candidate.release_type or 'unknown'}",
        f"   score: {candidate.score}",
        f"   confidence: {candidate.confidence}",
        f"   reason: {candidate.match_reason or 'unknown'}",
    ]


def _display_candidate_value(field: str, value: str) -> str:
    if field == "acoustid_fingerprint" and len(value) > 24:
        return value[:24] + "..."
    return value


def _provider_section(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("metadata_providers", {})
    return value if isinstance(value, dict) else {}


def _provider_specific(config: dict[str, Any], name: str) -> dict[str, Any]:
    nested = _provider_section(config).get(name, {})
    if isinstance(nested, dict):
        return nested
    dotted = config.get(f"metadata_providers.{name}", {})
    return dotted if isinstance(dotted, dict) else {}


def _first_logical(tracks: list[Track], field: str) -> str:
    for track in tracks:
        values = get_tag(track, field)
        if values:
            return values[0]
    return ""


def _existing_tag(track: Track, field: str) -> str:
    try:
        values = get_tag(track, field)
    except KeyError:
        return ""
    return values[0] if values else ""


def _norm_value(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _artist_name(row: dict[str, Any]) -> str:
    return str(row.get("name") or "").replace(" (2)", "").strip()


def _artist_credit_name(rows: list[Any]) -> str:
    names: list[str] = []
    for row in rows:
        if isinstance(row, str):
            continue
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("artist"), dict):
            name = row["artist"].get("name") or row["artist"].get("sort-name") or ""
        else:
            name = row.get("name") or ""
        if name:
            names.append(str(name))
    return "; ".join(dict.fromkeys(names))


def _recording_release_ids(recording: dict[str, Any]) -> tuple[str, str, str]:
    releases = recording.get("releases") or []
    if not releases:
        return "", "", ""
    release = releases[0]
    release_id = str(release.get("id") or "")
    release_group = release.get("releasegroup") or release.get("release-group") or {}
    release_group_id = str(release_group.get("id") or "") if isinstance(release_group, dict) else ""
    release_track_id = ""
    for medium in release.get("mediums") or release.get("media") or []:
        for track in medium.get("tracks") or []:
            release_track_id = str(track.get("id") or "")
            if release_track_id:
                return release_id, release_group_id, release_track_id
    return release_id, release_group_id, release_track_id


def _labels_and_catalog(release: dict[str, Any]) -> tuple[list[str], list[str]]:
    labels: list[str] = []
    catalog: list[str] = []
    for row in release.get("labels") or []:
        name = str(row.get("name") or "").strip()
        catno = str(row.get("catno") or "").strip()
        if name and name.lower() != "not on label":
            labels.append(name)
        if catno and catno.lower() not in {"none", "n/a"}:
            catalog.append(catno)
    return list(dict.fromkeys(labels)), list(dict.fromkeys(catalog))


def _formats(release: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    names: list[str] = []
    release_formats: list[str] = []
    descriptions: list[str] = []
    for row in release.get("formats") or []:
        name = str(row.get("name") or "").strip()
        qty = str(row.get("qty") or "").strip()
        if name:
            names.append(f"{qty}x{name}" if qty and qty != "1" else name)
            release_formats.append(name)
        descriptions.extend(str(item).strip() for item in row.get("descriptions") or [] if str(item).strip())
    return list(dict.fromkeys(names)), list(dict.fromkeys(release_formats)), list(dict.fromkeys(descriptions))


def _discogs_release_descriptions(descriptions: list[str]) -> tuple[str, str]:
    release_types: list[str] = []
    editions: list[str] = []
    for description in descriptions:
        normalized = description.strip().lower()
        if normalized in DISCOGS_RELEASE_TYPES:
            release_types.append(description.strip())
        elif _norm_value(normalized) in DISCOGS_TECHNICAL_DESCRIPTIONS or normalized in DISCOGS_AUDIO_CODECS:
            continue
        elif normalized in DISCOGS_EDITION_DESCRIPTIONS:
            editions.append(description.strip())
    return "; ".join(dict.fromkeys(release_types)), "; ".join(dict.fromkeys(editions))


def _discogs_audio_codec_decision(descriptions: list[str], tracks: list[Track]) -> tuple[str, str]:
    discogs_codecs = [DISCOGS_AUDIO_CODECS[description.strip().lower()] for description in descriptions if description.strip().lower() in DISCOGS_AUDIO_CODECS]
    if not discogs_codecs:
        return "", "no Discogs codec description"
    codec = discogs_codecs[0]
    if len(set(discogs_codecs)) > 1:
        return "", "skip codec format, Discogs lists multiple codecs"
    local_codecs = {_local_codec(track) for track in tracks if _local_codec(track)}
    if local_codecs == {codec}:
        return codec, f"write {codec} format, local codec matches"
    return "", f"skip {codec} as edition, codec/format descriptor only"


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
    if value == "wma":
        return "WMA"
    if value == "wav":
        return "WAV"
    if value == "aiff":
        return "AIFF"
    if value == "alac":
        return "ALAC"
    if value == "ogg":
        return "OGG"
    return value.upper()


def _local_format_label(tracks: list[Track]) -> str:
    labels = []
    for track in tracks:
        extension = track.format.upper()
        codec = _local_codec(track)
        label = f"{extension}/{codec}" if extension in {"M4A", "MP4"} and codec == "AAC" else codec
        if label and label not in labels:
            labels.append(label)
    return "/".join(labels)


def _identifiers(release: dict[str, Any]) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    for row in release.get("identifiers") or []:
        kind = str(row.get("type") or "").strip()
        value = str(row.get("value") or "").strip()
        if kind and value and kind not in identifiers:
            identifiers[kind] = value
    return identifiers


def _identifier_value(identifiers: dict[str, str], wanted: str) -> str:
    wanted_lower = wanted.lower()
    for key, value in identifiers.items():
        if key.lower() == wanted_lower:
            return value
    return ""


def _tracklist(release: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in release.get("tracklist") or []:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        rows.append({"position": str(row.get("position") or ""), "title": title, "duration": str(row.get("duration") or "")})
    return rows


def _isrcs(release: dict[str, Any]) -> dict[int, str]:
    isrcs: dict[int, str] = {}
    for index, row in enumerate(release.get("tracklist") or [], start=1):
        for identifier in row.get("identifiers") or []:
            if str(identifier.get("type") or "").upper() == "ISRC" and identifier.get("value"):
                isrcs[index] = str(identifier["value"])
    return isrcs


def _join_limited(values: list[Any], limit: int) -> str:
    return "; ".join(str(value).strip() for value in values[:limit] if str(value).strip())


def _title_values(values: list[Any]) -> list[str]:
    return [str(value).strip().title() for value in values if str(value).strip()]


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def _deezer_genre(album: dict[str, Any]) -> str:
    genres = (album.get("genres") or {}).get("data") or []
    names = [str(row.get("name") or "").strip() for row in genres if row.get("name")]
    return _join_limited(names, 3)


def _explicit_value(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return ""


def _itunes_explicit(value: Any) -> str:
    lowered = str(value or "").lower()
    if "clean" in lowered or "notexplicit" in lowered:
        return "false"
    if "explicit" in lowered:
        return "true"
    return ""


def _itunes_artwork(url: str) -> str:
    return re.sub(r"/100x100bb\.", "/1200x1200bb.", url or "")


def _date_prefix(value: str) -> str:
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value or "")
    if match:
        return match.group(1)
    return value


def _duration_score(local_tracks: list[Track], remote_tracks: list[dict[str, str]]) -> int:
    deltas: list[float] = []
    for local, remote in zip(sorted(local_tracks, key=lambda item: item.tracknumber or 999), remote_tracks):
        if local.duration is None:
            continue
        remote_duration = _duration_seconds(remote.get("duration", ""))
        if remote_duration is None:
            continue
        deltas.append(abs(float(local.duration) - remote_duration))
    if not deltas:
        return 0
    average = sum(deltas) / len(deltas)
    if average <= 3:
        return 10
    if average <= 8:
        return 5
    return 0


def _duration_seconds(value: str) -> float | None:
    value = str(value or "").strip()
    if not value:
        return None
    if ":" in value:
        parts = value.split(":")
        try:
            total = 0
            for part in parts:
                total = total * 60 + int(part)
            return float(total)
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _catalog_matches(left: str, right: str) -> bool:
    left_values = {_norm_value(part) for part in re.split(r"[;/,]", left or "") if _norm_value(part)}
    right_values = {_norm_value(part) for part in re.split(r"[;/,]", right or "") if _norm_value(part)}
    return bool(left_values and right_values and left_values.intersection(right_values))


def _year(value: str) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", value or "")
    return int(match.group(0)) if match else None
