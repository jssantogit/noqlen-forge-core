from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .db import apply_migrations, connect, connect_readonly, finish_operation, normalize_path, record_operation
from .fields import FieldDefinition, get_field, resolve_field_alias
from .output import render_plan
from .safety import SafetyError, automated_validation_enabled, require_lab_path_for_automated_apply
from .workflow import ChangePlan

PENDING_ACTIONS = {"", "review", "manual", "conflict", "ambiguous", "blocked"}
RESOLVE_ACTIONS = {"accept", "keep", "skip", "reject"}
TAG_FIELD_LABELS = {
    "style": "Style",
    "genre": "Genre",
    "label": "Label",
    "originaldate": "Original Date",
    "date": "Date",
    "release_type": "Release Type",
    "catalog_number": "Catalog Number",
    "barcode": "Barcode",
    "country": "Release Country",
    "mood": "Mood",
    "bpm": "BPM",
    "key": "Key",
    "energy": "Energy",
    "danceability": "Danceability",
    "mb_album_id": "MusicBrainz Album Id",
    "mb_track_id": "MusicBrainz Track Id",
    "mb_release_group_id": "MusicBrainz Release Group Id",
    "acoustid_id": "ACOUSTID_ID",
    "isrc": "ISRC",
}


@dataclass(slots=True)
class ReviewDecision:
    id: int
    field: str
    target_type: str
    target_id: str
    current_value: str
    candidate_value: str
    selected_value: str
    provider: str
    confidence: str
    action: str
    reason: str
    resolved: bool
    resolved_action: str
    provider_run_id: int | None = None


def review_list(config: dict[str, Any], path: Path | None = None, *, output_format: str = "text", verbose: bool = False) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 0, _render_list([], path, output_format, verbose)
    try:
        target = _target_for_path(conn, path) if path else None
        decisions = _pending_decisions(conn, target=target)
        candidates = _candidates_by_run(conn, [item.provider_run_id for item in decisions if item.provider_run_id])
    finally:
        conn.close()
    return (1 if decisions else 0), _render_list(decisions, path, output_format, verbose, candidates=candidates)


def review_show(config: dict[str, Any], decision_id: int, *, output_format: str = "text", verbose: bool = False) -> tuple[int, str]:
    conn = connect_readonly(config)
    if conn is None:
        return 1, "Review decision not found"
    try:
        decision = _decision_by_id(conn, decision_id)
        if decision is None:
            return 1, "Review decision not found"
        candidates = _candidates_by_run(conn, [decision.provider_run_id] if decision.provider_run_id else [])
    finally:
        conn.close()
    return 0, _render_show(decision, output_format, verbose, candidates.get(decision.provider_run_id or -1, []))


def review_resolve(
    config: dict[str, Any],
    target: str,
    *,
    field: str | None = None,
    action: str | None = None,
    value: str | None = None,
    apply: bool = False,
    force: bool = False,
    verbose: bool = False,
) -> tuple[int, str]:
    if action and action not in RESOLVE_ACTIONS:
        return 1, f"Unknown review action: {action}"
    if value is not None:
        action = action or "accept"
    action = action or "accept"
    try:
        with connect(config) as conn:
            apply_migrations(conn)
            decision = _resolve_target_decision(conn, target, field)
            if decision is None:
                return 0, "Review resolve: no pending decision found\nStatus: OK"
            field_def = _field_definition(decision.field)
            if field_def is None:
                return 1, f"Unknown field: {decision.field}"
            selected_value = _selected_value(decision, action, value)
            paths = _paths_for_decision(conn, decision)
            if apply and paths and automated_validation_enabled():
                require_lab_path_for_automated_apply(paths[0], context="noqlen-forge review resolve")
            plan, blocked = _resolution_plan(decision, field_def, selected_value, action, paths, force)
            if blocked:
                return 1, blocked
            if not apply:
                return 0, _render_resolve(decision, action, selected_value, plan, apply=False, verbose=verbose)
            op_id = record_operation(conn, "review_resolve", decision.target_type, decision.target_id, "apply", "running", f"decision {decision.id} {decision.field} {action}")
            written = _apply_resolution(conn, decision, field_def, selected_value, action, paths, force)
            _mark_resolved(conn, decision.id, action, selected_value)
            finish_operation(conn, op_id, "ok")
            conn.commit()
            return 0, _render_resolve(decision, action, selected_value, plan, apply=True, verbose=verbose, written=written)
    except (SafetyError, ValueError) as exc:
        return 1, str(exc)


def review_command(config: dict[str, Any], argv: list[str], *, output_format: str = "text", verbose: bool = False, action: str | None = None, value: str | None = None, field: str | None = None, apply: bool = False, force: bool = False) -> tuple[int, str]:
    if not argv:
        return review_list(config, None, output_format=output_format, verbose=verbose)
    command = argv[0]
    if command == "list":
        return review_list(config, Path(argv[1]) if len(argv) > 1 else None, output_format=output_format, verbose=verbose)
    if command == "show" and len(argv) > 1:
        return review_show(config, int(argv[1]), output_format=output_format, verbose=verbose)
    if command == "resolve" and len(argv) > 1:
        return review_resolve(config, argv[1], field=field, action=action, value=value, apply=apply, force=force, verbose=verbose)
    return review_list(config, Path(command), output_format=output_format, verbose=verbose)


def _pending_decisions(conn: sqlite3.Connection, *, target: dict[str, Any] | None = None) -> list[ReviewDecision]:
    clauses = ["COALESCE(resolved, 0) = 0"] if _column_exists(conn, "field_decisions", "resolved") else []
    params: list[Any] = []
    clauses.append("LOWER(COALESCE(action, '')) IN (" + ",".join("?" for _ in PENDING_ACTIONS) + ")")
    params.extend(sorted(PENDING_ACTIONS))
    if target:
        target_clauses: list[str] = []
        for target_type, key in (("album", "album_id"), ("track", "track_id"), ("file", "file_id")):
            if target.get(key) is not None:
                target_clauses.append("(target_type = ? AND target_id = ?)")
                params.extend([target_type, str(target[key])])
        for track_id in target.get("track_ids", []) or []:
            target_clauses.append("(target_type = ? AND target_id = ?)")
            params.extend(["track", str(track_id)])
        clauses.append("(" + " OR ".join(target_clauses or ["1 = 0"]) + ")")
    rows = conn.execute("SELECT * FROM field_decisions WHERE " + " AND ".join(clauses) + " ORDER BY id", params).fetchall()
    return [_decision_from_row(row) for row in rows]


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _decision_by_id(conn: sqlite3.Connection, decision_id: int) -> ReviewDecision | None:
    row = conn.execute("SELECT * FROM field_decisions WHERE id = ?", (decision_id,)).fetchone()
    return _decision_from_row(row) if row else None


def _resolve_target_decision(conn: sqlite3.Connection, target: str, field: str | None) -> ReviewDecision | None:
    if target.isdigit() and field is None:
        decision = _decision_by_id(conn, int(target))
        if decision and (not decision.resolved or decision.resolved_action):
            return None if decision.resolved else decision
        return decision
    if field is None:
        raise ValueError("Resolving by path requires --field")
    target_info = _target_for_path(conn, Path(target))
    if target_info is None:
        raise ValueError(f"Path not found in database: {target}")
    names = resolve_field_alias(field)
    if len(names) != 1 or get_field(names[0]) is None:
        raise ValueError(f"Unknown field: {field}")
    decisions = [item for item in _pending_decisions(conn, target=target_info) if item.field == names[0]]
    return decisions[0] if decisions else None


def _target_for_path(conn: sqlite3.Connection, path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
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
        return dict(row)
    prefix = normalized.rstrip("/") + "/%"
    row = conn.execute(
        """
        SELECT NULL AS file_id, NULL AS path, NULL AS track_id, NULL AS title, NULL AS artist, a.id AS album_id, a.album, a.albumartist
        FROM files f
        LEFT JOIN tracks t ON t.id = f.track_id
        LEFT JOIN albums a ON a.id = t.album_id
        WHERE f.path LIKE ? AND a.id IS NOT NULL
        GROUP BY a.id
        ORDER BY COUNT(f.id) DESC
        LIMIT 1
        """,
        (prefix,),
    ).fetchone()
    if not row:
        return None
    target = dict(row)
    track_rows = conn.execute(
        """
        SELECT t.id AS track_id
        FROM tracks t
        WHERE t.album_id = ?
        ORDER BY t.id
        """,
        (target["album_id"],),
    ).fetchall()
    target["track_ids"] = [int(item["track_id"]) for item in track_rows]
    return target


def _paths_for_decision(conn: sqlite3.Connection, decision: ReviewDecision) -> list[Path]:
    if decision.target_type == "file":
        rows = conn.execute("SELECT path FROM files WHERE id = ?", (decision.target_id,)).fetchall()
    elif decision.target_type == "track":
        rows = conn.execute("SELECT path FROM files WHERE track_id = ?", (decision.target_id,)).fetchall()
    else:
        rows = conn.execute("SELECT f.path FROM files f JOIN tracks t ON t.id = f.track_id WHERE t.album_id = ? ORDER BY f.path", (decision.target_id,)).fetchall()
    return [Path(row["path"]) for row in rows]


def _field_definition(field: str) -> FieldDefinition | None:
    names = resolve_field_alias(field)
    if len(names) != 1:
        return None
    return get_field(names[0])


def _selected_value(decision: ReviewDecision, action: str, value: str | None) -> str:
    if value is not None:
        return value.strip()
    if action == "accept":
        return decision.candidate_value
    if action == "keep":
        return decision.current_value
    return decision.selected_value or decision.current_value or decision.candidate_value


def _resolution_plan(decision: ReviewDecision, field_def: FieldDefinition, selected_value: str, action: str, paths: list[Path], force: bool) -> tuple[ChangePlan, str]:
    plan = ChangePlan()
    if field_def.protected and action == "accept" and decision.current_value and selected_value != decision.current_value and not force:
        return plan, f"Protected field {field_def.name} requires --force to overwrite\nStatus: REVIEW"
    if action == "accept" and not selected_value and decision.current_value and not force:
        return plan, f"Refusing to clear non-empty {field_def.name} without --force\nStatus: REVIEW"
    if action in {"keep", "skip", "reject"}:
        for path in paths or [Path(decision.target_id)]:
            plan.add_skip(path, decision.target_type, field_def.name, f"{action} manual review", old_value=decision.current_value, new_value=selected_value, source=decision.provider)
        return plan, ""
    if not field_def.writable or field_def.name not in TAG_FIELD_LABELS:
        for path in paths or [Path(decision.target_id)]:
            plan.add_skip(path, decision.target_type, field_def.name, f"field is not directly writable by review; use the dedicated command", old_value=decision.current_value, new_value=selected_value, source=decision.provider)
        return plan, ""
    for path in paths or [Path(decision.target_id)]:
        plan.add_write(path, decision.target_type, field_def.name, decision.current_value, selected_value, source=decision.provider, confidence=decision.confidence, reason=decision.reason)
    return plan, ""


def _apply_resolution(conn: sqlite3.Connection, decision: ReviewDecision, field_def: FieldDefinition, selected_value: str, action: str, paths: list[Path], force: bool) -> int:
    if action in {"keep", "skip", "reject"}:
        return 0
    if not field_def.writable or field_def.name not in TAG_FIELD_LABELS:
        return 0
    _write_db_value(conn, decision, field_def, selected_value)
    for path in paths:
        _write_tag(path, TAG_FIELD_LABELS[field_def.name], selected_value)
    return len(paths)


def _write_db_value(conn: sqlite3.Connection, decision: ReviewDecision, field_def: FieldDefinition, value: str) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if field_def.name in {"style", "genre"}:
        table = "album_tags" if decision.target_type == "album" else "track_tags"
        key_id = "album_id" if table == "album_tags" else "track_id"
        conn.execute(f"DELETE FROM {table} WHERE {key_id} = ? AND key = ?", (decision.target_id, field_def.name))
        for item in _split_values(value):
            conn.execute(f"INSERT OR IGNORE INTO {table}({key_id}, key, value, type, source, confidence, updated_at) VALUES (?, ?, ?, ?, 'review', ?, ?)", (decision.target_id, field_def.name, item, field_def.name, decision.confidence, now))
        return
    if field_def.db_table == "albums" or decision.target_type == "album":
        column = field_def.db_column or field_def.name
        conn.execute(f"UPDATE albums SET {column} = ?, updated_at = ? WHERE id = ?", (value, now, decision.target_id))
        return
    if field_def.db_table == "tracks" or decision.target_type == "track":
        column = field_def.db_column or field_def.name
        conn.execute(f"UPDATE tracks SET {column} = ?, updated_at = ? WHERE id = ?", (value, now, decision.target_id))
        return
    if field_def.db_table == "audio_features":
        column = field_def.db_column or field_def.name
        conn.execute(f"UPDATE audio_features SET {column} = ?, updated_at = ? WHERE track_id = ?", (value, now, decision.target_id))


def _write_tag(path: Path, label: str, value: str) -> None:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall(f"TXXX:{label}")
        tags.add(TXXX(encoding=3, desc=label, text=_split_values(value) or [value]))
        tags.save(path)
        return
    audio = MutagenFile(path, easy=False)
    if audio is None:
        return
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        if audio.tags is None:
            audio.add_tags()
        audio.tags[f"----:com.apple.iTunes:{label}"] = [MP4FreeForm(item.encode("utf-8")) for item in (_split_values(value) or [value])]
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        audio[label.upper().replace(" ", "")] = _split_values(value) or [value]
    audio.save()


def _mark_resolved(conn: sqlite3.Connection, decision_id: int, action: str, selected_value: str) -> None:
    resolved = 0 if action == "skip" else 1
    conn.execute(
        """
        UPDATE field_decisions
        SET selected_value = ?, resolved = ?, resolved_action = ?, resolved_by = 'manual', resolved_at = ?
        WHERE id = ?
        """,
        (selected_value, resolved, action, datetime.now(UTC).isoformat(timespec="seconds"), decision_id),
    )


def _decision_from_row(row: sqlite3.Row) -> ReviewDecision:
    keys = set(row.keys())
    return ReviewDecision(
        id=int(row["id"]),
        provider_run_id=int(row["provider_run_id"]) if row["provider_run_id"] is not None else None,
        target_type=str(row["target_type"] or ""),
        target_id=str(row["target_id"] or ""),
        field=str(row["field"] or ""),
        current_value=str(row["current_value"] or ""),
        candidate_value=str(row["candidate_value"] or ""),
        selected_value=str(row["selected_value"] or ""),
        provider=str(row["provider"] or ""),
        confidence=str(row["confidence"] or ""),
        action=str(row["action"] or ""),
        reason=str(row["reason"] or ""),
        resolved=bool(row["resolved"]) if "resolved" in keys else False,
        resolved_action=str(row["resolved_action"] or "") if "resolved_action" in keys else "",
    )


def _candidates_by_run(conn: sqlite3.Connection, run_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not run_ids:
        return {}
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(f"SELECT * FROM provider_candidates WHERE provider_run_id IN ({placeholders}) ORDER BY score DESC, id", run_ids).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["provider_run_id"]), []).append(dict(row))
    return grouped


def _render_list(decisions: list[ReviewDecision], target: Path | None, output_format: str, verbose: bool, candidates: dict[int, list[dict[str, Any]]] | None = None) -> str:
    if output_format == "json":
        return json.dumps(_json_payload(decisions, target), indent=2, sort_keys=True)
    title = f"Review: {target}" if target else "Review"
    lines = [title, f"Pending decisions: {len(decisions)}", ""]
    for index, decision in enumerate(decisions, 1):
        field_def = get_field(decision.field)
        lines.append(f"{index}. {(field_def.label if field_def else decision.field).strip()}")
        lines.append("   Status: REVIEW")
        lines.append(f"   Reason: {_safe(decision.reason) or 'manual decision required'}")
        lines.append(f"   Current: {_safe(decision.current_value) or 'none'}")
        if decision.candidate_value:
            lines.append(f"   Candidate: {_safe(decision.candidate_value)}")
        run_candidates = (candidates or {}).get(decision.provider_run_id or -1, [])
        if run_candidates:
            lines.append("   Suggested:")
            for candidate in run_candidates[:5]:
                summary = _candidate_summary(candidate)
                lines.append(f"   - {candidate.get('external_id')}{(' ' + summary) if summary else ''}")
        if verbose:
            lines.append(f"   Provider: {decision.provider or 'unknown'}")
            lines.append(f"   Confidence: {decision.confidence or 'unknown'}")
        lines.append("")
    if decisions:
        first = decisions[0].id
        lines.extend(["Next:", f"- noqlen-forge review show {first}", f"- noqlen-forge review resolve {first} --action accept --apply", ""])
    lines.append(f"Status: {'REVIEW' if decisions else 'OK'}")
    return "\n".join(lines).rstrip()


def _render_show(decision: ReviewDecision, output_format: str, verbose: bool, candidates: list[dict[str, Any]]) -> str:
    if output_format == "json":
        return json.dumps(_json_payload([decision], None), indent=2, sort_keys=True)
    field_def = get_field(decision.field)
    lines = [f"Review decision {decision.id}", f"Field: {field_def.label if field_def else decision.field}", "Status: REVIEW" if not decision.resolved else "Status: OK", f"Reason: {_safe(decision.reason) or 'manual decision required'}", f"Current: {_safe(decision.current_value) or 'none'}", f"Candidate: {_safe(decision.candidate_value) or 'none'}", f"Provider: {decision.provider or 'unknown'}", f"Confidence: {decision.confidence or 'unknown'}"]
    if candidates:
        lines.append("Candidates:")
        for candidate in candidates:
            summary = _candidate_summary(candidate)
            lines.append(f"- {candidate.get('external_id')}{(' ' + summary) if summary else ''}")
    lines.extend(["", "Actions: accept, keep, skip, reject"])
    return "\n".join(lines)


def _render_resolve(decision: ReviewDecision, action: str, value: str, plan: ChangePlan, *, apply: bool, verbose: bool, written: int = 0) -> str:
    status = "OK" if apply else "DRY"
    lines = [f"Review resolve: {decision.id}", f"Mode: {'APPLY' if apply else 'DRY-RUN'}", f"Action: {action}", f"Selected value: {_safe(value) or 'none'}", render_plan(plan, verbose=verbose)]
    if apply:
        lines.append(f"Tag writes: {written}")
    lines.append(f"Status: {status}")
    return "\n".join(lines)


def _json_payload(decisions: list[ReviewDecision], target: Path | None) -> dict[str, Any]:
    return {"status": "REVIEW" if decisions else "OK", "target": str(target) if target else "", "pending": len(decisions), "decisions": [_decision_json(item) for item in decisions]}


def _decision_json(decision: ReviewDecision) -> dict[str, Any]:
    return {"id": decision.id, "field": decision.field, "target_type": decision.target_type, "current_value": _safe(decision.current_value), "candidate_value": _safe(decision.candidate_value), "provider": decision.provider, "confidence": decision.confidence, "reason": _safe(decision.reason), "actions": ["accept", "keep", "skip", "reject"]}


def _candidate_summary(candidate: dict[str, Any]) -> str:
    try:
        payload = json.loads(candidate.get("payload_summary_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    parts = [str(payload.get(key, "")).strip() for key in ("format", "country", "title", "summary")]
    return ", ".join(item for item in parts if item)[:120]


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace(";", ";").split(";") if item.strip()]


def _safe(value: Any) -> str:
    text = str(value or "")
    lowered = text.casefold()
    if "lyric" in lowered or "fingerprint" in lowered or any(marker in lowered for marker in ("api_key", "apikey", "token=", "secret=")):
        return "[redacted sensitive output]"
    return text if len(text) <= 300 else text[:297] + "..."
