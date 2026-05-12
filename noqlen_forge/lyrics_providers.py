from __future__ import annotations

import json
import os
import time
from enum import Enum
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .audio import Track, get_tag
from .config import APP_USER_AGENT, LYRICS_API_KEY_ENV
from .provider_common import compare_ratio, confidence_allows, match_confidence, normalize_album_title, normalize_artist_name, normalize_track_title, safe_debug_url


LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"


USER_AGENT = f"{APP_USER_AGENT} (Noqlen Forge Core metadata tool)"


@dataclass(slots=True)
class LyricsSearchQuery:
    title: str
    artist: str
    album: str = ""
    duration: float | None = None


class LyricsKind(str, Enum):
    SYNCED = "synced"
    UNSYNCED = "unsynced"


@dataclass(slots=True)
class LyricsCandidate:
    provider: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float | None = None
    synced: bool = False
    plain_text: str = ""
    synced_text: str = ""
    language: str | None = None
    source_url: str | None = None
    confidence: str = "medium"
    match_reason: str = ""
    instrumental: bool = False
    raw_summary_json: str = ""
    external_id: str | None = None

    @property
    def text(self) -> str:
        return self.synced_text if self.synced else self.plain_text


@dataclass(slots=True)
class LyricsResult:
    text: str
    synced: bool
    source: str
    provider: str
    confidence: str = "medium"
    language: str | None = None
    duration: float | None = None
    match_reason: str = ""
    external_id: str | None = None
    instrumental: bool = False
    raw_summary_json: str = ""


@dataclass(slots=True)
class LyricsProviderResult:
    provider: str
    status: str
    candidates: list[LyricsCandidate] = field(default_factory=list)
    message: str = ""
    debug: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProviderAttempt:
    provider: str
    status: str
    message: str
    debug: list[str] = field(default_factory=list)
    result: LyricsResult | None = None


@dataclass(slots=True)
class LyricsProvider:
    name: str
    enabled: bool = True
    requires_api_key: bool = False
    supports_album: bool = False
    supports_track: bool = True

    def enabled_for(self, config: dict | None = None) -> bool:
        provider_config = lyrics_provider_config(config, self.name)
        return bool(provider_config.get("enabled", self.enabled))

    def search(self, query: LyricsSearchQuery, config: dict | None = None, debug: bool = False) -> list[LyricsCandidate]:
        track = Track(path=Path("."), format="", title=query.title, artist=query.artist, album=query.album, duration=query.duration)
        attempt = self.fetch(track, prefer_synced=bool((config or {}).get("prefer_synced", True)), debug=debug)
        return [candidate_from_result(attempt.result, title=query.title, artist=query.artist, album=query.album)] if attempt.result else []

    def fetch_candidate(self, candidate: LyricsCandidate, config: dict | None = None) -> LyricsCandidate:
        return candidate

    def normalize(self, text: str) -> str:
        return normalize_lyrics_text(text)

    def score(self, candidate: LyricsCandidate, query: LyricsSearchQuery) -> str:
        return candidate.confidence

    def fetch(self, track: Track, prefer_synced: bool = True, debug: bool = False) -> ProviderAttempt:
        raise NotImplementedError


class LocalLyricsProvider(LyricsProvider):
    def __init__(self) -> None:
        super().__init__(name="local", requires_api_key=False)

    def fetch(self, track: Track, prefer_synced: bool = True, debug: bool = False) -> ProviderAttempt:
        embedded = first_tag_text(track, "synced_lyrics") if prefer_synced else ""
        if embedded:
            return ProviderAttempt(self.name, "OK", "embedded synced lyrics", result=LyricsResult(embedded, True, "embedded", self.name, "high", match_reason="embedded lyrics"))
        embedded_plain = first_tag_text(track, "lyrics")
        if embedded_plain:
            return ProviderAttempt(self.name, "OK", "embedded lyrics", result=LyricsResult(embedded_plain, False, "embedded", self.name, "high", match_reason="embedded lyrics"))
        sidecar = find_sidecar_lyrics(track.path, prefer_synced=prefer_synced)
        if sidecar:
            return ProviderAttempt(self.name, "OK", sidecar.source, result=sidecar)
        return ProviderAttempt(self.name, "SKIP", "no embedded or sidecar lyrics")


class LRCLIBLyricsProvider(LyricsProvider):
    def __init__(self) -> None:
        super().__init__(name="lrclib", requires_api_key=False)

    def fetch(self, track: Track, prefer_synced: bool = True, debug: bool = False) -> ProviderAttempt:
        result = self.search(track_query(track), {"prefer_synced": prefer_synced}, debug=debug)
        if not result:
            return ProviderAttempt(self.name, "WARN", "no non-empty lyrics match", [f"candidates: 0"] if debug else [])
        selected = select_candidate_text(result, prefer_synced=prefer_synced, allow_unsynced=True)
        if selected is None:
            return ProviderAttempt(self.name, "WARN", "no allowed lyrics match")
        lyrics = result_from_candidate(selected)
        debug_lines = [f"selected id={lyrics.external_id or 'unknown'} type={'synced' if lyrics.synced else 'plain'} confidence={lyrics.confidence} reason={lyrics.match_reason}"] if debug else []
        return ProviderAttempt(self.name, "OK", f"{'synced' if lyrics.synced else 'plain'} {lyrics.confidence} confidence", debug_lines, lyrics)

    def search(self, query: LyricsSearchQuery, config: dict | None = None, debug: bool = False) -> list[LyricsCandidate]:
        params = {"track_name": query.title, "artist_name": query.artist}
        if query.album:
            params["album_name"] = query.album
        if query.duration:
            params["duration"] = str(int(round(query.duration)))
        if not params.get("track_name") or not params.get("artist_name"):
            return []
        url = f"{LRCLIB_SEARCH_URL}?{urllib.parse.urlencode({key: value for key, value in params.items() if value})}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read(1024 * 1024)
        except urllib.error.HTTPError:
            return []
        except Exception:
            return []
        try:
            rows = json.loads(payload.decode("utf-8"))
        except Exception:
            return []
        if not isinstance(rows, list):
            return []
        return lrclib_candidates(rows, query)


class CustomHttpLyricsProvider(LyricsProvider):
    def __init__(self) -> None:
        super().__init__(name="custom_http", enabled=False, requires_api_key=False)

    def enabled_for(self, config: dict | None = None) -> bool:
        provider_config = lyrics_provider_config(config, self.name)
        return bool(provider_config.get("enabled", self.enabled))

    def fetch(self, track: Track, prefer_synced: bool = True, debug: bool = False, config: dict | None = None) -> ProviderAttempt:  # type: ignore[override]
        provider_config = lyrics_provider_config(config, self.name)
        online_config = lyrics_online_config(config)
        if not bool(provider_config.get("enabled", self.enabled)):
            return ProviderAttempt(self.name, "SKIP", "provider disabled")
        base_url = str(provider_config.get("base_url", "") or "").strip()
        if not base_url:
            return ProviderAttempt(self.name, "SKIP", "requires base_url")
        if not bool(online_config.get("enabled", True)):
            return ProviderAttempt(self.name, "SKIP", "online lyrics disabled")
        query = track_query(track)
        candidates, warnings = self.search_with_warnings(query, config=config, prefer_synced=prefer_synced, debug=debug)
        selected = select_candidate_text(candidates, prefer_synced=prefer_synced, allow_unsynced=True)
        if selected is None:
            status = "WARN" if warnings else "WARN"
            return ProviderAttempt(self.name, status, warnings[0] if warnings else "no non-empty lyrics match", warnings if debug else [])
        result = result_from_candidate(selected)
        if result is None:
            return ProviderAttempt(self.name, "WARN", "no allowed lyrics match")
        debug_lines = [f"selected type={'synced' if result.synced else 'plain'} confidence={result.confidence} reason={result.match_reason}"] if debug else []
        return ProviderAttempt(self.name, "OK", f"{'synced' if result.synced else 'plain'} {result.confidence} confidence", debug_lines, result)

    def search(self, query: LyricsSearchQuery, config: dict | None = None, debug: bool = False) -> list[LyricsCandidate]:
        candidates, _warnings = self.search_with_warnings(query, config=config, prefer_synced=bool((config or {}).get("prefer_synced", True)), debug=debug)
        return candidates

    def search_with_warnings(self, query: LyricsSearchQuery, config: dict | None = None, prefer_synced: bool = True, debug: bool = False) -> tuple[list[LyricsCandidate], list[str]]:
        provider_config = lyrics_provider_config(config, self.name)
        online_config = lyrics_online_config(config)
        base_url = str(provider_config.get("base_url", "") or "").strip()
        if not base_url:
            return [], ["requires base_url"]
        params = {
            "artist": query.artist,
            "title": query.title,
            "album": query.album,
            "duration": str(int(round(query.duration))) if query.duration else "",
            "prefer_synced": "true" if prefer_synced else "false",
        }
        url = f"{base_url}?{urllib.parse.urlencode({key: value for key, value in params.items() if value})}"
        headers = {"Accept": "application/json", "User-Agent": str(online_config.get("user_agent", USER_AGENT) or USER_AGENT)}
        api_key = _custom_http_api_key(provider_config)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        _rate_limit(provider_config, online_config)
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=float(online_config.get("timeout_seconds", 20) or 20)) as response:
                payload = response.read(1024 * 1024)
        except urllib.error.HTTPError as exc:
            return [], [f"http {exc.code}"]
        except Exception as exc:
            return [], [f"request failed: {exc.__class__.__name__}"]
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except Exception:
            return [], ["invalid JSON response"]
        rows = parsed.get("results") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            return [], ["invalid response: expected results list"]
        return custom_http_candidates(rows[: int(online_config.get("max_results", 5) or 5)], query, bool(provider_config.get("supports_synced", True)), bool(provider_config.get("supports_unsynced", True))), []


class DisabledApiLyricsProvider(LyricsProvider):
    def __init__(self, name: str) -> None:
        super().__init__(name=name, enabled=False, requires_api_key=True)

    def fetch(self, track: Track, prefer_synced: bool = True, debug: bool = False) -> ProviderAttempt:
        return ProviderAttempt(self.name, "SKIP", f"{self.name} provider requires API access and is not implemented")


PROVIDERS = {provider.name: provider for provider in (LocalLyricsProvider(), LRCLIBLyricsProvider(), CustomHttpLyricsProvider(), DisabledApiLyricsProvider("genius"), DisabledApiLyricsProvider("musixmatch"), DisabledApiLyricsProvider("audd"))}


def fetch_lyrics_with_providers(track: Track, sources: list[str], min_confidence: str = "medium", prefer_synced: bool = True, allow_unsynced: bool = True, fallback_on_low_confidence: bool = True, fallback_on_instrumental: bool = False, debug: bool = False) -> tuple[LyricsResult | None, list[ProviderAttempt]]:
    attempts: list[ProviderAttempt] = []
    fallback: LyricsResult | None = None
    strong_results: list[LyricsResult] = []
    for source in sources:
        provider = PROVIDERS.get(source)
        if provider is None:
            attempts.append(ProviderAttempt(source, "WARN", "unknown lyrics provider"))
            continue
        if not provider.enabled_for(None):
            attempts.append(ProviderAttempt(source, "SKIP", "provider disabled"))
            continue
        attempt = provider.fetch(track, prefer_synced=prefer_synced, debug=debug)
        attempts.append(attempt)
        if attempt.result is None:
            continue
        if attempt.result.instrumental and not fallback_on_instrumental:
            attempts[-1].status = "WARN"
            attempts[-1].message = "instrumental lyrics skipped"
            continue
        if attempt.result.synced is False and not allow_unsynced:
            attempts[-1].status = "WARN"
            attempts[-1].message = "unsynced lyrics disabled"
            continue
        if confidence_allows(attempt.result.confidence, min_confidence):
            if attempt.result.confidence == "high":
                strong_results.append(attempt.result)
            if attempt.result.confidence == "high" or not fallback_on_low_confidence:
                return attempt.result, attempts
            fallback = fallback or attempt.result
            continue
        attempts[-1].status = "WARN"
        attempts[-1].message = f"confidence {attempt.result.confidence} below minimum {min_confidence}"
    return fallback, attempts


def find_sidecar_lyrics(file: Path, prefer_synced: bool = True) -> LyricsResult | None:
    candidates = [file.with_suffix(".lrc"), file.with_suffix(".txt"), file.parent / "track.lrc", file.parent / "track.txt"]
    if not prefer_synced:
        candidates = [file.with_suffix(".txt"), file.with_suffix(".lrc"), file.parent / "track.txt", file.parent / "track.lrc"]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = normalize_lyrics_text(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            text = normalize_lyrics_text(path.read_text(encoding="utf-8", errors="ignore"))
        if text:
            synced = is_lrc(text) or path.suffix.lower() == ".lrc"
            return LyricsResult(text=text, synced=synced, source=f"sidecar:{path.name}", provider="local", confidence="high", match_reason="local sidecar lyrics")
    return None


def best_lrclib_result(rows: list[dict], track: Track, prefer_synced: bool = True) -> LyricsResult | None:
    selected = select_candidate_text(lrclib_candidates(rows, track_query(track)), prefer_synced=prefer_synced, allow_unsynced=True)
    return result_from_candidate(selected) if selected else None


def lrclib_candidates(rows: list[dict], query: LyricsSearchQuery) -> list[LyricsCandidate]:
    candidates: list[LyricsCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        artist_ratio = compare_ratio(normalize_artist_name(query.artist), normalize_artist_name(str(row.get("artistName") or "")))
        title_ratio = compare_ratio(normalize_track_title(query.title), normalize_track_title(str(row.get("trackName") or "")))
        album_ratio = compare_ratio(normalize_album_title(query.album), normalize_album_title(str(row.get("albumName") or "")))
        row_duration = duration_value(row.get("duration"))
        duration_delta = abs(query.duration - row_duration) if query.duration is not None and row_duration is not None else None
        confidence, reason = match_confidence(artist_ratio, album_ratio, title_ratio, duration_delta=duration_delta, title_only=not query.album)
        plain = normalize_lyrics_text(str(row.get("plainLyrics") or ""))
        synced = normalize_lyrics_text(str(row.get("syncedLyrics") or ""))
        if not plain and not synced:
            continue
        if synced:
            candidates.append(LyricsCandidate(provider="lrclib", title=str(row.get("trackName") or ""), artist=str(row.get("artistName") or ""), album=str(row.get("albumName") or ""), duration=row_duration, synced=True, plain_text=plain, synced_text=synced, source_url="https://lrclib.net", confidence=confidence, match_reason=reason, instrumental=bool(row.get("instrumental")), external_id=str(row.get("id") or "") or None, raw_summary_json=_raw_summary(row)))
        if plain:
            candidates.append(LyricsCandidate(provider="lrclib", title=str(row.get("trackName") or ""), artist=str(row.get("artistName") or ""), album=str(row.get("albumName") or ""), duration=row_duration, synced=False, plain_text=plain, synced_text=synced, source_url="https://lrclib.net", confidence=confidence, match_reason=reason, instrumental=bool(row.get("instrumental")), external_id=str(row.get("id") or "") or None, raw_summary_json=_raw_summary(row)))
    return candidates


def custom_http_candidates(rows: list[dict], query: LyricsSearchQuery, supports_synced: bool = True, supports_unsynced: bool = True) -> list[LyricsCandidate]:
    candidates: list[LyricsCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        artist = str(row.get("artist") or "")
        title = str(row.get("title") or "")
        album = str(row.get("album") or "")
        row_duration = duration_value(row.get("duration"))
        artist_ratio = compare_ratio(normalize_artist_name(query.artist), normalize_artist_name(artist))
        title_ratio = compare_ratio(normalize_track_title(query.title), normalize_track_title(title))
        album_ratio = compare_ratio(normalize_album_title(query.album), normalize_album_title(album))
        duration_delta = abs(query.duration - row_duration) if query.duration is not None and row_duration is not None else None
        confidence, reason = match_confidence(artist_ratio, album_ratio, title_ratio, duration_delta=duration_delta, title_only=not query.album)
        plain = normalize_lyrics_text(str(row.get("plain") or row.get("plainLyrics") or ""))
        synced = normalize_lyrics_text(str(row.get("synced") or row.get("syncedLyrics") or ""))
        common = {
            "provider": "custom_http",
            "title": title,
            "artist": artist,
            "album": album,
            "duration": row_duration,
            "language": str(row.get("language") or "") or None,
            "source_url": str(row.get("source_url") or row.get("sourceUrl") or "") or None,
            "confidence": confidence,
            "match_reason": reason,
            "instrumental": bool(row.get("instrumental")),
            "raw_summary_json": _custom_raw_summary(row),
        }
        if synced and supports_synced:
            candidates.append(LyricsCandidate(synced=True, synced_text=synced, plain_text=plain, **common))
        if plain and supports_unsynced:
            candidates.append(LyricsCandidate(synced=False, synced_text=synced, plain_text=plain, **common))
    return candidates


def track_query(track: Track) -> LyricsSearchQuery:
    return LyricsSearchQuery(title=track.title, artist=track.artist or track.albumartist, album=track.album, duration=track.duration)


def candidate_from_result(result: LyricsResult | None, title: str = "", artist: str = "", album: str = "") -> LyricsCandidate:
    if result is None:
        return LyricsCandidate(provider="")
    return LyricsCandidate(provider=result.provider, title=title, artist=artist, album=album, duration=result.duration, synced=result.synced, plain_text="" if result.synced else result.text, synced_text=result.text if result.synced else "", language=result.language, source_url=result.source, confidence=result.confidence, match_reason=result.match_reason, instrumental=result.instrumental, raw_summary_json=result.raw_summary_json, external_id=result.external_id)


def result_from_candidate(candidate: LyricsCandidate | None) -> LyricsResult | None:
    if candidate is None:
        return None
    text = normalize_lyrics_text(candidate.text)
    if not text:
        return None
    return LyricsResult(text=text, synced=candidate.synced, source=candidate.source_url or candidate.provider, provider=candidate.provider, confidence=candidate.confidence, language=candidate.language, duration=candidate.duration, match_reason=candidate.match_reason, external_id=candidate.external_id, instrumental=candidate.instrumental, raw_summary_json=candidate.raw_summary_json)


def select_candidate_text(candidates: list[LyricsCandidate], prefer_synced: bool = True, allow_unsynced: bool = True) -> LyricsCandidate | None:
    allowed = [candidate for candidate in candidates if candidate.text and (candidate.synced or allow_unsynced)]
    if not allowed:
        return None
    def key(candidate: LyricsCandidate) -> tuple[int, int, int]:
        return ({"high": 2, "medium": 1, "low": 0}.get(candidate.confidence, 0), 1 if candidate.synced == prefer_synced else 0, 1 if candidate.synced else 0)
    return sorted(allowed, key=key, reverse=True)[0]


def lyrics_diverge(left: str, right: str) -> bool:
    left_norm = normalize_lyrics_text("\n".join(line for line in strip_lrc(left).splitlines() if line.strip()))
    right_norm = normalize_lyrics_text("\n".join(line for line in strip_lrc(right).splitlines() if line.strip()))
    if not left_norm or not right_norm:
        return False
    return compare_ratio(left_norm, right_norm) < 0.82


def strip_lrc(text: str) -> str:
    import re

    return re.sub(r"\[(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]", "", text or "")


def _raw_summary(row: dict) -> str:
    return json.dumps({"id": row.get("id"), "instrumental": row.get("instrumental"), "has_synced": bool(row.get("syncedLyrics")), "has_plain": bool(row.get("plainLyrics"))}, sort_keys=True)


def _custom_raw_summary(row: dict) -> str:
    return json.dumps({"instrumental": row.get("instrumental"), "has_synced": bool(row.get("synced") or row.get("syncedLyrics")), "has_plain": bool(row.get("plain") or row.get("plainLyrics")), "language": row.get("language")}, sort_keys=True)


def lyrics_online_config(config: dict | None) -> dict:
    lyrics = (config or {}).get("lyrics", {}) if isinstance(config, dict) else {}
    online = lyrics.get("online", {}) if isinstance(lyrics, dict) else {}
    return online if isinstance(online, dict) else {}


def lyrics_provider_config(config: dict | None, name: str) -> dict:
    lyrics = (config or {}).get("lyrics", {}) if isinstance(config, dict) else {}
    if not isinstance(lyrics, dict):
        return {}
    for container_name in ("provider_settings", "provider_configs"):
        container = lyrics.get(container_name, {})
        if isinstance(container, dict) and isinstance(container.get(name), dict):
            return dict(container[name])
    providers = lyrics.get("providers", {})
    if isinstance(providers, dict) and isinstance(providers.get(name), dict):
        return dict(providers[name])
    direct = lyrics.get(name, {})
    return dict(direct) if isinstance(direct, dict) else {}


def _custom_http_api_key(provider_config: dict) -> str:
    env_name = str(provider_config.get("api_key_env", LYRICS_API_KEY_ENV) or "").strip()
    return os.environ.get(env_name, "").strip() if env_name else ""


_LAST_ONLINE_CALL: dict[str, float] = {}


def _rate_limit(provider_config: dict, online_config: dict) -> None:
    if bool(provider_config.get("disable_rate_limit", False)):
        return
    delay = float(online_config.get("rate_limit_seconds", 1.0) or 0)
    if delay <= 0:
        return
    now = time.monotonic()
    last = _LAST_ONLINE_CALL.get("custom_http", 0.0)
    wait = delay - (now - last)
    if wait > 0:
        time.sleep(wait)
    _LAST_ONLINE_CALL["custom_http"] = time.monotonic()


def render_provider_list(config: dict | None = None, verbose: bool = False) -> str:
    lines = ["Lyrics providers:"]
    lines.append("- embedded: local, enabled")
    lines.append("- sidecar: local, enabled")
    for name in sorted(name for name in PROVIDERS if name != "local"):
        provider = PROVIDERS[name]
        kind = "local" if name == "local" else "online"
        status = "enabled" if provider.enabled_for(config) else "disabled"
        notes: list[str] = []
        if name == "custom_http" and not str(lyrics_provider_config(config, name).get("base_url", "") or "").strip():
            notes.append("requires base_url")
        if provider.requires_api_key:
            notes.append("requires API access")
        suffix = f", {', '.join(notes)}" if notes else ""
        lines.append(f"- {name}: {kind}, {status}{suffix}")
        if verbose and name == "custom_http":
            settings = lyrics_provider_config(config, name)
            base_url = "configured" if str(settings.get("base_url", "") or "").strip() else "not configured"
            lines.append(f"  base_url: {base_url}; supports synced={bool(settings.get('supports_synced', True))}; unsynced={bool(settings.get('supports_unsynced', True))}")
    return "\n".join(lines)


def first_tag_text(track: Track, logical_name: str) -> str:
    for value in get_tag(track, logical_name):
        text = normalize_lyrics_text(value)
        if text:
            return text
    return ""


def is_lrc(text: str) -> bool:
    import re

    return bool(re.search(r"\[(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]", text or ""))


def normalize_lyrics_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").strip().split("\n"))


def duration_value(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
