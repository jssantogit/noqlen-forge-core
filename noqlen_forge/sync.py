from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audio import Track, audio_files, get_tag, read_track
from .config import get_config_value
from .db import (
    apply_migrations,
    connect,
    connect_readonly,
    normalize_path,
    record_field_decision,
    record_provider_run,
    upsert_album,
    upsert_audio_features,
    upsert_file,
    upsert_track,
    _album_metadata,
    _audio_feature_metadata,
    _file_metadata,
    _finish_operation,
    _record_operation,
    _track_metadata,
)
from .fields import get_syncable_fields, is_asset_field, is_protected_field, resolve_field_alias
from .writers import WritePlan, apply_musicbrainz_writes


CONFLICT_POLICIES = {"review", "db-wins", "tags-wins", "skip"}
IDENTITY_FIELDS = {field.name for field in get_syncable_fields() if field.protected}
TRACK_TAG_FIELDS = {"genre", "style", "lastfm_tags"}
PRESENCE_FIELDS = {field.name for field in get_syncable_fields() if is_asset_field(field)}


@dataclass(slots=True)
class SyncDecision:
    path: Path
    field: str
    target_type: str
    target_id: int | None
    db_value: str
    tag_value: str
    action: str
    reason: str


@dataclass(slots=True)
class SyncPlan:
    target: Path
    direction: str
    apply: bool
    decisions: list[SyncDecision]
    files: list[Path]
    read_errors: list[str]

    @property
    def conflicts(self) -> int:
        return sum(1 for item in self.decisions if item.action == "review")

    @property
    def db_updates(self) -> int:
        return sum(1 for item in self.decisions if item.action == "write_db")

    @property
    def tag_writes(self) -> int:
        return sum(1 for item in self.decisions if item.action == "write_tags")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.decisions if item.action == "skip")


SYNC_FIELDS = [field.name for field in get_syncable_fields()]

FIELD_TO_TAG = {field.name: (field.tag_names[-1] if field.tag_names else field.label) for field in get_syncable_fields()}

ALBUM_COLUMNS = {"album", "albumartist", "date", "originaldate", "mb_album_id", "mb_release_group_id", "label", "catalog_number", "barcode", "country", "release_type", "edition"}
TRACK_COLUMNS = {"artist", "title", "track", "tracktotal", "disc", "disctotal", "mb_track_id", "mb_release_track_id", "acoustid_id", "isrc", "bpm", "key", "mood", "energy", "danceability", "albumartist"}
FEATURE_COLUMNS = {"bpm", "key", "replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak", "loudness", "energy", "danceability"}


def sync_path(
    target: Path,
    config: dict[str, Any],
    direction: str | None = None,
    apply: bool = False,
    force: bool = False,
    fields: list[str] | None = None,
    conflict_policy: str | None = None,
    verbose: bool = False,
    debug: bool = False,
) -> tuple[int, str]:
    configured_direction = str(get_config_value(config, "sync", "default_direction", "tags-to-db"))
    direction = direction or configured_direction
    if direction == "refresh":
        return 0, _render_refresh(target, apply)
    if direction not in {"tags-to-db", "db-to-tags"}:
        return 1, f"Invalid sync direction: {direction}"
    policy = conflict_policy or str(get_config_value(config, "sync", "conflict_policy", "review"))
    if policy not in CONFLICT_POLICIES:
        return 1, f"Invalid conflict policy: {policy}"
    selected_fields = _selected_fields(fields)
    write_empty_fields = bool(get_config_value(config, "sync", "write_empty_fields", False))
    protect_identity = bool(get_config_value(config, "sync", "protect_identity_fields", True))
    plan = plan_sync(target, config, direction, apply, force, selected_fields, policy, write_empty_fields, protect_identity)
    status = "REVIEW" if plan.conflicts else "OK"
    if apply and status != "REVIEW":
        if direction == "tags-to-db":
            _apply_sync_to_db(config, plan)
        else:
            errors = _apply_sync_to_tags(plan)
            if errors:
                return 1, _render_plan(plan, status="FAIL", verbose=verbose, debug=debug, errors=errors)
            _touch_db_tag_mtime(config, plan)
    elif apply:
        _record_review_operation(config, plan)
    return (1 if status == "REVIEW" else 0), _render_plan(plan, status=status, verbose=verbose, debug=debug)


def plan_sync(target: Path, config: dict[str, Any], direction: str, apply: bool, force: bool, fields: list[str], conflict_policy: str, write_empty_fields: bool = False, protect_identity: bool = True) -> SyncPlan:
    paths = audio_files(target)
    db_records = _load_db_records(config, paths)
    decisions: list[SyncDecision] = []
    errors: list[str] = []
    for path in paths:
        try:
            track = read_track(path)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            continue
        db_record = db_records.get(normalize_path(path), {})
        tag_record = _tag_record(track)
        for field in fields:
            db_value = _clean_value(db_record.get(field))
            tag_value = _clean_value(tag_record.get(field))
            if db_value == tag_value:
                continue
            decision = _decision(path, field, db_record, db_value, tag_value, direction, force, conflict_policy, write_empty_fields, protect_identity)
            decisions.append(decision)
    return SyncPlan(target=target, direction=direction, apply=apply, decisions=decisions, files=paths, read_errors=errors)


def _decision(path: Path, field: str, db_record: dict[str, Any], db_value: str, tag_value: str, direction: str, force: bool, policy: str, write_empty_fields: bool, protect_identity: bool) -> SyncDecision:
    protected = protect_identity and is_protected_field(field)
    target_type = _target_type(field)
    target_id = db_record.get(f"{target_type}_id")
    if field in PRESENCE_FIELDS:
        return SyncDecision(path, field, "file", db_record.get("file_id"), db_value, tag_value, "skip", "presence-only field")
    if direction == "tags-to-db":
        if not tag_value:
            action = "write_db" if db_value and write_empty_fields and force else "skip"
            reason = "empty tag value selected" if action == "write_db" else "empty tag value"
            return SyncDecision(path, field, target_type, target_id, db_value, tag_value, action, reason)
        if db_value and protected and not force:
            return SyncDecision(path, field, target_type, target_id, db_value, tag_value, "review", "protected identity differs")
        if db_value and policy == "review":
            return SyncDecision(path, field, target_type, target_id, db_value, tag_value, "review", "conflict")
        if db_value and policy in {"skip", "db-wins"}:
            return SyncDecision(path, field, target_type, target_id, db_value, tag_value, "skip", f"conflict policy {policy}")
        return SyncDecision(path, field, target_type, target_id, db_value, tag_value, "write_db", "tags value selected")
    if not db_value:
        action = "write_tags" if tag_value and write_empty_fields and force else "skip"
        reason = "empty database value selected" if action == "write_tags" else "empty database value"
        return SyncDecision(path, field, target_type, target_id, db_value, tag_value, action, reason)
    if tag_value and protected and not force:
        return SyncDecision(path, field, target_type, target_id, db_value, tag_value, "review", "protected identity differs")
    if tag_value and policy == "review":
        return SyncDecision(path, field, target_type, target_id, db_value, tag_value, "review", "conflict")
    if tag_value and policy in {"skip", "tags-wins"}:
        return SyncDecision(path, field, target_type, target_id, db_value, tag_value, "skip", f"conflict policy {policy}")
    return SyncDecision(path, field, target_type, target_id, db_value, tag_value, "write_tags", "database value selected")


def _apply_sync_to_db(config: dict[str, Any], plan: SyncPlan) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        op_id = _record_operation(conn, "sync", "path", str(plan.target), "apply", "running", f"{plan.direction}: {plan.db_updates} db updates")
        run_id = record_provider_run(conn, "sync", "path", str(plan.target), "ok", query=plan.direction)
        records = _load_db_records_from_conn(conn, plan.files)
        tracks = {normalize_path(path): read_track(path) for path in plan.files}
        for key, track in tracks.items():
            if key not in records:
                album_id = upsert_album(conn, _album_metadata(track))
                track_id = upsert_track(conn, _track_metadata(track), album_id=album_id)
                upsert_file(conn, track.path, _file_metadata(track), track_id=track_id)
                upsert_audio_features(conn, track_id, _audio_feature_metadata(track))
        records = _load_db_records_from_conn(conn, plan.files)
        for decision in plan.decisions:
            if decision.action != "write_db":
                continue
            record = records.get(normalize_path(decision.path), {})
            _write_db_field(conn, record, decision.field, decision.tag_value)
            record_field_decision(conn, run_id, decision.target_type, decision.target_id or record.get(f"{decision.target_type}_id") or "", decision.field, current_value=decision.db_value, candidate_value=decision.tag_value, selected_value=decision.tag_value, provider="sync", confidence="local", action="write_db", reason=decision.reason)
        _finish_operation(conn, op_id, "ok")
        conn.commit()


def _apply_sync_to_tags(plan: SyncPlan) -> list[str]:
    grouped: dict[Path, dict[str, str]] = {}
    for decision in plan.decisions:
        if decision.action == "write_tags" and decision.field not in PRESENCE_FIELDS:
            grouped.setdefault(decision.path, {})[FIELD_TO_TAG[decision.field]] = decision.db_value
    return apply_musicbrainz_writes([WritePlan(path=path, changes=changes) for path, changes in grouped.items()], apply=True)


def _touch_db_tag_mtime(config: dict[str, Any], plan: SyncPlan) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        op_id = _record_operation(conn, "sync", "path", str(plan.target), "apply", "running", f"{plan.direction}: {plan.tag_writes} tag writes")
        run_id = record_provider_run(conn, "sync", "path", str(plan.target), "ok", query=plan.direction)
        now = datetime.now(UTC).timestamp()
        for path in {item.path for item in plan.decisions if item.action == "write_tags"}:
            conn.execute("UPDATE files SET tag_mtime = ?, db_mtime = ?, updated_at = ? WHERE path = ?", (now, now, _now(), normalize_path(path)))
        for decision in plan.decisions:
            if decision.action == "write_tags":
                record_field_decision(conn, run_id, decision.target_type, decision.target_id or "", decision.field, current_value=decision.tag_value, candidate_value=decision.db_value, selected_value=decision.db_value, provider="sync", confidence="local", action="write_tags", reason=decision.reason)
        _finish_operation(conn, op_id, "ok")
        conn.commit()


def _record_review_operation(config: dict[str, Any], plan: SyncPlan) -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        op_id = _record_operation(conn, "sync", "path", str(plan.target), "apply", "review", f"{plan.direction}: {plan.conflicts} conflicts")
        _finish_operation(conn, op_id, "review")
        conn.commit()


def _write_db_field(conn: sqlite3.Connection, record: dict[str, Any], field: str, value: str) -> None:
    now = _now()
    if (field in ALBUM_COLUMNS or field == "media") and record.get("album_id"):
        column = "release_format" if field == "media" else field
        conn.execute(f"UPDATE albums SET {column} = ?, updated_at = ? WHERE id = ?", (value, now, record["album_id"]))
    if field in TRACK_COLUMNS and record.get("track_id"):
        conn.execute(f"UPDATE tracks SET {field} = ?, updated_at = ? WHERE id = ?", (_typed_value(field, value), now, record["track_id"]))
    if field in FEATURE_COLUMNS and record.get("track_id"):
        conn.execute(
            f"INSERT INTO audio_features(track_id, {field}, source, confidence, updated_at) VALUES (?, ?, 'tags', 'tag', ?) ON CONFLICT(track_id) DO UPDATE SET {field} = excluded.{field}, updated_at = excluded.updated_at",
            (record["track_id"], _typed_value(field, value), now),
        )
    if field in TRACK_TAG_FIELDS and record.get("track_id"):
        conn.execute("DELETE FROM track_tags WHERE track_id = ? AND key = ?", (record["track_id"], field))
        conn.execute("INSERT OR IGNORE INTO track_tags(track_id, key, value, type, source, confidence, updated_at) VALUES (?, ?, ?, 'tag', 'sync', 'local', ?)", (record["track_id"], field, value, now))
    if record.get("file_id"):
        stamp = datetime.now(UTC).timestamp()
        conn.execute("UPDATE files SET db_mtime = ?, updated_at = ? WHERE id = ?", (stamp, now, record["file_id"]))


def _load_db_records(config: dict[str, Any], paths: list[Path]) -> dict[str, dict[str, Any]]:
    conn = connect_readonly(config)
    if conn is None:
        return {}
    with conn:
        return _load_db_records_from_conn(conn, paths)


def _load_db_records_from_conn(conn: sqlite3.Connection, paths: list[Path]) -> dict[str, dict[str, Any]]:
    if not paths:
        return {}
    normalized = [normalize_path(path) for path in paths]
    placeholders = ", ".join("?" for _ in normalized)
    rows = conn.execute(
        f"""
        SELECT f.id AS file_id, f.path, f.has_cover AS cover, f.has_lyrics AS lyrics,
               t.id AS track_id, t.title, t.artist, t.albumartist, t.track, t.tracktotal, t.disc, t.disctotal,
               t.mb_track_id, t.mb_release_track_id, t.acoustid_id, t.isrc, t.bpm, t.key, t.mood, t.energy, t.danceability,
               a.id AS album_id, a.album, a.albumartist AS album_albumartist, a.date, a.originaldate, a.mb_album_id,
               a.mb_release_group_id, a.label, a.catalog_number, a.barcode, a.country, a.release_format AS media,
               a.release_type, a.edition,
               af.replaygain_track_gain, af.replaygain_track_peak, af.replaygain_album_gain, af.replaygain_album_peak, af.loudness
        FROM files f
        LEFT JOIN tracks t ON t.id = f.track_id
        LEFT JOIN albums a ON a.id = t.album_id
        LEFT JOIN audio_features af ON af.track_id = t.id
        WHERE f.path IN ({placeholders})
        """,
        normalized,
    ).fetchall()
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["albumartist"] = item.get("album_albumartist") or item.get("albumartist")
        records[item["path"]] = item
    if records:
        track_ids = [str(item["track_id"]) for item in records.values() if item.get("track_id")]
        if track_ids:
            tag_rows = conn.execute(f"SELECT track_id, key, value FROM track_tags WHERE track_id IN ({', '.join('?' for _ in track_ids)})", track_ids).fetchall()
            by_track = {item.get("track_id"): item for item in records.values()}
            for tag in tag_rows:
                by_track.get(tag["track_id"], {})[tag["key"]] = tag["value"]
    return records


def _tag_record(track: Track) -> dict[str, Any]:
    values: dict[str, Any] = {
        "album": track.album,
        "albumartist": track.albumartist,
        "artist": track.artist,
        "title": track.title,
        "track": track.tracknumber,
        "date": track.date,
        "cover": "1" if get_tag(track, "cover") else "0",
        "lyrics": "1" if get_tag(track, "lyrics") or get_tag(track, "synced_lyrics") else "0",
    }
    for field in SYNC_FIELDS:
        if field in values or field in PRESENCE_FIELDS:
            continue
        values[field] = _first_tag(track, field)
    return values


def _selected_fields(fields: list[str] | None) -> list[str]:
    if not fields:
        return SYNC_FIELDS
    selected = []
    for field in fields:
        resolved = resolve_field_alias(field)
        if not resolved:
            raise ValueError(f"Unsupported sync field: {field}")
        for clean in resolved:
            if clean not in SYNC_FIELDS:
                raise ValueError(f"Unsupported sync field: {field}")
            selected.append(clean)
    return list(dict.fromkeys(selected))


def _first_tag(track: Track, field: str) -> str:
    values = get_tag(track, field)
    return values[0] if values else ""


def _clean_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return (f"{value:.6f}").rstrip("0").rstrip(".")
    return str(value).strip()


def _typed_value(field: str, value: str) -> Any:
    if field in {"track", "tracktotal", "disc", "disctotal", "energy", "danceability"}:
        try:
            return int(float(value))
        except ValueError:
            return None
    if field in {"bpm", "replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak", "loudness"}:
        try:
            return float(value)
        except ValueError:
            return None
    return value


def _target_type(field: str) -> str:
    if field in ALBUM_COLUMNS or field == "media":
        return "album"
    if field in PRESENCE_FIELDS:
        return "file"
    return "track"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _render_plan(plan: SyncPlan, status: str, verbose: bool = False, debug: bool = False, errors: list[str] | None = None) -> str:
    mode = "APPLY" if plan.apply else "DRY-RUN"
    write_step = "Update database" if plan.direction == "tags-to-db" else "Write tags"
    write_count = plan.db_updates if plan.direction == "tags-to-db" else plan.tag_writes
    write_status = "DRY" if not plan.apply and write_count else "OK" if plan.apply and status == "OK" else "SKIP" if status == "REVIEW" else "OK"
    lines = [f"Mode: {mode}", f"Direction: {plan.direction}", ""]
    lines.append(f"[1/4] Read database       OK      {len(plan.files)} files known")
    lines.append(f"[2/4] Read tags           {'OK' if not plan.read_errors else 'WARN':<7} {len(plan.files) - len(plan.read_errors)}/{len(plan.files)} files")
    compare_status = "REVIEW" if plan.conflicts else "OK"
    lines.append(f"[3/4] Compare             {compare_status:<7} {len(plan.decisions)} changed fields")
    lines.append(f"[4/4] {write_step:<19} {write_status:<7} {'blocked by review' if status == 'REVIEW' else str(write_count) + ' fields'}")
    lines.extend(["", "Final:", f"DB updates: {plan.db_updates}", f"Tag writes: {plan.tag_writes}", f"Skipped: {plan.skipped}", f"Conflicts: {plan.conflicts}", f"Status: {status}"])
    protected = sum(1 for item in plan.decisions if item.action == "review" and is_protected_field(item.field))
    if protected:
        lines.extend(["", "Warnings:", f"- {protected} protected identity fields differ. Use --force only if you are sure."])
    if verbose:
        lines.append("")
        lines.append("Decisions:")
        for item in plan.decisions:
            lines.append(f"- {item.path.name}: {item.field} {item.action} ({item.reason})")
    if debug and plan.read_errors:
        lines.append("")
        lines.append("Read errors:")
        lines.extend(f"- {error}" for error in plan.read_errors)
    if errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in errors)
    return "\n".join(lines)


def _render_refresh(target: Path, apply: bool) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    return "\n".join([
        f"Mode: {mode}",
        "Direction: refresh",
        "",
        "[1/3] Read identifiers    OK      existing IDs only",
        "[2/3] Provider refresh    SKIP    deep provider refresh is reserved for a future sync block",
        "[3/3] Write changes       SKIP    no refresh writes planned",
        "",
        "Final:",
        "DB updates: 0",
        "Tag writes: 0",
        "Status: OK",
        f"Target: {target}",
    ])
