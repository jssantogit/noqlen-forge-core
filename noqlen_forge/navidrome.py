from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import APP_SLUG, get_config_value
from .db import apply_migrations, connect, connect_readonly, database_path, execute_query, normalize_path, parse_query


class NavidromeError(RuntimeError):
    pass


@dataclass(slots=True)
class NavidromeConfig:
    base_url: str
    username: str
    password: str = ""
    token: str = ""
    salt: str = ""
    client_name: str = APP_SLUG
    api_version: str = "1.16.1"
    auth: str = "password"
    timeout_seconds: float = 20.0
    verify_ssl: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "NavidromeConfig":
        section = config.get("navidrome") if isinstance(config.get("navidrome"), dict) else {}
        base_url = str(section.get("base_url") or get_config_value(config, "navidrome", "base_url", "") or "").rstrip("/")
        username = str(section.get("username") or "").strip()
        password = os.environ.get("NOQLEN_FORGE_NAVIDROME_PASSWORD", str(section.get("password") or "")).strip()
        token = os.environ.get("NOQLEN_FORGE_NAVIDROME_TOKEN", str(section.get("token") or "")).strip()
        salt = os.environ.get("NOQLEN_FORGE_NAVIDROME_SALT", str(section.get("salt") or "")).strip()
        return cls(
            base_url=base_url,
            username=username,
            password=password,
            token=token,
            salt=salt,
            client_name=str(section.get("client_name") or APP_SLUG),
            api_version=str(section.get("api_version") or "1.16.1"),
            auth=str(section.get("auth") or "password"),
            timeout_seconds=float(section.get("timeout_seconds") or 20),
            verify_ssl=bool(section.get("verify_ssl", True)),
        )


@dataclass(slots=True)
class RatingItem:
    navidrome_id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    duration: float | None = None
    track: int | None = None
    disc: int | None = None
    year: int | None = None
    starred: bool = False
    starred_at: str = ""
    rating: float | None = None
    play_count: int | None = None
    last_played: str = ""
    path: str = ""
    mb_track_id: str = ""
    mb_release_track_id: str = ""
    acoustid_id: str = ""
    isrc: str = ""
    raw_summary: dict[str, Any] | None = None


@dataclass(slots=True)
class BackupSummary:
    status: str
    total_items: int
    matched_items: int
    unmatched_items: int
    rated_items: int
    starred_items: int
    saved_items: int
    mode: str


@dataclass(slots=True)
class PlaylistItem:
    position: int
    song: RatingItem


@dataclass(slots=True)
class PlaylistBackup:
    navidrome_playlist_id: str
    name: str
    owner: str = ""
    comment: str = ""
    song_count: int | None = None
    duration: float | None = None
    public: bool | None = None
    changed_at: str = ""
    created_at_remote: str = ""
    raw_summary: dict[str, Any] | None = None
    items: list[PlaylistItem] | None = None


class NavidromeClient:
    def __init__(self, config: NavidromeConfig):
        self.config = config
        if not config.base_url:
            raise NavidromeError("Navidrome base_url is required")
        if not config.username:
            raise NavidromeError("Navidrome username is required")

    def ping(self) -> dict[str, Any]:
        return self._get("ping")

    def get_starred2(self) -> dict[str, Any]:
        return self._get("getStarred2")

    def get_starred(self) -> dict[str, Any]:
        return self._get("getStarred")

    def get_song(self, song_id: str) -> dict[str, Any]:
        return self._get("getSong", {"id": song_id})

    def get_playlists(self) -> dict[str, Any]:
        return self._get("getPlaylists")

    def get_playlist(self, playlist_id: str) -> dict[str, Any]:
        return self._get("getPlaylist", {"id": playlist_id})

    def search3(self, query: str, *, song_count: int = 20) -> dict[str, Any]:
        return self._get("search3", {"query": query, "songCount": str(song_count), "albumCount": "0", "artistCount": "0"})

    def create_playlist(self, name: str, song_ids: list[str]) -> dict[str, Any]:
        return self._get("createPlaylist", {"name": name, "songId": song_ids})

    def update_playlist(self, playlist_id: str, song_ids: list[str], *, name: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"playlistId": playlist_id, "songId": song_ids}
        if name:
            params["name"] = name
        return self._get("updatePlaylist", params)

    def set_rating(self, song_id: str, rating: int) -> dict[str, Any]:
        return self._get("setRating", {"id": song_id, "rating": str(rating)})

    def star(self, song_id: str) -> dict[str, Any]:
        return self._get("star", {"id": song_id})

    def unstar(self, song_id: str) -> dict[str, Any]:
        return self._get("unstar", {"id": song_id})

    def iter_rating_items(self) -> list[RatingItem]:
        payload = self._starred_payload_with_fallback()
        songs = _extract_song_list(payload)
        items: list[RatingItem] = []
        seen: set[str] = set()
        for song in songs:
            item = normalize_song_payload(song)
            if not item.navidrome_id or item.navidrome_id in seen:
                continue
            seen.add(item.navidrome_id)
            if item.rating is None or item.play_count is None or not item.last_played:
                try:
                    detail = self.get_song(item.navidrome_id)
                    detail_song = detail.get("subsonic-response", {}).get("song") or detail.get("song")
                    if isinstance(detail_song, dict):
                        detailed = normalize_song_payload({**song, **detail_song})
                        item = detailed
                except NavidromeError:
                    pass
            items.append(item)
        return items

    def iter_playlists(self, *, playlist_id: str | None = None, name: str | None = None) -> list[PlaylistBackup]:
        playlists = _extract_playlist_summaries(self.get_playlists())
        if playlist_id:
            playlists = [playlist for playlist in playlists if playlist.navidrome_playlist_id == playlist_id]
        if name:
            playlists = [playlist for playlist in playlists if playlist.name.casefold() == name.casefold()]
        backups: list[PlaylistBackup] = []
        for playlist in playlists:
            detail = self.get_playlist(playlist.navidrome_playlist_id)
            detailed = normalize_playlist_payload(detail, fallback=playlist)
            items = []
            for position, song in enumerate(_playlist_entry_payloads(detail), start=1):
                item = normalize_song_payload(song)
                if item.navidrome_id and not item.title:
                    try:
                        song_detail = self.get_song(item.navidrome_id)
                        detail_song = song_detail.get("subsonic-response", {}).get("song") or song_detail.get("song")
                        if isinstance(detail_song, dict):
                            item = normalize_song_payload({**song, **detail_song})
                    except NavidromeError:
                        pass
                items.append(PlaylistItem(position=position, song=item))
            detailed.items = items
            backups.append(detailed)
        return backups

    def _starred_payload_with_fallback(self) -> dict[str, Any]:
        try:
            return self.get_starred2()
        except NavidromeError:
            return self.get_starred()

    def _get(self, method: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {**build_auth_params(self.config), **(extra or {})}
        url = f"{self.config.base_url}/rest/{method}.view?{urllib.parse.urlencode(params, doseq=True)}"
        context = None if self.config.verify_ssl else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(url, timeout=self.config.timeout_seconds, context=context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise NavidromeError(f"Navidrome HTTP error {exc.code} for {method}") from exc
        except urllib.error.URLError as exc:
            raise NavidromeError(f"Navidrome connection failed for {method}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise NavidromeError(f"Navidrome returned invalid JSON for {method}") from exc
        response_payload = payload.get("subsonic-response", payload)
        status = str(response_payload.get("status") or "").lower()
        if status == "failed":
            error = response_payload.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else "authentication or API error"
            raise NavidromeError(f"Navidrome API failed for {method}: {_clean_error(message)}")
        return payload


def build_auth_params(config: NavidromeConfig) -> dict[str, str]:
    if config.auth not in {"password", "token"}:
        raise NavidromeError("Navidrome auth must be password or token")
    params = {"u": config.username, "v": config.api_version, "c": config.client_name, "f": "json"}
    if config.auth == "token":
        if not config.token or not config.salt:
            raise NavidromeError("Navidrome token auth requires token and salt")
        params.update({"t": config.token, "s": config.salt})
        return params
    if not config.password:
        raise NavidromeError("Navidrome password auth requires password")
    params["p"] = config.password
    return params


def normalize_song_payload(payload: dict[str, Any]) -> RatingItem:
    starred_at = _text(payload.get("starred") or payload.get("starredAt"))
    raw = {key: payload.get(key) for key in ("id", "title", "artist", "album", "albumArtist", "duration", "userRating", "rating", "starred", "playCount", "played", "path") if key in payload}
    return RatingItem(
        navidrome_id=_text(payload.get("id")),
        title=_text(payload.get("title")),
        artist=_text(payload.get("artist")),
        album=_text(payload.get("album")),
        albumartist=_text(payload.get("albumArtist") or payload.get("albumartist")),
        duration=_float(payload.get("duration")),
        track=_int(payload.get("track")),
        disc=_int(payload.get("discNumber") or payload.get("disc")),
        year=_int(payload.get("year")),
        starred=bool(starred_at or payload.get("starred") is True),
        starred_at=starred_at,
        rating=_float(payload.get("userRating") if payload.get("userRating") is not None else payload.get("rating")),
        play_count=_int(payload.get("playCount") if payload.get("playCount") is not None else payload.get("playedCount")),
        last_played=_text(payload.get("played") or payload.get("lastPlayed")),
        path=_text(payload.get("path") or payload.get("suffixPath")),
        mb_track_id=_text(payload.get("musicBrainzTrackId") or payload.get("mbTrackId") or payload.get("musicbrainz_trackid")),
        mb_release_track_id=_text(payload.get("musicBrainzReleaseTrackId") or payload.get("mbReleaseTrackId")),
        acoustid_id=_text(payload.get("acoustId") or payload.get("acoustid_id")),
        isrc=_text(payload.get("isrc")),
        raw_summary=raw,
    )


def build_player_track_identity(item: RatingItem) -> tuple[str, str, str]:
    if item.mb_track_id:
        return f"mb_track:{item.mb_track_id.casefold()}", "mb_track_id", "high"
    if item.mb_release_track_id:
        return f"mb_release_track:{item.mb_release_track_id.casefold()}", "mb_release_track_id", "high"
    if item.acoustid_id:
        return f"acoustid:{item.acoustid_id.casefold()}", "acoustid_id", "high"
    if item.isrc:
        return f"isrc:{item.isrc.casefold()}", "isrc", "high"
    if item.artist and item.title and item.duration:
        return f"artist_title_duration:{_norm(item.artist)}:{_norm(item.title)}:{round(float(item.duration))}", "artist_title_duration", "medium"
    if item.albumartist and item.album and item.track and item.title:
        return f"album_track_title:{_norm(item.albumartist)}:{_norm(item.album)}:{item.track}:{_norm(item.title)}", "album_track_title", "medium"
    if item.navidrome_id:
        return f"navidrome:{item.navidrome_id}", "navidrome_id", "low"
    return "unknown", "unknown", "low"


def navidrome_ping(config: dict[str, Any]) -> tuple[int, str]:
    try:
        nd_config = NavidromeConfig.from_config(config)
        client = NavidromeClient(nd_config)
        client.ping()
    except NavidromeError as exc:
        return 1, f"Navidrome ping\nServer: {_safe_server(config)}\nUser: {_safe_user(config)}\n\nStatus: FAIL\nError: {_clean_error(str(exc))}"
    return 0, f"Navidrome ping\nServer: {nd_config.base_url}\nUser: {nd_config.username}\n\nStatus: OK\nServer reachable"


def playlists_list(config: dict[str, Any], *, output_format: str = "text", output: Path | None = None, client: NavidromeClient | None = None, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    try:
        nd_config = client.config if client else NavidromeConfig.from_config(config)
        active_client = client or NavidromeClient(nd_config)
        active_client.ping()
        playlists = _extract_playlists(active_client.get_playlists())
    except NavidromeError as exc:
        return 1, f"Navidrome playlists list\nMode: READ-ONLY\nStatus: FAIL\nError: {_clean_error(str(exc))}"
    payload = {"status": "OK", "mode": "READ-ONLY", "server": nd_config.base_url, "user": nd_config.username, "count": len(playlists), "playlists": playlists}
    text = _render_playlists_list_text(payload)
    return 0, _format_playlist_output(payload, output_format, output, text, "list")


def playlists_push(config: dict[str, Any], query: str, *, name: str | None = None, playlist_id: str | None = None, apply: bool = False, replace: bool = False, append: bool = False, preserve_existing: bool = False, allow_medium_confidence: bool = False, force: bool = False, sort: str | None = None, reverse: bool = False, limit: int | None = None, path_mode: str = "absolute", library_root: Path | None = None, output_format: str = "text", output: Path | None = None, client: NavidromeClient | None = None, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    return _playlists_push_query(config, query, name=name, playlist_id=playlist_id, apply=apply, replace=replace, append=append, preserve_existing=preserve_existing, allow_medium_confidence=allow_medium_confidence, force=force, sort=sort, reverse=reverse, limit=limit, path_mode=path_mode, library_root=library_root, output_format=output_format, output=output, client=client, verbose=verbose, debug=debug)


def playlists_push_smart(config: dict[str, Any], smart_name: str, *, apply: bool = False, replace: bool = False, append: bool = False, preserve_existing: bool = False, allow_medium_confidence: bool = False, force: bool = False, output_format: str = "text", output: Path | None = None, client: NavidromeClient | None = None, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    try:
        definition = _load_smart_playlist(config, smart_name)
    except (ValueError, sqlite3.DatabaseError) as exc:
        return 1, f"Navidrome playlist push\nSmart playlist: {smart_name}\nStatus: FAIL\nError: {_clean_error(str(exc))}"
    return _playlists_push_query(config, definition["query"], name=smart_name, playlist_id=None, apply=apply, replace=replace, append=append, preserve_existing=preserve_existing, allow_medium_confidence=allow_medium_confidence, force=force, sort=definition.get("sort"), reverse=bool(definition.get("reverse")), limit=definition.get("limit_count"), path_mode=definition.get("path_mode") or "absolute", library_root=Path(definition["library_root"]) if definition.get("library_root") else None, output_format=output_format, output=output, client=client, verbose=verbose, debug=debug)


def playlists_diff(config: dict[str, Any], query: str, *, name: str | None = None, playlist_id: str | None = None, sort: str | None = None, reverse: bool = False, limit: int | None = None, path_mode: str = "absolute", library_root: Path | None = None, output_format: str = "text", output: Path | None = None, client: NavidromeClient | None = None, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    return _playlists_push_query(config, query, name=name, playlist_id=playlist_id, apply=False, replace=True, append=False, preserve_existing=False, allow_medium_confidence=False, force=False, sort=sort, reverse=reverse, limit=limit, path_mode=path_mode, library_root=library_root, output_format=output_format, output=output, client=client, verbose=verbose, debug=debug, diff_only=True)


def playlists_backup(config: dict[str, Any], *, apply: bool = False, playlist_id: str | None = None, name: str | None = None, output_format: str = "text", output: Path | None = None, client: NavidromeClient | None = None, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    started = _now()
    mode = "APPLY" if apply else "DRY-RUN"
    nd_config = client.config if client else NavidromeConfig.from_config(config)
    try:
        active_client = client or NavidromeClient(nd_config)
        active_client.ping()
        playlists = active_client.iter_playlists(playlist_id=playlist_id, name=name) if hasattr(active_client, "iter_playlists") else _fetch_playlist_backups(active_client, playlist_id=playlist_id, name=name)
    except NavidromeError as exc:
        payload = _playlist_backup_payload(nd_config, mode, [], {}, "FAIL", _clean_error(str(exc)))
        return 1, _format_playlist_backup_result(payload, output_format, output, _render_playlist_backup_text(payload))
    matches = _match_playlist_items(config, playlists)
    matched = sum(1 for match in matches.values() if match.get("match_confidence") in {"high", "medium", "low"})
    total_items = sum(len(playlist.items or []) for playlist in playlists)
    status = "WARN" if matched < total_items else "OK"
    payload = _playlist_backup_payload(nd_config, mode, playlists, matches, status, "")
    if apply:
        _save_playlist_backup(config, nd_config, playlists, matches, payload, started)
    return 0, _format_playlist_backup_result(payload, output_format, output, _render_playlist_backup_text(payload))


def playlists_status(config: dict[str, Any]) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 0, "Navidrome playlist backup status\nStatus: WARN\nNo database found"
    with conn:
        if not _table_exists(conn, "navidrome_playlist_backup_runs"):
            return 0, "Navidrome playlist backup status\nStatus: WARN\nNo backups found"
        run = conn.execute(
            """
            SELECT r.*, a.base_url, a.username FROM navidrome_playlist_backup_runs r
            LEFT JOIN player_accounts a ON a.id = r.player_account_id
            ORDER BY r.id DESC LIMIT 1
            """
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) AS count FROM navidrome_playlist_backups").fetchone()["count"]
    if not run:
        return 0, f"Navidrome playlist backup status\nPlaylists: {total}\nStatus: WARN\nNo backup runs found"
    lines = ["Navidrome playlist backup status", f"Server: {run['base_url'] or ''}", f"User: {run['username'] or ''}", f"Last backup: {run['finished_at'] or run['started_at']}", f"Playlists: {total}", f"Items: {run['total_items']}", f"Matched: {run['matched_items']}", f"Unmatched: {run['unmatched_items']}", f"Status: {run['status']}"]
    return 0, "\n".join(lines)


def playlists_export(config: dict[str, Any], *, output_format: str, output: Path) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, "Navidrome playlist backup export\nStatus: FAIL\nNo database found"
    with conn:
        if not _table_exists(conn, "navidrome_playlist_backups"):
            return 1, "Navidrome playlist backup export\nStatus: FAIL\nNo backups found"
        payload = _load_playlist_backup_export(conn)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n" if output_format == "json" else _playlist_backup_export_csv(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    item_count = sum(len(playlist["items"]) for playlist in payload["playlists"])
    return 0, f"Navidrome playlist backup export\nPlaylists: {len(payload['playlists'])}\nItems: {item_count}\nOutput: {output}\nStatus: OK"


def _playlists_push_query(config: dict[str, Any], query: str, *, name: str | None, playlist_id: str | None, apply: bool, replace: bool, append: bool, preserve_existing: bool, allow_medium_confidence: bool, force: bool, sort: str | None, reverse: bool, limit: int | None, path_mode: str, library_root: Path | None, output_format: str, output: Path | None, client: NavidromeClient | None, verbose: bool, debug: bool, diff_only: bool = False) -> tuple[int, str]:
    started = _now()
    mode = "READ-ONLY" if diff_only else "APPLY" if apply else "DRY-RUN"
    policy = _playlist_policy(replace=replace, append=append, preserve_existing=preserve_existing)
    if not name and not playlist_id:
        return 1, "Navidrome playlist push\nStatus: FAIL\nError: --name or --playlist-id is required"
    if sum(1 for value in (replace, append, preserve_existing) if value) > 1:
        return 1, "Navidrome playlist push\nStatus: FAIL\nError: choose only one of --replace, --append, or --preserve-existing"
    try:
        rows = _playlist_query_rows(config, query, sort=sort, reverse=reverse, limit=limit)
        nd_config = client.config if client else NavidromeConfig.from_config(config)
        active_client = client or NavidromeClient(nd_config)
        active_client.ping()
        playlists = _extract_playlists(active_client.get_playlists())
        existing = _find_playlist(playlists, name=name, playlist_id=playlist_id)
        existing_song_ids = _playlist_song_ids(active_client, existing["id"]) if existing else []
        resolved = resolve_navidrome_song_ids(config, rows, active_client, allow_medium_confidence=allow_medium_confidence)
        payload = _build_playlist_payload(nd_config, query, rows, playlists, existing, existing_song_ids, resolved, name=name, playlist_id=playlist_id, mode=mode, policy=policy, apply=apply, diff_only=diff_only, force=force)
        if payload["status"] not in {"FAIL", "REVIEW"} and apply and not diff_only:
            _apply_playlist_plan(active_client, payload)
            _save_playlist_push_run(config, nd_config, payload, started, "")
    except (NavidromeError, ValueError, sqlite3.DatabaseError) as exc:
        payload = _failed_playlist_payload(mode, name, playlist_id, query, _clean_error(str(exc)))
    text = _render_playlist_push_text(payload, diff_only=diff_only)
    code = 1 if payload["status"] in {"FAIL", "REVIEW"} else 0
    return code, _format_playlist_output(payload, output_format, output, text, "push")


def resolve_navidrome_song_ids(config: dict[str, Any], tracks: list[sqlite3.Row | dict[str, Any]], client: NavidromeClient, *, allow_medium_confidence: bool = False) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in tracks:
        track = dict(row)
        match = _match_track_to_navidrome(config, track, client)
        if match and match["confidence"] == "medium" and not allow_medium_confidence:
            unmatched.append(_playlist_unmatched(track, "medium confidence requires --allow-medium-confidence", match))
            continue
        if match and match["confidence"] == "low":
            unmatched.append(_playlist_unmatched(track, "low confidence match skipped", match))
            continue
        if match and match["song_id"] not in seen:
            seen.add(match["song_id"])
            matched.append({**_track_summary(track), **match})
            continue
        unmatched.append(_playlist_unmatched(track, "no navidrome match", match))
    return {"matched": matched, "unmatched": unmatched, "song_ids": [item["song_id"] for item in matched]}


def _match_track_to_navidrome(config: dict[str, Any], track: dict[str, Any], client: NavidromeClient) -> dict[str, Any] | None:
    backup = _backup_match_for_track(config, track)
    if backup:
        song = _safe_get_song_item(client, str(backup["navidrome_id"]))
        if song and _track_matches_song(track, song, allow_path=False):
            return {"song_id": song.navidrome_id, "confidence": "high", "method": "player_rating_backup", "reason": "saved navidrome_id with matching identity"}
    candidates = _search_track_candidates(track, client)
    for confidence in ("high", "medium"):
        for song in candidates:
            match = _song_match_reason(track, song)
            if match and match["confidence"] == confidence:
                return {"song_id": song.navidrome_id, **match}
    for song in candidates:
        if track.get("path") and song.path and normalize_path(str(track.get("path"))) == normalize_path(song.path):
            return {"song_id": song.navidrome_id, "confidence": "low", "method": "path", "reason": "path only"}
    return None


def _playlist_query_rows(config: dict[str, Any], query: str, *, sort: str | None, reverse: bool, limit: int | None) -> list[sqlite3.Row]:
    conn = connect_readonly(config)
    if conn is None:
        raise ValueError(f"Database not initialized: {database_path(config)}")
    try:
        plan = parse_query(query)
        rows = _hydrate_playlist_rows(conn, execute_query(conn, plan, "tracks", 100000))
    finally:
        conn.close()
    if sort:
        rows.sort(key=lambda row: _row_sort_key(row, sort), reverse=reverse)
    elif reverse:
        rows.reverse()
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be greater than zero")
        rows = rows[:limit]
    return rows


def _hydrate_playlist_rows(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    hydrated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        track_id = item.get("track_id") or item.get("id")
        if track_id is not None:
            extra = conn.execute("SELECT mb_track_id, mb_release_track_id, acoustid_id, isrc FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if extra:
                item.update(dict(extra))
        hydrated.append(item)
    return hydrated


def _backup_match_for_track(config: dict[str, Any], track: dict[str, Any]) -> dict[str, Any] | None:
    conn = connect_readonly(config)
    if conn is None:
        return None
    try:
        if not _table_exists(conn, "player_rating_backups"):
            return None
        row = conn.execute(
            """
            SELECT navidrome_id, identity_key, identity_method, match_confidence
            FROM player_rating_backups
            WHERE library_track_id = ? AND navidrome_id IS NOT NULL AND navidrome_id != ''
            ORDER BY CASE match_confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, updated_at DESC
            LIMIT 1
            """,
            (track.get("track_id") or track.get("id"),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _safe_get_song_item(client: NavidromeClient, song_id: str) -> RatingItem | None:
    try:
        payload = client.get_song(song_id)
    except (NavidromeError, AttributeError):
        return None
    song = payload.get("subsonic-response", {}).get("song") or payload.get("song")
    return normalize_song_payload(song) if isinstance(song, dict) else None


def _search_track_candidates(track: dict[str, Any], client: NavidromeClient) -> list[RatingItem]:
    terms = [str(track.get("title") or "")]
    if track.get("artist"):
        terms.append(str(track.get("artist")))
    try:
        payload = client.search3(" ".join(term for term in terms if term), song_count=25)
    except (NavidromeError, AttributeError):
        return []
    response = payload.get("subsonic-response", payload)
    result = response.get("searchResult3") if isinstance(response, dict) else {}
    songs = result.get("song") if isinstance(result, dict) else []
    if isinstance(songs, dict):
        songs = [songs]
    return [normalize_song_payload(song) for song in songs if isinstance(song, dict)] if isinstance(songs, list) else []


def _track_matches_song(track: dict[str, Any], song: RatingItem, *, allow_path: bool) -> bool:
    return _song_match_reason(track, song, allow_path=allow_path) is not None


def _song_match_reason(track: dict[str, Any], song: RatingItem, *, allow_path: bool = False) -> dict[str, str] | None:
    for field, value, song_value in (("mb_track_id", track.get("mb_track_id"), song.mb_track_id), ("mb_release_track_id", track.get("mb_release_track_id"), song.mb_release_track_id), ("acoustid_id", track.get("acoustid_id"), song.acoustid_id), ("isrc", track.get("isrc"), song.isrc)):
        if value and song_value and str(value).casefold() == str(song_value).casefold():
            return {"confidence": "high", "method": field, "reason": f"matched by {field}"}
    if track.get("artist") and track.get("title") and track.get("duration") is not None and song.artist and song.title and song.duration is not None:
        if _norm(str(track["artist"])) == _norm(song.artist) and _norm(str(track["title"])) == _norm(song.title) and abs(float(track["duration"]) - float(song.duration)) <= 2:
            return {"confidence": "medium", "method": "artist_title_duration", "reason": "matched by artist + title + duration"}
    if track.get("albumartist") and track.get("album") and track.get("track") and track.get("title") and song.albumartist and song.album and song.track and song.title:
        if _norm(str(track["albumartist"])) == _norm(song.albumartist) and _norm(str(track["album"])) == _norm(song.album) and int(track["track"]) == int(song.track) and _norm(str(track["title"])) == _norm(song.title):
            return {"confidence": "medium", "method": "album_track_title", "reason": "matched by album artist + album + track + title"}
    if allow_path and track.get("path") and song.path and normalize_path(str(track.get("path"))) == normalize_path(song.path):
        return {"confidence": "low", "method": "path", "reason": "matched by path only"}
    return None


def _build_playlist_payload(config: NavidromeConfig, query: str, rows: list[sqlite3.Row], playlists: list[dict[str, Any]], existing: dict[str, Any] | None, existing_song_ids: list[str], resolved: dict[str, Any], *, name: str | None, playlist_id: str | None, mode: str, policy: str, apply: bool, diff_only: bool, force: bool) -> dict[str, Any]:
    target_name = name or (existing.get("name") if existing else "")
    action = "update" if existing else "create"
    local_song_ids = resolved["song_ids"]
    if existing and policy == "none" and not diff_only:
        status = "REVIEW"
        final_song_ids = existing_song_ids
        reason = "existing playlist requires --replace, --append, or --preserve-existing"
    elif existing and policy in {"append", "preserve-existing"}:
        final_song_ids = list(dict.fromkeys([*existing_song_ids, *local_song_ids]))
        status = "WARN" if resolved["unmatched"] else "OK"
        reason = "append matched songs"
    elif existing:
        final_song_ids = local_song_ids
        status = "WARN" if resolved["unmatched"] else "OK"
        reason = "replace playlist contents"
    else:
        final_song_ids = local_song_ids
        status = "WARN" if resolved["unmatched"] else "OK"
        reason = "create playlist"
    existing_set = set(existing_song_ids)
    final_set = set(final_song_ids)
    add_ids = [song_id for song_id in final_song_ids if song_id not in existing_set]
    remove_ids = [song_id for song_id in existing_song_ids if song_id not in final_set]
    payload = {
        "status": status,
        "mode": mode,
        "server": config.base_url,
        "user": config.username,
        "query": query,
        "playlist": {"name": target_name, "id": existing.get("id") if existing else playlist_id, "action": action, "policy": policy},
        "summary": {"local_tracks": len(rows), "matched": len(resolved["matched"]), "unmatched": len(resolved["unmatched"]), "would_add": len(add_ids), "would_remove": len(remove_ids), "existing_tracks": len(existing_song_ids), "final_tracks": len(final_song_ids)},
        "matched": resolved["matched"][:100],
        "unmatched": resolved["unmatched"][:100],
        "add_song_ids": add_ids,
        "remove_song_ids": remove_ids,
        "final_song_ids": final_song_ids,
        "review_reason": reason if status == "REVIEW" else "",
        "plan_reason": reason,
    }
    return payload


def _apply_playlist_plan(client: NavidromeClient, payload: dict[str, Any]) -> None:
    playlist = payload["playlist"]
    song_ids = payload["final_song_ids"]
    if playlist["action"] == "create":
        client.create_playlist(playlist["name"], song_ids)
        payload["summary"]["created"] = 1
    else:
        client.update_playlist(str(playlist["id"]), song_ids, name=playlist.get("name") or None)
        payload["summary"]["updated"] = 1


def _save_playlist_push_run(config: dict[str, Any], nd_config: NavidromeConfig, payload: dict[str, Any], started: str, error: str) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        now = _now()
        conn.execute(
            """
            INSERT INTO player_accounts(player, name, base_url, username, server_id, created_at, updated_at)
            VALUES ('navidrome', 'Navidrome', ?, ?, ?, ?, ?)
            ON CONFLICT(player, base_url, username) DO UPDATE SET updated_at = excluded.updated_at, server_id = excluded.server_id
            """,
            (nd_config.base_url, nd_config.username, _server_id(nd_config.base_url), now, now),
        )
        account_id = int(conn.execute("SELECT id FROM player_accounts WHERE player = 'navidrome' AND base_url = ? AND username = ?", (nd_config.base_url, nd_config.username)).fetchone()["id"])
        summary = payload["summary"]
        conn.execute(
            """
            INSERT INTO navidrome_playlist_push_runs(player_account_id, playlist_id, playlist_name, mode, policy, status, started_at, finished_at, local_tracks, matched_tracks, unmatched_tracks, added_tracks, removed_tracks, error, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, payload["playlist"].get("id") or "", payload["playlist"].get("name") or "", str(payload["mode"]).lower(), payload["playlist"].get("policy") or "", payload["status"], started, now, summary["local_tracks"], summary["matched"], summary["unmatched"], summary["would_add"], summary["would_remove"], _clean_error(error), json.dumps(summary, sort_keys=True)),
        )
        conn.commit()


def _failed_playlist_payload(mode: str, name: str | None, playlist_id: str | None, query: str, error: str) -> dict[str, Any]:
    return {"status": "FAIL", "mode": mode, "query": query, "playlist": {"name": name or "", "id": playlist_id, "action": "unknown", "policy": "none"}, "summary": {"local_tracks": 0, "matched": 0, "unmatched": 0, "would_add": 0, "would_remove": 0, "existing_tracks": 0, "final_tracks": 0}, "matched": [], "unmatched": [], "add_song_ids": [], "remove_song_ids": [], "final_song_ids": [], "error": error}


def _extract_playlists(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("subsonic-response", payload)
    container = response.get("playlists") if isinstance(response, dict) else {}
    playlists = container.get("playlist") if isinstance(container, dict) else []
    if isinstance(playlists, dict):
        playlists = [playlists]
    rows = []
    for item in playlists if isinstance(playlists, list) else []:
        if not isinstance(item, dict):
            continue
        rows.append({"id": _text(item.get("id")), "name": _text(item.get("name")), "song_count": _int(item.get("songCount") if item.get("songCount") is not None else item.get("entryCount")) or 0, "owner": _text(item.get("owner"))})
    return rows


def _fetch_playlist_backups(client: Any, *, playlist_id: str | None = None, name: str | None = None) -> list[PlaylistBackup]:
    playlists = _extract_playlist_summaries(client.get_playlists())
    if playlist_id:
        playlists = [playlist for playlist in playlists if playlist.navidrome_playlist_id == playlist_id]
    if name:
        playlists = [playlist for playlist in playlists if playlist.name.casefold() == name.casefold()]
    backups: list[PlaylistBackup] = []
    for playlist in playlists:
        detail = client.get_playlist(playlist.navidrome_playlist_id)
        detailed = normalize_playlist_payload(detail, fallback=playlist)
        items = []
        for position, song in enumerate(_playlist_entry_payloads(detail), start=1):
            item = normalize_song_payload(song)
            if item.navidrome_id and not item.title and hasattr(client, "get_song"):
                try:
                    song_detail = client.get_song(item.navidrome_id)
                    detail_song = song_detail.get("subsonic-response", {}).get("song") or song_detail.get("song")
                    if isinstance(detail_song, dict):
                        item = normalize_song_payload({**song, **detail_song})
                except NavidromeError:
                    pass
            items.append(PlaylistItem(position=position, song=item))
        detailed.items = items
        backups.append(detailed)
    return backups


def normalize_playlist_payload(payload: dict[str, Any], *, fallback: PlaylistBackup | None = None) -> PlaylistBackup:
    response = payload.get("subsonic-response", payload)
    source = response.get("playlist") if isinstance(response, dict) else {}
    if not isinstance(source, dict):
        source = {}
    raw = {key: source.get(key) for key in ("id", "name", "owner", "comment", "songCount", "duration", "public", "changed", "created") if key in source}
    return PlaylistBackup(
        navidrome_playlist_id=_text(source.get("id") or (fallback.navidrome_playlist_id if fallback else "")),
        name=_text(source.get("name") or (fallback.name if fallback else "")),
        owner=_text(source.get("owner") or (fallback.owner if fallback else "")),
        comment=_text(source.get("comment") or (fallback.comment if fallback else "")),
        song_count=_int(source.get("songCount") if source.get("songCount") is not None else source.get("song_count") if source.get("song_count") is not None else (fallback.song_count if fallback else None)),
        duration=_float(source.get("duration") if source.get("duration") is not None else (fallback.duration if fallback else None)),
        public=_bool_or_none(source.get("public") if source.get("public") is not None else (fallback.public if fallback else None)),
        changed_at=_text(source.get("changed") or source.get("changedAt") or (fallback.changed_at if fallback else "")),
        created_at_remote=_text(source.get("created") or source.get("createdAt") or (fallback.created_at_remote if fallback else "")),
        raw_summary=raw or (fallback.raw_summary if fallback else {}),
        items=[],
    )


def _extract_playlist_summaries(payload: dict[str, Any]) -> list[PlaylistBackup]:
    return [normalize_playlist_payload({"subsonic-response": {"playlist": row}}) for row in _extract_playlists(payload)]


def _playlist_entry_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("subsonic-response", payload)
    playlist = response.get("playlist") if isinstance(response, dict) else {}
    entries = playlist.get("entry") if isinstance(playlist, dict) else []
    if isinstance(entries, dict):
        return [entries]
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _match_playlist_items(config: dict[str, Any], playlists: list[PlaylistBackup]) -> dict[tuple[str, int], dict[str, Any]]:
    conn = connect_readonly(config)
    if conn is None:
        return {(playlist.navidrome_playlist_id, item.position): _unmatched(item.song) for playlist in playlists for item in (playlist.items or [])}
    matches: dict[tuple[str, int], dict[str, Any]] = {}
    with conn:
        if not _table_exists(conn, "tracks"):
            return {(playlist.navidrome_playlist_id, item.position): _unmatched(item.song) for playlist in playlists for item in (playlist.items or [])}
        for playlist in playlists:
            for item in playlist.items or []:
                match = _match_item(conn, item.song)
                if match.get("match_confidence") == "none":
                    match = _match_playlist_item_by_rating_backup(conn, item.song) or match
                matches[(playlist.navidrome_playlist_id, item.position)] = match
    return matches


def _match_playlist_item_by_rating_backup(conn: sqlite3.Connection, item: RatingItem) -> dict[str, Any] | None:
    if not _table_exists(conn, "player_rating_backups"):
        return None
    identity_key, _method, _confidence = build_player_track_identity(item)
    if item.navidrome_id and identity_key and not identity_key.startswith("navidrome:"):
        row = conn.execute(
            """
            SELECT library_track_id, library_file_id FROM player_rating_backups
            WHERE navidrome_id = ? AND identity_key = ? AND library_track_id IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (item.navidrome_id, identity_key),
        ).fetchone()
        if row:
            return {"library_track_id": row["library_track_id"], "library_file_id": row["library_file_id"], "match_confidence": "medium", "match_reason": "rating_backup_navidrome_id_identity"}
    return None


def _playlist_backup_payload(config: NavidromeConfig, mode: str, playlists: list[PlaylistBackup], matches: dict[tuple[str, int], dict[str, Any]], status: str, error: str) -> dict[str, Any]:
    total_items = sum(len(playlist.items or []) for playlist in playlists)
    matched = sum(1 for match in matches.values() if match.get("match_confidence") in {"high", "medium", "low"})
    payload_playlists = []
    for playlist in playlists:
        payload_items = []
        for item in playlist.items or []:
            identity_key, identity_method, identity_confidence = build_player_track_identity(item.song)
            match = matches.get((playlist.navidrome_playlist_id, item.position), _unmatched(item.song))
            payload_items.append({"position": item.position, "navidrome_song_id": item.song.navidrome_id, "title": item.song.title, "artist": item.song.artist, "album": item.song.album, "albumartist": item.song.albumartist, "duration": item.song.duration, "path": item.song.path, "identity_key": identity_key, "identity_method": identity_method, "identity_confidence": identity_confidence, "match_confidence": match.get("match_confidence"), "match_reason": match.get("match_reason"), "library_track_id": match.get("library_track_id"), "library_file_id": match.get("library_file_id")})
        payload_playlists.append({"id": playlist.navidrome_playlist_id, "name": playlist.name, "owner": playlist.owner, "comment": playlist.comment, "song_count": playlist.song_count if playlist.song_count is not None else len(payload_items), "duration": playlist.duration, "public": playlist.public, "changed_at": playlist.changed_at, "created_at_remote": playlist.created_at_remote, "items": payload_items})
    payload = {"status": status, "mode": mode, "server": config.base_url, "user": config.username, "summary": {"total_playlists": len(playlists), "total_items": total_items, "matched_items": matched, "unmatched_items": total_items - matched, "saved_playlists": len(playlists) if mode == "APPLY" and status != "FAIL" else 0, "saved_items": total_items if mode == "APPLY" and status != "FAIL" else 0}, "playlists": payload_playlists}
    if error:
        payload["error"] = _clean_error(error)
    return payload


def _save_playlist_backup(config: dict[str, Any], nd_config: NavidromeConfig, playlists: list[PlaylistBackup], matches: dict[tuple[str, int], dict[str, Any]], payload: dict[str, Any], started: str) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        now = _now()
        account_id = _ensure_player_account(conn, nd_config, now)
        for playlist in playlists:
            row_id = conn.execute(
                """
                INSERT INTO navidrome_playlist_backups(player_account_id, navidrome_playlist_id, name, owner, comment, song_count, duration, public, changed_at, created_at_remote, backed_up_at, updated_at, raw_summary_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_account_id, navidrome_playlist_id) DO UPDATE SET name = excluded.name, owner = excluded.owner, comment = excluded.comment, song_count = excluded.song_count, duration = excluded.duration, public = excluded.public, changed_at = excluded.changed_at, created_at_remote = excluded.created_at_remote, backed_up_at = excluded.backed_up_at, updated_at = excluded.updated_at, raw_summary_json = excluded.raw_summary_json
                RETURNING id
                """,
                (account_id, playlist.navidrome_playlist_id, playlist.name, playlist.owner, playlist.comment, playlist.song_count if playlist.song_count is not None else len(playlist.items or []), playlist.duration, None if playlist.public is None else 1 if playlist.public else 0, playlist.changed_at, playlist.created_at_remote, now, now, json.dumps(playlist.raw_summary or {}, sort_keys=True)),
            ).fetchone()
            playlist_backup_id = int(row_id["id"])
            conn.execute("DELETE FROM navidrome_playlist_items WHERE playlist_backup_id = ?", (playlist_backup_id,))
            for item in playlist.items or []:
                identity_key, identity_method, identity_confidence = build_player_track_identity(item.song)
                match = matches.get((playlist.navidrome_playlist_id, item.position), _unmatched(item.song))
                conn.execute(
                    """
                    INSERT INTO navidrome_playlist_items(playlist_backup_id, position, navidrome_song_id, library_track_id, library_file_id, identity_key, identity_method, identity_confidence, match_confidence, match_reason, title, artist, album, albumartist, duration, path, raw_summary_json, backed_up_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (playlist_backup_id, item.position, item.song.navidrome_id, match.get("library_track_id"), match.get("library_file_id"), identity_key, identity_method, identity_confidence, match.get("match_confidence"), match.get("match_reason"), item.song.title, item.song.artist, item.song.album, item.song.albumartist, item.song.duration, item.song.path, json.dumps(item.song.raw_summary or {}, sort_keys=True), now),
                )
        summary = payload["summary"]
        conn.execute(
            """
            INSERT INTO navidrome_playlist_backup_runs(player_account_id, mode, status, started_at, finished_at, total_playlists, total_items, matched_items, unmatched_items, error, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
            """,
            (account_id, str(payload["mode"]).lower(), payload["status"], started, now, summary["total_playlists"], summary["total_items"], summary["matched_items"], summary["unmatched_items"], json.dumps(summary, sort_keys=True)),
        )
        conn.commit()


def _ensure_player_account(conn: sqlite3.Connection, nd_config: NavidromeConfig, now: str) -> int:
    conn.execute(
        """
        INSERT INTO player_accounts(player, name, base_url, username, server_id, created_at, updated_at)
        VALUES ('navidrome', 'Navidrome', ?, ?, ?, ?, ?)
        ON CONFLICT(player, base_url, username) DO UPDATE SET updated_at = excluded.updated_at, server_id = excluded.server_id
        """,
        (nd_config.base_url, nd_config.username, _server_id(nd_config.base_url), now, now),
    )
    return int(conn.execute("SELECT id FROM player_accounts WHERE player = 'navidrome' AND base_url = ? AND username = ?", (nd_config.base_url, nd_config.username)).fetchone()["id"])


def _render_playlist_backup_text(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    mode = payload["mode"]
    connect_status = "FAIL" if payload.get("error") and not payload.get("playlists") else "OK"
    fetch_status = "FAIL" if payload["status"] == "FAIL" else "OK"
    match_status = "WARN" if summary["unmatched_items"] else "OK"
    save_status = "DRY" if mode == "DRY-RUN" else "OK" if payload["status"] != "FAIL" else "FAIL"
    lines = ["Navidrome playlist backup", f"Server: {payload.get('server') or ''}", f"User: {payload.get('user') or ''}", f"Mode: {mode}", "", f"[1/5] Connect            {connect_status:<6} " + (payload.get("error", "server reachable") if connect_status == "FAIL" else "server reachable"), f"[2/5] Fetch playlists    {fetch_status:<6} {summary['total_playlists']} playlists", f"[3/5] Fetch entries      {fetch_status:<6} {summary['total_items']} items", f"[4/5] Match library      {match_status:<6} matched {summary['matched_items']}/{summary['total_items']}", f"[5/5] Save backup        {save_status:<6} " + (f"would save {summary['total_playlists']} playlists" if mode == "DRY-RUN" else f"saved {summary['saved_playlists']} playlists"), "", "Final:"]
    if mode == "APPLY":
        lines.extend([f"Saved playlists: {summary['saved_playlists']}", f"Saved items: {summary['saved_items']}"])
    else:
        lines.extend([f"Playlists: {summary['total_playlists']}", f"Items: {summary['total_items']}"])
    lines.extend([f"Matched: {summary['matched_items']}", f"Unmatched: {summary['unmatched_items']}", f"Status: {payload['status']}"])
    return "\n".join(lines)


def _format_playlist_backup_result(payload: dict[str, Any], output_format: str, output: Path | None, text: str) -> str:
    if output_format == "json":
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    elif output_format == "csv":
        rendered = _playlist_backup_payload_csv(payload)
    else:
        rendered = text
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        return text + f"\nOutput: {output}"
    return rendered.rstrip("\n")


def _playlist_backup_payload_csv(payload: dict[str, Any]) -> str:
    columns = ["playlist_name", "playlist_id", "position", "title", "artist", "album", "albumartist", "duration", "navidrome_song_id", "identity_key", "identity_method", "match_confidence", "path"]
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    for playlist in payload.get("playlists", []):
        for item in playlist.get("items", []):
            writer.writerow({"playlist_name": playlist.get("name", ""), "playlist_id": playlist.get("id", ""), **{column: item.get(column, "") for column in columns if column not in {"playlist_name", "playlist_id"}}})
    return handle.getvalue()


def _load_playlist_backup_export(conn: sqlite3.Connection) -> dict[str, Any]:
    playlists = []
    for playlist in conn.execute("SELECT * FROM navidrome_playlist_backups ORDER BY name, navidrome_playlist_id"):
        items = [dict(row) for row in conn.execute("SELECT position, navidrome_song_id, library_track_id, library_file_id, identity_key, identity_method, identity_confidence, match_confidence, match_reason, title, artist, album, albumartist, duration, path FROM navidrome_playlist_items WHERE playlist_backup_id = ? ORDER BY position", (playlist["id"],))]
        playlists.append({"id": playlist["navidrome_playlist_id"], "name": playlist["name"], "owner": playlist["owner"], "comment": playlist["comment"], "song_count": playlist["song_count"], "duration": playlist["duration"], "public": None if playlist["public"] is None else bool(playlist["public"]), "changed_at": playlist["changed_at"], "created_at_remote": playlist["created_at_remote"], "backed_up_at": playlist["backed_up_at"], "items": items})
    return {"playlists": playlists}


def _playlist_backup_export_csv(payload: dict[str, Any]) -> str:
    return _playlist_backup_payload_csv({"playlists": payload.get("playlists", [])})


def _playlist_song_ids(client: NavidromeClient, playlist_id: str) -> list[str]:
    payload = client.get_playlist(playlist_id)
    response = payload.get("subsonic-response", payload)
    playlist = response.get("playlist") if isinstance(response, dict) else {}
    entries = playlist.get("entry") if isinstance(playlist, dict) else []
    if isinstance(entries, dict):
        entries = [entries]
    return [_text(entry.get("id")) for entry in entries if isinstance(entry, dict) and _text(entry.get("id"))]


def _find_playlist(playlists: list[dict[str, Any]], *, name: str | None, playlist_id: str | None) -> dict[str, Any] | None:
    if playlist_id:
        return next((item for item in playlists if item.get("id") == playlist_id), {"id": playlist_id, "name": name or "", "song_count": 0, "owner": ""})
    if name:
        return next((item for item in playlists if str(item.get("name") or "").casefold() == name.casefold()), None)
    return None


def _load_smart_playlist(config: dict[str, Any], name: str) -> dict[str, Any]:
    conn = connect_readonly(config)
    if conn is None:
        raise ValueError(f"Database not initialized: {database_path(config)}")
    try:
        row = conn.execute("SELECT * FROM smart_playlists WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"Smart playlist not found: {name}")
        return dict(row)
    finally:
        conn.close()


def _playlist_policy(*, replace: bool, append: bool, preserve_existing: bool) -> str:
    if replace:
        return "replace"
    if append:
        return "append"
    if preserve_existing:
        return "preserve-existing"
    return "none"


def _track_summary(track: dict[str, Any]) -> dict[str, Any]:
    return {"library_track_id": track.get("track_id") or track.get("id"), "title": track.get("title") or "", "artist": track.get("artist") or "", "album": track.get("album") or "", "path": track.get("path") or ""}


def _playlist_unmatched(track: dict[str, Any], reason: str, match: dict[str, Any] | None) -> dict[str, Any]:
    return {**_track_summary(track), "reason": reason, "match_confidence": match.get("confidence") if match else "none", "match_method": match.get("method") if match else ""}


def _row_sort_key(row: sqlite3.Row, field: str) -> Any:
    key = field.strip().casefold().replace("-", "_")
    try:
        value = row[key]
    except (KeyError, IndexError):
        raise ValueError(f"Unsupported sort field: {field}") from None
    return "" if value is None else str(value).casefold() if isinstance(value, str) else value


def _render_playlists_list_text(payload: dict[str, Any]) -> str:
    lines = ["Navidrome playlists", f"Server: {payload.get('server') or ''}", f"User: {payload.get('user') or ''}", "Mode: READ-ONLY", "", f"Playlists: {payload['count']}"]
    for item in payload["playlists"]:
        owner = f" owner={item['owner']}" if item.get("owner") else ""
        lines.append(f"- {item['id']} | {item['name']} | {item['song_count']} songs{owner}")
    lines.append("Status: OK")
    return "\n".join(lines)


def _render_playlist_push_text(payload: dict[str, Any], *, diff_only: bool) -> str:
    summary = payload["summary"]
    title = "Navidrome playlist diff" if diff_only else "Navidrome playlist push"
    playlist = payload["playlist"]
    lines = [title, f"Playlist: {playlist.get('name') or playlist.get('id') or ''}", f"Mode: {payload['mode']}", "", f"[1/5] Query library       {'OK' if payload['status'] != 'FAIL' else 'FAIL':<6} {summary['local_tracks']} tracks", f"[2/5] Fetch playlists     {'OK' if payload['status'] != 'FAIL' else 'FAIL':<6} existing={summary['existing_tracks']}", f"[3/5] Resolve songs       {'WARN' if summary['unmatched'] else 'OK':<6} {summary['matched']} matched, {summary['unmatched']} unmatched", f"[4/5] Build plan          {payload['status'] if payload['status'] == 'REVIEW' else 'OK':<6} {playlist.get('action')} playlist, policy={playlist.get('policy')}"]
    if diff_only:
        lines.append(f"[5/5] Compare             {payload['status']:<6} add {summary['would_add']}, remove {summary['would_remove']}")
    else:
        step_status = "DRY" if payload["mode"] == "DRY-RUN" else "OK" if payload["status"] not in {"FAIL", "REVIEW"} else payload["status"]
        action_text = f"would {playlist.get('action')} playlist" if payload["mode"] == "DRY-RUN" else f"{playlist.get('action')} playlist"
        lines.append(f"[5/5] Push playlist       {step_status:<6} {action_text}")
    lines.extend(["", "Final:" if payload["mode"] == "APPLY" else "Plan:", f"Action: {playlist.get('action')}", f"Tracks: {summary['final_tracks']}", f"Would add: {summary['would_add']}", f"Would remove: {summary['would_remove']}", f"Unmatched: {summary['unmatched']}"])
    if payload.get("review_reason"):
        lines.append(f"Review: {payload['review_reason']}")
    if payload.get("error"):
        lines.append(f"Error: {payload['error']}")
    lines.append(f"Status: {payload['status']}")
    return "\n".join(lines)


def _format_playlist_output(payload: dict[str, Any], output_format: str, output: Path | None, text: str, kind: str) -> str:
    if output_format == "json":
        rendered = json.dumps(_public_playlist_payload(payload), indent=2, sort_keys=True) + "\n"
    elif output_format == "csv":
        rendered = _playlist_csv(payload, kind)
    else:
        rendered = text
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        return text + f"\nOutput: {output}"
    return rendered.rstrip("\n")


def _public_playlist_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {key: payload[key] for key in ("status", "mode", "playlist", "summary", "unmatched") if key in payload}
    if "playlists" in payload:
        allowed["playlists"] = payload["playlists"]
        allowed["count"] = payload.get("count", len(payload["playlists"]))
    if payload.get("error"):
        allowed["error"] = payload["error"]
    return allowed


def _playlist_csv(payload: dict[str, Any], kind: str) -> str:
    handle = io.StringIO()
    if kind == "list":
        columns = ["id", "name", "song_count", "owner"]
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows({column: item.get(column, "") for column in columns} for item in payload.get("playlists", []))
        return handle.getvalue()
    columns = ["type", "title", "artist", "album", "song_id", "confidence", "method", "reason"]
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    for item in payload.get("matched", []):
        writer.writerow({"type": "matched", "title": item.get("title", ""), "artist": item.get("artist", ""), "album": item.get("album", ""), "song_id": item.get("song_id", ""), "confidence": item.get("confidence", ""), "method": item.get("method", ""), "reason": item.get("reason", "")})
    for item in payload.get("unmatched", []):
        writer.writerow({"type": "unmatched", "title": item.get("title", ""), "artist": item.get("artist", ""), "album": item.get("album", ""), "song_id": "", "confidence": item.get("match_confidence", ""), "method": item.get("match_method", ""), "reason": item.get("reason", "")})
    return handle.getvalue()


def ratings_backup(config: dict[str, Any], *, apply: bool = False, output: Path | None = None, output_format: str = "text", client: NavidromeClient | None = None) -> tuple[int, str]:
    started = _now()
    nd_config = client.config if client else NavidromeConfig.from_config(config)
    mode = "APPLY" if apply else "DRY-RUN"
    try:
        active_client = client or NavidromeClient(nd_config)
        active_client.ping()
        items = active_client.iter_rating_items()
    except NavidromeError as exc:
        return 1, _render_backup(nd_config, mode, [], BackupSummary("FAIL", 0, 0, 0, 0, 0, 0, mode), error=str(exc))
    matches = _match_items(config, items)
    matched = sum(1 for match in matches.values() if match["match_confidence"] in {"high", "medium", "low"})
    summary = BackupSummary(
        status="WARN" if items and matched < len(items) else "OK",
        total_items=len(items),
        matched_items=matched,
        unmatched_items=len(items) - matched,
        rated_items=sum(1 for item in items if item.rating is not None),
        starred_items=sum(1 for item in items if item.starred),
        saved_items=len(items) if apply else 0,
        mode=mode,
    )
    if apply:
        _save_backup(config, nd_config, items, matches, summary, started)
    rendered = _render_backup(nd_config, mode, items, summary)
    if output:
        _write_items(output, output_format, _export_rows_from_items(items, matches), force=True)
        rendered += f"\nOutput: {output}"
    return 0, rendered


def ratings_status(config: dict[str, Any]) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 0, "Navidrome ratings status\nStatus: WARN\nNo database found"
    with conn:
        if not _table_exists(conn, "player_rating_backups"):
            return 0, "Navidrome ratings status\nStatus: WARN\nNo backups found"
        run = conn.execute(
            """
            SELECT r.*, a.base_url, a.username FROM player_rating_backup_runs r
            LEFT JOIN player_accounts a ON a.id = r.player_account_id
            ORDER BY r.id DESC LIMIT 1
            """
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) AS count FROM player_rating_backups WHERE player = 'navidrome'").fetchone()["count"]
    if not run:
        return 0, f"Navidrome ratings status\nBackups: {total}\nStatus: WARN\nNo backup runs found"
    lines = ["Navidrome ratings status", f"Server: {run['base_url'] or ''}", f"User: {run['username'] or ''}", f"Last backup: {run['finished_at'] or run['started_at']}", f"Backups: {total}", f"Matched: {run['matched_items']}", f"Unmatched: {run['unmatched_items']}", f"Rated: {run['rated_items']}", f"Starred: {run['starred_items']}", f"Status: {run['status']}"]
    return 0, "\n".join(lines)


def ratings_diff(
    config: dict[str, Any],
    *,
    server: bool = False,
    backup_only: bool = False,
    output_format: str = "text",
    output: Path | None = None,
    verbose: bool = False,
    debug: bool = False,
    client: NavidromeClient | None = None,
) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 0, _format_diff_result({"status": "WARN", "server": _safe_server(config), "user": _safe_user(config), "backup_run_id": None, "summary": _empty_diff_summary(), "items": []}, output_format, output, "Navidrome ratings diff\nStatus: WARN\nNo database found")
    fetch_server = bool(server and not backup_only)
    nd_config = client.config if client else NavidromeConfig.from_config(config)
    with conn:
        if not _table_exists(conn, "player_rating_backups"):
            return 0, _format_diff_result({"status": "WARN", "server": nd_config.base_url, "user": nd_config.username, "backup_run_id": None, "summary": _empty_diff_summary(), "items": []}, output_format, output, "Navidrome ratings diff\nStatus: WARN\nNo backups found")
        run = conn.execute(
            """
            SELECT r.id, r.player_account_id, a.base_url, a.username
            FROM player_rating_backup_runs r
            LEFT JOIN player_accounts a ON a.id = r.player_account_id
            WHERE a.player = 'navidrome'
            ORDER BY r.id DESC LIMIT 1
            """
        ).fetchone()
        if not run:
            return 0, _format_diff_result({"status": "WARN", "server": nd_config.base_url, "user": nd_config.username, "backup_run_id": None, "summary": _empty_diff_summary(), "items": []}, output_format, output, "Navidrome ratings diff\nStatus: WARN\nNo backup runs found")
        backups = [dict(row) for row in conn.execute("SELECT * FROM player_rating_backups WHERE player_account_id = ? AND player = 'navidrome' ORDER BY artist, title", (run["player_account_id"],))]
        library = _load_library_rating_rows(conn)
    library_matches = _match_backups_to_library(backups, library)
    server_items: list[RatingItem] = []
    server_error = ""
    if fetch_server:
        try:
            active_client = client or NavidromeClient(nd_config)
            active_client.ping()
            server_items = active_client.iter_rating_items()
        except NavidromeError as exc:
            server_error = _clean_error(str(exc))
    payload = _build_diff_payload(
        backups,
        library,
        library_matches,
        server_items,
        backup_run_id=int(run["id"]),
        server=str(run["base_url"] or nd_config.base_url),
        user=str(run["username"] or nd_config.username),
        include_server=fetch_server,
        server_error=server_error,
        verbose=verbose,
        debug=debug,
    )
    text = _render_diff_text(payload, len(backups), len(library), fetch_server, server_error)
    return 0, _format_diff_result(payload, output_format, output, text)


def ratings_export(config: dict[str, Any], *, output_format: str, output: Path) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, "Navidrome ratings export\nStatus: FAIL\nNo database found"
    with conn:
        if not _table_exists(conn, "player_rating_backups"):
            return 1, "Navidrome ratings export\nStatus: FAIL\nNo backups found"
        rows = [dict(row) for row in conn.execute("SELECT identity_key, identity_method, title, artist, album, rating, starred, play_count, last_played, match_confidence FROM player_rating_backups WHERE player = 'navidrome' ORDER BY artist, title")]
    _write_items(output, output_format, rows, force=True)
    return 0, f"Navidrome ratings export\nRows: {len(rows)}\nOutput: {output}\nStatus: OK"


def ratings_restore(
    config: dict[str, Any],
    *,
    apply: bool = False,
    restore_ratings: bool = True,
    restore_starred: bool = True,
    only_matched: bool = False,
    allow_medium_confidence: bool = False,
    force: bool = False,
    preserve_server: bool = False,
    output_format: str = "text",
    output: Path | None = None,
    verbose: bool = False,
    debug: bool = False,
    client: NavidromeClient | None = None,
) -> tuple[int, str]:
    started = _now()
    mode = "APPLY" if apply else "DRY-RUN"
    nd_config = client.config if client else NavidromeConfig.from_config(config)
    conn = connect_readonly(config)
    if conn is None:
        payload = _empty_restore_payload(nd_config, mode, "WARN", "No database found")
        return 0, _format_restore_result(payload, output_format, output, _render_restore_text(payload))
    with conn:
        if not _table_exists(conn, "player_rating_backups"):
            payload = _empty_restore_payload(nd_config, mode, "WARN", "No backups found")
            return 0, _format_restore_result(payload, output_format, output, _render_restore_text(payload))
        run = conn.execute(
            """
            SELECT r.id, r.player_account_id, a.base_url, a.username
            FROM player_rating_backup_runs r
            LEFT JOIN player_accounts a ON a.id = r.player_account_id
            WHERE a.player = 'navidrome'
            ORDER BY r.id DESC LIMIT 1
            """
        ).fetchone()
        if not run:
            payload = _empty_restore_payload(nd_config, mode, "WARN", "No backup runs found")
            return 0, _format_restore_result(payload, output_format, output, _render_restore_text(payload))
        backups = [dict(row) for row in conn.execute("SELECT * FROM player_rating_backups WHERE player_account_id = ? AND player = 'navidrome' ORDER BY artist, title", (run["player_account_id"],))]
    try:
        active_client = client or NavidromeClient(nd_config)
        active_client.ping()
        server_items = active_client.iter_rating_items()
    except NavidromeError as exc:
        payload = _empty_restore_payload(nd_config, mode, "FAIL", _clean_error(str(exc)))
        return 1, _format_restore_result(payload, output_format, output, _render_restore_text(payload))
    plan = _build_restore_plan(
        backups,
        server_items,
        restore_ratings=restore_ratings,
        restore_starred=restore_starred,
        allow_medium_confidence=allow_medium_confidence,
        force=force,
        preserve_server=preserve_server,
        only_matched=only_matched,
    )
    error = ""
    if apply:
        for item in plan["items"]:
            if item["status"] != "planned":
                continue
            try:
                if item["action"] == "set_rating":
                    active_client.set_rating(item["navidrome_id"], int(float(item["new_value"])))
                    item["status"] = "applied"
                    item["applied_at"] = _now()
                elif item["action"] == "star":
                    active_client.star(item["navidrome_id"])
                    item["status"] = "applied"
                    item["applied_at"] = _now()
                elif item["action"] == "unstar":
                    active_client.unstar(item["navidrome_id"])
                    item["status"] = "applied"
                    item["applied_at"] = _now()
            except NavidromeError as exc:
                item["status"] = "failed"
                item["reason"] = _clean_error(str(exc))
                error = item["reason"]
                break
    payload = _restore_payload(plan, nd_config, mode, int(run["id"]), error=error, verbose=verbose, debug=debug)
    if apply:
        _save_restore_run(config, int(run["player_account_id"]), payload, started, error)
    return (1 if payload["status"] == "FAIL" else 0), _format_restore_result(payload, output_format, output, _render_restore_text(payload))


def _empty_restore_payload(config: NavidromeConfig, mode: str, status: str, message: str) -> dict[str, Any]:
    return {
        "status": status,
        "mode": mode,
        "server": config.base_url,
        "user": config.username,
        "backup_run_id": None,
        "summary": {"would_set_ratings": 0, "would_star": 0, "would_unstar": 0, "applied_rating_updates": 0, "applied_star_updates": 0, "skipped": 0, "conflicts": 0},
        "items": [],
        "message": message,
    }


def _build_restore_plan(
    backups: list[dict[str, Any]],
    server_items: list[RatingItem],
    *,
    restore_ratings: bool,
    restore_starred: bool,
    allow_medium_confidence: bool,
    force: bool,
    preserve_server: bool,
    only_matched: bool,
) -> dict[str, Any]:
    server_index = _server_restore_index(server_items)
    items: list[dict[str, Any]] = []
    for backup in backups:
        match = _match_backup_to_server(backup, server_index)
        if match is None:
            if only_matched or backup.get("rating") is not None or backup.get("starred") is not None:
                items.append(_restore_skip_item(backup, "skip", "none", "no safe current server identity match"))
            continue
        server_item, confidence, reason = match
        allowed = confidence == "high" or (confidence == "medium" and allow_medium_confidence) or (confidence == "low" and force)
        if not allowed:
            items.append(_restore_skip_item(backup, "review", confidence, f"{confidence} confidence requires explicit restore policy", server_item=server_item, reason=reason))
            continue
        if restore_ratings and backup.get("rating") is not None:
            old_rating = _rating_value(server_item.rating)
            new_rating = _rating_value(backup.get("rating"))
            if old_rating != new_rating:
                conflict = old_rating is not None
                if preserve_server and conflict:
                    items.append(_restore_skip_item(backup, "skip", confidence, "server rating preserved", server_item=server_item, reason=reason, action="set_rating", old_value=old_rating, new_value=new_rating))
                else:
                    items.append(_restore_action_item(backup, server_item, "set_rating", old_rating, new_rating, confidence, reason, conflict))
        if restore_starred and backup.get("starred") is not None:
            old_starred = bool(server_item.starred)
            new_starred = bool(int(backup.get("starred") or 0))
            if old_starred != new_starred:
                action = "star" if new_starred else "unstar"
                conflict = old_starred is True and new_starred is False
                if preserve_server and conflict:
                    items.append(_restore_skip_item(backup, "skip", confidence, "server favorite preserved", server_item=server_item, reason=reason, action=action, old_value=old_starred, new_value=new_starred))
                else:
                    items.append(_restore_action_item(backup, server_item, action, old_starred, new_starred, confidence, reason, conflict))
    return {"items": items, "total_items": len(backups)}


def _server_restore_index(items: list[RatingItem]) -> dict[str, Any]:
    by_nav: dict[str, RatingItem] = {}
    by_key: dict[str, RatingItem] = {}
    for item in items:
        if item.navidrome_id:
            by_nav[item.navidrome_id] = item
        for key, _method, _confidence in _rating_item_identity_keys(item):
            by_key.setdefault(key, item)
    return {"by_nav": by_nav, "by_key": by_key}


def _match_backup_to_server(backup: dict[str, Any], index: dict[str, Any]) -> tuple[RatingItem, str, str] | None:
    backup_keys = [(str(backup.get("identity_key") or ""), str(backup.get("identity_method") or ""), str(backup.get("identity_confidence") or backup.get("match_confidence") or "low"))]
    backup_keys = [key for key in backup_keys if key[0] and not key[0].startswith("navidrome:") and key[0] != "unknown"]
    navidrome_id = str(backup.get("navidrome_id") or "")
    if navidrome_id and navidrome_id in index["by_nav"]:
        item = index["by_nav"][navidrome_id]
        server_keys = {key for key, _method, _confidence in _rating_item_identity_keys(item)}
        for key, method, confidence in backup_keys:
            if key in server_keys:
                return item, _confidence_rank(confidence), f"matched by navidrome_id and {method}"
        return item, "low", "matched by navidrome_id only"
    for key, method, confidence in backup_keys:
        item = index["by_key"].get(key)
        if item is not None:
            return item, _confidence_rank(confidence), f"matched by {method}"
    return None


def _rating_item_identity_keys(item: RatingItem) -> list[tuple[str, str, str]]:
    keys = []
    identity_key, identity_method, confidence = build_player_track_identity(item)
    if identity_key and not identity_key.startswith("navidrome:") and identity_key != "unknown":
        keys.append((identity_key, identity_method, confidence))
    return keys


def _confidence_rank(value: str) -> str:
    return value if value in {"high", "medium", "low"} else "low"


def _restore_action_item(backup: dict[str, Any], server_item: RatingItem, action: str, old_value: Any, new_value: Any, confidence: str, reason: str, conflict: bool) -> dict[str, Any]:
    return {
        "backup_id": backup.get("id"),
        "action": action,
        "title": backup.get("title") or server_item.title,
        "artist": backup.get("artist") or server_item.artist,
        "album": backup.get("album") or server_item.album,
        "backup_rating": backup.get("rating"),
        "server_rating": server_item.rating,
        "backup_starred": bool(int(backup.get("starred") or 0)),
        "server_starred": bool(server_item.starred),
        "navidrome_id": server_item.navidrome_id,
        "old_value": old_value,
        "new_value": new_value,
        "match_confidence": confidence,
        "status": "planned",
        "reason": ("conflict; " if conflict else "") + reason,
        "conflict": conflict,
    }


def _restore_skip_item(backup: dict[str, Any], status: str, confidence: str, message: str, *, server_item: RatingItem | None = None, reason: str = "", action: str = "skip", old_value: Any = None, new_value: Any = None) -> dict[str, Any]:
    return {
        "backup_id": backup.get("id"),
        "action": action,
        "title": backup.get("title") or (server_item.title if server_item else ""),
        "artist": backup.get("artist") or (server_item.artist if server_item else ""),
        "album": backup.get("album") or (server_item.album if server_item else ""),
        "backup_rating": backup.get("rating"),
        "server_rating": server_item.rating if server_item else None,
        "backup_starred": bool(int(backup.get("starred") or 0)),
        "server_starred": bool(server_item.starred) if server_item else None,
        "navidrome_id": server_item.navidrome_id if server_item else str(backup.get("navidrome_id") or ""),
        "old_value": old_value,
        "new_value": new_value,
        "match_confidence": confidence,
        "status": status,
        "reason": f"{message}" + (f"; {reason}" if reason else ""),
        "conflict": False,
    }


def _restore_payload(plan: dict[str, Any], config: NavidromeConfig, mode: str, backup_run_id: int, *, error: str, verbose: bool, debug: bool) -> dict[str, Any]:
    items = plan["items"]
    summary = {
        "would_set_ratings": sum(1 for item in items if item["action"] == "set_rating" and item["status"] in {"planned", "applied"}),
        "would_star": sum(1 for item in items if item["action"] == "star" and item["status"] in {"planned", "applied"}),
        "would_unstar": sum(1 for item in items if item["action"] == "unstar" and item["status"] in {"planned", "applied"}),
        "applied_rating_updates": sum(1 for item in items if item["action"] == "set_rating" and item["status"] == "applied"),
        "applied_star_updates": sum(1 for item in items if item["action"] in {"star", "unstar"} and item["status"] == "applied"),
        "skipped": sum(1 for item in items if item["status"] in {"skip", "review"}),
        "conflicts": sum(1 for item in items if item.get("conflict") and item["status"] != "applied"),
    }
    failed = any(item["status"] == "failed" for item in items) or bool(error)
    status = "FAIL" if failed else "REVIEW" if summary["conflicts"] or any(item["status"] == "review" or item.get("match_confidence") == "low" for item in items) else "OK"
    payload_items = items if verbose or debug else items[:100]
    payload = {"status": status, "mode": mode, "server": config.base_url, "user": config.username, "backup_run_id": backup_run_id, "summary": summary, "items": payload_items}
    if error:
        payload["error"] = _clean_error(error)
    return payload


def _save_restore_run(config: dict[str, Any], account_id: int, payload: dict[str, Any], started: str, error: str) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        now = _now()
        summary = payload["summary"]
        run_id = conn.execute(
            """
            INSERT INTO player_rating_restore_runs(player_account_id, mode, status, started_at, finished_at, total_items, planned_rating_updates, applied_rating_updates, planned_star_updates, applied_star_updates, skipped_items, conflict_items, error, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, str(payload["mode"]).lower(), payload["status"], started, now, len(payload["items"]), summary["would_set_ratings"], summary["applied_rating_updates"], summary["would_star"] + summary["would_unstar"], summary["applied_star_updates"], summary["skipped"], summary["conflicts"], _clean_error(error), json.dumps(summary, sort_keys=True)),
        ).lastrowid
        for item in payload["items"]:
            conn.execute(
                """
                INSERT INTO player_rating_restore_actions(restore_run_id, backup_id, navidrome_id, action, old_value, new_value, match_confidence, status, reason, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, item.get("backup_id"), item.get("navidrome_id") or "", item.get("action") or "", _text(item.get("old_value")), _text(item.get("new_value")), item.get("match_confidence") or "", item.get("status") or "", item.get("reason") or "", item.get("applied_at") or None),
            )
        conn.commit()


def _render_restore_text(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    mode = payload["mode"]
    planned_ratings = summary["would_set_ratings"]
    planned_starred = summary["would_star"] + summary["would_unstar"]
    high = sum(1 for item in payload["items"] if item.get("match_confidence") == "high")
    medium = sum(1 for item in payload["items"] if item.get("match_confidence") == "medium")
    low = sum(1 for item in payload["items"] if item.get("match_confidence") == "low")
    lines = [
        "Navidrome ratings restore",
        f"Server: {payload.get('server') or ''}",
        f"User: {payload.get('user') or ''}",
        f"Mode: {mode}",
        "",
        f"[1/6] Load backup       {'OK' if payload.get('backup_run_id') else payload['status']:<6} {payload.get('backup_run_id') or payload.get('message', '')}",
        f"[2/6] Fetch server      {'OK' if payload['status'] != 'FAIL' else 'FAIL':<6} current API state",
        f"[3/6] Match identity    {'WARN' if medium or low else 'OK':<6} {high} high, {medium} medium, {low} low",
        f"[4/6] Build plan        {'REVIEW' if summary['conflicts'] else 'OK':<6} {summary['conflicts']} conflicts",
        f"[5/6] Restore ratings   {'DRY' if mode == 'DRY-RUN' else 'OK':<6} " + (f"would set {planned_ratings} ratings" if mode == "DRY-RUN" else f"set {summary['applied_rating_updates']} ratings"),
        f"[6/6] Restore starred   {'DRY' if mode == 'DRY-RUN' else 'OK':<6} " + (f"would update {planned_starred} favorites" if mode == "DRY-RUN" else f"updated {summary['applied_star_updates']} favorites"),
        "",
    ]
    if mode == "DRY-RUN":
        lines += ["Plan:", f"- set rating: {planned_ratings}", f"- star: {summary['would_star']}", f"- unstar: {summary['would_unstar']}", f"- skipped: {summary['skipped']}", f"- conflicts: {summary['conflicts']}", "", f"Status: {payload['status']}"]
    else:
        lines += ["Final:", f"Ratings restored: {summary['applied_rating_updates']}", f"Starred restored: {summary['applied_star_updates'] - summary['would_unstar'] if summary['applied_star_updates'] else 0}", f"Unstarred: {summary['would_unstar']}", f"Skipped: {summary['skipped']}", f"Conflicts: {summary['conflicts']}", f"Status: {payload['status']}"]
    if payload.get("error"):
        lines.append(f"Error: {payload['error']}")
    return "\n".join(lines)


def _format_restore_result(payload: dict[str, Any], output_format: str, output: Path | None, text: str) -> str:
    if output_format == "json":
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    elif output_format == "csv":
        rendered = _restore_csv(payload["items"])
    else:
        rendered = text
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        return text + f"\nOutput: {output}"
    return rendered.rstrip("\n")


def _restore_csv(items: list[dict[str, Any]]) -> str:
    columns = ["action", "title", "artist", "album", "backup_rating", "server_rating", "backup_starred", "server_starred", "navidrome_id", "old_value", "new_value", "match_confidence", "status", "reason"]
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    for item in items:
        writer.writerow({column: item.get(column, "") for column in columns})
    return handle.getvalue()


def _match_items(config: dict[str, Any], items: Iterable[RatingItem]) -> dict[str, dict[str, Any]]:
    conn = connect_readonly(config)
    if conn is None:
        return {item.navidrome_id: _unmatched(item) for item in items}
    matches: dict[str, dict[str, Any]] = {}
    with conn:
        if not _table_exists(conn, "tracks"):
            return {item.navidrome_id: _unmatched(item) for item in items}
        for item in items:
            matches[item.navidrome_id] = _match_item(conn, item)
    return matches


def _match_item(conn: sqlite3.Connection, item: RatingItem) -> dict[str, Any]:
    checks = [
        ("mb_track_id", item.mb_track_id, "SELECT t.id AS track_id, f.id AS file_id FROM tracks t LEFT JOIN files f ON f.track_id = t.id WHERE lower(t.mb_track_id) = lower(?) LIMIT 1", "high"),
        ("mb_release_track_id", item.mb_release_track_id, "SELECT t.id AS track_id, f.id AS file_id FROM tracks t LEFT JOIN files f ON f.track_id = t.id WHERE lower(t.mb_release_track_id) = lower(?) LIMIT 1", "high"),
        ("acoustid_id", item.acoustid_id, "SELECT t.id AS track_id, f.id AS file_id FROM tracks t LEFT JOIN files f ON f.track_id = t.id WHERE lower(t.acoustid_id) = lower(?) LIMIT 1", "high"),
        ("isrc", item.isrc, "SELECT t.id AS track_id, f.id AS file_id FROM tracks t LEFT JOIN files f ON f.track_id = t.id WHERE lower(t.isrc) = lower(?) LIMIT 1", "high"),
    ]
    for reason, value, sql, confidence in checks:
        if value:
            row = conn.execute(sql, (value,)).fetchone()
            if row:
                return {"library_track_id": row["track_id"], "library_file_id": row["file_id"], "match_confidence": confidence, "match_reason": reason}
    if item.artist and item.title and item.duration:
        row = conn.execute(
            """
            SELECT t.id AS track_id, f.id AS file_id FROM tracks t
            LEFT JOIN files f ON f.track_id = t.id
            WHERE lower(t.artist) = lower(?) AND lower(t.title) = lower(?) AND f.duration IS NOT NULL AND abs(f.duration - ?) <= 2
            LIMIT 1
            """,
            (item.artist, item.title, float(item.duration)),
        ).fetchone()
        if row:
            return {"library_track_id": row["track_id"], "library_file_id": row["file_id"], "match_confidence": "medium", "match_reason": "artist_title_duration"}
    if item.albumartist and item.album and item.track and item.title:
        row = conn.execute(
            """
            SELECT t.id AS track_id, f.id AS file_id FROM tracks t
            LEFT JOIN albums a ON a.id = t.album_id
            LEFT JOIN files f ON f.track_id = t.id
            WHERE lower(t.albumartist) = lower(?) AND lower(a.album) = lower(?) AND t.track = ? AND lower(t.title) = lower(?)
            LIMIT 1
            """,
            (item.albumartist, item.album, item.track, item.title),
        ).fetchone()
        if row:
            return {"library_track_id": row["track_id"], "library_file_id": row["file_id"], "match_confidence": "medium", "match_reason": "album_track_title"}
    if item.path:
        row = conn.execute("SELECT track_id, id AS file_id FROM files WHERE path = ? LIMIT 1", (normalize_path(item.path),)).fetchone()
        if row and row["track_id"]:
            return {"library_track_id": row["track_id"], "library_file_id": row["file_id"], "match_confidence": "low", "match_reason": "path"}
    return _unmatched(item)


def _save_backup(config: dict[str, Any], nd_config: NavidromeConfig, items: list[RatingItem], matches: dict[str, dict[str, Any]], summary: BackupSummary, started: str) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        now = _now()
        conn.execute(
            """
            INSERT INTO player_accounts(player, name, base_url, username, server_id, created_at, updated_at)
            VALUES ('navidrome', 'Navidrome', ?, ?, ?, ?, ?)
            ON CONFLICT(player, base_url, username) DO UPDATE SET updated_at = excluded.updated_at, server_id = excluded.server_id
            """,
            (nd_config.base_url, nd_config.username, _server_id(nd_config.base_url), now, now),
        )
        account_id = int(conn.execute("SELECT id FROM player_accounts WHERE player = 'navidrome' AND base_url = ? AND username = ?", (nd_config.base_url, nd_config.username)).fetchone()["id"])
        for item in items:
            identity_key, identity_method, identity_confidence = build_player_track_identity(item)
            match = matches.get(item.navidrome_id, _unmatched(item))
            conn.execute(
                """
                INSERT INTO player_rating_backups(player_account_id, player, user, navidrome_id, library_track_id, library_file_id, identity_key, identity_method, identity_confidence, match_confidence, match_reason, title, artist, album, albumartist, duration, rating, starred, starred_at, play_count, last_played, path, raw_summary_json, backed_up_at, updated_at)
                VALUES (?, 'navidrome', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_account_id, navidrome_id) DO UPDATE SET library_track_id = excluded.library_track_id, library_file_id = excluded.library_file_id, identity_key = excluded.identity_key, identity_method = excluded.identity_method, identity_confidence = excluded.identity_confidence, match_confidence = excluded.match_confidence, match_reason = excluded.match_reason, title = excluded.title, artist = excluded.artist, album = excluded.album, albumartist = excluded.albumartist, duration = excluded.duration, rating = excluded.rating, starred = excluded.starred, starred_at = excluded.starred_at, play_count = excluded.play_count, last_played = excluded.last_played, path = excluded.path, raw_summary_json = excluded.raw_summary_json, backed_up_at = excluded.backed_up_at, updated_at = excluded.updated_at
                """,
                (account_id, nd_config.username, item.navidrome_id, match.get("library_track_id"), match.get("library_file_id"), identity_key, identity_method, identity_confidence, match.get("match_confidence"), match.get("match_reason"), item.title, item.artist, item.album, item.albumartist, item.duration, item.rating, 1 if item.starred else 0, item.starred_at, item.play_count, item.last_played, item.path, json.dumps(item.raw_summary or {}, sort_keys=True), now, now),
            )
        conn.execute(
            """
            INSERT INTO player_rating_backup_runs(player_account_id, mode, status, started_at, finished_at, total_items, matched_items, unmatched_items, rated_items, starred_items, error, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
            """,
            (account_id, summary.mode.lower(), summary.status, started, now, summary.total_items, summary.matched_items, summary.unmatched_items, summary.rated_items, summary.starred_items, json.dumps(_summary_payload(summary), sort_keys=True)),
        )
        conn.commit()


def _summary_payload(summary: BackupSummary) -> dict[str, Any]:
    return {
        "status": summary.status,
        "total_items": summary.total_items,
        "matched_items": summary.matched_items,
        "unmatched_items": summary.unmatched_items,
        "rated_items": summary.rated_items,
        "starred_items": summary.starred_items,
        "saved_items": summary.saved_items,
        "mode": summary.mode,
    }


def _render_backup(config: NavidromeConfig, mode: str, items: list[RatingItem], summary: BackupSummary, error: str = "") -> str:
    fetch_status = "FAIL" if summary.status == "FAIL" else "OK"
    match_status = "WARN" if summary.unmatched_items else "OK"
    save_status = "OK" if mode == "APPLY" and summary.status != "FAIL" else "SKIP" if summary.status != "FAIL" else "FAIL"
    lines = ["Navidrome ratings backup", f"Server: {config.base_url}", f"User: {config.username}", f"Mode: {mode}", "", "[1/5] Connect            " + ("FAIL" if error else "OK") + (f"     {_clean_error(error)}" if error else "      server reachable"), f"[2/5] Fetch ratings      {fetch_status:<6} {len(items)} items", f"[3/5] Normalize          {'OK' if summary.status != 'FAIL' else 'FAIL':<6} {len(items)} songs", f"[4/5] Match library      {match_status:<6} matched {summary.matched_items}/{summary.total_items}", f"[5/5] Save backup        {save_status:<6} " + (f"saved {summary.saved_items} ratings" if mode == "APPLY" else f"would save {summary.total_items} ratings"), "", "Final:", f"Items: {summary.total_items}", f"Matched: {summary.matched_items}", f"Unmatched: {summary.unmatched_items}", f"Rated: {summary.rated_items}", f"Starred: {summary.starred_items}", f"Status: {summary.status}"]
    return "\n".join(lines)


def _extract_song_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("subsonic-response", payload)
    starred2 = response.get("starred2") if isinstance(response, dict) else None
    starred = response.get("starred") if isinstance(response, dict) else None
    container = starred2 if isinstance(starred2, dict) else starred if isinstance(starred, dict) else {}
    songs = container.get("song") if isinstance(container, dict) else []
    if isinstance(songs, dict):
        return [songs]
    return [song for song in songs if isinstance(song, dict)] if isinstance(songs, list) else []


def _export_rows_from_items(items: list[RatingItem], matches: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        identity_key, identity_method, _confidence = build_player_track_identity(item)
        match = matches.get(item.navidrome_id, {})
        rows.append({"identity_key": identity_key, "identity_method": identity_method, "title": item.title, "artist": item.artist, "album": item.album, "rating": item.rating, "starred": item.starred, "play_count": item.play_count, "last_played": item.last_played, "match_confidence": match.get("match_confidence", "none")})
    return rows


def _load_library_rating_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "tracks") or not _table_exists(conn, "files"):
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.id AS library_track_id, f.id AS library_file_id, t.title, t.artist, t.albumartist,
                   a.album, t.track, t.mb_track_id, t.mb_release_track_id, t.acoustid_id, t.isrc,
                   f.duration, f.path
            FROM tracks t
            LEFT JOIN albums a ON a.id = t.album_id
            LEFT JOIN files f ON f.track_id = t.id AND COALESCE(f.status, 'active') = 'active'
            ORDER BY t.artist, t.title, f.path
            """
        )
    ]


def _match_backups_to_library(backups: list[dict[str, Any]], library: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    identity_index: dict[str, dict[str, Any]] = {}
    track_index = {int(row["library_track_id"]): row for row in library if row.get("library_track_id") is not None}
    for row in library:
        for key, method, confidence in _library_identity_keys(row):
            identity_index.setdefault(key, {**row, "identity_key": key, "identity_method": method, "match_confidence": confidence})
    matches: dict[int, dict[str, Any]] = {}
    for backup in backups:
        row_id = int(backup["id"])
        identity_key = str(backup.get("identity_key") or "")
        if identity_key and identity_key in identity_index and not identity_key.startswith("navidrome:"):
            matches[row_id] = identity_index[identity_key]
            continue
        track_id = backup.get("library_track_id")
        if track_id is not None and int(track_id) in track_index:
            row = track_index[int(track_id)]
            matches[row_id] = {**row, "identity_key": identity_key, "identity_method": backup.get("identity_method") or "track_id", "match_confidence": backup.get("match_confidence") or "low"}
    return matches


def _library_identity_keys(row: dict[str, Any]) -> list[tuple[str, str, str]]:
    keys: list[tuple[str, str, str]] = []
    for column, prefix in (("mb_track_id", "mb_track"), ("mb_release_track_id", "mb_release_track"), ("acoustid_id", "acoustid"), ("isrc", "isrc")):
        value = _text(row.get(column))
        if value:
            keys.append((f"{prefix}:{value.casefold()}", column, "high"))
    if row.get("artist") and row.get("title") and row.get("duration") is not None:
        keys.append((f"artist_title_duration:{_norm(str(row['artist']))}:{_norm(str(row['title']))}:{round(float(row['duration']))}", "artist_title_duration", "medium"))
    if row.get("albumartist") and row.get("album") and row.get("track") and row.get("title"):
        keys.append((f"album_track_title:{_norm(str(row['albumartist']))}:{_norm(str(row['album']))}:{int(row['track'])}:{_norm(str(row['title']))}", "album_track_title", "medium"))
    return keys


def _build_diff_payload(
    backups: list[dict[str, Any]],
    library: list[dict[str, Any]],
    library_matches: dict[int, dict[str, Any]],
    server_items: list[RatingItem],
    *,
    backup_run_id: int,
    server: str,
    user: str,
    include_server: bool,
    server_error: str,
    verbose: bool,
    debug: bool,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    matched_track_ids = {int(row["library_track_id"]) for row in library_matches.values() if row.get("library_track_id") is not None}
    for backup in backups:
        match = library_matches.get(int(backup["id"]))
        if match is None:
            items.append(_diff_item("unmatched_backup", backup, None, reason="backup item does not match the current Noqlen Forge library"))
            continue
        backup_path = _text(backup.get("path"))
        current_path = _text(match.get("path"))
        if backup_path and current_path and normalize_path(backup_path) != normalize_path(current_path) and not str(backup.get("identity_key") or "").startswith("navidrome:"):
            items.append(_diff_item("moved_path", backup, match, reason="path changed but stable identity still matches"))
    backed_identity_keys = {str(row.get("identity_key") or "") for row in backups if (row.get("rating") is not None or int(row.get("starred") or 0))}
    for row in library:
        track_id = row.get("library_track_id")
        if track_id is None or int(track_id) in matched_track_ids:
            continue
        if any(key in backed_identity_keys for key, _method, _confidence in _library_identity_keys(row)):
            continue
        items.append(_library_diff_item(row, "library_without_rating", "track has no saved Navidrome rating/favorite backup"))
    if include_server and not server_error:
        backup_by_nav = {str(row.get("navidrome_id") or ""): row for row in backups if row.get("navidrome_id")}
        seen_nav: set[str] = set()
        for server_item in server_items:
            if not server_item.navidrome_id:
                continue
            backup = backup_by_nav.get(server_item.navidrome_id)
            if backup is None:
                if server_item.rating is not None or server_item.starred:
                    items.append(_server_diff_item("new_on_server", server_item, "rated/favorited server item is not present in the backup"))
                continue
            seen_nav.add(server_item.navidrome_id)
            if _rating_value(backup.get("rating")) != _rating_value(server_item.rating):
                items.append(_diff_item("changed_rating", backup, library_matches.get(int(backup["id"])), server_item=server_item, reason="server rating differs from latest backup"))
            if bool(int(backup.get("starred") or 0)) != bool(server_item.starred):
                items.append(_diff_item("changed_starred", backup, library_matches.get(int(backup["id"])), server_item=server_item, reason="server favorite state differs from latest backup"))
        for backup in backups:
            navidrome_id = str(backup.get("navidrome_id") or "")
            if navidrome_id and navidrome_id not in seen_nav:
                items.append(_diff_item("missing_on_server", backup, library_matches.get(int(backup["id"])), reason="backup item no longer appears on the server by navidrome_id"))
    summary = _empty_diff_summary()
    summary["changed_ratings"] = sum(1 for item in items if item["type"] == "changed_rating")
    summary["new_on_server"] = sum(1 for item in items if item["type"] == "new_on_server")
    summary["missing_on_server"] = sum(1 for item in items if item["type"] == "missing_on_server")
    summary["unmatched_backup"] = sum(1 for item in items if item["type"] == "unmatched_backup")
    summary["library_without_rating"] = sum(1 for item in items if item["type"] == "library_without_rating")
    summary["moved_paths_matched"] = sum(1 for item in items if item["type"] == "moved_path")
    status = "WARN" if any(summary.values()) or any(item["type"] == "changed_starred" for item in items) or server_error else "OK"
    payload = {"status": status, "server": server, "user": user, "backup_run_id": backup_run_id, "summary": summary, "items": items}
    if server_error:
        payload["server_error"] = server_error
    if not verbose and not debug:
        payload["items"] = items[:100]
    return payload


def _diff_item(diff_type: str, backup: dict[str, Any], match: dict[str, Any] | None, *, reason: str, server_item: RatingItem | None = None) -> dict[str, Any]:
    return {
        "type": diff_type,
        "identity_key": backup.get("identity_key") or "",
        "identity_method": backup.get("identity_method") or "",
        "title": backup.get("title") or (server_item.title if server_item else ""),
        "artist": backup.get("artist") or (server_item.artist if server_item else ""),
        "album": backup.get("album") or (server_item.album if server_item else ""),
        "backup_rating": backup.get("rating"),
        "server_rating": server_item.rating if server_item else None,
        "backup_starred": bool(int(backup.get("starred") or 0)),
        "server_starred": bool(server_item.starred) if server_item else None,
        "navidrome_id": backup.get("navidrome_id") or (server_item.navidrome_id if server_item else ""),
        "library_track_id": match.get("library_track_id") if match else backup.get("library_track_id"),
        "path": match.get("path") if match else backup.get("path") or "",
        "match_confidence": (match.get("match_confidence") if match else backup.get("match_confidence")) or "none",
        "reason": reason,
    }


def _library_diff_item(row: dict[str, Any], diff_type: str, reason: str) -> dict[str, Any]:
    keys = _library_identity_keys(row)
    identity_key, identity_method, confidence = keys[0] if keys else ("", "", "none")
    return {"type": diff_type, "identity_key": identity_key, "identity_method": identity_method, "title": row.get("title") or "", "artist": row.get("artist") or "", "album": row.get("album") or "", "backup_rating": None, "server_rating": None, "backup_starred": None, "server_starred": None, "navidrome_id": "", "library_track_id": row.get("library_track_id"), "path": row.get("path") or "", "match_confidence": confidence, "reason": reason}


def _server_diff_item(diff_type: str, item: RatingItem, reason: str) -> dict[str, Any]:
    identity_key, identity_method, confidence = build_player_track_identity(item)
    return {"type": diff_type, "identity_key": identity_key, "identity_method": identity_method, "title": item.title, "artist": item.artist, "album": item.album, "backup_rating": None, "server_rating": item.rating, "backup_starred": None, "server_starred": item.starred, "navidrome_id": item.navidrome_id, "library_track_id": None, "path": item.path, "match_confidence": confidence, "reason": reason}


def _rating_value(value: Any) -> float | None:
    return None if value in (None, "") else float(value)


def _empty_diff_summary() -> dict[str, int]:
    return {"changed_ratings": 0, "new_on_server": 0, "missing_on_server": 0, "unmatched_backup": 0, "library_without_rating": 0, "moved_paths_matched": 0}


def _render_diff_text(payload: dict[str, Any], backup_count: int, library_count: int, fetched_server: bool, server_error: str) -> str:
    summary = payload["summary"]
    matched = backup_count - summary["unmatched_backup"]
    changed = summary["changed_ratings"] + summary["new_on_server"] + summary["missing_on_server"] + sum(1 for item in payload["items"] if item["type"] == "changed_starred")
    lines = [
        "Navidrome ratings diff",
        f"Server: {payload.get('server') or ''}",
        f"User: {payload.get('user') or ''}",
        "Mode: READ-ONLY",
        "",
        f"[1/5] Load backup       {'OK' if backup_count else 'WARN':<6} {backup_count} items",
        f"[2/5] Load library      {'OK' if library_count else 'WARN':<6} {library_count} tracks",
        f"[3/5] Match identity    {'WARN' if summary['unmatched_backup'] else 'OK':<6} {matched} matched, {summary['unmatched_backup']} unmatched",
        f"[4/5] Fetch server      {'OK' if fetched_server and not server_error else 'WARN' if server_error else 'SKIP':<6} " + (f"{server_error}" if server_error else ("server compared" if fetched_server else "use --server to compare current API state")),
        f"[5/5] Compare           {payload['status']:<6} {changed} changes",
        "",
        "Diff:",
        f"- Changed ratings: {summary['changed_ratings']}",
        f"- New on server: {summary['new_on_server']}",
        f"- Missing on server: {summary['missing_on_server']}",
        f"- Unmatched backup: {summary['unmatched_backup']}",
        f"- Library tracks without rating: {summary['library_without_rating']}",
        f"- Moved paths matched by identity: {summary['moved_paths_matched']}",
        "",
        f"Status: {payload['status']}",
    ]
    return "\n".join(lines)


def _format_diff_result(payload: dict[str, Any], output_format: str, output: Path | None, text: str) -> str:
    if output_format == "json":
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    elif output_format == "csv":
        rendered = _diff_csv(payload["items"])
    else:
        rendered = text
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        return text + f"\nOutput: {output}"
    return rendered.rstrip("\n")


def _diff_csv(items: list[dict[str, Any]]) -> str:
    columns = ["diff_type", "title", "artist", "album", "identity_key", "identity_method", "match_confidence", "backup_rating", "server_rating", "backup_starred", "server_starred", "navidrome_id", "library_track_id", "path", "reason"]
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    for item in items:
        writer.writerow({"diff_type": item.get("type", ""), **{column: item.get(column, "") for column in columns if column != "diff_type"}})
    return handle.getvalue()


def _write_items(path: Path, output_format: str, rows: list[dict[str, Any]], *, force: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise NavidromeError(f"Output exists: {path}")
    if output_format == "json":
        path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["identity_key", "identity_method", "title", "artist", "album", "rating", "starred", "play_count", "last_played", "match_confidence"])
        writer.writeheader()
        writer.writerows(rows)


def _format_payload(payload: dict[str, Any], output_format: str, text: str) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) if output_format == "json" else text


def _unmatched(item: RatingItem) -> dict[str, Any]:
    return {"library_track_id": None, "library_file_id": None, "match_confidence": "none", "match_reason": "unmatched"}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _safe_server(config: dict[str, Any]) -> str:
    return str(get_config_value(config, "navidrome", "base_url", "") or "")


def _safe_user(config: dict[str, Any]) -> str:
    return str(get_config_value(config, "navidrome", "username", "") or "")


def _server_id(base_url: str) -> str:
    return hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:16]


def _clean_error(value: Any) -> str:
    text = str(value)
    for marker in ("password", "token", "salt", "p=", "t=", "s="):
        if marker.casefold() in text.casefold():
            return "[redacted sensitive output]"
    return text


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes"}


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
