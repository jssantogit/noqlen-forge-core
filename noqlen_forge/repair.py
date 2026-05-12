from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .audio import audio_files
from .config import get_config_value
from .db import apply_migrations, connect, connect_readonly, database_path, normalize_path, record_operation, upsert_file
from .duplicates import duplicates_path
from .output import render_final_summary, render_steps
from .workflow import ChangePlan, OperationContext, StepResult, Status, WorkflowRunner


@dataclass(slots=True)
class RepairItem:
    action: str
    target_path: Path
    target_type: str = "file"
    old_value: Any = None
    new_value: Any = None
    reason: str = ""
    target_id: int | None = None


@dataclass(slots=True)
class RepairPlan:
    kind: str
    items: list[RepairItem] = field(default_factory=list)
    review_items: list[RepairItem] = field(default_factory=list)

    @property
    def status(self) -> Status:
        if self.review_items:
            return Status.REVIEW
        return Status.WARN if self.items else Status.OK


def repair_path(config: dict[str, Any], target: Path | None = None, *, kind: str = "all", apply: bool = False, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    if not bool(get_config_value(config, "repair", "enabled", True)):
        return 1, "Repair is disabled in config"
    if kind in {"duplicates", "duplicate"}:
        return _repair_duplicates(config, target=target, apply=apply, verbose=verbose, debug=debug)
    context_target = target or Path(str(get_config_value(config, "library", "root", "") or "."))
    context = OperationContext.from_flags("maintain repair", target=context_target, apply=apply, verbose=verbose, debug=debug, config=config)
    try:
        context.safety_context.check_apply_allowed(apply, context="noqlen-forge maintain repair")
    except Exception as exc:
        return 1, str(exc)
    conn = connect(config) if apply else connect_readonly(config)
    if conn is None:
        return 1, f"Database not initialized: {database_path(config)}"
    state: dict[str, Any] = {"counts": {}, "plan": RepairPlan(kind), "applied": 0}

    def read_database(_: OperationContext, index: int, total: int) -> StepResult:
        if apply:
            apply_migrations(conn)
        state["counts"] = _counts(conn)
        return StepResult(index, total, "Read database", Status.OK, f"{state['counts'].get('files', 0)} files")

    def check_targets(_: OperationContext, index: int, total: int) -> StepResult:
        plan = _build_plan(conn, kind, target)
        state["plan"] = plan
        status = plan.status
        label = _plan_summary(plan)
        return StepResult(index, total, "Check repair", status, label)

    def build_plan(_: OperationContext, index: int, total: int) -> StepResult:
        plan = state["plan"]
        change_plan = _as_change_plan(plan)
        summary = f"{len(change_plan.changes)} safe updates"
        if plan.review_items:
            summary = f"{len(plan.review_items)} review items"
        return StepResult(index, total, "Build plan", plan.status, summary)

    def apply_repair(_: OperationContext, index: int, total: int) -> StepResult:
        plan = state["plan"]
        if not apply:
            return StepResult(index, total, "Apply repair", Status.DRY, _dry_summary(plan))
        if plan.review_items:
            _record_repair_operation(conn, kind, target, "apply", "review", _plan_summary(plan))
            conn.commit()
            return StepResult(index, total, "Apply repair", Status.REVIEW, "human review required")
        applied = _apply_plan(conn, plan)
        state["applied"] = applied
        final_status = Status.OK if applied or not plan.items else Status.WARN
        _record_repair_operation(conn, kind, target, "apply", final_status.value.lower(), f"applied {applied} safe updates")
        conn.commit()
        return StepResult(index, total, "Apply repair", Status.APPLY if applied else Status.OK, f"applied {applied} safe updates")

    workflow = WorkflowRunner(context).run([read_database, check_targets, build_plan, apply_repair])
    plan = state["plan"]
    final_status = _final_status(plan, apply=apply, applied=int(state.get("applied") or 0), workflow_status=workflow.status)
    lines = [f"Repair: {_title(kind)}", f"Mode: {'APPLY' if apply else 'DRY-RUN'}", "", render_steps(workflow.steps, verbose=verbose, debug=debug), "", _render_plan(plan, apply=apply, verbose=verbose, applied=int(state.get("applied") or 0)), "", render_final_summary(final_status, _final_summary(plan, apply=apply, applied=int(state.get("applied") or 0)))]
    return (1 if final_status == Status.FAIL else 0), "\n".join(part for part in lines if part)


def find_missing_file_records(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, path, status FROM files WHERE COALESCE(status, 'active') != 'missing' ORDER BY path").fetchall()


def mark_files_missing(conn: sqlite3.Connection, file_ids: list[int]) -> int:
    if not file_ids:
        return 0
    placeholders = ", ".join("?" for _ in file_ids)
    conn.execute(f"UPDATE files SET status = 'missing', updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", file_ids)
    return len(file_ids)


def find_untracked_files(path: Path, conn: sqlite3.Connection) -> list[Path]:
    known = {str(row["path"] or "") for row in conn.execute("SELECT path FROM files")}
    return [item for item in audio_files(path) if normalize_path(item) not in known]


def repair_untracked_scan(path: Path, conn: sqlite3.Connection) -> int:
    count = 0
    for file_path in find_untracked_files(path, conn):
        try:
            stat = file_path.stat()
        except OSError:
            continue
        upsert_file(conn, file_path, {"size": stat.st_size, "mtime": stat.st_mtime, "tag_mtime": stat.st_mtime, "db_mtime": stat.st_mtime, "format": file_path.suffix.lower().lstrip("."), "status": "active"}, track_id=None)
        count += 1
    return count


def find_db_orphans(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    track_status = "COALESCE(status, 'active')" if _column_exists(conn, "tracks", "status") else "'active'"
    album_status = "COALESCE(a.status, 'active')" if _column_exists(conn, "albums", "status") else "'active'"
    track_status_select = "status" if _column_exists(conn, "tracks", "status") else "'active' AS status"
    album_status_select = "a.status" if _column_exists(conn, "albums", "status") else "'active' AS status"
    return {
        "files_without_track": conn.execute("SELECT id, path, status FROM files WHERE track_id IS NULL AND COALESCE(status, 'active') = 'active' ORDER BY path").fetchall(),
        "tracks_without_album": conn.execute(f"SELECT id, title, {track_status_select} FROM tracks WHERE album_id IS NULL AND {track_status} = 'active' ORDER BY id").fetchall(),
        "albums_without_tracks": conn.execute(f"SELECT a.id, a.album, {album_status_select} FROM albums a LEFT JOIN tracks t ON t.album_id = a.id WHERE t.id IS NULL AND {album_status} = 'active' ORDER BY a.id").fetchall(),
        "running_operations": conn.execute("SELECT id, operation FROM operations WHERE finished_at IS NULL OR LOWER(COALESCE(status, '')) = 'running' ORDER BY id").fetchall(),
        "running_provider_runs": conn.execute("SELECT id, provider FROM provider_runs WHERE finished_at IS NULL OR LOWER(COALESCE(status, '')) = 'running' ORDER BY id").fetchall(),
        "pending_field_decisions_missing_target": conn.execute("SELECT id, target_type, target_id FROM field_decisions WHERE COALESCE(resolved, 0) = 0 AND NOT EXISTS (SELECT 1 FROM tracks t WHERE field_decisions.target_type = 'track' AND CAST(t.id AS TEXT) = field_decisions.target_id) AND NOT EXISTS (SELECT 1 FROM albums a WHERE field_decisions.target_type = 'album' AND CAST(a.id AS TEXT) = field_decisions.target_id) ORDER BY id").fetchall(),
    }


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(str(row["name"]) == column for row in conn.execute(f"PRAGMA table_info({table})"))


def build_repair_plan(conn: sqlite3.Connection, kind: str, target: Path | None = None) -> RepairPlan:
    return _build_plan(conn, kind, target)


def apply_repair_plan(conn: sqlite3.Connection, plan: RepairPlan) -> int:
    return _apply_plan(conn, plan)


def _build_plan(conn: sqlite3.Connection, kind: str, target: Path | None) -> RepairPlan:
    if kind in {"missing-files", "missing_files"}:
        return _missing_files_plan(conn)
    if kind == "untracked":
        if target is None:
            raise ValueError("Path is required for repair untracked")
        return _untracked_plan(conn, target)
    if kind == "db":
        return _db_plan(conn)
    if kind in {"all", "path"}:
        plan = RepairPlan("all")
        plan.items.extend(_missing_files_plan(conn).items)
        if target is not None:
            plan.items.extend(_untracked_plan(conn, target).items)
        db_plan = _db_plan(conn)
        plan.items.extend(db_plan.items)
        plan.review_items.extend(db_plan.review_items)
        return plan
    raise ValueError(f"Unknown repair target: {kind}")


def _missing_files_plan(conn: sqlite3.Connection) -> RepairPlan:
    plan = RepairPlan("missing-files")
    for row in find_missing_file_records(conn):
        path = Path(str(row["path"] or ""))
        if str(path) and not path.exists():
            plan.items.append(RepairItem("mark-missing", path, old_value=row["status"], new_value="missing", reason="database path is absent on disk", target_id=int(row["id"])))
    return plan


def _untracked_plan(conn: sqlite3.Connection, target: Path) -> RepairPlan:
    plan = RepairPlan("untracked")
    for path in find_untracked_files(target, conn):
        plan.items.append(RepairItem("scan-untracked", path, old_value=None, new_value="active", reason="audio file is not present in database"))
    return plan


def _db_plan(conn: sqlite3.Connection) -> RepairPlan:
    plan = RepairPlan("db")
    orphans = find_db_orphans(conn)
    for row in orphans["files_without_track"]:
        plan.items.append(RepairItem("mark-file-stale", Path(str(row["path"] or f"file:{row['id']}")), old_value=row["status"], new_value="stale", reason="file row has no track", target_id=int(row["id"])))
    for row in orphans["tracks_without_album"]:
        plan.items.append(RepairItem("mark-track-stale", Path(f"track:{row['id']}"), "track", row["status"], "stale", "track row has no album", int(row["id"])))
    for row in orphans["albums_without_tracks"]:
        plan.items.append(RepairItem("mark-album-stale", Path(f"album:{row['id']}"), "album", row["status"], "stale", "album row has no tracks", int(row["id"])))
    for row in orphans["running_operations"]:
        plan.items.append(RepairItem("finish-operation-warn", Path(f"operation:{row['id']}"), "operation", "running", "warn", "operation did not finish", int(row["id"])))
    for row in orphans["running_provider_runs"]:
        plan.items.append(RepairItem("finish-provider-run-warn", Path(f"provider_run:{row['id']}"), "provider_run", "running", "warn", "provider run did not finish", int(row["id"])))
    for row in orphans["pending_field_decisions_missing_target"]:
        plan.items.append(RepairItem("resolve-decision-stale", Path(f"field_decision:{row['id']}"), "field_decision", "pending", "stale", "decision target no longer exists", int(row["id"])))
    return plan


def _apply_plan(conn: sqlite3.Connection, plan: RepairPlan) -> int:
    applied = 0
    missing_ids = [item.target_id for item in plan.items if item.action == "mark-missing" and item.target_id is not None]
    applied += mark_files_missing(conn, [int(item) for item in missing_ids])
    for item in plan.items:
        if item.action == "scan-untracked":
            applied += repair_untracked_scan(item.target_path, conn)
        elif item.action == "mark-file-stale" and item.target_id is not None:
            conn.execute("UPDATE files SET status = 'stale', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (item.target_id,))
            applied += 1
        elif item.action == "mark-track-stale" and item.target_id is not None:
            conn.execute("UPDATE tracks SET status = 'stale', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (item.target_id,))
            applied += 1
        elif item.action == "mark-album-stale" and item.target_id is not None:
            conn.execute("UPDATE albums SET status = 'stale', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (item.target_id,))
            applied += 1
        elif item.action == "finish-operation-warn" and item.target_id is not None:
            conn.execute("UPDATE operations SET status = 'warn', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (item.target_id,))
            applied += 1
        elif item.action == "finish-provider-run-warn" and item.target_id is not None:
            conn.execute("UPDATE provider_runs SET status = 'warn', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (item.target_id,))
            applied += 1
        elif item.action == "resolve-decision-stale" and item.target_id is not None:
            conn.execute("UPDATE field_decisions SET resolved = 1, resolved_at = CURRENT_TIMESTAMP, resolved_action = 'stale', resolved_by = 'repair' WHERE id = ?", (item.target_id,))
            applied += 1
    return applied


def _repair_duplicates(config: dict[str, Any], target: Path | None, apply: bool, verbose: bool, debug: bool) -> tuple[int, str]:
    code, report = duplicates_path(config, target=target, scope="both", output_format="text", verbose=verbose, debug=debug)
    status = Status.REVIEW if "Duplicate" in report and "none" not in report else Status.OK
    if apply and status == Status.REVIEW:
        try:
            context_target = target or Path(str(get_config_value(config, "library", "root", "") or "."))
            OperationContext.from_flags("maintain repair duplicates", target=context_target, apply=True, config=config).safety_context.check_apply_allowed(True, context="noqlen-forge maintain repair duplicates")
        except Exception as exc:
            return 1, str(exc)
    lines = ["Repair: duplicates", f"Mode: {'APPLY' if apply else 'DRY-RUN'}", "", report, "", "Plan:", "- duplicates require human REVIEW; no files will be moved or deleted", "", render_final_summary(status, {"Would delete records": 0, "Would modify files": 0})]
    return code, "\n".join(lines)


def _record_repair_operation(conn: sqlite3.Connection, kind: str, target: Path | None, mode: str, status: str, summary: str) -> None:
    operation_id = record_operation(conn, "repair", "path" if target else "library", normalize_path(target) if target else "library", mode, status, f"{kind}: {summary}")
    conn.execute("UPDATE operations SET finished_at = CURRENT_TIMESTAMP WHERE id = ?", (operation_id,))


def _as_change_plan(plan: RepairPlan) -> ChangePlan:
    change_plan = ChangePlan()
    for item in plan.items:
        change_plan.add_write(item.target_path, item.target_type, item.action, item.old_value, item.new_value, reason=item.reason)
    for item in plan.review_items:
        change_plan.add_conflict(item.target_path, item.target_type, item.action, item.old_value, item.new_value, item.reason)
    return change_plan


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = ["albums", "tracks", "files", "operations", "provider_runs", "field_decisions"]
    return {name: int(conn.execute(f"SELECT COUNT(*) AS count FROM {name}").fetchone()["count"]) for name in names}


def _plan_summary(plan: RepairPlan) -> str:
    if plan.review_items:
        return f"{len(plan.review_items)} review items"
    if not plan.items:
        return "nothing to repair"
    return f"{len(plan.items)} safe updates"


def _dry_summary(plan: RepairPlan) -> str:
    if not plan.items and not plan.review_items:
        return "no changes"
    return f"would apply {len(plan.items)} safe updates"


def _render_plan(plan: RepairPlan, *, apply: bool, verbose: bool, applied: int) -> str:
    if not plan.items and not plan.review_items:
        return "Plan:\n- no repair needed"
    lines = ["Plan:"]
    grouped: dict[str, list[RepairItem]] = {}
    for item in [*plan.items, *plan.review_items]:
        grouped.setdefault(item.action, []).append(item)
    for action, items in grouped.items():
        lines.append(f"- {action}: {len(items)}")
        if verbose:
            lines.extend(f"  {item.target_path}" for item in items[:50])
    if apply:
        lines.append(f"Applied: {applied}")
    return "\n".join(lines)


def _final_summary(plan: RepairPlan, *, apply: bool, applied: int) -> dict[str, int]:
    mark_missing = sum(1 for item in plan.items if item.action == "mark-missing")
    scan = sum(1 for item in plan.items if item.action == "scan-untracked")
    stale = sum(1 for item in plan.items if "stale" in item.action)
    prefix = "" if apply else "Would "
    return {f"{prefix}mark missing": mark_missing, f"{prefix}scan untracked": scan, f"{prefix}mark stale": stale, "Deleted records": 0, "Modified files": 0, "Applied": applied if apply else 0}


def _final_status(plan: RepairPlan, *, apply: bool, applied: int, workflow_status: Status) -> Status:
    if workflow_status == Status.FAIL:
        return Status.FAIL
    if plan.review_items:
        return Status.REVIEW
    if apply:
        return Status.OK
    return Status.WARN if plan.items else Status.OK


def _title(kind: str) -> str:
    return str(kind).replace("_", " ").replace("-", " ")
