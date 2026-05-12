from __future__ import annotations

import csv
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

from .db import apply_migrations, connect, connect_readonly, database_path, execute_query, parse_query
from .export import _guard_output_path
from .safety import SafetyError

SMART_PLAYLIST_FORMATS = {"m3u", "m3u8", "json", "csv"}
PATH_MODES = {"absolute", "relative", "library"}
SORT_FIELDS = {"title", "artist", "album", "albumartist", "path", "track", "disc", "rating", "starred", "status"}


@dataclass(frozen=True)
class SmartPlaylistDefinition:
    id: int | None
    name: str
    query: str
    default_format: str = "m3u8"
    sort: str | None = None
    reverse: bool = False
    limit_count: int | None = None
    path_mode: str = "absolute"
    library_root: str | None = None
    description: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


def smart_create(config: dict[str, Any], name: str, query: str, *, apply: bool = False, default_format: str = "m3u8", sort: str | None = None, reverse: bool = False, limit: int | None = None, path_mode: str = "absolute", library_root: Path | None = None, force: bool = False, output_format: str = "text", verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    try:
        definition = _definition(None, name, query, default_format, sort, reverse, limit, path_mode, library_root)
        rows = _matching_rows(config, definition)
        existing = _get_definition(config, name)
        if existing and not force:
            return 1, f"Smart playlist already exists: {name}\nUse --force to replace."
        saved = False
        if apply:
            with connect(config) as conn:
                apply_migrations(conn)
                now = _now()
                if existing:
                    conn.execute(
                        """
                        UPDATE smart_playlists
                        SET query = ?, default_format = ?, sort = ?, reverse = ?, limit_count = ?, path_mode = ?, library_root = ?, updated_at = ?
                        WHERE name = ?
                        """,
                        (definition.query, definition.default_format, definition.sort, int(definition.reverse), definition.limit_count, definition.path_mode, definition.library_root, now, name),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO smart_playlists(name, query, default_format, sort, reverse, limit_count, path_mode, library_root, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (definition.name, definition.query, definition.default_format, definition.sort, int(definition.reverse), definition.limit_count, definition.path_mode, definition.library_root, now, now),
                    )
                conn.commit()
                saved = True
    except (ValueError, sqlite3.DatabaseError, SafetyError) as exc:
        return 1, f"Smart playlist create failed: {exc}"
    payload = {"status": "OK", "mode": "APPLY" if apply else "DRY-RUN", "name": name, "query": query, "tracks_now": len(rows), "saved": saved}
    if output_format == "json":
        return 0, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return 0, "\n".join([
        "Smart playlist create",
        f"Name: {name}",
        f"Query: {query}",
        f"Mode: {'APPLY' if apply else 'DRY-RUN'}",
        "",
        f"[1/3] Validate query       OK      {len(rows)} matching tracks",
        f"[2/3] Validate options     OK      format={definition.default_format}, sort={definition.sort or '-'}",
        f"[3/3] Save definition      OK      {'saved' if saved else 'would save'}",
        "",
        f"Smart playlist: {name}",
        f"Tracks now: {len(rows)}",
        "Status: OK",
    ])


def smart_list(config: dict[str, Any], *, output_format: str = "text", verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    try:
        definitions = _all_definitions(config)
        items = []
        for definition in definitions:
            count = len(_matching_rows(config, definition))
            items.append({"name": definition.name, "query": definition.query, "tracks_now": count, "last_export": _last_export(config, definition.id)})
    except (ValueError, sqlite3.DatabaseError) as exc:
        return 1, f"Smart playlist list failed: {exc}"
    if output_format == "json":
        return 0, json.dumps({"status": "OK", "count": len(items), "smart_playlists": items}, ensure_ascii=False, indent=2, sort_keys=True)
    lines = [f"Smart playlists: {len(items)}"]
    for item in items:
        lines.extend(["", f"- {item['name']}", f"  Query: {item['query']}", f"  Last export: {item['last_export'] or 'never'}", f"  Tracks now: {item['tracks_now']}"])
    return 0, "\n".join(lines)


def smart_show(config: dict[str, Any], name: str, *, output_format: str = "text", verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    try:
        definition = _require_definition(config, name)
        rows = _matching_rows(config, definition)
        exports = _exports(config, definition.id)
    except (ValueError, sqlite3.DatabaseError) as exc:
        return 1, f"Smart playlist show failed: {exc}"
    payload = {"status": "OK", "smart_playlist": _definition_dict(definition), "tracks_now": len(rows), "last_exports": exports}
    if output_format == "json":
        return 0, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    lines = [
        "Smart playlist",
        f"Name: {definition.name}",
        f"Query: {definition.query}",
        f"Default format: {definition.default_format}",
        f"Sort: {definition.sort or '-'}",
        f"Reverse: {'yes' if definition.reverse else 'no'}",
        f"Limit: {definition.limit_count if definition.limit_count is not None else '-'}",
        f"Path mode: {definition.path_mode}",
        f"Library root: {definition.library_root or '-'}",
        f"Tracks now: {len(rows)}",
        "Last exports:",
    ]
    lines.extend([f"- {item['exported_at']} {item['format']} {item['track_count']} tracks {item['output_path'] or ''}" for item in exports] or ["- none"])
    lines.append("Status: OK")
    return 0, "\n".join(lines)


def smart_export(config: dict[str, Any], name: str, *, export_format: str | None = None, output: Path | None = None, force: bool = False, path_mode: str | None = None, library_root: Path | None = None, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    try:
        definition = _require_definition(config, name)
        if export_format:
            definition = _replace_definition(definition, default_format=export_format)
        if path_mode or library_root:
            changes: dict[str, Any] = {}
            if path_mode:
                changes["path_mode"] = path_mode
            if library_root:
                changes["library_root"] = str(library_root.expanduser())
            definition = _replace_definition(definition, **changes)
        rows = _matching_rows(config, definition)
        rendered = _render_playlist(definition, rows, output=output)
        if output is None:
            return 0, rendered
        written = _write_output(output, rendered, definition.default_format, force=force)
    except (ValueError, sqlite3.DatabaseError, OSError, SafetyError) as exc:
        return 1, f"Smart playlist export failed: {exc}"
    return 0, "\n".join(["Smart playlist export", f"Name: {name}", f"Format: {definition.default_format.upper()}", f"Tracks: {len(rows)}", f"Output: {written}", "Status: OK"])


def smart_refresh(config: dict[str, Any], name: str, *, output: Path | None = None, force: bool = False, export_format: str | None = None, path_mode: str | None = None, library_root: Path | None = None, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    return smart_export(config, name, export_format=export_format, output=output, force=force, path_mode=path_mode, library_root=library_root, verbose=verbose, debug=debug)


def smart_delete(config: dict[str, Any], name: str, *, apply: bool = False, output_format: str = "text", verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    try:
        definition = _require_definition(config, name)
        deleted = False
        if apply:
            with connect(config) as conn:
                apply_migrations(conn)
                conn.execute("DELETE FROM smart_playlists WHERE id = ?", (definition.id,))
                conn.commit()
                deleted = True
    except (ValueError, sqlite3.DatabaseError) as exc:
        return 1, f"Smart playlist delete failed: {exc}"
    payload = {"status": "OK", "mode": "APPLY" if apply else "DRY-RUN", "name": name, "deleted": deleted}
    if output_format == "json":
        return 0, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return 0, "\n".join(["Smart playlist delete", f"Name: {name}", f"Mode: {'APPLY' if apply else 'DRY-RUN'}", "Files: not touched", f"Definition: {'deleted' if deleted else 'would delete'}", "Status: OK"])


def smart_rename(config: dict[str, Any], old_name: str, new_name: str, *, apply: bool = False, force: bool = False, output_format: str = "text", verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    try:
        definition = _require_definition(config, old_name)
        existing = _get_definition(config, new_name)
        if existing and not force:
            return 1, f"Smart playlist already exists: {new_name}\nUse --force to replace."
        renamed = False
        if apply:
            with connect(config) as conn:
                apply_migrations(conn)
                if existing:
                    conn.execute("DELETE FROM smart_playlists WHERE id = ?", (existing.id,))
                conn.execute("UPDATE smart_playlists SET name = ?, updated_at = ? WHERE id = ?", (new_name, _now(), definition.id))
                conn.commit()
                renamed = True
    except (ValueError, sqlite3.DatabaseError) as exc:
        return 1, f"Smart playlist rename failed: {exc}"
    payload = {"status": "OK", "mode": "APPLY" if apply else "DRY-RUN", "old_name": old_name, "new_name": new_name, "renamed": renamed}
    if output_format == "json":
        return 0, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return 0, "\n".join(["Smart playlist rename", f"From: {old_name}", f"To: {new_name}", f"Mode: {'APPLY' if apply else 'DRY-RUN'}", "Files: not touched", f"Definition: {'renamed' if renamed else 'would rename'}", "Status: OK"])


def _definition(id_: int | None, name: str, query: str, default_format: str, sort: str | None, reverse: bool, limit: int | None, path_mode: str, library_root: Path | None) -> SmartPlaylistDefinition:
    name = name.strip()
    query = query.strip()
    default_format = default_format.strip().casefold()
    path_mode = path_mode.strip().casefold()
    sort = sort.strip().casefold().replace("-", "_") if sort else None
    if not name:
        raise ValueError("name is required")
    if not query:
        raise ValueError("query is required")
    if default_format not in SMART_PLAYLIST_FORMATS:
        raise ValueError(f"Unsupported playlist format: {default_format}")
    if path_mode not in PATH_MODES:
        raise ValueError(f"Unsupported path mode: {path_mode}")
    if sort and sort not in SORT_FIELDS:
        raise ValueError(f"Unsupported sort field: {sort}")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than zero")
    root = str(library_root.expanduser()) if library_root else None
    if path_mode == "library" and not root:
        raise ValueError("--path-mode library requires --library-root")
    return SmartPlaylistDefinition(id_, name, query, default_format, sort, reverse, limit, path_mode, root)


def _replace_definition(definition: SmartPlaylistDefinition, **changes: Any) -> SmartPlaylistDefinition:
    values = _definition_dict(definition)
    values.update(changes)
    return _definition(definition.id, values["name"], values["query"], values["default_format"], values.get("sort"), bool(values.get("reverse")), values.get("limit_count"), values["path_mode"], Path(values["library_root"]) if values.get("library_root") else None)


def _matching_rows(config: dict[str, Any], definition: SmartPlaylistDefinition) -> list[sqlite3.Row]:
    conn = connect_readonly(config)
    if conn is None:
        raise ValueError(f"Database not initialized: {database_path(config)}")
    try:
        plan = parse_query(definition.query)
        rows = execute_query(conn, plan, "tracks", 100000)
    finally:
        conn.close()
    if definition.sort:
        rows.sort(key=lambda row: _sort_key(row, definition.sort or ""), reverse=definition.reverse)
    elif definition.reverse:
        rows.reverse()
    if definition.limit_count is not None:
        rows = rows[: definition.limit_count]
    return rows


def _sort_key(row: sqlite3.Row, field: str) -> Any:
    value = row[field]
    if value is None:
        return ""
    return str(value).casefold() if isinstance(value, str) else value


def _render_playlist(definition: SmartPlaylistDefinition, rows: list[sqlite3.Row], *, output: Path | None) -> str:
    if definition.default_format in {"m3u", "m3u8"}:
        paths = [_playlist_path(row["path"], definition, output) for row in rows]
        return "\n".join(["#EXTM3U", *paths]) + "\n"
    tracks = [_track_payload(row, _playlist_path(row["path"], definition, output)) for row in rows]
    if definition.default_format == "json":
        return json.dumps({"type": "smart_playlist", "name": definition.name, "query": definition.query, "generated_at": _now(), "count": len(tracks), "tracks": tracks}, ensure_ascii=False, indent=2, sort_keys=True)
    buffer = StringIO()
    fields = ["path", "title", "artist", "album", "albumartist", "track", "disc", "rating", "starred"]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows({field: item.get(field, "") for field in fields} for item in tracks)
    return buffer.getvalue()


def _playlist_path(path: str, definition: SmartPlaylistDefinition, output: Path | None) -> str:
    source = Path(path).expanduser()
    if definition.path_mode == "absolute":
        return str(source.resolve(strict=False))
    if definition.path_mode == "library":
        root = Path(definition.library_root or "").expanduser().resolve(strict=False)
        try:
            return str(source.resolve(strict=False).relative_to(root))
        except ValueError as exc:
            raise ValueError(f"Track path is outside --library-root: {source}") from exc
    base = (output.parent if output else Path.cwd()).expanduser().resolve(strict=False)
    return os.path.relpath(source.resolve(strict=False), base)


def _track_payload(row: sqlite3.Row, path: str) -> dict[str, Any]:
    return {"path": path, "title": row["title"] or "", "artist": row["artist"] or "", "album": row["album"] or "", "albumartist": row["albumartist"] or "", "track": row["track"], "disc": row["disc"], "rating": row["rating"], "starred": bool(row["starred"])}


def _write_output(path: Path, data: str, export_format: str, *, force: bool) -> Path:
    target = path.expanduser()
    if target.exists() and target.is_dir():
        target = target / f"smart-playlist.{export_format}"
    elif not target.suffix:
        target = target.with_suffix(f".{export_format}")
    _guard_output_path(target)
    if target.exists() and not force:
        raise SafetyError(f"Output already exists: {target}\nUse --force to overwrite.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")
    return target.resolve(strict=False)


def _get_definition(config: dict[str, Any], name: str) -> SmartPlaylistDefinition | None:
    conn = connect_readonly(config)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM smart_playlists WHERE name = ?", (name,)).fetchone()
    except sqlite3.DatabaseError:
        row = None
    finally:
        conn.close()
    return _row_definition(row) if row else None


def _require_definition(config: dict[str, Any], name: str) -> SmartPlaylistDefinition:
    definition = _get_definition(config, name)
    if definition is None:
        raise ValueError(f"Smart playlist not found: {name}")
    return definition


def _all_definitions(config: dict[str, Any]) -> list[SmartPlaylistDefinition]:
    conn = connect_readonly(config)
    if conn is None:
        raise ValueError(f"Database not initialized: {database_path(config)}")
    try:
        rows = conn.execute("SELECT * FROM smart_playlists ORDER BY name").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError("Smart playlist schema is not initialized. Run `noqlen-forge db init`.") from exc
    finally:
        conn.close()
    return [_row_definition(row) for row in rows]


def _row_definition(row: sqlite3.Row) -> SmartPlaylistDefinition:
    return SmartPlaylistDefinition(id=int(row["id"]), name=row["name"], query=row["query"], default_format=row["default_format"] or "m3u8", sort=row["sort"], reverse=bool(row["reverse"]), limit_count=row["limit_count"], path_mode=row["path_mode"] or "absolute", library_root=row["library_root"], description=row["description"], created_at=row["created_at"], updated_at=row["updated_at"])


def _definition_dict(definition: SmartPlaylistDefinition) -> dict[str, Any]:
    return {"id": definition.id, "name": definition.name, "query": definition.query, "default_format": definition.default_format, "sort": definition.sort, "reverse": definition.reverse, "limit_count": definition.limit_count, "path_mode": definition.path_mode, "library_root": definition.library_root, "description": definition.description, "created_at": definition.created_at, "updated_at": definition.updated_at}


def _last_export(config: dict[str, Any], playlist_id: int | None) -> str | None:
    if playlist_id is None:
        return None
    exports = _exports(config, playlist_id, limit=1)
    return str(exports[0]["exported_at"]) if exports else None


def _exports(config: dict[str, Any], playlist_id: int | None, limit: int = 5) -> list[dict[str, Any]]:
    if playlist_id is None:
        return []
    conn = connect_readonly(config)
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT output_path, format, track_count, status, exported_at, summary FROM smart_playlist_exports WHERE smart_playlist_id = ? ORDER BY exported_at DESC LIMIT ?", (playlist_id, limit)).fetchall()
    except sqlite3.DatabaseError:
        rows = []
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
