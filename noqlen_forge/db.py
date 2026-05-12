from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audio import Track, audio_files, get_tag, read_track
from .config import APP_SLUG, get_config_value
from .fields import get_queryable_fields, resolve_field_alias

SCHEMA_VERSION = 13


def database_path(config: dict[str, Any]) -> Path:
    configured = str(get_config_value(config, "database", "path", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    root = os.environ.get("XDG_DATA_HOME")
    if root:
        return Path(root) / APP_SLUG / "library.db"
    return Path.home() / ".local" / "share" / APP_SLUG / "library.db"


def connect(config: dict[str, Any]) -> sqlite3.Connection:
    path = database_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def connect_readonly(config: dict[str, Any]) -> sqlite3.Connection | None:
    path = database_path(config)
    if not path.exists():
        return None
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(config: dict[str, Any]) -> Path:
    with connect(config) as conn:
        apply_migrations(conn)
    return database_path(config)


def current_schema_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'").fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
    return int(row["version"] if row else 0)


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    for version, name, sql in MIGRATIONS:
        if current_schema_version(conn) >= version:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, _now()),
        )
    conn.commit()


def get_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {
        "albums": _table_count(conn, "albums"),
        "tracks": _table_count(conn, "tracks"),
        "files": _table_count(conn, "files"),
        "missing_files": 0,
    }
    if _table_exists(conn, "files"):
        counts["missing_files"] = int(conn.execute("SELECT COUNT(*) AS count FROM files WHERE status = 'missing'").fetchone()["count"])
    return counts


def db_status(config: dict[str, Any]) -> dict[str, Any]:
    path = database_path(config)
    if not path.exists():
        return {"path": path, "version": 0, "counts": {"albums": 0, "tracks": 0, "files": 0, "missing_files": 0}, "last_operation": None}
    with connect(config) as conn:
        version = current_schema_version(conn)
        counts = get_counts(conn)
        last_operation = None
        if _table_exists(conn, "operations"):
            row = conn.execute("SELECT operation, mode, status, finished_at FROM operations ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                last_operation = dict(row)
    return {"path": path, "version": version, "counts": counts, "last_operation": last_operation}


def upsert_album(conn: sqlite3.Connection, metadata: dict[str, Any]) -> int:
    values = {key: _text(metadata.get(key)) for key in ALBUM_FIELDS if key != "id"}
    values["status"] = values.get("status") or "active"
    values["album_key"] = _album_key(metadata)
    values["updated_at"] = _now()
    values["created_at"] = values.get("created_at") or values["updated_at"]
    insert_fields = [key for key in ALBUM_FIELDS if key != "id"]
    update_fields = [key for key in insert_fields if key not in {"album_key", "created_at"}]
    conn.execute(
        f"""
        INSERT INTO albums({', '.join(insert_fields)})
        VALUES ({', '.join('?' for _ in insert_fields)})
        ON CONFLICT(album_key) DO UPDATE SET {', '.join(f'{key} = excluded.{key}' for key in update_fields)}
        """,
        [values.get(key) for key in insert_fields],
    )
    row = conn.execute("SELECT id FROM albums WHERE album_key = ?", (values["album_key"],)).fetchone()
    return int(row["id"])


def upsert_track(conn: sqlite3.Connection, metadata: dict[str, Any], album_id: int | None = None) -> int:
    existing_id = metadata.get("id")
    values = {key: metadata.get(key) for key in TRACK_FIELDS if key != "id"}
    values["status"] = values.get("status") or "active"
    values["album_id"] = album_id if album_id is not None else metadata.get("album_id")
    values["updated_at"] = _now()
    values["created_at"] = values.get("created_at") or values["updated_at"]
    if existing_id:
        update_fields = [key for key in TRACK_FIELDS if key not in {"id", "created_at"}]
        conn.execute(
            f"UPDATE tracks SET {', '.join(f'{key} = ?' for key in update_fields)} WHERE id = ?",
            [values.get(key) for key in update_fields] + [existing_id],
        )
        return int(existing_id)
    insert_fields = [key for key in TRACK_FIELDS if key != "id"]
    cursor = conn.execute(
        f"INSERT INTO tracks({', '.join(insert_fields)}) VALUES ({', '.join('?' for _ in insert_fields)})",
        [values.get(key) for key in insert_fields],
    )
    return int(cursor.lastrowid)


def upsert_file(conn: sqlite3.Connection, path: Path | str, metadata: dict[str, Any], track_id: int | None = None) -> int:
    normalized = normalize_path(path)
    values = {key: metadata.get(key) for key in FILE_FIELDS if key not in {"id", "path"}}
    values["track_id"] = track_id if track_id is not None else metadata.get("track_id")
    values["path_hash"] = values.get("path_hash") or _hash_text(normalized)
    values["updated_at"] = _now()
    values["created_at"] = values.get("created_at") or values["updated_at"]
    insert_fields = [key for key in FILE_FIELDS if key != "id"]
    row_values = {"path": normalized, **values}
    update_fields = [key for key in insert_fields if key not in {"path", "created_at"}]
    conn.execute(
        f"""
        INSERT INTO files({', '.join(insert_fields)})
        VALUES ({', '.join('?' for _ in insert_fields)})
        ON CONFLICT(path) DO UPDATE SET {', '.join(f'{key} = excluded.{key}' for key in update_fields)}
        """,
        [row_values.get(key) for key in insert_fields],
    )
    row = conn.execute("SELECT id FROM files WHERE path = ?", (normalized,)).fetchone()
    return int(row["id"])


def upsert_audio_features(conn: sqlite3.Connection, track_id: int, metadata: dict[str, Any]) -> int:
    values = {key: metadata.get(key) for key in AUDIO_FEATURE_FIELDS if key not in {"id", "track_id"}}
    values["track_id"] = track_id
    values["updated_at"] = _now()
    insert_fields = [key for key in AUDIO_FEATURE_FIELDS if key != "id"]
    update_fields = [key for key in insert_fields if key != "track_id"]
    row_values = {"track_id": track_id, **values}
    conn.execute(
        f"""
        INSERT INTO audio_features({', '.join(insert_fields)})
        VALUES ({', '.join('?' for _ in insert_fields)})
        ON CONFLICT(track_id) DO UPDATE SET {', '.join(f'{key} = excluded.{key}' for key in update_fields)}
        """,
        [row_values.get(key) for key in insert_fields],
    )
    row = conn.execute("SELECT id FROM audio_features WHERE track_id = ?", (track_id,)).fetchone()
    return int(row["id"])


def record_provider_run(conn: sqlite3.Connection, provider: str, target_type: str, target_id: str | int, status: str, query: str = "", config_hash: str = "", error: str = "", started_at: str | None = None, finished_at: str | None = None) -> int:
    cursor = conn.execute(
        """
        INSERT INTO provider_runs(provider, target_type, target_id, status, started_at, finished_at, query, config_hash, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (provider, target_type, str(target_id), status, started_at or _now(), finished_at or _now(), query, config_hash, error),
    )
    return int(cursor.lastrowid)


def record_candidate(conn: sqlite3.Connection, provider_run_id: int, provider: str, external_id: str, score: float | None = None, confidence: str = "", selected: bool = False, rejected_reason: str = "", payload_summary: dict[str, Any] | str | None = None) -> int:
    payload = payload_summary if isinstance(payload_summary, str) else json.dumps(payload_summary or {}, sort_keys=True)
    cursor = conn.execute(
        """
        INSERT INTO provider_candidates(provider_run_id, provider, external_id, score, confidence, selected, rejected_reason, payload_summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (provider_run_id, provider, external_id, score, confidence, 1 if selected else 0, rejected_reason, payload),
    )
    return int(cursor.lastrowid)


def record_field_decision(conn: sqlite3.Connection, provider_run_id: int, target_type: str, target_id: str | int, field: str, current_value: str = "", candidate_value: str = "", selected_value: str = "", provider: str = "", confidence: str = "", action: str = "", reason: str = "") -> int:
    cursor = conn.execute(
        """
        INSERT INTO field_decisions(provider_run_id, target_type, target_id, field, current_value, candidate_value, selected_value, provider, confidence, action, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (provider_run_id, target_type, str(target_id), field, current_value, candidate_value, selected_value, provider, confidence, action, reason),
    )
    return int(cursor.lastrowid)


def record_operation(conn: sqlite3.Connection, operation: str, target_type: str, target_id: str | int, mode: str, status: str, summary: str) -> int:
    return _record_operation(conn, operation, target_type, str(target_id), mode, status, summary)


def finish_operation(conn: sqlite3.Connection, operation_id: int, status: str) -> None:
    _finish_operation(conn, operation_id, status)


def scan_library(config: dict[str, Any], target: Path, apply: bool = False, verbose: bool = False) -> tuple[int, str]:
    files = audio_files(target)
    tracks: list[Track] = []
    errors: list[str] = []
    for path in files:
        try:
            tracks.append(read_track(path))
        except Exception as exc:  # mutagen can raise format-specific parse errors.
            errors.append(f"{path}: {exc}")
    plans = _scan_plans(config, target, tracks)
    mode = "APPLY" if apply else "DRY-RUN"
    path = database_path(config)
    lines = [f"Library DB: {path}", f"Mode: {mode}", ""]
    lines.append(f"[1/3] Discover files      OK     {len(files)} files")
    read_status = "OK" if not errors else "WARN"
    lines.append(f"[2/3] Read tags           {read_status:<6} {len(tracks)}/{len(files)}")
    if verbose and errors:
        lines.extend(f"- {error}" for error in errors)
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        with connect(config) as conn:
            apply_migrations(conn)
            before = get_counts(conn)
            op_id = _record_operation(conn, "db scan", "path", str(target), "apply", "running", f"{len(files)} files")
            marked_missing = _mark_missing_files(conn, target, {normalize_path(track.path) for track in tracks})
            skipped_unchanged = 0
            for track in tracks:
                if _file_is_unchanged(conn, track.path):
                    skipped_unchanged += 1
                    continue
                album_id = upsert_album(conn, _album_metadata(track))
                existing_track_id = _existing_track_for_path(conn, track.path)
                track_id = upsert_track(conn, {**_track_metadata(track), "id": existing_track_id} if existing_track_id else _track_metadata(track), album_id=album_id)
                upsert_file(conn, track.path, _file_metadata(track), track_id=track_id)
                upsert_audio_features(conn, track_id, _audio_feature_metadata(track))
            _finish_operation(conn, op_id, "ok")
            conn.commit()
            after = get_counts(conn)
        added = {key: after[key] - before[key] for key in ("albums", "tracks", "files")}
        lines.append(f"[3/3] Update database     OK     added {added['albums']} albums, {added['tracks']} tracks, {added['files']} files")
        if skipped_unchanged:
            lines.append(f"      Unchanged files     OK     skipped {skipped_unchanged}")
        if marked_missing:
            lines.append(f"      Missing files       OK     marked {marked_missing} missing")
    else:
        added = plans
        lines.append(f"[3/3] Update database     DRY    would add {added['albums']} albums, {added['tracks']} tracks, {added['files']} files")
        if added.get("missing_files", 0):
            lines.append(f"      Missing files       DRY    would mark {added['missing_files']} missing")
    lines.extend(["", f"Albums: +{added['albums']}", f"Tracks: +{added['tracks']}", f"Files: +{added['files']}", f"Status: {'WARN' if errors else 'OK'}"])
    return (1 if errors else 0), "\n".join(lines)


def record_organized_file(config: dict[str, Any], source: Path, destination: Path, track: Track, mode: str, status: str, summary: str) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        op_id = _record_operation(conn, "organize", "file", normalize_path(destination), mode, "running", summary)
        album_id = upsert_album(conn, _album_metadata(track))
        existing_track_id = _existing_track_for_path(conn, source) or _existing_track_for_path(conn, destination)
        track_id = upsert_track(conn, {**_track_metadata(track), "id": existing_track_id} if existing_track_id else _track_metadata(track), album_id=album_id)
        if normalize_path(source) != normalize_path(destination):
            conn.execute("DELETE FROM files WHERE path = ?", (normalize_path(destination),))
            conn.execute(
                "UPDATE files SET path = ?, path_hash = ?, updated_at = ? WHERE path = ?",
                (normalize_path(destination), _hash_text(normalize_path(destination)), _now(), normalize_path(source)),
            )
            destination_track = Track(destination, track.format, track.album, track.albumartist, track.artist, track.title, track.tracknumber, track.date, track.duration, track.tags)
            upsert_file(conn, destination, _file_metadata(destination_track), track_id=track_id)
        else:
            upsert_file(conn, destination, _file_metadata(track), track_id=track_id)
        upsert_audio_features(conn, track_id, _audio_feature_metadata(track))
        _finish_operation(conn, op_id, status.casefold())
        conn.commit()


def record_import_operation(config: dict[str, Any], target: Path, mode: str, status: str, summary: str) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        op_id = _record_operation(conn, "import", "path", normalize_path(target), mode, "running", summary)
        _finish_operation(conn, op_id, status.casefold())
        conn.commit()


def record_operation_start(conn: sqlite3.Connection, operation: str, target_type: str, target_id: str, mode: str, summary: str = "") -> int:
    return _record_operation(conn, operation, target_type, target_id, mode, "running", summary)


def record_operation_finish(conn: sqlite3.Connection, operation_id: int, status: str) -> None:
    _finish_operation(conn, operation_id, status.casefold())


def record_operation_summary(conn: sqlite3.Connection, operation_id: int, summary: str) -> None:
    conn.execute("UPDATE operations SET summary = ? WHERE id = ?", (summary, operation_id))


def render_status(status: dict[str, Any]) -> str:
    counts = status["counts"]
    lines = [
        "Library database",
        f"Path: {status['path']}",
        "Mode: READ-ONLY",
        f"Schema version: {status['version']}",
        f"Albums: {counts['albums']}",
        f"Tracks: {counts['tracks']}",
        f"Files: {counts['files']}",
        f"Missing files: {counts['missing_files']}",
    ]
    if status.get("last_operation"):
        op = status["last_operation"]
        lines.append(f"Last operation: {op['operation']} {op['mode']} {op['status']}")
    lines.append("Status: OK")
    return "\n".join(lines)


@dataclass(frozen=True)
class QueryTerm:
    field: str | None
    value: str
    negated: bool = False


@dataclass(frozen=True)
class QueryPlan:
    raw: str
    terms: tuple[QueryTerm, ...]


DB_QUERYABLE_FIELDS = {
    "album",
    "albumartist",
    "artist",
    "bpm",
    "catalog_number",
    "country",
    "cover",
    "danceability",
    "date",
    "disc",
    "duration",
    "energy",
    "format",
    "genre",
    "key",
    "label",
    "lyrics",
    "mb_album_id",
    "mb_release_group_id",
    "mb_track_id",
    "media",
    "mood",
    "originaldate",
    "path",
    "release_type",
    "replaygain",
    "style",
    "synced_lyrics",
    "title",
    "track",
}

QUERY_FIELDS = ({field.name for field in get_queryable_fields()} & DB_QUERYABLE_FIELDS) | {
    "has",
    "mbids",
    "missing",
    "provider",
    "rating",
    "review",
    "starred",
    "status",
    "year",
}

NUMERIC_QUERY_FIELDS = {"bpm", "energy", "danceability", "duration", "rating", "year", "track", "disc"}


def db_query(config: dict[str, Any], query: str, target: str = "tracks", missing_field: str | None = None, limit: int = 50, output_format: str = "text", verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, f"Database not initialized: {database_path(config)}"
    with conn:
        try:
            expression = query.strip()
            if missing_field:
                expression = " ".join(part for part in [expression, f"missing:{missing_field}"] if part)
            plan = parse_query(expression)
            rows = execute_query(conn, plan, target, limit)
        except ValueError as exc:
            message = str(exc)
            if message.startswith("Unknown field:"):
                return 1, message
            return 1, f"Invalid query: {message}"
        except sqlite3.DatabaseError as exc:
            return 1, f"Query failed: {exc}"
        return 0, format_query_results(plan, target, rows, output_format=output_format, verbose=verbose, debug=debug)


def db_explain(config: dict[str, Any], path: Path, field: str | None = None, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, f"Database not initialized: {database_path(config)}"
    with conn:
        target = _explain_target(conn, path)
        if target is None:
            return 1, f"Not found in database: {normalize_path(path)}"
        runs = _provider_runs_for_target(conn, target)
        decisions = _field_decisions_for_target(conn, target, field)
        candidates = _candidates_for_runs(conn, [int(row["id"]) for row in runs])
        return 0, _render_explain(target, runs, candidates, decisions, field=field, verbose=verbose, debug=debug)


def parse_query(query: str) -> QueryPlan:
    try:
        parts = shlex.split(query or "")
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    terms: list[QueryTerm] = []
    for part in parts:
        negated = False
        if part.startswith("-") and len(part) > 1:
            negated = True
            part = part[1:]
        if part.startswith("not:") and len(part) > 4:
            negated = True
            part = part[4:]
        if ":" not in part:
            terms.append(QueryTerm(None, part, negated))
            continue
        field, value = part.split(":", 1)
        field = _canonical_query_field(field)
        value = value.strip()
        if not value:
            raise ValueError(f"empty value for {field}")
        terms.append(QueryTerm(field, value, negated))
    return QueryPlan(raw=query or "", terms=tuple(terms))


def build_sql_query(plan: QueryPlan, scope: str) -> tuple[str, list[Any]]:
    where, params = _query_where(plan)
    if scope == "albums":
        sql = f"""
            SELECT a.id AS album_id, a.album, COALESCE(a.albumartist, '') AS albumartist, COUNT(f.id) AS tracks,
                   SUM(CASE WHEN {_has_missing_sql('lyrics', True)} THEN 1 ELSE 0 END) AS missing_lyrics,
                   SUM(CASE WHEN {_has_missing_sql('cover', True)} THEN 1 ELSE 0 END) AS missing_cover,
                   SUM(CASE WHEN {_has_missing_sql('key', True)} THEN 1 ELSE 0 END) AS missing_key,
                   SUM(CASE WHEN {_has_missing_sql('replaygain', True)} THEN 1 ELSE 0 END) AS missing_replaygain
            FROM files f
            LEFT JOIN tracks t ON t.id = f.track_id
            LEFT JOIN albums a ON a.id = t.album_id
            LEFT JOIN audio_features af ON af.track_id = t.id
            WHERE {where}
            GROUP BY a.id
            ORDER BY a.albumartist, a.album
            LIMIT ?
        """
    elif scope == "files":
        sql = f"""
            SELECT f.id AS file_id, t.id AS track_id, a.id AS album_id, f.path, f.status, t.title, t.artist, a.album,
                   COALESCE(a.albumartist, t.albumartist, '') AS albumartist,
                   (SELECT MAX(prb.rating) FROM player_rating_backups prb WHERE prb.library_track_id = t.id OR prb.library_file_id = f.id) AS rating,
                   CASE WHEN EXISTS (SELECT 1 FROM player_rating_backups prb WHERE (prb.library_track_id = t.id OR prb.library_file_id = f.id) AND COALESCE(prb.starred, 0) = 1) THEN 1 ELSE 0 END AS starred,
                   CASE WHEN {_has_missing_sql('lyrics', True)} THEN 1 ELSE 0 END AS missing_lyrics,
                   CASE WHEN {_has_missing_sql('cover', True)} THEN 1 ELSE 0 END AS missing_cover,
                   CASE WHEN {_has_missing_sql('key', True)} THEN 1 ELSE 0 END AS missing_key,
                   CASE WHEN {_has_missing_sql('replaygain', True)} THEN 1 ELSE 0 END AS missing_replaygain
            FROM files f
            LEFT JOIN tracks t ON t.id = f.track_id
            LEFT JOIN albums a ON a.id = t.album_id
            LEFT JOIN audio_features af ON af.track_id = t.id
            WHERE {where}
            ORDER BY f.path
            LIMIT ?
        """
    else:
        sql = f"""
            SELECT t.id AS track_id, f.id AS file_id, a.id AS album_id, t.track, t.disc, t.title, t.artist,
                   COALESCE(t.albumartist, a.albumartist, '') AS albumartist, a.album, f.path, f.status,
                   (SELECT MAX(prb.rating) FROM player_rating_backups prb WHERE prb.library_track_id = t.id OR prb.library_file_id = f.id) AS rating,
                   CASE WHEN EXISTS (SELECT 1 FROM player_rating_backups prb WHERE (prb.library_track_id = t.id OR prb.library_file_id = f.id) AND COALESCE(prb.starred, 0) = 1) THEN 1 ELSE 0 END AS starred,
                   CASE WHEN {_has_missing_sql('lyrics', True)} THEN 1 ELSE 0 END AS missing_lyrics,
                   CASE WHEN {_has_missing_sql('cover', True)} THEN 1 ELSE 0 END AS missing_cover,
                   CASE WHEN {_has_missing_sql('key', True)} THEN 1 ELSE 0 END AS missing_key,
                   CASE WHEN {_has_missing_sql('replaygain', True)} THEN 1 ELSE 0 END AS missing_replaygain
            FROM files f
            LEFT JOIN tracks t ON t.id = f.track_id
            LEFT JOIN albums a ON a.id = t.album_id
            LEFT JOIN audio_features af ON af.track_id = t.id
            WHERE {where}
            ORDER BY COALESCE(a.albumartist, t.albumartist, t.artist), a.album, COALESCE(t.disc, 1), COALESCE(t.track, 999), t.title, f.path
            LIMIT ?
        """
    return sql, params


def execute_query(conn: sqlite3.Connection, plan: QueryPlan, scope: str, limit: int) -> list[sqlite3.Row]:
    if scope not in {"albums", "tracks", "files"}:
        raise ValueError(f"unsupported scope: {scope}")
    sql, params = build_sql_query(plan, scope)
    return list(conn.execute(sql, params + [_safe_limit(limit)]))


def format_query_results(plan: QueryPlan, scope: str, rows: list[sqlite3.Row], output_format: str = "text", verbose: bool = False, debug: bool = False) -> str:
    if output_format == "json":
        return _render_query_json(plan, scope, rows)
    if output_format != "text":
        raise ValueError(f"unsupported format: {output_format}")
    if scope == "albums":
        body = _render_album_rows(rows, verbose=verbose, debug=debug)
        noun = "album" if len(rows) == 1 else "albums"
    elif scope == "files":
        body = _render_file_rows(rows, verbose=verbose, debug=debug)
        noun = "file" if len(rows) == 1 else "files"
    else:
        body = _render_track_rows(rows, verbose=verbose, debug=debug)
        noun = "track" if len(rows) == 1 else "tracks"
    return "\n".join([f"Query: {plan.raw}", f"Results: {len(rows)} {noun}", "", body, "", "Status: OK"])


def _canonical_query_field(field: str) -> str:
    resolved = resolve_field_alias(field)
    raw = field.strip().casefold().replace("-", "_")
    canonical = raw if raw in {"mbids", "year"} else resolved[0]
    if canonical not in QUERY_FIELDS:
        raise ValueError(f"Unknown field: {field}\nUse `noqlen-forge db query --help` or `noqlen-forge fields` to list supported fields.")
    return canonical


def _query_where(plan: QueryPlan) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for term in plan.terms:
        clause, clause_params = _term_sql(term)
        if term.negated:
            clause = f"NOT ({clause})"
        clauses.append(clause)
        params.extend(clause_params)
    return (" AND ".join(clauses) if clauses else "1 = 1"), params


def _term_sql(term: QueryTerm) -> tuple[str, list[Any]]:
    if term.field is None:
        return _free_text_sql(term.value)
    field = term.field
    value = term.value
    if field == "missing":
        return _has_missing_sql(_canonical_missing_field(value), missing=True), []
    if field == "has":
        return _has_missing_sql(_canonical_missing_field(value), missing=False), []
    if field in {"cover", "lyrics", "synced_lyrics", "replaygain"}:
        want = _parse_bool(value)
        return _has_missing_sql(field, missing=not want), []
    if field == "provider":
        return "(" + _provider_run_exists_sql("LOWER(pr.provider) = ?") + " OR " + _field_decision_exists_sql("LOWER(fd.provider) = ?") + ")", [value.casefold(), value.casefold()]
    if field == "review":
        want = _parse_bool(value)
        clause = "(" + _provider_run_exists_sql("LOWER(pr.status) = 'review'") + " OR " + _field_decision_exists_sql("LOWER(fd.action) = 'review' AND COALESCE(fd.resolved, 0) = 0") + ")"
        return clause if want else f"NOT ({clause})", []
    if field == "starred":
        clause = "EXISTS (SELECT 1 FROM player_rating_backups prb WHERE (prb.library_track_id = t.id OR prb.library_file_id = f.id) AND COALESCE(prb.starred, 0) = 1)"
        return clause if _parse_bool(value) else f"NOT ({clause})", []
    if field == "status":
        return "(LOWER(COALESCE(f.status, '')) = ? OR " + _provider_run_exists_sql("LOWER(pr.status) = ?") + ")", [value.casefold(), value.casefold()]
    if field in NUMERIC_QUERY_FIELDS:
        return _numeric_sql(field, value)
    return _text_field_sql(field, value)


def _free_text_sql(value: str) -> tuple[str, list[Any]]:
    like = f"%{value}%"
    return (
        "(t.artist LIKE ? OR t.albumartist LIKE ? OR a.albumartist LIKE ? OR a.album LIKE ? OR t.title LIKE ? OR f.path LIKE ? OR "
        + _tag_value_exists_sql("t.id", "a.id", keys=("genre", "style"))
        + ")",
        [like, like, like, like, like, like, like, like],
    )


def _text_field_sql(field: str, value: str) -> tuple[str, list[Any]]:
    like = f"%{value}%"
    if field == "artist":
        return "t.artist LIKE ?", [like]
    if field == "album":
        return "a.album LIKE ?", [like]
    if field == "albumartist":
        return "(t.albumartist LIKE ? OR a.albumartist LIKE ?)", [like, like]
    if field == "title":
        return "t.title LIKE ?", [like]
    if field == "path":
        return "f.path LIKE ?", [like]
    if field == "label":
        return "a.label LIKE ?", [like]
    if field == "country":
        return "a.country LIKE ?", [like]
    if field == "release_type":
        return "a.release_type LIKE ?", [like]
    if field == "media":
        return "(f.format LIKE ? OR f.codec LIKE ?)", [like, like]
    if field == "format":
        return "f.format LIKE ?", [like]
    if field == "catalog_number":
        return "a.catalog_number LIKE ?", [like]
    if field == "originaldate":
        return "a.originaldate LIKE ?", [like]
    if field == "date":
        return "(a.originaldate LIKE ? OR a.date LIKE ?)", [like, like]
    if field == "key":
        return "(t.key LIKE ? OR af.key LIKE ?)", [like, like]
    if field == "mood":
        return "t.mood LIKE ?", [like]
    if field in {"mb_album_id", "mb_release_group_id"}:
        return f"a.{field} LIKE ?", [like]
    if field == "mb_track_id":
        return "(t.mb_track_id LIKE ? OR t.mb_release_track_id LIKE ?)", [like, like]
    if field == "mbids":
        return "(a.mb_album_id LIKE ? OR a.mb_release_group_id LIKE ? OR t.mb_track_id LIKE ? OR t.mb_release_track_id LIKE ?)", [like, like, like, like]
    if field == "style":
        return _tag_key_sql(("style",), like)
    if field == "genre":
        return _tag_key_sql(("genre", "style"), like)
    raise ValueError(f"unsupported field: {field}")


def _numeric_sql(field: str, value: str) -> tuple[str, list[Any]]:
    match = re.fullmatch(r"(>=|<=|>|<)?\s*(-?\d+(?:\.\d+)?)", value)
    if not match:
        raise ValueError(f"invalid numeric filter for {field}: {value}")
    op = match.group(1) or "="
    number: Any = float(match.group(2)) if "." in match.group(2) else int(match.group(2))
    if field == "bpm":
        return f"(t.bpm {op} ? OR af.bpm {op} ?)", [number, number]
    if field == "energy":
        return f"(t.energy {op} ? OR af.energy {op} ?)", [number, number]
    if field == "danceability":
        return f"(t.danceability {op} ? OR af.danceability {op} ?)", [number, number]
    if field == "duration":
        return f"f.duration {op} ?", [number]
    if field == "rating":
        return f"EXISTS (SELECT 1 FROM player_rating_backups prb WHERE (prb.library_track_id = t.id OR prb.library_file_id = f.id) AND prb.rating {op} ?)", [number]
    if field == "track":
        return f"t.track {op} ?", [number]
    if field == "disc":
        return f"t.disc {op} ?", [number]
    return f"(CAST(substr(COALESCE(a.originaldate, a.date, ''), 1, 4) AS INTEGER) {op} ?)", [number]


def _parse_bool(value: str) -> bool:
    lowered = value.casefold()
    if lowered in {"1", "true", "yes", "y", "sim"}:
        return True
    if lowered in {"0", "false", "no", "n", "nao", "não"}:
        return False
    raise ValueError(f"expected true or false, got {value}")


def _canonical_missing_field(field: str) -> str:
    resolved = resolve_field_alias(field)
    raw = field.strip().casefold().replace("-", "_")
    canonical = raw if raw in {"mbids", "year"} else resolved[0]
    if canonical == "mbids":
        return "mbids"
    if canonical not in QUERY_FIELDS:
        raise ValueError(f"Unknown field: {field}\nUse `noqlen-forge db query --help` or `noqlen-forge fields` to list supported fields.")
    return canonical


def _has_missing_sql(field: str, missing: bool) -> str:
    field = field.strip().casefold()
    resolved = resolve_field_alias(field)
    field = field if field in {"mbids", "year"} else resolved[0]
    if field == "lyrics":
        expr = "(COALESCE(f.has_lyrics, 0) = 1 OR EXISTS (SELECT 1 FROM lyrics lx WHERE lx.track_id = t.id))"
    elif field in {"synced_lyrics", "syncedlyrics"}:
        expr = "(COALESCE(f.has_synced_lyrics, 0) = 1 OR EXISTS (SELECT 1 FROM lyrics lx WHERE lx.track_id = t.id AND COALESCE(lx.synced, 0) = 1))"
    elif field == "cover":
        expr = "(COALESCE(f.has_cover, 0) = 1 OR EXISTS (SELECT 1 FROM artwork aw WHERE aw.file_id = f.id OR aw.album_id = a.id))"
    elif field == "key":
        expr = "((t.key IS NOT NULL AND TRIM(t.key) != '') OR (af.key IS NOT NULL AND TRIM(af.key) != ''))"
    elif field == "mood":
        expr = "(t.mood IS NOT NULL AND TRIM(t.mood) != '')"
    elif field in {"replaygain", "replaygain_track", "replaygain_track_gain"}:
        expr = "(EXISTS (SELECT 1 FROM audio_features af WHERE af.track_id = t.id AND af.replaygain_track_gain IS NOT NULL AND af.replaygain_track_peak IS NOT NULL))"
    elif field in {"replaygain_album", "replaygain_album_gain"}:
        expr = "(EXISTS (SELECT 1 FROM audio_features af WHERE af.track_id = t.id AND af.replaygain_album_gain IS NOT NULL AND af.replaygain_album_peak IS NOT NULL))"
    elif field in {"loudness", "lufs"}:
        expr = "(EXISTS (SELECT 1 FROM audio_features af WHERE af.track_id = t.id AND af.loudness IS NOT NULL))"
    elif field == "style":
        expr = _tag_has_sql("style", "t.id", "a.id")
    elif field == "genre":
        expr = _tag_has_sql("genre", "t.id", "a.id")
    elif field in {"artist", "albumartist", "title"}:
        expr = f"(t.{field} IS NOT NULL AND TRIM(t.{field}) != '')"
    elif field == "album":
        expr = "(a.album IS NOT NULL AND TRIM(a.album) != '')"
    elif field == "label":
        expr = "(a.label IS NOT NULL AND TRIM(a.label) != '')"
    elif field == "mb_album_id":
        expr = "(a.mb_album_id IS NOT NULL AND TRIM(a.mb_album_id) != '')"
    elif field == "mb_track_id":
        expr = "((t.mb_track_id IS NOT NULL AND TRIM(t.mb_track_id) != '') OR (t.mb_release_track_id IS NOT NULL AND TRIM(t.mb_release_track_id) != ''))"
    elif field == "mb_release_group_id":
        expr = "(a.mb_release_group_id IS NOT NULL AND TRIM(a.mb_release_group_id) != '')"
    elif field == "mbids":
        expr = "((a.mb_album_id IS NOT NULL AND TRIM(a.mb_album_id) != '') AND (a.mb_release_group_id IS NOT NULL AND TRIM(a.mb_release_group_id) != '') AND ((t.mb_track_id IS NOT NULL AND TRIM(t.mb_track_id) != '') OR (t.mb_release_track_id IS NOT NULL AND TRIM(t.mb_release_track_id) != '')))"
    else:
        expr = "0"
    return f"NOT ({expr})" if missing else expr


def _tag_has_sql(key: str, track_id: str, album_id: str) -> str:
    return f"(EXISTS (SELECT 1 FROM track_tags tt WHERE tt.track_id = {track_id} AND LOWER(tt.key) = '{key}') OR EXISTS (SELECT 1 FROM album_tags at WHERE at.album_id = {album_id} AND LOWER(at.key) = '{key}'))"


def _tag_exists_sql(key: str, track_id: str, album_id: str) -> str:
    return f"(EXISTS (SELECT 1 FROM track_tags tt WHERE tt.track_id = {track_id} AND LOWER(tt.key) = '{key}' AND tt.value LIKE ?) OR EXISTS (SELECT 1 FROM album_tags at WHERE at.album_id = {album_id} AND LOWER(at.key) = '{key}' AND at.value LIKE ?))"


def _tag_value_exists_sql(track_id: str, album_id: str, keys: tuple[str, ...]) -> str:
    placeholders = ", ".join(repr(key) for key in keys)
    return f"(EXISTS (SELECT 1 FROM track_tags tt WHERE tt.track_id = {track_id} AND LOWER(tt.key) IN ({placeholders}) AND tt.value LIKE ?) OR EXISTS (SELECT 1 FROM album_tags at WHERE at.album_id = {album_id} AND LOWER(at.key) IN ({placeholders}) AND at.value LIKE ?))"


def _tag_key_sql(keys: tuple[str, ...], like: str) -> tuple[str, list[Any]]:
    placeholders = ", ".join(repr(key) for key in keys)
    return f"(EXISTS (SELECT 1 FROM track_tags tt WHERE tt.track_id = t.id AND LOWER(tt.key) IN ({placeholders}) AND tt.value LIKE ?) OR EXISTS (SELECT 1 FROM album_tags at WHERE at.album_id = a.id AND LOWER(at.key) IN ({placeholders}) AND at.value LIKE ?))", [like, like]


def _provider_run_exists_sql(condition: str) -> str:
    return f"EXISTS (SELECT 1 FROM provider_runs pr WHERE {condition} AND ((pr.target_type = 'album' AND pr.target_id = CAST(a.id AS TEXT)) OR (pr.target_type = 'track' AND pr.target_id = CAST(t.id AS TEXT)) OR (pr.target_type = 'file' AND pr.target_id = CAST(f.id AS TEXT))))"


def _field_decision_exists_sql(condition: str) -> str:
    return f"EXISTS (SELECT 1 FROM field_decisions fd WHERE {condition} AND ((fd.target_type = 'album' AND fd.target_id = CAST(a.id AS TEXT)) OR (fd.target_type = 'track' AND fd.target_id = CAST(t.id AS TEXT)) OR (fd.target_type = 'file' AND fd.target_id = CAST(f.id AS TEXT))))"


def _render_track_rows(rows: list[sqlite3.Row], verbose: bool = False, debug: bool = False) -> str:
    if not rows:
        return "No tracks found"
    lines = [f"{_fit('Title', 38)}  {_fit('Artist', 18)}  Album"]
    for row in rows:
        title = row["title"] or Path(row["path"] or "").name
        number = f"{int(row['track']):02d} " if row["track"] is not None else ""
        line = f"{_fit(number + title, 38)}  {_fit(row['artist'] or '', 18)}  {row['album'] or ''}"
        if verbose:
            line += f"  {row['path']}"
        if debug:
            line += f"  ids album={row['album_id']} track={row['track_id']} file={row['file_id']}"
        lines.append(line)
    return "\n".join(lines)


def _render_album_rows(rows: list[sqlite3.Row], verbose: bool = False, debug: bool = False) -> str:
    if not rows:
        return "No albums found"
    lines = [f"{_fit('Album', 30)}  {_fit('Album Artist', 20)}  Tracks  Missing"]
    for row in rows:
        missing = ",".join(_row_missing(row)) or "-"
        line = f"{_fit(row['album'] or '', 30)}  {_fit(row['albumartist'] or '', 20)}  {_fit(row['tracks'], 6)}  {missing}"
        if debug:
            line += f"  id={row['album_id']}"
        lines.append(line)
    return "\n".join(lines)


def _render_file_rows(rows: list[sqlite3.Row], verbose: bool = False, debug: bool = False) -> str:
    if not rows:
        return "No files found"
    lines = [f"{_fit('Path', 60)}  Track"]
    for row in rows:
        line = f"{_fit(row['path'] or '', 60)}  {row['title'] or ''}"
        if verbose:
            line += f"  {row['artist'] or ''}  {row['status'] or ''}"
        if debug:
            line += f"  ids album={row['album_id']} track={row['track_id']} file={row['file_id']}"
        lines.append(line)
    return "\n".join(lines)


def _render_query_json(plan: QueryPlan, scope: str, rows: list[sqlite3.Row]) -> str:
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if scope == "albums":
            safe = {
                "album": item.get("album") or "",
                "albumartist": item.get("albumartist") or "",
                "tracks": int(item.get("tracks") or 0),
                "missing": _row_missing(row),
            }
        elif scope == "files":
            safe = {
                "path": item.get("path") or "",
                "title": item.get("title") or "",
                "artist": item.get("artist") or "",
                "album": item.get("album") or "",
                "status": item.get("status") or "",
                "missing": _row_missing(row),
            }
        else:
            safe = {
                "title": item.get("title") or "",
                "artist": item.get("artist") or "",
                "album": item.get("album") or "",
                "albumartist": item.get("albumartist") or "",
                "path": item.get("path") or "",
                "missing": _row_missing(row),
            }
        results.append(safe)
    return json.dumps({"status": "OK", "query": plan.raw, "scope": scope, "count": len(rows), "results": results}, ensure_ascii=False, indent=2, sort_keys=True)


def _row_missing(row: sqlite3.Row) -> list[str]:
    missing: list[str] = []
    for field in ("lyrics", "cover", "key", "replaygain"):
        try:
            value = row[f"missing_{field}"]
        except (IndexError, KeyError):
            continue
        if int(value or 0) > 0:
            missing.append(field)
    return missing


def _render_missing(field: str, rows: list[sqlite3.Row]) -> str:
    title = field.replace('_', ' ').title()
    if not rows:
        return f"Missing {title}: none"
    lines = [f"Missing {title}:"]
    for row in rows:
        name = " - ".join(part for part in [row["albumartist"], row["album"]] if part)
        lines.append(f"- {name or 'Unknown Album'}: {row['missing_count']}/{row['total_count']} missing")
    return "\n".join(lines)


def _explain_target(conn: sqlite3.Connection, path: Path) -> dict[str, Any] | None:
    normalized = normalize_path(path)
    row = conn.execute(
        """
        SELECT f.id AS file_id, f.path, t.id AS track_id, t.title, t.artist, a.id AS album_id, a.album, a.albumartist
        FROM files f
        LEFT JOIN tracks t ON t.id = f.track_id
        LEFT JOIN albums a ON a.id = t.album_id
        WHERE f.path = ?
        """,
        (normalized,),
    ).fetchone()
    if row:
        return {"kind": "track", **dict(row)}
    prefix = normalized.rstrip("/") + "/%"
    album = conn.execute(
        """
        SELECT a.id AS album_id, a.album, a.albumartist, COUNT(f.id) AS files
        FROM files f
        LEFT JOIN tracks t ON t.id = f.track_id
        LEFT JOIN albums a ON a.id = t.album_id
        WHERE f.path LIKE ?
        GROUP BY a.id
        ORDER BY files DESC
        LIMIT 1
        """,
        (prefix,),
    ).fetchone()
    if album and album["album_id"] is not None:
        return {"kind": "album", **dict(album)}
    return None


def _provider_runs_for_target(conn: sqlite3.Connection, target: dict[str, Any]) -> list[sqlite3.Row]:
    clauses, params = _target_clauses("pr", target)
    return list(conn.execute(f"SELECT * FROM provider_runs pr WHERE {' OR '.join(clauses)} ORDER BY finished_at DESC, id DESC", params))


def _field_decisions_for_target(conn: sqlite3.Connection, target: dict[str, Any], field: str | None) -> list[sqlite3.Row]:
    clauses, params = _target_clauses("fd", target)
    extra = ""
    if field:
        extra = " AND (LOWER(fd.field) = ? OR LOWER(fd.provider) = ?)"
        params.extend([field.casefold(), field.casefold()])
    return list(conn.execute(f"SELECT fd.* FROM field_decisions fd WHERE ({' OR '.join(clauses)}){extra} ORDER BY fd.id", params))


def _target_clauses(alias: str, target: dict[str, Any]) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for target_type, key in (("album", "album_id"), ("track", "track_id"), ("file", "file_id")):
        if target.get(key) is not None:
            clauses.append(f"({alias}.target_type = ? AND {alias}.target_id = ?)")
            params.extend([target_type, str(target[key])])
    return clauses or ["1 = 0"], params


def _candidates_for_runs(conn: sqlite3.Connection, run_ids: list[int]) -> dict[int, list[sqlite3.Row]]:
    if not run_ids:
        return {}
    placeholders = ", ".join("?" for _ in run_ids)
    rows = list(conn.execute(f"SELECT * FROM provider_candidates WHERE provider_run_id IN ({placeholders}) ORDER BY selected DESC, score DESC, id", run_ids))
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(int(row["provider_run_id"]), []).append(row)
    return grouped


def _render_explain(target: dict[str, Any], runs: list[sqlite3.Row], candidates: dict[int, list[sqlite3.Row]], decisions: list[sqlite3.Row], field: str | None, verbose: bool, debug: bool) -> str:
    subject = f"Album: {target.get('albumartist') or ''} - {target.get('album') or ''}" if target["kind"] == "album" else f"Track: {target.get('artist') or ''} - {target.get('title') or ''}"
    lines = [subject.strip(), ""]
    if target.get("path"):
        lines.append(f"File: {target['path']}")
    if debug:
        ids = ", ".join(f"{key}={target[key]}" for key in ("album_id", "track_id", "file_id") if target.get(key) is not None)
        lines.append(f"IDs: {ids}")
    if lines[-1] != "":
        lines.append("")
    lines.append("Last enrich:")
    if runs:
        latest = runs[0]
        providers = ", ".join(dict.fromkeys(row["provider"] or "unknown" for row in runs))
        lines.append(f"- status: {str(latest['status'] or '').upper()}")
        lines.append(f"- providers: {providers}")
        lines.append(f"- date: {latest['finished_at'] or latest['started_at'] or ''}")
    else:
        lines.append("- none")
    if runs:
        lines.extend(["", "Provider runs:"])
        for run in runs:
            prefix = f"- {run['provider']}: {run['status']}"
            if debug:
                prefix += f" id={run['id']} target={run['target_type']}:{run['target_id']}"
            lines.append(prefix)
            if verbose and run["error"]:
                lines.append(f"  error: {_safe_output(run['error'], 'error')}")
            for candidate in candidates.get(int(run["id"]), []):
                state = "selected" if candidate["selected"] else "rejected"
                reason = f" reason={_safe_output(candidate['rejected_reason'], 'reason')}" if candidate["rejected_reason"] else ""
                lines.append(f"  candidate {candidate['provider']}:{candidate['external_id']} {state} score={candidate['score']} confidence={candidate['confidence']}{reason}")
    lines.extend(["", "Field decisions:"])
    if decisions:
        for decision in decisions:
            lines.append(f"- {str(decision['field'] or '').replace('_', ' ').title()}: {_safe_output(decision['selected_value'] or decision['current_value'] or decision['candidate_value'], decision['field'])}")
            lines.append(f"  provider: {decision['provider'] or 'unknown'}")
            lines.append(f"  action: {decision['action'] or 'unknown'}")
            if decision["confidence"]:
                lines.append(f"  confidence: {decision['confidence']}")
            if decision["current_value"]:
                lines.append(f"  current: {_safe_output(decision['current_value'], decision['field'])}")
            if decision["candidate_value"]:
                lines.append(f"  suggested: {_safe_output(decision['candidate_value'], decision['field'])}")
            if decision["reason"]:
                lines.append(f"  reason: {_safe_output(decision['reason'], 'reason')}")
            if debug:
                lines.append(f"  id: {decision['id']} run={decision['provider_run_id']}")
    else:
        label = f" for {field}" if field else ""
        lines.append(f"- none{label}")
    return "\n".join(lines)


def _safe_limit(limit: int) -> int:
    return max(1, min(int(limit or 50), 500))


def _fit(value: Any, width: int) -> str:
    text = str(value or "")
    if len(text) > width:
        return text[: max(0, width - 3)] + "..."
    return text.ljust(width)


def _safe_output(value: Any, field: Any) -> str:
    text = str(value or "")
    name = str(field or "").casefold()
    if name in {"lyrics", "unsyncedlyrics", "syncedlyrics"}:
        return "[lyrics hidden]"
    if any(secret in name for secret in ("api_key", "apikey", "token", "secret", "password")):
        return "[secret hidden]"
    lowered = text.casefold()
    for marker in ("api_key=", "apikey=", "token=", "secret=", "password="):
        if marker in lowered:
            return "[secret hidden]"
    return text if len(text) <= 300 else text[:297] + "..."


def normalize_path(path: Path | str) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_tag(track: Track, logical_name: str) -> str | None:
    values = get_tag(track, logical_name)
    return values[0] if values else None


def _int_tag(track: Track, logical_name: str) -> int | None:
    value = _first_tag(track, logical_name)
    if not value:
        return None
    try:
        return int(str(value).split("/", 1)[0])
    except ValueError:
        return None


def _float_tag(track: Track, logical_name: str) -> float | None:
    value = _first_tag(track, logical_name)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else None


def _album_metadata(track: Track) -> dict[str, Any]:
    return {
        "album": track.album,
        "albumartist": track.albumartist or track.artist,
        "date": track.date,
        "originaldate": _first_tag(track, "originaldate"),
        "mb_album_id": _first_tag(track, "mb_album_id"),
        "mb_release_group_id": _first_tag(track, "mb_release_group_id"),
        "label": _first_tag(track, "label"),
        "catalog_number": _first_tag(track, "catalog_number"),
        "barcode": _first_tag(track, "barcode"),
        "country": _first_tag(track, "country"),
        "release_format": _first_tag(track, "release_format"),
        "release_type": _first_tag(track, "release_type"),
        "edition": _first_tag(track, "edition"),
    }


def _track_metadata(track: Track) -> dict[str, Any]:
    return {
        "title": track.title,
        "artist": track.artist,
        "albumartist": track.albumartist,
        "track": track.tracknumber,
        "tracktotal": _int_tag(track, "tracktotal"),
        "disc": _int_tag(track, "disc"),
        "disctotal": _int_tag(track, "disctotal"),
        "mb_track_id": _first_tag(track, "mb_track_id"),
        "mb_release_track_id": _first_tag(track, "mb_release_track_id"),
        "acoustid_id": _first_tag(track, "acoustid_id"),
        "isrc": _first_tag(track, "isrc"),
        "bpm": _float_tag(track, "bpm"),
        "key": _first_tag(track, "key"),
        "mood": _first_tag(track, "mood"),
        "energy": _int_tag(track, "energy"),
        "danceability": _int_tag(track, "danceability"),
    }


def _audio_feature_metadata(track: Track) -> dict[str, Any]:
    return {
        "bpm": _float_tag(track, "bpm"),
        "key": _first_tag(track, "key"),
        "replaygain_track_gain": _float_tag(track, "replaygain_track_gain"),
        "replaygain_track_peak": _float_tag(track, "replaygain_track_peak"),
        "replaygain_album_gain": _float_tag(track, "replaygain_album_gain"),
        "replaygain_album_peak": _float_tag(track, "replaygain_album_peak"),
        "loudness": _float_tag(track, "loudness"),
        "energy": _int_tag(track, "energy"),
        "danceability": _int_tag(track, "danceability"),
        "source": "tags",
        "confidence": "tag",
    }


def _file_metadata(track: Track) -> dict[str, Any]:
    from .cover import detect_embedded_cover

    stat = track.path.stat()
    return {
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "tag_mtime": stat.st_mtime,
        "db_mtime": datetime.now(UTC).timestamp(),
        "format": track.format,
        "duration": track.duration,
        "has_cover": 1 if get_tag(track, "cover") or detect_embedded_cover(track.path) else 0,
        "has_lyrics": 1 if get_tag(track, "lyrics") else 0,
        "has_synced_lyrics": 1 if get_tag(track, "synced_lyrics") else 0,
        "status": "active",
    }


def _album_key(metadata: dict[str, Any]) -> str:
    explicit = _text(metadata.get("album_key"))
    if explicit:
        return explicit
    mb_album_id = _text(metadata.get("mb_album_id"))
    if mb_album_id:
        return f"mb:{mb_album_id}"
    parts = [metadata.get("albumartist") or "", metadata.get("album") or "", metadata.get("date") or ""]
    return "text:" + "|".join(str(part).strip().casefold() for part in parts)


def _scan_plans(config: dict[str, Any], target: Path, tracks: list[Track]) -> dict[str, int]:
    album_keys = {_album_key(_album_metadata(track)) for track in tracks}
    file_paths = {normalize_path(track.path) for track in tracks}
    plans = {"albums": len(album_keys), "tracks": len(tracks), "files": len(file_paths), "missing_files": 0}
    conn = connect_readonly(config)
    if conn is None:
        return plans
    with conn:
        existing_albums = _existing_values(conn, "albums", "album_key", album_keys)
        existing_files = _existing_values(conn, "files", "path", file_paths)
        plans["missing_files"] = _missing_file_count(conn, target, file_paths)
    plans["albums"] = len(album_keys - existing_albums)
    plans["files"] = len(file_paths - existing_files)
    plans["tracks"] = plans["files"]
    return plans


def _missing_file_count(conn: sqlite3.Connection, target: Path, active_paths: set[str]) -> int:
    clauses, params = _target_path_filter(target)
    rows = conn.execute(f"SELECT path FROM files WHERE status = 'active' AND {clauses}", params).fetchall()
    return sum(1 for row in rows if row["path"] not in active_paths and not Path(row["path"]).exists())


def _existing_values(conn: sqlite3.Connection, table: str, column: str, values: set[str]) -> set[str]:
    if not values:
        return set()
    placeholders = ", ".join("?" for _ in values)
    rows = conn.execute(f"SELECT {column} FROM {table} WHERE {column} IN ({placeholders})", list(values)).fetchall()
    return {str(row[column]) for row in rows if row[column] is not None}


def _mark_missing_files(conn: sqlite3.Connection, target: Path, active_paths: set[str]) -> int:
    clauses, params = _target_path_filter(target)
    rows = conn.execute(f"SELECT path FROM files WHERE status = 'active' AND {clauses}", params).fetchall()
    missing = [row["path"] for row in rows if row["path"] not in active_paths and not Path(row["path"]).exists()]
    if not missing:
        return 0
    placeholders = ", ".join("?" for _ in missing)
    conn.execute(f"UPDATE files SET status = 'missing', updated_at = ? WHERE path IN ({placeholders})", [_now(), *missing])
    return len(missing)


def _target_path_filter(target: Path) -> tuple[str, list[str]]:
    normalized = normalize_path(target)
    if target.is_file():
        return "path = ?", [normalized]
    return "(path = ? OR path LIKE ?)", [normalized, normalized.rstrip("/") + "/%"]


def _existing_track_for_path(conn: sqlite3.Connection, path: Path) -> int | None:
    row = conn.execute("SELECT track_id FROM files WHERE path = ?", (normalize_path(path),)).fetchone()
    if row and row["track_id"] is not None:
        return int(row["track_id"])
    return None


def _file_is_unchanged(conn: sqlite3.Connection, path: Path) -> bool:
    row = conn.execute("SELECT size, mtime, track_id, status FROM files WHERE path = ?", (normalize_path(path),)).fetchone()
    if row is None or row["track_id"] is None or row["status"] != "active":
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    return row["size"] == stat.st_size and float(row["mtime"] or -1) == stat.st_mtime


def _record_operation(conn: sqlite3.Connection, operation: str, target_type: str, target_id: str, mode: str, status: str, summary: str) -> int:
    cursor = conn.execute(
        "INSERT INTO operations(operation, target_type, target_id, mode, status, started_at, summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (operation, target_type, target_id, mode, status, _now(), summary),
    )
    return int(cursor.lastrowid)


def _finish_operation(conn: sqlite3.Connection, operation_id: int, status: str) -> None:
    conn.execute("UPDATE operations SET status = ?, finished_at = ? WHERE id = ?", (status, _now(), operation_id))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


ALBUM_FIELDS = ["id", "album_key", "album", "albumartist", "date", "originaldate", "mb_album_id", "mb_release_group_id", "discogs_release_id", "label", "catalog_number", "barcode", "country", "release_format", "release_type", "edition", "status", "created_at", "updated_at"]
TRACK_FIELDS = ["id", "album_id", "title", "artist", "albumartist", "track", "tracktotal", "disc", "disctotal", "mb_track_id", "mb_release_track_id", "acoustid_id", "isrc", "bpm", "key", "mood", "energy", "danceability", "status", "created_at", "updated_at"]
FILE_FIELDS = ["id", "track_id", "path", "path_hash", "size", "mtime", "tag_mtime", "db_mtime", "format", "codec", "bitrate", "sample_rate", "channels", "duration", "has_cover", "has_lyrics", "has_synced_lyrics", "status", "created_at", "updated_at"]
AUDIO_FEATURE_FIELDS = ["id", "track_id", "bpm", "key", "replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak", "loudness", "energy", "danceability", "source", "confidence", "updated_at"]


MIGRATIONS = [
    (
        1,
        "initial_library_schema",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS albums (
            id INTEGER PRIMARY KEY,
            album_key TEXT UNIQUE,
            album TEXT,
            albumartist TEXT,
            date TEXT,
            originaldate TEXT,
            mb_album_id TEXT,
            mb_release_group_id TEXT,
            discogs_release_id TEXT,
            label TEXT,
            catalog_number TEXT,
            barcode TEXT,
            country TEXT,
            release_format TEXT,
            release_type TEXT,
            edition TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY,
            album_id INTEGER REFERENCES albums(id) ON DELETE SET NULL,
            title TEXT,
            artist TEXT,
            albumartist TEXT,
            track INTEGER,
            tracktotal INTEGER,
            disc INTEGER,
            disctotal INTEGER,
            mb_track_id TEXT,
            mb_release_track_id TEXT,
            acoustid_id TEXT,
            isrc TEXT,
            bpm REAL,
            key TEXT,
            mood TEXT,
            energy INTEGER,
            danceability INTEGER,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            track_id INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
            path TEXT UNIQUE NOT NULL,
            path_hash TEXT,
            size INTEGER,
            mtime REAL,
            tag_mtime REAL,
            db_mtime REAL,
            format TEXT,
            codec TEXT,
            bitrate INTEGER,
            sample_rate INTEGER,
            channels INTEGER,
            duration REAL,
            has_cover INTEGER DEFAULT 0,
            has_lyrics INTEGER DEFAULT 0,
            has_synced_lyrics INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS track_tags (
            id INTEGER PRIMARY KEY,
            track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
            key TEXT,
            value TEXT,
            type TEXT,
            source TEXT,
            confidence TEXT,
            updated_at TEXT,
            UNIQUE(track_id, key, value)
        );

        CREATE TABLE IF NOT EXISTS album_tags (
            id INTEGER PRIMARY KEY,
            album_id INTEGER REFERENCES albums(id) ON DELETE CASCADE,
            key TEXT,
            value TEXT,
            type TEXT,
            source TEXT,
            confidence TEXT,
            updated_at TEXT,
            UNIQUE(album_id, key, value)
        );

        CREATE TABLE IF NOT EXISTS provider_runs (
            id INTEGER PRIMARY KEY,
            provider TEXT,
            target_type TEXT,
            target_id TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            query TEXT,
            config_hash TEXT,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS provider_candidates (
            id INTEGER PRIMARY KEY,
            provider_run_id INTEGER REFERENCES provider_runs(id) ON DELETE CASCADE,
            provider TEXT,
            external_id TEXT,
            score REAL,
            confidence TEXT,
            selected INTEGER DEFAULT 0,
            rejected_reason TEXT,
            payload_summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS field_decisions (
            id INTEGER PRIMARY KEY,
            provider_run_id INTEGER REFERENCES provider_runs(id) ON DELETE CASCADE,
            target_type TEXT,
            target_id TEXT,
            field TEXT,
            current_value TEXT,
            candidate_value TEXT,
            selected_value TEXT,
            provider TEXT,
            confidence TEXT,
            action TEXT,
            reason TEXT
        );

        CREATE TABLE IF NOT EXISTS artwork (
            id INTEGER PRIMARY KEY,
            album_id INTEGER REFERENCES albums(id) ON DELETE CASCADE,
            file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
            source TEXT,
            provider TEXT,
            mime TEXT,
            width INTEGER,
            height INTEGER,
            size_bytes INTEGER,
            embedded INTEGER,
            path TEXT,
            hash TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS lyrics (
            id INTEGER PRIMARY KEY,
            track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
            source TEXT,
            provider TEXT,
            synced INTEGER,
            embedded INTEGER,
            sidecar_path TEXT,
            text_hash TEXT,
            language TEXT,
            confidence TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS lyrics_provider_cache (
            id INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            title TEXT,
            artist TEXT,
            album TEXT,
            duration REAL,
            status TEXT,
            confidence TEXT,
            synced INTEGER,
            text_hash TEXT,
            source_url TEXT,
            cached_at TEXT,
            raw_summary_json TEXT,
            UNIQUE(provider, query_hash)
        );

        CREATE TABLE IF NOT EXISTS audio_features (
            id INTEGER PRIMARY KEY,
            track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE CASCADE,
            bpm REAL,
            key TEXT,
            replaygain_track_gain REAL,
            replaygain_track_peak REAL,
            loudness REAL,
            energy INTEGER,
            danceability INTEGER,
            source TEXT,
            confidence TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS fingerprints (
            id INTEGER PRIMARY KEY,
            track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
            file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
            acoustid_id TEXT,
            fingerprint TEXT,
            duration REAL,
            source TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS operations (
            id INTEGER PRIMARY KEY,
            operation TEXT,
            target_type TEXT,
            target_id TEXT,
            mode TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            summary TEXT,
            reversible INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
        CREATE INDEX IF NOT EXISTS idx_files_track_id ON files(track_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks(album_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);
        CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
        CREATE INDEX IF NOT EXISTS idx_albums_albumartist_album ON albums(albumartist, album);
        CREATE INDEX IF NOT EXISTS idx_albums_mb_album_id ON albums(mb_album_id);
        CREATE INDEX IF NOT EXISTS idx_albums_mb_release_group_id ON albums(mb_release_group_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_mb_track_id ON tracks(mb_track_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_acoustid_id ON tracks(acoustid_id);
        CREATE INDEX IF NOT EXISTS idx_track_tags_key_value ON track_tags(key, value);
        CREATE INDEX IF NOT EXISTS idx_album_tags_key_value ON album_tags(key, value);
        CREATE INDEX IF NOT EXISTS idx_provider_candidates_provider_external_id ON provider_candidates(provider, external_id);
        CREATE INDEX IF NOT EXISTS idx_field_decisions_field_provider ON field_decisions(field, provider);
        """,
    )
    ,
    (
        2,
        "audio_features_replaygain_album",
        """
        ALTER TABLE audio_features ADD COLUMN replaygain_album_gain REAL;
        ALTER TABLE audio_features ADD COLUMN replaygain_album_peak REAL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_audio_features_track_id ON audio_features(track_id);
        """,
    ),
    (
        3,
        "manual_review_resolution",
        """
        ALTER TABLE field_decisions ADD COLUMN resolved_at TEXT;
        ALTER TABLE field_decisions ADD COLUMN resolved_action TEXT;
        ALTER TABLE field_decisions ADD COLUMN resolved_by TEXT DEFAULT 'manual';
        ALTER TABLE field_decisions ADD COLUMN resolved INTEGER DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_field_decisions_resolved ON field_decisions(resolved, action);
        """,
    ),
    (
        4,
        "repair_stale_status",
        """
        ALTER TABLE albums ADD COLUMN status TEXT DEFAULT 'active';
        ALTER TABLE tracks ADD COLUMN status TEXT DEFAULT 'active';
        """,
    ),
    (
        5,
        "player_rating_backups",
        """
        CREATE TABLE IF NOT EXISTS player_accounts (
            id INTEGER PRIMARY KEY,
            player TEXT NOT NULL,
            name TEXT,
            base_url TEXT,
            username TEXT,
            server_id TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(player, base_url, username)
        );

        CREATE TABLE IF NOT EXISTS player_rating_backups (
            id INTEGER PRIMARY KEY,
            player_account_id INTEGER REFERENCES player_accounts(id) ON DELETE CASCADE,
            player TEXT NOT NULL,
            user TEXT,
            navidrome_id TEXT,
            library_track_id INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
            library_file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
            identity_key TEXT,
            identity_method TEXT,
            identity_confidence TEXT,
            match_confidence TEXT,
            match_reason TEXT,
            title TEXT,
            artist TEXT,
            album TEXT,
            albumartist TEXT,
            duration REAL,
            rating REAL,
            starred INTEGER,
            starred_at TEXT,
            play_count INTEGER,
            last_played TEXT,
            path TEXT,
            raw_summary_json TEXT,
            backed_up_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(player_account_id, navidrome_id)
        );

        CREATE TABLE IF NOT EXISTS player_rating_backup_runs (
            id INTEGER PRIMARY KEY,
            player_account_id INTEGER REFERENCES player_accounts(id) ON DELETE CASCADE,
            mode TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            total_items INTEGER,
            matched_items INTEGER,
            unmatched_items INTEGER,
            rated_items INTEGER,
            starred_items INTEGER,
            error TEXT,
            summary TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_player_rating_backups_identity_key ON player_rating_backups(identity_key);
        CREATE INDEX IF NOT EXISTS idx_player_rating_backups_navidrome_id ON player_rating_backups(navidrome_id);
        CREATE INDEX IF NOT EXISTS idx_player_rating_backups_library_track_id ON player_rating_backups(library_track_id);
        CREATE INDEX IF NOT EXISTS idx_player_rating_backups_rating ON player_rating_backups(rating);
        CREATE INDEX IF NOT EXISTS idx_player_rating_backups_starred ON player_rating_backups(starred);
        """,
    ),
    (
        6,
        "player_rating_restore_runs",
        """
        CREATE TABLE IF NOT EXISTS player_rating_restore_runs (
            id INTEGER PRIMARY KEY,
            player_account_id INTEGER REFERENCES player_accounts(id) ON DELETE CASCADE,
            mode TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            total_items INTEGER,
            planned_rating_updates INTEGER,
            applied_rating_updates INTEGER,
            planned_star_updates INTEGER,
            applied_star_updates INTEGER,
            skipped_items INTEGER,
            conflict_items INTEGER,
            error TEXT,
            summary TEXT
        );

        CREATE TABLE IF NOT EXISTS player_rating_restore_actions (
            id INTEGER PRIMARY KEY,
            restore_run_id INTEGER REFERENCES player_rating_restore_runs(id) ON DELETE CASCADE,
            backup_id INTEGER REFERENCES player_rating_backups(id) ON DELETE SET NULL,
            navidrome_id TEXT,
            action TEXT,
            old_value TEXT,
            new_value TEXT,
            match_confidence TEXT,
            status TEXT,
            reason TEXT,
            applied_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_player_rating_restore_actions_run ON player_rating_restore_actions(restore_run_id);
        CREATE INDEX IF NOT EXISTS idx_player_rating_restore_actions_backup ON player_rating_restore_actions(backup_id);
        """,
    ),
    (
        7,
        "smart_playlists",
        """
        CREATE TABLE IF NOT EXISTS smart_playlists (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            query TEXT NOT NULL,
            default_format TEXT DEFAULT 'm3u8',
            sort TEXT,
            reverse INTEGER DEFAULT 0,
            limit_count INTEGER,
            path_mode TEXT DEFAULT 'absolute',
            library_root TEXT,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS smart_playlist_exports (
            id INTEGER PRIMARY KEY,
            smart_playlist_id INTEGER REFERENCES smart_playlists(id) ON DELETE CASCADE,
            output_path TEXT,
            format TEXT,
            track_count INTEGER,
            status TEXT,
            exported_at TEXT NOT NULL,
            summary TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_smart_playlists_name ON smart_playlists(name);
        CREATE INDEX IF NOT EXISTS idx_smart_playlist_exports_playlist ON smart_playlist_exports(smart_playlist_id);
        CREATE INDEX IF NOT EXISTS idx_smart_playlist_exports_exported_at ON smart_playlist_exports(exported_at);
        """,
    ),
    (
        8,
        "navidrome_playlist_push_runs",
        """
        CREATE TABLE IF NOT EXISTS navidrome_playlist_push_runs (
            id INTEGER PRIMARY KEY,
            player_account_id INTEGER REFERENCES player_accounts(id) ON DELETE CASCADE,
            playlist_id TEXT,
            playlist_name TEXT,
            mode TEXT,
            policy TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            local_tracks INTEGER,
            matched_tracks INTEGER,
            unmatched_tracks INTEGER,
            added_tracks INTEGER,
            removed_tracks INTEGER,
            error TEXT,
            summary TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_navidrome_playlist_push_runs_account ON navidrome_playlist_push_runs(player_account_id);
        CREATE INDEX IF NOT EXISTS idx_navidrome_playlist_push_runs_playlist ON navidrome_playlist_push_runs(playlist_id);
        """,
    ),
    (
        9,
        "navidrome_playlist_backups",
        """
        CREATE TABLE IF NOT EXISTS navidrome_playlist_backup_runs (
            id INTEGER PRIMARY KEY,
            player_account_id INTEGER REFERENCES player_accounts(id) ON DELETE CASCADE,
            mode TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            total_playlists INTEGER,
            total_items INTEGER,
            matched_items INTEGER,
            unmatched_items INTEGER,
            error TEXT,
            summary TEXT
        );

        CREATE TABLE IF NOT EXISTS navidrome_playlist_backups (
            id INTEGER PRIMARY KEY,
            player_account_id INTEGER REFERENCES player_accounts(id) ON DELETE CASCADE,
            navidrome_playlist_id TEXT NOT NULL,
            name TEXT NOT NULL,
            owner TEXT,
            comment TEXT,
            song_count INTEGER,
            duration REAL,
            public INTEGER,
            changed_at TEXT,
            created_at_remote TEXT,
            backed_up_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            raw_summary_json TEXT,
            UNIQUE(player_account_id, navidrome_playlist_id)
        );

        CREATE TABLE IF NOT EXISTS navidrome_playlist_items (
            id INTEGER PRIMARY KEY,
            playlist_backup_id INTEGER REFERENCES navidrome_playlist_backups(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            navidrome_song_id TEXT,
            library_track_id INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
            library_file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
            identity_key TEXT,
            identity_method TEXT,
            identity_confidence TEXT,
            match_confidence TEXT,
            match_reason TEXT,
            title TEXT,
            artist TEXT,
            album TEXT,
            albumartist TEXT,
            duration REAL,
            path TEXT,
            raw_summary_json TEXT,
            backed_up_at TEXT NOT NULL,
            UNIQUE(playlist_backup_id, position)
        );

        CREATE INDEX IF NOT EXISTS idx_navidrome_playlist_backups_account ON navidrome_playlist_backups(player_account_id);
        CREATE INDEX IF NOT EXISTS idx_navidrome_playlist_backups_playlist ON navidrome_playlist_backups(navidrome_playlist_id);
        CREATE INDEX IF NOT EXISTS idx_navidrome_playlist_backups_name ON navidrome_playlist_backups(name);
        CREATE INDEX IF NOT EXISTS idx_navidrome_playlist_items_backup ON navidrome_playlist_items(playlist_backup_id);
        CREATE INDEX IF NOT EXISTS idx_navidrome_playlist_items_song ON navidrome_playlist_items(navidrome_song_id);
        CREATE INDEX IF NOT EXISTS idx_navidrome_playlist_items_identity ON navidrome_playlist_items(identity_key);
        CREATE INDEX IF NOT EXISTS idx_navidrome_playlist_items_track ON navidrome_playlist_items(library_track_id);
        """,
    ),
    (
        10,
        "lyrics_provider_cache",
        """
        CREATE TABLE IF NOT EXISTS lyrics_provider_cache (
            id INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            title TEXT,
            artist TEXT,
            album TEXT,
            duration REAL,
            status TEXT,
            confidence TEXT,
            synced INTEGER,
            text_hash TEXT,
            source_url TEXT,
            cached_at TEXT,
            raw_summary_json TEXT,
            UNIQUE(provider, query_hash)
        );
        """,
    ),
    (
        11,
        "lyrics_selection_reason",
        """
        ALTER TABLE lyrics ADD COLUMN selection_reason TEXT;
        """,
    ),
    (
        12,
        "core_query_indexes",
        """
        CREATE INDEX IF NOT EXISTS idx_files_status_path ON files(status, path);
        CREATE INDEX IF NOT EXISTS idx_provider_runs_target ON provider_runs(target_type, target_id, finished_at);
        CREATE INDEX IF NOT EXISTS idx_field_decisions_target_field ON field_decisions(target_type, target_id, field, resolved);
        CREATE INDEX IF NOT EXISTS idx_operations_operation_started ON operations(operation, started_at);
        """,
    ),
    (
        13,
        "jobs",
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            target TEXT,
            target_type TEXT,
            mode TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL,
            progress_current INTEGER DEFAULT 0,
            progress_total INTEGER DEFAULT 0,
            progress_label TEXT,
            resumable INTEGER DEFAULT 0,
            cancelable INTEGER DEFAULT 1,
            canceled_at TEXT,
            error TEXT,
            summary TEXT,
            options_json TEXT,
            result_json TEXT
        );

        CREATE TABLE IF NOT EXISTS job_steps (
            id INTEGER PRIMARY KEY,
            job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
            step_index INTEGER,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            elapsed_seconds REAL,
            summary TEXT,
            details_json TEXT,
            warnings_json TEXT,
            errors_json TEXT
        );

        CREATE TABLE IF NOT EXISTS job_events (
            id INTEGER PRIMARY KEY,
            job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT,
            data_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_kind ON jobs(kind);
        CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
        CREATE INDEX IF NOT EXISTS idx_job_steps_job_id ON job_steps(job_id);
        CREATE INDEX IF NOT EXISTS idx_job_events_job_id_created_at ON job_events(job_id, created_at);
        """,
    ),
]
