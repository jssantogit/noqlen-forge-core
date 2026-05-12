from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
import sqlite3
from typing import Any

from .config import get_config_value
from .db import connect_readonly, database_path, execute_query, normalize_path, parse_query
from .duplicates import duplicates_path
from .fields import get_field, resolve_field_alias, supported_field_names
from .reports import missing_report
from .safety import SafetyError, automated_validation_enabled, is_dangerous_real_library_path, is_noqlen_forge_lab_path

EXPORT_FORMATS = {"json", "csv"}
EXPORT_SCOPES = {"albums", "tracks", "files"}
SENSITIVE_EXACT_FIELDS = {"lyrics", "synced_lyrics", "syncedlyrics", "unsyncedlyrics", "fingerprint", "fingerprints", "text_hash", "payload_summary_json"}
SENSITIVE_NAME_MARKERS = ("api_key", "apikey", "token", "secret", "password", "fingerprint")

TRACK_CSV_FIELDS = [
    "path",
    "title",
    "artist",
    "album",
    "albumartist",
    "track",
    "disc",
    "date",
    "originaldate",
    "genre",
    "style",
    "mood",
    "bpm",
    "key",
    "energy",
    "danceability",
    "replaygain_track_gain",
    "replaygain_album_gain",
    "has_cover",
    "has_lyrics",
    "has_synced_lyrics",
    "mb_album_id",
    "mb_track_id",
    "mb_release_group_id",
    "acoustid_id",
    "label",
    "catalog_number",
    "release_type",
]

ALBUM_CSV_FIELDS = [
    "album",
    "albumartist",
    "date",
    "originaldate",
    "tracks",
    "label",
    "catalog_number",
    "barcode",
    "country",
    "release_type",
    "mb_album_id",
    "mb_release_group_id",
    "has_cover_count",
    "has_lyrics_count",
    "missing_fields",
]

FILE_CSV_FIELDS = ["path", "title", "artist", "album", "albumartist", "format", "duration", "status", "has_cover", "has_lyrics", "has_synced_lyrics"]


def export_data(
    config: dict[str, Any],
    query: str | None = None,
    *,
    export_format: str = "json",
    output: Path | None = None,
    force: bool = False,
    scope: str = "tracks",
    all_data: bool = False,
    missing: str | None = None,
    duplicates: bool = False,
    reviews: bool = False,
    library: bool = False,
    fields: str | None = None,
    exclude_fields: str | None = None,
    include_tags: bool = False,
    include_audio: bool = False,
    include_assets: bool = False,
    include_provider_history: bool = False,
    verbose: bool = False,
    debug: bool = False,
) -> tuple[int, str]:
    if export_format not in EXPORT_FORMATS:
        return 1, f"Unsupported export format: {export_format}"
    selected = [bool(query), all_data, bool(missing), duplicates, reviews, library]
    if sum(1 for item in selected if item) != 1:
        return 1, "Choose exactly one export target: QUERY, --all, --missing, --duplicates, --reviews, or --library"
    if scope not in EXPORT_SCOPES:
        return 1, f"Unsupported export scope: {scope}"

    code, payload = _build_payload(
        config,
        query=query,
        scope=scope,
        all_data=all_data,
        missing=missing,
        duplicates=duplicates,
        reviews=reviews,
        library=library,
        include_tags=include_tags,
        include_audio=include_audio,
        include_assets=include_assets,
        include_provider_history=include_provider_history,
        verbose=verbose,
        debug=debug,
    )
    if code != 0:
        return code, str(payload)
    try:
        rows, field_order = _rows_for_csv(payload, fields=fields, exclude_fields=exclude_fields)
    except ValueError as exc:
        return 1, str(exc)
    rendered = _render_json(payload, fields=fields, exclude_fields=exclude_fields) if export_format == "json" else _render_csv(rows, field_order)
    if output is None:
        return 0, rendered
    try:
        written = _write_output(output, rendered, export_format, force=force)
    except (OSError, SafetyError) as exc:
        return 1, str(exc)
    lines = [f"Export: {payload.get('type', 'library')}", f"Format: {export_format.upper()}", f"Rows: {len(rows)}", f"Output: {written}", f"Status: {payload.get('status', 'OK')}"]
    return 0, "\n".join(lines)


def _build_payload(config: dict[str, Any], **kwargs: Any) -> tuple[int, dict[str, Any] | str]:
    if kwargs.get("missing"):
        return _missing_payload(config, str(kwargs["missing"]), verbose=kwargs["verbose"], debug=kwargs["debug"])
    if kwargs.get("duplicates"):
        return _duplicates_payload(config, verbose=kwargs["verbose"], debug=kwargs["debug"])
    if kwargs.get("reviews"):
        return _reviews_payload(config)
    if kwargs.get("library") or kwargs.get("all_data"):
        return _library_payload(config, include_provider_history=kwargs["include_provider_history"])
    return _query_payload(
        config,
        str(kwargs.get("query") or ""),
        scope=str(kwargs.get("scope") or "tracks"),
        include_tags=kwargs["include_tags"],
        include_audio=kwargs["include_audio"],
        include_assets=kwargs["include_assets"],
        include_provider_history=kwargs["include_provider_history"],
    )


def _query_payload(config: dict[str, Any], query: str, *, scope: str, include_tags: bool, include_audio: bool, include_assets: bool, include_provider_history: bool) -> tuple[int, dict[str, Any] | str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, f"Database not initialized: {database_path(config)}"
    try:
        plan = parse_query(query.strip())
        with conn:
            base_rows = execute_query(conn, plan, scope, 100000)
            ids = [int(row[f"{scope[:-1]}_id"]) for row in base_rows if row[f"{scope[:-1]}_id"] is not None] if scope in {"albums", "files"} else [int(row["track_id"]) for row in base_rows if row["track_id"] is not None]
            results = _records_by_scope(conn, scope, ids, include_provider_history=include_provider_history)
    except (ValueError, sqlite3.DatabaseError) as exc:
        conn.close()
        return 1, f"Export query failed: {exc}"
    finally:
        try:
            conn.close()
        except Exception:
            pass
    slim = [_shape_record(item, scope, include_tags=include_tags, include_audio=include_audio, include_assets=include_assets, include_provider_history=include_provider_history) for item in results]
    return 0, {"status": "OK", "type": "query", "scope": scope, "query": query, "generated_at": _now(), "count": len(slim), "results": slim}


def _missing_payload(config: dict[str, Any], field: str, *, verbose: bool, debug: bool) -> tuple[int, dict[str, Any] | str]:
    code, output = missing_report(config, fields=[field], output_format="json", verbose=verbose, debug=debug)
    if code != 0:
        return code, output
    payload = json.loads(output)
    payload.update({"type": "missing", "generated_at": _now(), "count": payload.get("summary", {}).get("tracks_affected", 0)})
    return 0, payload


def _duplicates_payload(config: dict[str, Any], *, verbose: bool, debug: bool) -> tuple[int, dict[str, Any] | str]:
    strategy = str(get_config_value(config, "duplicates", "default_strategy", "safe"))
    scope = str(get_config_value(config, "duplicates", "default_scope", "tracks"))
    code, output = duplicates_path(config, scope=scope, strategy=strategy, output_format="json", verbose=verbose, debug=debug)
    if code != 0:
        return code, output
    payload = json.loads(output)
    payload.update({"type": "duplicates", "generated_at": _now(), "count": len(payload.get("groups", []))})
    return 0, payload


def _reviews_payload(config: dict[str, Any]) -> tuple[int, dict[str, Any] | str]:
    conn = connect_readonly(config)
    if conn is None:
        return 0, {"status": "OK", "type": "reviews", "generated_at": _now(), "pending": [], "resolved": [], "count": 0}
    with conn:
        rows = conn.execute("SELECT * FROM field_decisions ORDER BY COALESCE(resolved, 0), id").fetchall()
    conn.close()
    decisions = [_safe_decision(dict(row)) for row in rows]
    pending = [item for item in decisions if not item["resolved"]]
    resolved = [item for item in decisions if item["resolved"]]
    return 0, {"status": "REVIEW" if pending else "OK", "type": "reviews", "generated_at": _now(), "pending": pending, "resolved": resolved, "count": len(decisions)}


def _library_payload(config: dict[str, Any], *, include_provider_history: bool) -> tuple[int, dict[str, Any] | str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, f"Database not initialized: {database_path(config)}"
    with conn:
        tracks = _records_by_scope(conn, "tracks", None, include_provider_history=include_provider_history)
        albums = _records_by_scope(conn, "albums", None, include_provider_history=include_provider_history)
        files = _records_by_scope(conn, "files", None, include_provider_history=include_provider_history)
    conn.close()
    return 0, {"status": "OK", "type": "library", "generated_at": _now(), "summary": {"albums": len(albums), "tracks": len(tracks), "files": len(files)}, "albums": albums, "tracks": tracks, "files": files}


def _records_by_scope(conn: sqlite3.Connection, scope: str, ids: list[int] | None, *, include_provider_history: bool) -> list[dict[str, Any]]:
    clause = ""
    params: list[Any] = []
    if ids is not None:
        if not ids:
            return []
        column = "a.id" if scope == "albums" else "f.id" if scope == "files" else "t.id"
        clause = f"WHERE {column} IN ({', '.join('?' for _ in ids)})"
        params = ids
    if scope == "albums":
        rows = conn.execute(
            f"""
            SELECT a.*, COUNT(f.id) AS tracks,
                   SUM(CASE WHEN COALESCE(f.has_cover, 0) = 1 OR EXISTS (SELECT 1 FROM artwork aw WHERE aw.album_id = a.id OR aw.file_id = f.id) THEN 1 ELSE 0 END) AS has_cover_count,
                   SUM(CASE WHEN COALESCE(f.has_lyrics, 0) = 1 OR EXISTS (SELECT 1 FROM lyrics lx WHERE lx.track_id = t.id) THEN 1 ELSE 0 END) AS has_lyrics_count
            FROM albums a
            LEFT JOIN tracks t ON t.album_id = a.id
            LEFT JOIN files f ON f.track_id = t.id
            {clause}
            GROUP BY a.id
            ORDER BY a.albumartist, a.album
            """,
            params,
        ).fetchall()
        return [_album_record(conn, dict(row), include_provider_history=include_provider_history) for row in rows]
    rows = conn.execute(
        f"""
        SELECT f.id AS file_id, f.path, f.format, f.codec, f.bitrate, f.sample_rate, f.duration, f.has_cover, f.has_lyrics, f.has_synced_lyrics, f.status,
               t.id AS track_id, t.title, t.artist, t.albumartist, t.track, t.disc, t.mb_track_id, t.mb_release_track_id, t.acoustid_id, t.isrc, t.bpm AS track_bpm, t.key AS track_key, t.mood, t.energy AS track_energy, t.danceability AS track_danceability,
               a.id AS album_id, a.album, a.albumartist AS album_albumartist, a.date, a.originaldate, a.mb_album_id, a.mb_release_group_id, a.label, a.catalog_number, a.barcode, a.country, a.release_type,
               af.bpm AS feature_bpm, af.key AS feature_key, af.replaygain_track_gain, af.replaygain_album_gain, af.loudness, af.energy AS feature_energy, af.danceability AS feature_danceability
        FROM files f
        LEFT JOIN tracks t ON t.id = f.track_id
        LEFT JOIN albums a ON a.id = t.album_id
        LEFT JOIN audio_features af ON af.track_id = t.id
        {clause}
        ORDER BY COALESCE(a.albumartist, t.albumartist, t.artist), a.album, COALESCE(t.disc, 1), COALESCE(t.track, 999), t.title, f.path
        """,
        params,
    ).fetchall()
    records = [_track_file_record(conn, dict(row), include_provider_history=include_provider_history) for row in rows]
    return records if scope == "tracks" else [_file_record(item) for item in records]


def _album_record(conn: sqlite3.Connection, row: dict[str, Any], *, include_provider_history: bool) -> dict[str, Any]:
    missing = []
    for field in ("album", "albumartist", "mb_album_id", "mb_release_group_id"):
        if not row.get(field):
            missing.append(field)
    record = {
        "album_id": row.get("id"),
        "album": row.get("album") or "",
        "albumartist": row.get("albumartist") or "",
        "date": row.get("date") or "",
        "originaldate": row.get("originaldate") or "",
        "tracks": int(row.get("tracks") or 0),
        "label": row.get("label") or "",
        "catalog_number": row.get("catalog_number") or "",
        "barcode": row.get("barcode") or "",
        "country": row.get("country") or "",
        "release_type": row.get("release_type") or "",
        "mb_album_id": row.get("mb_album_id") or "",
        "mb_release_group_id": row.get("mb_release_group_id") or "",
        "has_cover_count": int(row.get("has_cover_count") or 0),
        "has_lyrics_count": int(row.get("has_lyrics_count") or 0),
        "missing_fields": missing,
        "tags": _tags(conn, "album_tags", "album_id", row.get("id")),
    }
    if include_provider_history:
        record["provider_history"] = _provider_history(conn, "album", row.get("id"))
    return record


def _track_file_record(conn: sqlite3.Connection, row: dict[str, Any], *, include_provider_history: bool) -> dict[str, Any]:
    record = {
        "path": row.get("path") or "",
        "title": row.get("title") or "",
        "artist": row.get("artist") or "",
        "album": row.get("album") or "",
        "albumartist": row.get("album_albumartist") or row.get("albumartist") or "",
        "track": row.get("track"),
        "disc": row.get("disc"),
        "date": row.get("date") or "",
        "originaldate": row.get("originaldate") or "",
        "genre": "; ".join(_tag_values(conn, row.get("track_id"), row.get("album_id"), "genre")),
        "style": "; ".join(_tag_values(conn, row.get("track_id"), row.get("album_id"), "style")),
        "mood": row.get("mood") or "",
        "bpm": row.get("feature_bpm") if row.get("feature_bpm") is not None else row.get("track_bpm"),
        "key": row.get("feature_key") or row.get("track_key") or "",
        "energy": row.get("feature_energy") if row.get("feature_energy") is not None else row.get("track_energy"),
        "danceability": row.get("feature_danceability") if row.get("feature_danceability") is not None else row.get("track_danceability"),
        "replaygain_track_gain": row.get("replaygain_track_gain"),
        "replaygain_album_gain": row.get("replaygain_album_gain"),
        "replaygain": bool(row.get("replaygain_track_gain") is not None and row.get("replaygain_album_gain") is not None),
        "has_cover": bool(row.get("has_cover")),
        "has_lyrics": bool(row.get("has_lyrics")),
        "has_synced_lyrics": bool(row.get("has_synced_lyrics")),
        "mb_album_id": row.get("mb_album_id") or "",
        "mb_track_id": row.get("mb_track_id") or row.get("mb_release_track_id") or "",
        "mb_release_group_id": row.get("mb_release_group_id") or "",
        "acoustid_id": row.get("acoustid_id") or "",
        "label": row.get("label") or "",
        "catalog_number": row.get("catalog_number") or "",
        "release_type": row.get("release_type") or "",
        "format": row.get("format") or "",
        "duration": row.get("duration"),
        "status": row.get("status") or "",
        "metadata": {"mb_track_id": row.get("mb_track_id") or "", "mb_album_id": row.get("mb_album_id") or "", "mb_release_group_id": row.get("mb_release_group_id") or "", "bpm": row.get("feature_bpm") if row.get("feature_bpm") is not None else row.get("track_bpm")},
        "assets": {"cover": bool(row.get("has_cover")), "lyrics": bool(row.get("has_lyrics")), "synced_lyrics": bool(row.get("has_synced_lyrics"))},
        "tags": _tags(conn, "track_tags", "track_id", row.get("track_id")),
        "audio_features": {"bpm": row.get("feature_bpm"), "key": row.get("feature_key"), "energy": row.get("feature_energy"), "danceability": row.get("feature_danceability"), "replaygain_track_gain": row.get("replaygain_track_gain"), "replaygain_album_gain": row.get("replaygain_album_gain"), "loudness": row.get("loudness")},
    }
    if include_provider_history:
        record["provider_history"] = _provider_history(conn, "track", row.get("track_id")) + _provider_history(conn, "file", row.get("file_id"))
    return record


def _file_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in FILE_CSV_FIELDS}


def _shape_record(record: dict[str, Any], scope: str, *, include_tags: bool, include_audio: bool, include_assets: bool, include_provider_history: bool) -> dict[str, Any]:
    if scope == "albums":
        base = {key: record.get(key) for key in ALBUM_CSV_FIELDS if key in record}
    elif scope == "files":
        base = {key: record.get(key) for key in FILE_CSV_FIELDS if key in record}
    else:
        base = {key: record.get(key) for key in TRACK_CSV_FIELDS if key in record}
        if "replaygain" in record:
            base["replaygain"] = record["replaygain"]
        base["metadata"] = record.get("metadata", {})
    if include_tags and "tags" in record:
        base["tags"] = record["tags"]
    if include_audio and "audio_features" in record:
        base["audio_features"] = record["audio_features"]
    if include_assets and "assets" in record:
        base["assets"] = record["assets"]
    if include_provider_history and "provider_history" in record:
        base["provider_history"] = record["provider_history"]
    return base


def _rows_for_csv(payload: dict[str, Any], *, fields: str | None, exclude_fields: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    if payload.get("type") == "library":
        rows = [_flatten({"record_type": "album", **item}) for item in payload.get("albums", [])]
        rows.extend(_flatten({"record_type": "track", **item}) for item in payload.get("tracks", []))
        rows.extend(_flatten({"record_type": "file", **item}) for item in payload.get("files", []))
        order = ["record_type"] + sorted({key for row in rows for key in row if key != "record_type"})
        return _filter_rows(rows, order, fields, exclude_fields)
    if payload.get("type") == "missing":
        rows = []
        for album in payload.get("albums", []):
            for track in album.get("tracks", []):
                rows.append(_flatten({"album": album.get("album", ""), "albumartist": album.get("albumartist", ""), **track, "missing_fields": track.get("missing", [])}))
        return _filter_rows(rows, ["path", "artist", "title", "album", "albumartist", "track", "missing_fields"], fields, exclude_fields)
    if payload.get("type") == "duplicates":
        rows = []
        for index, group in enumerate(payload.get("groups", []), 1):
            for member in group.get("files") or group.get("albums") or []:
                rows.append(_flatten({"group": index, "scope": group.get("scope"), "reason": group.get("reason"), "confidence": group.get("confidence"), **member}))
        order = ["group", "scope", "reason", "confidence", "path", "artist", "title", "album", "albumartist", "duration", "files"]
        return _filter_rows(rows, order, fields, exclude_fields)
    if payload.get("type") == "reviews":
        rows = [_flatten({"review_state": "pending", **item}) for item in payload.get("pending", [])]
        rows.extend(_flatten({"review_state": "resolved", **item}) for item in payload.get("resolved", []))
        order = ["review_state", "id", "field", "target_type", "target_id", "current_value", "candidate_value", "selected_value", "provider", "confidence", "action", "resolved_action", "reason", "resolved"]
        return _filter_rows(rows, order, fields, exclude_fields)
    rows = [_flatten(item) for item in payload.get("results", [])]
    if payload.get("scope") == "albums":
        order = ALBUM_CSV_FIELDS
    elif payload.get("scope") == "files":
        order = FILE_CSV_FIELDS
    else:
        order = TRACK_CSV_FIELDS
    return _filter_rows(rows, order, fields, exclude_fields)


def _filter_rows(rows: list[dict[str, Any]], order: list[str], fields: str | None, exclude_fields: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    available = list(dict.fromkeys(order + [key for row in rows for key in row]))
    excluded = set(_field_list(exclude_fields, allow_unknown=True))
    if fields:
        selected = _field_list(fields, allow_unknown=False)
    else:
        selected = available
    selected = [field for field in selected if field in available and field not in excluded and not _sensitive_name(field)]
    return [{field: _csv_value(row.get(field)) for field in selected} for row in rows], selected


def _field_list(raw: str | None, *, allow_unknown: bool) -> list[str]:
    values: list[str] = []
    for item in str(raw or "").split(","):
        field = item.strip().casefold().replace("-", "_")
        if not field:
            continue
        resolved = resolve_field_alias(field)
        for name in resolved:
            if not allow_unknown and name not in supported_field_names() and name not in set(TRACK_CSV_FIELDS + ALBUM_CSV_FIELDS + FILE_CSV_FIELDS + ["missing_fields", "review_state", "group", "reason", "confidence", "record_type"]):
                raise ValueError(f"Unknown export field: {field}")
            if name not in values:
                values.append(name)
    return values


def _render_json(payload: dict[str, Any], *, fields: str | None, exclude_fields: str | None) -> str:
    if not fields and not exclude_fields:
        return json.dumps(_sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True)
    rows, _ = _rows_for_csv(payload, fields=fields, exclude_fields=exclude_fields)
    scoped = dict(payload)
    if "results" in scoped:
        scoped["results"] = rows
        scoped["count"] = len(rows)
    else:
        scoped["rows"] = rows
        scoped["count"] = len(rows)
    return json.dumps(_sanitize(scoped), ensure_ascii=False, indent=2, sort_keys=True)


def _render_csv(rows: list[dict[str, Any]], fields: list[str]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _write_output(path: Path, data: str, export_format: str, *, force: bool) -> Path:
    target = path.expanduser()
    if target.exists() and target.is_dir():
        target = target / f"noqlen-forge-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{export_format}"
    elif not target.suffix:
        target = target.with_suffix(f".{export_format}")
    _guard_output_path(target)
    if target.exists() and not force:
        raise SafetyError(f"Output already exists: {target}\nUse --force to overwrite.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")
    return target.resolve(strict=False)


def _guard_output_path(path: Path) -> None:
    target = path.resolve(strict=False)
    if is_dangerous_real_library_path(target):
        raise SafetyError(f"Refusing export output in dangerous path: {target}")
    if automated_validation_enabled() and not (is_noqlen_forge_lab_path(target) or str(target).startswith("/tmp/")):
        raise SafetyError(f"Refusing automated export output outside MusicLab or /tmp: {target}")


def _tags(conn: sqlite3.Connection, table: str, key_column: str, key_value: Any) -> dict[str, list[str]]:
    if key_value is None:
        return {}
    rows = conn.execute(f"SELECT key, value FROM {table} WHERE {key_column} = ? ORDER BY key, value", (key_value,)).fetchall()
    tags: dict[str, list[str]] = {}
    for row in rows:
        key = str(row["key"] or "")
        if _sensitive_name(key):
            continue
        tags.setdefault(key, []).append(str(row["value"] or ""))
    return tags


def _tag_values(conn: sqlite3.Connection, track_id: Any, album_id: Any, key: str) -> list[str]:
    values: list[str] = []
    for table, column, value in (("track_tags", "track_id", track_id), ("album_tags", "album_id", album_id)):
        if value is None:
            continue
        rows = conn.execute(f"SELECT value FROM {table} WHERE {column} = ? AND LOWER(key) = ? ORDER BY value", (value, key)).fetchall()
        values.extend(str(row["value"] or "") for row in rows if str(row["value"] or ""))
    return list(dict.fromkeys(values))


def _provider_history(conn: sqlite3.Connection, target_type: str, target_id: Any) -> list[dict[str, Any]]:
    if target_id is None:
        return []
    rows = conn.execute("SELECT provider, target_type, target_id, status, started_at, finished_at, query, error FROM provider_runs WHERE target_type = ? AND target_id = ? ORDER BY id", (target_type, str(target_id))).fetchall()
    return [_sanitize(dict(row)) for row in rows]


def _safe_decision(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _safe_value(key, value) for key, value in row.items() if key != "provider_run_id" and not _sensitive_name(key)}


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}_{key}" if prefix else str(key)
        if _sensitive_name(name):
            continue
        if isinstance(item, dict):
            result.update(_flatten(item, name))
        else:
            result[name] = _csv_value(item)
    return result


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(_sanitize(value), ensure_ascii=False, sort_keys=True)
    return "" if value is None else value


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items() if not _sensitive_name(str(key))}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return _safe_value("", value)


def _safe_value(key: str, value: Any) -> Any:
    if _sensitive_name(key):
        return "[redacted]"
    if not isinstance(value, str):
        return value
    lowered = value.casefold()
    if any(marker in lowered for marker in ("api_key=", "apikey=", "token=", "secret=", "password=", "fingerprint")):
        return "[redacted]"
    return value if len(value) <= 500 else value[:497] + "..."


def _sensitive_name(name: str) -> bool:
    lowered = str(name or "").casefold()
    if get_field(lowered) and lowered not in {"lyrics", "synced_lyrics"}:
        return False
    return lowered in SENSITIVE_EXACT_FIELDS or any(item in lowered for item in SENSITIVE_NAME_MARKERS)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
