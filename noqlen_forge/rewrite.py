from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audio import Track, audio_files, get_tag, read_track
from .config import get_config_value
from .db import apply_migrations, connect, connect_readonly, normalize_path, record_field_decision, record_operation, record_provider_run, finish_operation, upsert_album, upsert_audio_features, upsert_file, upsert_track
from .fields import FieldDefinition, get_field, is_protected_field, resolve_field_alias
from .sync import ALBUM_COLUMNS, FEATURE_COLUMNS, TRACK_COLUMNS, TRACK_TAG_FIELDS, _album_metadata, _audio_feature_metadata, _file_metadata, _load_db_records_from_conn, _target_type, _track_metadata, _typed_value
from .writers import WritePlan, apply_musicbrainz_writes


SUPPORTED_REWRITE_FIELDS = {
    "artist",
    "albumartist",
    "album",
    "title",
    "genre",
    "style",
    "mood",
    "label",
    "release_type",
    "country",
    "media",
    "edition",
    "lastfm_tags",
}

PROTECTED_REWRITE_FIELDS = {"mb_album_id", "mb_track_id", "mb_release_group_id", "acoustid_id", "isrc"}


@dataclass(slots=True, frozen=True)
class RewriteRuleSet:
    rules: dict[str, dict[str, str]]
    case_sensitive: bool
    separator: str
    trim_values: bool
    dedupe_values: bool

    @property
    def count(self) -> int:
        return sum(len(values) for values in self.rules.values())


@dataclass(slots=True, frozen=True)
class RewriteDecision:
    path: Path
    field: str
    target_type: str
    target_id: int | None
    old_value: str
    new_value: str
    target: str
    action: str
    reason: str = ""


@dataclass(slots=True)
class RewritePlan:
    target: Path
    apply: bool
    apply_to_tags: bool
    apply_to_db: bool
    files: list[Path]
    rules: RewriteRuleSet
    decisions: list[RewriteDecision]
    read_errors: list[str]

    @property
    def tag_writes(self) -> int:
        return len({item.path for item in self.decisions if item.action == "write" and item.target == "tags"})

    @property
    def db_updates(self) -> int:
        return len({item.path for item in self.decisions if item.action == "write" and item.target == "db"})

    @property
    def changes(self) -> int:
        return sum(1 for item in self.decisions if item.action == "write")

    @property
    def conflicts(self) -> int:
        return sum(1 for item in self.decisions if item.action == "review")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.decisions if item.action == "skip")


def rewrite_path(
    target: Path,
    config: dict[str, Any],
    *,
    apply: bool = False,
    fields: list[str] | None = None,
    db_only: bool = False,
    tags_only: bool = False,
    force: bool = False,
    verbose: bool = False,
    debug: bool = False,
) -> tuple[int, str]:
    if not bool(get_config_value(config, "rewrite", "enabled", True)):
        return 0, "Rewrite: disabled by config\nStatus: OK"
    if db_only and tags_only:
        return 1, "Cannot combine --db-only and --tags-only"
    apply_to_tags = not db_only and bool(get_config_value(config, "rewrite", "apply_to_tags", True))
    apply_to_db = not tags_only and bool(get_config_value(config, "rewrite", "apply_to_db", True))
    try:
        selected = _selected_fields(fields)
    except ValueError as exc:
        return 1, str(exc)
    rules = load_rewrite_rules(config, selected)
    plan = plan_rewrite(target, config, rules, apply=apply, apply_to_tags=apply_to_tags, apply_to_db=apply_to_db, fields=selected, force=force)
    status = "REVIEW" if plan.conflicts else "WARN" if plan.read_errors else "OK"
    errors: list[str] = []
    if apply and status == "OK":
        if apply_to_tags:
            errors.extend(_apply_rewrite_to_tags(plan))
        if not errors and apply_to_db:
            _apply_rewrite_to_db(config, plan)
        if errors:
            status = "FAIL"
    elif apply and status == "REVIEW":
        _record_rewrite_operation(config, plan, "review")
    return (1 if status in {"REVIEW", "FAIL"} else 0), _render_plan(plan, status=status, verbose=verbose, debug=debug, errors=errors)


def load_rewrite_rules(config: dict[str, Any], fields: list[str] | None = None) -> RewriteRuleSet:
    rewrite = config.get("rewrite") if isinstance(config.get("rewrite"), dict) else {}
    case_sensitive = bool(rewrite.get("case_sensitive", False))
    multi = rewrite.get("multi_value") if isinstance(rewrite.get("multi_value"), dict) else {}
    selected = set(fields or SUPPORTED_REWRITE_FIELDS)
    rules: dict[str, dict[str, str]] = {}
    for raw_field, raw_rules in rewrite.items():
        if raw_field in {"enabled", "case_sensitive", "apply_to_db", "apply_to_tags", "dry_run_by_default", "multi_value"}:
            continue
        definition = get_field(raw_field)
        if not definition or definition.name not in selected or not isinstance(raw_rules, dict):
            continue
        clean_rules = {str(old): str(new).strip() for old, new in raw_rules.items() if str(old).strip() and str(new).strip()}
        if clean_rules:
            rules[definition.name] = clean_rules
    return RewriteRuleSet(rules=rules, case_sensitive=case_sensitive, separator=str(multi.get("separator", "; ") or "; "), trim_values=bool(multi.get("trim_values", True)), dedupe_values=bool(multi.get("dedupe_values", True)))


def plan_rewrite(target: Path, config: dict[str, Any], rules: RewriteRuleSet, *, apply: bool, apply_to_tags: bool, apply_to_db: bool, fields: list[str], force: bool = False) -> RewritePlan:
    paths = audio_files(target)
    decisions: list[RewriteDecision] = []
    read_errors: list[str] = []
    db_records = _load_records(config, paths) if apply_to_db else {}
    for path in paths:
        track: Track | None = None
        if apply_to_tags:
            try:
                track = read_track(path)
            except Exception as exc:
                read_errors.append(f"{path}: {exc}")
        for field in fields:
            definition = get_field(field)
            if not definition or field not in rules.rules:
                continue
            if definition.protected and not force:
                decisions.append(RewriteDecision(path, field, _target_type(field), None, "", "", "tags" if apply_to_tags else "db", "review", "protected identity requires --force"))
                continue
            if apply_to_tags and track is not None:
                old_value = _tag_value(track, field)
                _add_decision(decisions, path, field, None, old_value, rules, target="tags", definition=definition)
            if apply_to_db:
                record = db_records.get(normalize_path(path), {})
                old_value = _db_value(record, field)
                target_id = record.get(f"{_target_type(field)}_id")
                _add_decision(decisions, path, field, target_id, old_value, rules, target="db", definition=definition)
    return RewritePlan(target=target, apply=apply, apply_to_tags=apply_to_tags, apply_to_db=apply_to_db, files=paths, rules=rules, decisions=decisions, read_errors=read_errors)


def _add_decision(decisions: list[RewriteDecision], path: Path, field: str, target_id: int | None, old_value: str, rules: RewriteRuleSet, *, target: str, definition: FieldDefinition) -> None:
    if not old_value.strip():
        return
    new_value = rewrite_value(old_value, rules.rules[field], rules, multi_value=definition.multi_value)
    if not new_value.strip() or new_value == old_value:
        return
    decisions.append(RewriteDecision(path, field, _target_type(field), target_id, old_value, new_value, target, "write", "rewrite rule"))


def rewrite_value(value: str, field_rules: dict[str, str], rules: RewriteRuleSet, *, multi_value: bool) -> str:
    parts = _split_values(value, rules.separator) if multi_value else [value]
    rewritten: list[str] = []
    seen: set[str] = set()
    lookup = field_rules if rules.case_sensitive else {key.casefold(): replacement for key, replacement in field_rules.items()}
    for part in parts:
        item = part.strip() if rules.trim_values else part
        if not item:
            continue
        key = item if rules.case_sensitive else item.casefold()
        new_item = lookup.get(key, item).strip()
        if not new_item:
            continue
        dedupe_key = new_item if rules.case_sensitive else new_item.casefold()
        if rules.dedupe_values and dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rewritten.append(new_item)
    return rules.separator.join(rewritten) if multi_value else (rewritten[0] if rewritten else "")


def _split_values(value: str, separator: str) -> list[str]:
    if separator and separator in value:
        return value.split(separator)
    if ";" in value:
        return value.split(";")
    return [value]


def _apply_rewrite_to_tags(plan: RewritePlan) -> list[str]:
    grouped: dict[Path, dict[str, str]] = {}
    for decision in plan.decisions:
        if decision.action == "write" and decision.target == "tags":
            field = get_field(decision.field)
            tag_name = field.tag_names[-1] if field and field.tag_names else decision.field
            grouped.setdefault(decision.path, {})[tag_name] = decision.new_value
    return apply_musicbrainz_writes([WritePlan(path=path, changes=changes) for path, changes in grouped.items()], apply=True)


def _apply_rewrite_to_db(config: dict[str, Any], plan: RewritePlan) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        op_id = record_operation(conn, "rewrite", "path", normalize_path(plan.target), "apply", "running", f"{plan.db_updates} db updates")
        run_id = record_provider_run(conn, "rewrite", "path", normalize_path(plan.target), "ok", query="rewrite")
        _ensure_db_records(conn, plan.files)
        records = _load_db_records_from_conn(conn, plan.files)
        for decision in plan.decisions:
            if decision.action != "write" or decision.target != "db":
                continue
            record = records.get(normalize_path(decision.path), {})
            _write_db_field(conn, record, decision.field, decision.new_value)
            target_id = decision.target_id or record.get(f"{decision.target_type}_id") or ""
            record_field_decision(conn, run_id, decision.target_type, target_id, decision.field, current_value=decision.old_value, candidate_value=decision.new_value, selected_value=decision.new_value, provider="rewrite", confidence="local", action="write_db", reason=decision.reason)
        finish_operation(conn, op_id, "ok")
        conn.commit()


def _record_rewrite_operation(config: dict[str, Any], plan: RewritePlan, status: str) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        op_id = record_operation(conn, "rewrite", "path", normalize_path(plan.target), "apply", status, f"{plan.conflicts} conflicts")
        finish_operation(conn, op_id, status)
        conn.commit()


def _ensure_db_records(conn: sqlite3.Connection, paths: list[Path]) -> None:
    records = _load_db_records_from_conn(conn, paths)
    for path in paths:
        if normalize_path(path) in records:
            continue
        track = read_track(path)
        album_id = upsert_album(conn, _album_metadata(track))
        track_id = upsert_track(conn, _track_metadata(track), album_id=album_id)
        upsert_file(conn, track.path, _file_metadata(track), track_id=track_id)
        upsert_audio_features(conn, track_id, _audio_feature_metadata(track))


def _write_db_field(conn: sqlite3.Connection, record: dict[str, Any], field: str, value: str) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if (field in ALBUM_COLUMNS or field == "media") and record.get("album_id"):
        column = "release_format" if field == "media" else field
        conn.execute(f"UPDATE albums SET {column} = ?, updated_at = ? WHERE id = ?", (value, now, record["album_id"]))
    if field in TRACK_COLUMNS and record.get("track_id"):
        conn.execute(f"UPDATE tracks SET {field} = ?, updated_at = ? WHERE id = ?", (_typed_value(field, value), now, record["track_id"]))
    if field in FEATURE_COLUMNS and record.get("track_id"):
        conn.execute(f"INSERT INTO audio_features(track_id, {field}, source, confidence, updated_at) VALUES (?, ?, 'rewrite', 'local', ?) ON CONFLICT(track_id) DO UPDATE SET {field} = excluded.{field}, updated_at = excluded.updated_at", (record["track_id"], _typed_value(field, value), now))
    if field in TRACK_TAG_FIELDS and record.get("track_id"):
        conn.execute("DELETE FROM track_tags WHERE track_id = ? AND key = ?", (record["track_id"], field))
        for item in _split_values(value, "; "):
            clean = item.strip()
            if clean:
                conn.execute("INSERT OR IGNORE INTO track_tags(track_id, key, value, type, source, confidence, updated_at) VALUES (?, ?, ?, 'tag', 'rewrite', 'local', ?)", (record["track_id"], field, clean, now))
    if record.get("file_id"):
        stamp = datetime.now(UTC).timestamp()
        conn.execute("UPDATE files SET db_mtime = ?, updated_at = ? WHERE id = ?", (stamp, now, record["file_id"]))


def _load_records(config: dict[str, Any], paths: list[Path]) -> dict[str, dict[str, Any]]:
    conn = connect_readonly(config)
    if conn is None:
        return {}
    with conn:
        records = _load_db_records_from_conn(conn, paths)
        _load_multi_values(conn, records)
        return records


def _load_multi_values(conn: sqlite3.Connection, records: dict[str, dict[str, Any]]) -> None:
    track_ids = [item["track_id"] for item in records.values() if item.get("track_id")]
    if not track_ids:
        return
    placeholders = ", ".join("?" for _ in track_ids)
    rows = conn.execute(f"SELECT track_id, key, value FROM track_tags WHERE track_id IN ({placeholders}) ORDER BY id", track_ids).fetchall()
    by_track = {item.get("track_id"): item for item in records.values()}
    grouped: dict[tuple[int, str], list[str]] = {}
    for row in rows:
        grouped.setdefault((row["track_id"], row["key"]), []).append(row["value"])
    for (track_id, key), values in grouped.items():
        by_track.get(track_id, {})[key] = "; ".join(values)


def _tag_value(track: Track, field: str) -> str:
    direct = {
        "album": track.album,
        "albumartist": track.albumartist,
        "artist": track.artist,
        "title": track.title,
    }
    if field in direct:
        return direct[field]
    values = get_tag(track, field)
    return "; ".join(values)


def _db_value(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if value is None:
        return ""
    return str(value).strip()


def _selected_fields(fields: list[str] | None) -> list[str]:
    if not fields:
        return sorted(SUPPORTED_REWRITE_FIELDS)
    selected: list[str] = []
    for field in fields:
        resolved = resolve_field_alias(field)
        for clean in resolved:
            if clean not in SUPPORTED_REWRITE_FIELDS and clean not in PROTECTED_REWRITE_FIELDS:
                raise ValueError(f"Unsupported rewrite field: {field}")
            selected.append(clean)
    return list(dict.fromkeys(selected))


def _render_plan(plan: RewritePlan, *, status: str, verbose: bool, debug: bool, errors: list[str] | None = None) -> str:
    mode = "APPLY" if plan.apply else "DRY-RUN"
    write_status = "DRY" if not plan.apply and plan.changes else "OK" if status == "OK" else status
    title = _title(plan)
    lines = [f"Rewrite: {title}", f"Files: {len(plan.files)}", f"Mode: {mode}", ""]
    lines.append(f"[1/4] Read tags        {'OK' if not plan.read_errors else 'WARN':<7} {len(plan.files) - len(plan.read_errors)}/{len(plan.files)} files")
    lines.append(f"[2/4] Load rules       OK      {plan.rules.count} rules")
    lines.append(f"[3/4] Plan changes     {'REVIEW' if plan.conflicts else 'OK':<7} {plan.changes} changes")
    action = "would update" if not plan.apply else "updated"
    lines.append(f"[4/4] Apply rewrite    {write_status:<7} {action} {plan.tag_writes if plan.apply_to_tags else plan.db_updates} files")
    planned = [item for item in plan.decisions if item.action == "write"]
    if planned:
        lines.extend(["", "Planned:"])
        seen: set[tuple[str, str, str]] = set()
        for item in planned:
            key = (item.field, item.old_value, item.new_value)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {item.field}: {item.old_value} -> {item.new_value}")
    reviews = [item for item in plan.decisions if item.action == "review"]
    if reviews:
        lines.extend(["", "Review:"])
        for item in reviews:
            lines.append(f"- {item.field}: {item.reason}. Resolve with noqlen-forge review or rerun with --force if intentional.")
    lines.extend(["", "Final:"])
    lines.append(f"{'Would update tags' if not plan.apply else 'Updated tags'}: {plan.tag_writes if plan.apply_to_tags else 0}")
    lines.append(f"{'Would update DB' if not plan.apply else 'Updated DB'}: {plan.db_updates if plan.apply_to_db else 0}")
    lines.append(f"Skipped: {plan.skipped}")
    lines.append(f"Status: {status}")
    if verbose:
        lines.extend(["", "Decisions:"])
        for item in plan.decisions:
            lines.append(f"- {item.path.name}: {item.target} {item.field} {item.action} ({item.reason})")
    if debug and plan.read_errors:
        lines.extend(["", "Read errors:"])
        lines.extend(f"- {error}" for error in plan.read_errors)
    if errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in errors)
    return "\n".join(lines)


def _title(plan: RewritePlan) -> str:
    if not plan.files:
        return str(plan.target)
    try:
        track = read_track(plan.files[0])
        artist = track.albumartist or track.artist
        return f"{artist} - {track.album}" if artist and track.album else str(plan.target)
    except Exception:
        return str(plan.target)
