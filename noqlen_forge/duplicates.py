from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import get_config_value
from .db import connect_readonly, database_path, normalize_path


TRACK_CRITERIA = {"path", "mb_track_id", "mb_release_track_id", "acoustid", "acoustid_id", "artist,title,duration", "albumartist,album,track,title"}
ALBUM_CRITERIA = {"mb_album_id", "mb_release_group_id", "albumartist,album", "albumartist,album,originaldate", "trackset"}


def duplicates_path(config: dict[str, Any], target: Path | None = None, scope: str = "tracks", by: str | None = None, strategy: str = "safe", output_format: str = "text", verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, f"Database not initialized: {database_path(config)}\nRun: noqlen-forge db scan PATH --apply"
    if strategy not in {"safe", "strict", "loose"}:
        return 1, f"Invalid duplicates strategy: {strategy}"
    criteria = _criteria(config, scope, by, strategy)
    target_filter = _target_filter(target)
    with conn:
        if target is not None and not _has_target_rows(conn, target_filter):
            return 1, f"No database rows found for {normalize_path(target)}\nRun: noqlen-forge db scan {target} --apply"
        groups = []
        if scope in {"tracks", "both"}:
            groups.extend(_track_groups(conn, criteria, strategy, target_filter, _duration_delta(config)))
        if scope in {"albums", "both"}:
            groups.extend(_album_groups(conn, criteria, strategy, target_filter))
    status = _status(groups)
    result = {"scope": scope, "strategy": strategy, "groups": groups, "status": status}
    if output_format == "json":
        return 0, json.dumps(_json_result(result, debug=debug), indent=2, sort_keys=True)
    return 0, _render_text(result, verbose=verbose, debug=debug)


def _criteria(config: dict[str, Any], scope: str, by: str | None, strategy: str) -> list[str]:
    if by:
        values = [item.strip().casefold() for item in by.split(",") if item.strip()]
        joined = ",".join(values)
        if joined in TRACK_CRITERIA or joined in ALBUM_CRITERIA:
            return [joined]
        return values
    if scope == "albums":
        base = ["mb_album_id", "mb_release_group_id", "albumartist,album,originaldate", "trackset"]
    else:
        base = []
        if bool(get_config_value(config, "duplicates", "include_path_duplicates", True)):
            base.append("path")
        if bool(get_config_value(config, "duplicates", "include_mbids", True)):
            base.extend(["mb_track_id", "mb_release_track_id"])
        if bool(get_config_value(config, "duplicates", "include_acoustid", True)):
            base.append("acoustid")
        if bool(get_config_value(config, "duplicates", "include_title_artist_duration", True)):
            base.extend(["artist,title,duration", "albumartist,album,track,title"])
    if strategy == "strict":
        return [item for item in base if item in {"mb_track_id", "mb_release_track_id", "acoustid", "mb_album_id", "mb_release_group_id"}]
    if strategy == "loose" and scope != "albums" and "artist,title,duration" not in base:
        base.append("artist,title,duration")
    return base


def _duration_delta(config: dict[str, Any]) -> float:
    return float(get_config_value(config, "duplicates", "min_duration_delta", 2.0) or 2.0)


def _target_filter(target: Path | None) -> tuple[str, list[str]]:
    if target is None:
        return "1 = 1", []
    normalized = normalize_path(target)
    if target.is_file():
        return "f.path = ?", [normalized]
    return "(f.path = ? OR f.path LIKE ?)", [normalized, normalized.rstrip("/") + "/%"]


def _has_target_rows(conn: sqlite3.Connection, target_filter: tuple[str, list[str]]) -> bool:
    clause, params = target_filter
    row = conn.execute(f"SELECT 1 FROM files f WHERE {clause} LIMIT 1", params).fetchone()
    return row is not None


def _track_rows(conn: sqlite3.Connection, target_filter: tuple[str, list[str]]) -> list[dict[str, Any]]:
    clause, params = target_filter
    rows = conn.execute(
        f"""
        SELECT f.id AS file_id, f.path, f.duration, t.id AS track_id, t.title, t.artist, t.albumartist,
               t.track, t.disc, t.mb_track_id, t.mb_release_track_id, t.acoustid_id,
               a.id AS album_id, a.album, a.albumartist AS album_albumartist, a.mb_album_id, a.mb_release_group_id, a.originaldate, a.date
        FROM files f
        LEFT JOIN tracks t ON t.id = f.track_id
        LEFT JOIN albums a ON a.id = t.album_id
        WHERE f.status = 'active' AND {clause}
        ORDER BY COALESCE(a.albumartist, t.albumartist, t.artist), a.album, COALESCE(t.disc, 1), COALESCE(t.track, 999), t.title, f.path
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _track_groups(conn: sqlite3.Connection, criteria: list[str], strategy: str, target_filter: tuple[str, list[str]], duration_delta: float) -> list[dict[str, Any]]:
    rows = _track_rows(conn, target_filter)
    groups: list[dict[str, Any]] = []
    seen: set[tuple[int, ...]] = set()
    for criterion in criteria:
        buckets: dict[Any, list[dict[str, Any]]] = {}
        if criterion in {"mb_track_id", "mb_release_track_id", "acoustid", "acoustid_id", "path", "albumartist,album,track,title"}:
            for row in rows:
                key = _track_key(row, criterion)
                if key:
                    buckets.setdefault(key, []).append(row)
        elif criterion == "artist,title,duration":
            for row in rows:
                key = (_norm(row.get("artist")), _norm(row.get("title")))
                if key[0] and key[1]:
                    buckets.setdefault(key, []).append(row)
        for bucket in buckets.values():
            for members in _duration_members(bucket, duration_delta) if criterion == "artist,title,duration" else [bucket]:
                if len(members) < 2:
                    continue
                identity = tuple(sorted(int(item["file_id"]) for item in members))
                if identity in seen:
                    continue
                seen.add(identity)
                groups.append(_group("tracks", criterion, _confidence(criterion, strategy), members))
    return groups


def _track_key(row: dict[str, Any], criterion: str) -> Any:
    if criterion == "path":
        return _norm_path(row.get("path"))
    if criterion == "acoustid":
        return _norm(row.get("acoustid_id"))
    if criterion in {"mb_track_id", "mb_release_track_id", "acoustid_id"}:
        return _norm(row.get(criterion))
    if criterion == "albumartist,album,track,title":
        return (_norm(row.get("album_albumartist") or row.get("albumartist") or row.get("artist")), _norm(row.get("album")), row.get("disc") or 1, row.get("track"), _norm(row.get("title")))
    return None


def _duration_members(rows: list[dict[str, Any]], duration_delta: float) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda item: float(item.get("duration") or 0))
    groups: list[list[dict[str, Any]]] = []
    for row in ordered:
        duration = float(row.get("duration") or 0)
        placed = False
        for group in groups:
            first = float(group[0].get("duration") or 0)
            if duration and first and abs(duration - first) <= duration_delta:
                group.append(row)
                placed = True
                break
        if not placed:
            groups.append([row])
    return groups


def _album_rows(conn: sqlite3.Connection, target_filter: tuple[str, list[str]]) -> list[dict[str, Any]]:
    clause, params = target_filter
    rows = conn.execute(
        f"""
        SELECT a.id AS album_id, a.album, a.albumartist, a.date, a.originaldate, a.mb_album_id, a.mb_release_group_id,
               COUNT(f.id) AS files, MIN(f.path) AS first_path,
               GROUP_CONCAT(COALESCE(t.track, 0) || ':' || COALESCE(t.title, '') || ':' || ROUND(COALESCE(f.duration, 0))) AS trackset
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN files f ON f.track_id = t.id
        WHERE f.status = 'active' AND {clause}
        GROUP BY a.id
        ORDER BY a.albumartist, a.album, a.id
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _album_groups(conn: sqlite3.Connection, criteria: list[str], strategy: str, target_filter: tuple[str, list[str]]) -> list[dict[str, Any]]:
    rows = _album_rows(conn, target_filter)
    groups: list[dict[str, Any]] = []
    seen: set[tuple[int, ...]] = set()
    for criterion in criteria:
        buckets: dict[Any, list[dict[str, Any]]] = {}
        for row in rows:
            key = _album_key(row, criterion)
            if key:
                buckets.setdefault(key, []).append(row)
        for members in buckets.values():
            if len(members) < 2:
                continue
            identity = tuple(sorted(int(item["album_id"]) for item in members))
            if identity in seen:
                continue
            seen.add(identity)
            groups.append(_group("albums", criterion, _confidence(criterion, strategy), members))
    return groups


def _album_key(row: dict[str, Any], criterion: str) -> Any:
    if criterion == "mb_album_id":
        return _norm(row.get("mb_album_id"))
    if criterion == "mb_release_group_id":
        return (_norm(row.get("mb_release_group_id")), _norm(row.get("albumartist")), _norm(row.get("album")))
    if criterion == "albumartist,album":
        return (_norm(row.get("albumartist")), _norm(row.get("album")))
    if criterion == "albumartist,album,originaldate":
        return (_norm(row.get("albumartist")), _norm(row.get("album")), _year(row.get("originaldate") or row.get("date")))
    if criterion == "trackset":
        return (_norm(row.get("albumartist")), _norm(row.get("album")), row.get("trackset"))
    return None


def _group(scope: str, reason: str, confidence: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    key = "files" if scope == "tracks" else "albums"
    return {"scope": scope, "reason": _reason(reason), "criterion": reason, "confidence": confidence, key: [_public_member(member, scope) for member in members]}


def _confidence(criterion: str, strategy: str) -> str:
    if criterion in {"mb_track_id", "mb_release_track_id", "acoustid", "acoustid_id", "mb_album_id", "mb_release_group_id"}:
        return "high"
    if strategy == "loose" and criterion in {"albumartist,album", "trackset"}:
        return "low"
    return "medium"


def _reason(criterion: str) -> str:
    return {
        "path": "same normalized path",
        "mb_track_id": "same MB Track ID",
        "mb_release_track_id": "same MB Release Track ID",
        "acoustid": "same AcoustID",
        "acoustid_id": "same AcoustID",
        "artist,title,duration": "same artist/title/duration",
        "albumartist,album,track,title": "same album/track/title",
        "mb_album_id": "same MB Album ID",
        "mb_release_group_id": "same release group/album",
        "albumartist,album,originaldate": "same album artist/album/year",
        "albumartist,album": "same album artist/album",
        "trackset": "same track set",
    }.get(criterion, criterion)


def _public_member(row: dict[str, Any], scope: str) -> dict[str, Any]:
    if scope == "albums":
        return {"album_id": row.get("album_id"), "albumartist": row.get("albumartist") or "", "album": row.get("album") or "", "path": row.get("first_path") or "", "files": row.get("files") or 0}
    return {"file_id": row.get("file_id"), "track_id": row.get("track_id"), "artist": row.get("artist") or "", "title": row.get("title") or "", "album": row.get("album") or "", "path": row.get("path") or "", "duration": row.get("duration")}


def _status(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return "OK"
    if any(group["confidence"] == "low" for group in groups):
        return "REVIEW"
    return "WARN"


def _json_result(result: dict[str, Any], debug: bool) -> dict[str, Any]:
    if debug:
        return result
    groups = []
    for group in result["groups"]:
        clean = {key: value for key, value in group.items() if key != "criterion"}
        groups.append(clean)
    return {**result, "groups": groups}


def _render_text(result: dict[str, Any], verbose: bool, debug: bool) -> str:
    groups = result["groups"]
    scope = result["scope"]
    label = "Duplicate albums" if scope == "albums" else "Duplicate tracks"
    if not groups:
        return f"{label}: none\nStatus: OK"
    lines = [f"{label}: {len(groups)} groups", ""]
    duplicate_items = 0
    for index, group in enumerate(groups, 1):
        lines.append(f"Group {index}: {group['reason']}")
        lines.append(f"Confidence: {group['confidence']}")
        members = group.get("files") or group.get("albums") or []
        duplicate_items += len(members)
        for member in members:
            if group["scope"] == "albums":
                lines.append(f"- {member['albumartist']} - {member['album']} ({member['files']} files)")
            else:
                lines.append(f"- {member['artist']} - {member['title']}")
            lines.append(f"  {member['path']}")
            if debug:
                ids = f"ids file={member.get('file_id')} track={member.get('track_id')}" if group["scope"] == "tracks" else f"ids album={member.get('album_id')}"
                lines.append(f"  {ids}")
        if verbose and debug:
            lines.append(f"Criterion: {group.get('criterion', '')}")
        lines.append("")
    lines.append(f"Duplicate groups: {len(groups)}")
    lines.append(f"Duplicate files: {duplicate_items}")
    lines.append(f"Status: {result['status']}")
    return "\n".join(lines)


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _norm_path(value: Any) -> str:
    return str(value or "").strip()


def _year(value: Any) -> str:
    text = str(value or "").strip()
    return text[:4] if len(text) >= 4 else text
