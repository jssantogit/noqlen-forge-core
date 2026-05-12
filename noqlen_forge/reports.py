from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .audio import audio_files
from .config import get_config_value
from .db import connect_readonly, database_path, normalize_path
from .fields import resolve_field_alias, supported_field_names
from .workflow import OperationContext, StepResult, Status, WorkflowRunner


DEFAULT_MISSING_FIELDS = [
    "cover",
    "lyrics",
    "synced_lyrics",
    "key",
    "replaygain",
    "bpm",
    "mood",
    "style",
    "label",
    "originaldate",
    "mb_album_id",
    "mb_track_id",
]

SUPPORTED_FIELDS = supported_field_names() | {
    "album",
    "albumartist",
    "artist",
    "title",
    "track",
    "date",
    "originaldate",
    "mb_album_id",
    "mb_release_group_id",
    "mb_track_id",
    "mb_release_track_id",
    "acoustid_id",
    "label",
    "style",
    "genre",
    "mood",
    "bpm",
    "key",
    "energy",
    "danceability",
    "lastfm_tags",
    "replaygain",
    "replaygain_track",
    "replaygain_album",
    "loudness",
    "cover",
    "lyrics",
    "synced_lyrics",
    "sidecar_lrc",
    "catalog_number",
    "barcode",
    "country",
    "media",
    "release_type",
    "isrc",
    "replaygain_track",
    "replaygain_album",
}


def missing_report(config: dict[str, Any], fields: list[str] | None = None, library: Path | None = None, scope: str = "albums", output_format: str = "text", verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, f"Database not initialized: {database_path(config)}"
    state: dict[str, Any] = {"requested": [], "duplicate_paths": [], "rows": []}
    context = OperationContext.from_flags("report missing", target=library, verbose=verbose, debug=debug, config=config, database_enabled=True)

    def normalize_step(_: OperationContext, index: int, total: int) -> StepResult:
        state["requested"] = _normalize_fields(fields or list(get_config_value(config, "reports", "default_missing_fields", DEFAULT_MISSING_FIELDS)))
        status = Status.OK if state["requested"] else Status.FAIL
        return StepResult(index, total, "Normalize fields", status, f"{len(state['requested'])} fields" if state["requested"] else "no supported fields")

    def query_step(_: OperationContext, index: int, total: int) -> StepResult:
        with conn:
            state["duplicate_paths"] = _duplicate_paths(conn)
            state["rows"] = _missing_track_rows(conn, state["requested"], library)
        status = Status.REVIEW if state["duplicate_paths"] else Status.WARN if state["rows"] else Status.OK
        return StepResult(index, total, "Query database", status, f"{len(state['rows'])} tracks")

    workflow = WorkflowRunner(context).run([normalize_step, query_step])
    requested = state["requested"]
    if workflow.status == Status.FAIL or not requested:
        return 1, "No supported missing fields requested"
    duplicate_paths = state["duplicate_paths"]
    rows = state["rows"]
    status = "REVIEW" if duplicate_paths else "WARN" if rows else "OK"
    payload = _missing_payload(status, requested, rows, library, duplicate_paths)
    if output_format == "json":
        return 0, json.dumps(payload, indent=2, sort_keys=True)
    if scope == "tracks":
        return 0, _render_missing_tracks(payload, verbose=verbose, debug=debug)
    return 0, _render_missing_albums(payload, verbose=verbose, debug=debug)


def untracked_report(config: dict[str, Any], path: Path | None, output_format: str = "text", verbose: bool = False) -> tuple[int, str]:
    target = path or Path(str(get_config_value(config, "library", "root", "") or ""))
    if not str(target):
        return 1, "Library path is required"
    target = target.expanduser()
    if not target.exists():
        return 1, f"Path not found: {target}"
    conn = connect_readonly(config)
    if conn is None:
        return 1, f"Database not initialized: {database_path(config)}"
    disk_paths = [normalize_path(item) for item in audio_files(target)]
    with conn:
        db_paths = _known_paths(conn)
    missing = [item for item in sorted(disk_paths) if item not in db_paths]
    payload = {"status": "WARN" if missing else "OK", "scope": normalize_path(target), "files": missing, "summary": {"untracked": len(missing)}}
    if output_format == "json":
        return 0, json.dumps(payload, indent=2, sort_keys=True)
    return 0, _render_untracked(payload, verbose=verbose)


def missing_files_report(config: dict[str, Any], output_format: str = "text", verbose: bool = False) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, f"Database not initialized: {database_path(config)}"
    with conn:
        rows = [str(row["path"] or "") for row in conn.execute("SELECT path FROM files ORDER BY path")]
    missing = [path for path in rows if path and not Path(path).exists()]
    payload = {"status": "WARN" if missing else "OK", "files": missing, "summary": {"missing_files": len(missing)}}
    if output_format == "json":
        return 0, json.dumps(payload, indent=2, sort_keys=True)
    return 0, _render_missing_files(payload, verbose=verbose)


def _normalize_fields(fields: list[str]) -> list[str]:
    expanded: list[str] = []
    for raw in fields:
        for item in str(raw or "").split(","):
            field = item.strip().casefold().replace("-", "_")
            if not field:
                continue
            expanded.extend(resolve_field_alias(field))
    result: list[str] = []
    for field in expanded:
        if field in SUPPORTED_FIELDS and field not in result:
            result.append(field)
    return result


def _missing_track_rows(conn: sqlite3.Connection, fields: list[str], library: Path | None) -> list[dict[str, Any]]:
    path_filter = ""
    params: list[Any] = []
    if library is not None:
        normalized = normalize_path(library)
        path_filter = "WHERE f.path = ? OR f.path LIKE ?"
        params.extend([normalized, normalized.rstrip("/") + "/%"])
    sql = f"""
        SELECT f.id AS file_id, f.path, a.id AS album_id, COALESCE(a.album, '') AS album,
               COALESCE(a.albumartist, t.albumartist, t.artist, '') AS albumartist,
               t.id AS track_id, COALESCE(t.title, '') AS title, COALESCE(t.artist, '') AS artist,
               t.track, a.date AS album_date, a.originaldate, a.mb_album_id, a.mb_release_group_id,
               a.label, a.catalog_number, a.barcode, a.country, a.release_format, a.release_type,
               t.mb_track_id, t.mb_release_track_id, t.acoustid_id, t.isrc, t.bpm AS track_bpm,
               t.key AS track_key, t.mood, t.energy AS track_energy, t.danceability AS track_danceability,
               f.has_cover, f.has_lyrics, f.has_synced_lyrics,
               af.bpm AS feature_bpm, af.key AS feature_key, af.replaygain_track_gain, af.replaygain_track_peak,
               af.replaygain_album_gain, af.replaygain_album_peak, af.loudness, af.energy AS feature_energy,
               af.danceability AS feature_danceability
        FROM files f
        LEFT JOIN tracks t ON t.id = f.track_id
        LEFT JOIN albums a ON a.id = t.album_id
        LEFT JOIN audio_features af ON af.track_id = t.id
        {path_filter}
        ORDER BY albumartist, album, COALESCE(t.track, 999), title, f.path
    """
    rows: list[dict[str, Any]] = []
    for row in conn.execute(sql, params):
        missing = [field for field in fields if _field_missing(conn, row, field)]
        if missing:
            data = dict(row)
            data["missing_fields"] = missing
            rows.append(data)
    return rows


def _field_missing(conn: sqlite3.Connection, row: sqlite3.Row, field: str) -> bool:
    track_id = row["track_id"]
    album_id = row["album_id"]
    file_id = row["file_id"]
    if field == "album":
        return _empty(row["album"])
    if field == "albumartist":
        return _empty(row["albumartist"])
    if field in {"artist", "title"}:
        return _empty(row[field])
    if field == "track":
        return row["track"] is None
    if field == "date":
        return _empty(row["album_date"])
    if field in {"originaldate", "mb_album_id", "mb_release_group_id", "label", "catalog_number", "barcode", "country", "release_type", "isrc", "mb_track_id", "mb_release_track_id", "acoustid_id", "mood"}:
        return _empty(row[field])
    if field == "media":
        return _empty(row["release_format"])
    if field == "cover":
        return not bool(row["has_cover"]) and not _exists(conn, "SELECT 1 FROM artwork WHERE file_id = ? OR album_id = ?", [file_id, album_id])
    if field == "lyrics":
        return not bool(row["has_lyrics"]) and not _exists(conn, "SELECT 1 FROM lyrics WHERE track_id = ?", [track_id])
    if field == "synced_lyrics":
        return not bool(row["has_synced_lyrics"]) and not _exists(conn, "SELECT 1 FROM lyrics WHERE track_id = ? AND synced = 1", [track_id])
    if field == "sidecar_lrc":
        sidecar = Path(str(row["path"] or "")).with_suffix(".lrc")
        return not sidecar.exists() and not _exists(conn, "SELECT 1 FROM lyrics WHERE track_id = ? AND COALESCE(sidecar_path, '') != ''", [track_id])
    if field in {"style", "genre", "lastfm_tags"}:
        keys = [field] if field != "lastfm_tags" else ["lastfm_tags", "lastfm", "last.fm"]
        return not any(_tag_exists(conn, key, track_id, album_id) for key in keys)
    if field == "bpm":
        return row["track_bpm"] is None and row["feature_bpm"] is None
    if field == "key":
        return _empty(row["track_key"]) and _empty(row["feature_key"])
    if field == "energy":
        return row["track_energy"] is None and row["feature_energy"] is None
    if field == "danceability":
        return row["track_danceability"] is None and row["feature_danceability"] is None
    if field == "replaygain":
        return _field_missing(conn, row, "replaygain_track") or _field_missing(conn, row, "replaygain_album")
    if field == "replaygain_track":
        return row["replaygain_track_gain"] is None or row["replaygain_track_peak"] is None
    if field == "replaygain_album":
        return row["replaygain_album_gain"] is None or row["replaygain_album_peak"] is None
    if field == "loudness":
        return row["loudness"] is None
    return True


def _exists(conn: sqlite3.Connection, sql: str, params: list[Any]) -> bool:
    return conn.execute(sql, params).fetchone() is not None


def _tag_exists(conn: sqlite3.Connection, key: str, track_id: Any, album_id: Any) -> bool:
    return _exists(conn, "SELECT 1 FROM track_tags WHERE track_id = ? AND LOWER(key) = ?", [track_id, key]) or _exists(conn, "SELECT 1 FROM album_tags WHERE album_id = ? AND LOWER(key) = ?", [album_id, key])


def _empty(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _duplicate_paths(conn: sqlite3.Connection) -> list[str]:
    return [str(row["path"] or "") for row in conn.execute("SELECT path FROM files GROUP BY path HAVING COUNT(*) > 1")]


def _known_paths(conn: sqlite3.Connection) -> set[str]:
    return {str(row["path"] or "") for row in conn.execute("SELECT path FROM files")}


def _missing_payload(status: str, fields: list[str], rows: list[dict[str, Any]], library: Path | None, duplicate_paths: list[str]) -> dict[str, Any]:
    albums: dict[tuple[Any, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["album_id"], row["albumartist"], row["album"])
        album = albums.setdefault(key, {"album": row["album"], "albumartist": row["albumartist"], "missing": {field: {"missing": 0, "total": 0} for field in fields}, "tracks": []})
        track_missing = list(row["missing_fields"])
        album["tracks"].append({"title": row["title"], "artist": row["artist"], "track": row["track"], "path": row["path"], "missing": track_missing})
    for album in albums.values():
        total = len(album["tracks"])
        for field in fields:
            album["missing"][field]["total"] = total
            album["missing"][field]["missing"] = sum(1 for track in album["tracks"] if field in track["missing"])
        album["missing"] = {field: counts for field, counts in album["missing"].items() if counts["missing"] > 0}
    tracks_affected = len(rows)
    payload = {"status": status, "scope": normalize_path(library) if library else "library", "fields": fields, "albums": list(albums.values()), "summary": {"albums": len(albums), "tracks_affected": tracks_affected}}
    if duplicate_paths:
        payload["duplicates"] = duplicate_paths
    return payload


def _render_missing_albums(payload: dict[str, Any], verbose: bool, debug: bool) -> str:
    fields = payload["fields"]
    lines = ["Missing report", f"Scope: {payload['scope']}", f"Fields: {', '.join(fields)}", ""]
    if not payload["albums"]:
        for field in fields:
            lines.append(f"{_title(field)}: complete")
        lines.append(f"Status: {payload['status']}")
        return "\n".join(lines)
    lines.append(f"Albums with missing fields: {payload['summary']['albums']}")
    lines.append("")
    for album in payload["albums"]:
        lines.append(" - ".join(part for part in [album["albumartist"], album["album"]] if part) or "Unknown Album")
        for field, counts in album["missing"].items():
            lines.append(f"- {_title(field)}: {counts['missing']}/{counts['total']} missing")
        if verbose:
            for track in album["tracks"]:
                lines.append(f"  {track['path']}")
        lines.append("")
    lines.extend(["Final:", f"Albums: {payload['summary']['albums']}", f"Tracks affected: {payload['summary']['tracks_affected']}", f"Status: {payload['status']}"])
    if debug and payload.get("duplicates"):
        lines.append(f"Duplicate paths: {len(payload['duplicates'])}")
    return "\n".join(lines).rstrip()


def _render_missing_tracks(payload: dict[str, Any], verbose: bool, debug: bool) -> str:
    tracks = [track for album in payload["albums"] for track in album["tracks"]]
    label = ", ".join(_title(field) for field in payload["fields"])
    if not tracks:
        return f"Missing {label}: none\nStatus: {payload['status']}"
    lines = [f"Missing {label}: {len(tracks)} tracks", ""]
    for track in tracks:
        lines.append(f"- {track['artist']} - {track['title']}")
        lines.append(f"  {track['path']}")
        if verbose or debug:
            lines.append(f"  missing: {', '.join(track['missing'])}")
    lines.extend(["", f"Status: {payload['status']}"])
    return "\n".join(lines)


def _render_untracked(payload: dict[str, Any], verbose: bool) -> str:
    files = payload["files"]
    if not files:
        return "Untracked files: none\nStatus: OK"
    lines = [f"Untracked files: {len(files)}", ""]
    lines.extend(f"- {path}" for path in files)
    lines.extend(["", "Final:", f"Untracked: {len(files)}", "Status: WARN", "", "To import into the database, run:", f"noqlen-forge db scan {payload['scope']} --apply"])
    return "\n".join(lines)


def _render_missing_files(payload: dict[str, Any], verbose: bool) -> str:
    files = payload["files"]
    if not files:
        return "Missing files in database: none\nStatus: OK"
    lines = [f"Missing files in database: {len(files)}", ""]
    lines.extend(f"- {path}" for path in files)
    lines.extend(["", "Final:", f"Missing files: {len(files)}", "Status: WARN", "", "No database rows were removed. A future repair/prune command can clean these records."])
    return "\n".join(lines)


def _title(field: str) -> str:
    names = {"mb_album_id": "MB Album Id", "mb_track_id": "MB Track Id", "mb_release_group_id": "MB Release Group Id", "synced_lyrics": "Synced Lyrics", "sidecar_lrc": "Sidecar LRC", "replaygain": "ReplayGain", "replaygain_track": "ReplayGain Track", "replaygain_album": "ReplayGain Album"}
    return names.get(field, field.replace("_", " ").title())
