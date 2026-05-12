from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config import get_config_value
from .db import apply_migrations, connect
from .services.types import sanitize_value_for_output, workflow_result_to_dict
from .workflow import SafetyContext, Status, StepResult, WorkflowResult


class JobCanceled(RuntimeError):
    pass


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    WARNING = "warning"
    REVIEW = "review"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(slots=True, frozen=True)
class JobOptions:
    kind: str
    target: str = ""
    target_type: str = "path"
    mode: str = "read-only"
    options: dict[str, Any] = field(default_factory=dict)
    resumable: bool = False
    cancelable: bool = True


@dataclass(slots=True, frozen=True)
class JobEvent:
    event_type: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class JobResult:
    job: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def job_status_from_workflow(status: Status | str) -> JobStatus:
    value = Status(str(status).upper()) if not isinstance(status, Status) else status
    if value == Status.WARN:
        return JobStatus.WARNING
    if value == Status.REVIEW:
        return JobStatus.REVIEW
    if value == Status.FAIL:
        return JobStatus.FAILED
    return JobStatus.COMPLETED


def _json(value: Any) -> str:
    return json.dumps(sanitize_value_for_output(value), ensure_ascii=False, sort_keys=True)


def _load_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _summary_text(value: Any) -> str:
    safe = sanitize_value_for_output(value)
    if isinstance(safe, dict):
        for key in ("message", "summary", "status"):
            if safe.get(key):
                return str(safe[key])[:500]
        return ", ".join(f"{key}={safe[key]}" for key in sorted(safe)[:4])[:500]
    return str(safe or "")[:500]


class JobStore:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def create_job(self, options: JobOptions) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = utc_now()
        with connect(self.config) as conn:
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO jobs(id, kind, target, target_type, mode, status, created_at, updated_at,
                                 progress_current, progress_total, progress_label, resumable, cancelable, options_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '', ?, ?, ?)
                """,
                (job_id, options.kind, options.target, options.target_type, options.mode, JobStatus.PENDING.value, now, now, int(options.resumable), int(options.cancelable), _json(options.options)),
            )
            conn.execute("INSERT INTO job_events(job_id, created_at, event_type, message, data_json) VALUES (?, ?, ?, ?, ?)", (job_id, now, "created", "job created", _json({"kind": options.kind})))
            conn.commit()
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with connect(self.config) as conn:
            apply_migrations(conn)
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_dict(row) if row else None

    def list_jobs(self, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with connect(self.config) as conn:
            apply_migrations(conn)
            rows = conn.execute(sql, params).fetchall()
        return [self._job_dict(row) for row in rows]

    def get_steps(self, job_id: str) -> list[dict[str, Any]]:
        with connect(self.config) as conn:
            apply_migrations(conn)
            rows = conn.execute("SELECT * FROM job_steps WHERE job_id = ? ORDER BY step_index, id", (job_id,)).fetchall()
        return [self._step_dict(row) for row in rows]

    def get_events(self, job_id: str) -> list[dict[str, Any]]:
        with connect(self.config) as conn:
            apply_migrations(conn)
            rows = conn.execute("SELECT * FROM job_events WHERE job_id = ? ORDER BY created_at, id", (job_id,)).fetchall()
        return [self._event_dict(row) for row in rows]

    def get_result(self, job_id: str) -> JobResult | None:
        job = self.get_job(job_id)
        if job is None:
            return None
        return JobResult(job=job, steps=self.get_steps(job_id), events=self.get_events(job_id))

    def mark_running(self, job_id: str) -> None:
        now = utc_now()
        with connect(self.config) as conn:
            apply_migrations(conn)
            conn.execute("UPDATE jobs SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ?", (JobStatus.RUNNING.value, now, now, job_id))
            conn.execute("INSERT INTO job_events(job_id, created_at, event_type, message, data_json) VALUES (?, ?, ?, ?, ?)", (job_id, now, "started", "job started", _json({})))
            conn.commit()

    def update_progress(self, job_id: str, current: int, total: int, label: str = "") -> None:
        with connect(self.config) as conn:
            apply_migrations(conn)
            conn.execute("UPDATE jobs SET progress_current = ?, progress_total = ?, progress_label = ?, updated_at = ? WHERE id = ?", (current, total, label, utc_now(), job_id))
            conn.commit()

    def add_event(self, job_id: str, event_type: str, message: str = "", data: dict[str, Any] | None = None) -> None:
        with connect(self.config) as conn:
            apply_migrations(conn)
            conn.execute("INSERT INTO job_events(job_id, created_at, event_type, message, data_json) VALUES (?, ?, ?, ?, ?)", (job_id, utc_now(), event_type, message, _json(data or {})))
            conn.commit()

    def upsert_step(self, job_id: str, step: StepResult, *, started_at: str | None = None, finished_at: str | None = None) -> None:
        with connect(self.config) as conn:
            apply_migrations(conn)
            existing = conn.execute("SELECT id FROM job_steps WHERE job_id = ? AND step_index = ?", (job_id, step.index)).fetchone()
            values = (step.name, str(step.status.value), started_at, finished_at, float(step.elapsed_seconds or 0), step.summary, _json(step.details), _json(step.warnings), _json([]), job_id, step.index)
            if existing:
                conn.execute("""UPDATE job_steps SET name = ?, status = ?, started_at = COALESCE(started_at, ?), finished_at = ?, elapsed_seconds = ?, summary = ?, details_json = ?, warnings_json = ?, errors_json = ? WHERE job_id = ? AND step_index = ?""", values)
            else:
                conn.execute("""INSERT INTO job_steps(name, status, started_at, finished_at, elapsed_seconds, summary, details_json, warnings_json, errors_json, job_id, step_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)
            conn.commit()

    def save_workflow_result(self, job_id: str, result: WorkflowResult) -> None:
        payload = workflow_result_to_dict(result)
        status = job_status_from_workflow(result.status)
        summary = _summary_text(result.summary or {"status": result.status.value})
        now = utc_now()
        with connect(self.config) as conn:
            apply_migrations(conn)
            conn.execute("DELETE FROM job_steps WHERE job_id = ?", (job_id,))
            for step in result.steps:
                conn.execute(
                    """INSERT INTO job_steps(job_id, step_index, name, status, started_at, finished_at, elapsed_seconds, summary, details_json, warnings_json, errors_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job_id, step.index, step.name, step.status.value, None, now, float(step.elapsed_seconds or 0), step.summary, _json(step.details), _json(step.warnings), _json([])),
                )
            conn.execute(
                """UPDATE jobs SET status = ?, finished_at = ?, updated_at = ?, progress_current = ?, progress_total = ?, progress_label = ?, summary = ?, result_json = ?, error = ? WHERE id = ?""",
                (status.value, now, now, len(result.steps), len(result.steps), result.steps[-1].name if result.steps else "", summary, _json(payload), "; ".join(result.errors)[:500] if result.errors else None, job_id),
            )
            conn.execute("INSERT INTO job_events(job_id, created_at, event_type, message, data_json) VALUES (?, ?, ?, ?, ?)", (job_id, now, "finished", f"job {status.value}", _json({"status": status.value})))
            conn.commit()
        result.job = {"job_id": job_id, "resumable": bool(self.get_job(job_id or "") and self.get_job(job_id)["resumable"]), "cancelable": True, "progress_current": len(result.steps), "progress_total": len(result.steps), "progress_label": result.steps[-1].name if result.steps else ""}

    def cancel(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        if not job.get("cancelable"):
            raise ValueError("Job is not cancelable")
        now = utc_now()
        with connect(self.config) as conn:
            apply_migrations(conn)
            conn.execute("UPDATE jobs SET status = ?, canceled_at = ?, finished_at = COALESCE(finished_at, ?), updated_at = ? WHERE id = ?", (JobStatus.CANCELED.value, now, now, now, job_id))
            conn.execute("INSERT INTO job_events(job_id, created_at, event_type, message, data_json) VALUES (?, ?, ?, ?, ?)", (job_id, now, "canceled", "job canceled", _json({})))
            conn.commit()
        return True

    def mark_failed(self, job_id: str, error: str) -> None:
        now = utc_now()
        with connect(self.config) as conn:
            apply_migrations(conn)
            conn.execute("UPDATE jobs SET status = ?, error = ?, finished_at = ?, updated_at = ? WHERE id = ?", (JobStatus.FAILED.value, error[:500], now, now, job_id))
            conn.execute("INSERT INTO job_events(job_id, created_at, event_type, message, data_json) VALUES (?, ?, ?, ?, ?)", (job_id, now, "failed", error[:500], _json({})))
            conn.commit()

    def prune(self, *, apply: bool = False, days: int | None = None) -> dict[str, Any]:
        history_days = int(days if days is not None else get_config_value(self.config, "jobs", "history_days", 30))
        prune_completed = bool(get_config_value(self.config, "jobs", "prune_completed", True))
        prune_failed = bool(get_config_value(self.config, "jobs", "prune_failed", False))
        statuses = [JobStatus.WARNING.value, JobStatus.REVIEW.value, JobStatus.CANCELED.value]
        if prune_completed:
            statuses.append(JobStatus.COMPLETED.value)
        if prune_failed:
            statuses.append(JobStatus.FAILED.value)
        cutoff = (datetime.now(UTC) - timedelta(days=history_days)).isoformat()
        with connect(self.config) as conn:
            apply_migrations(conn)
            placeholders = ", ".join("?" for _ in statuses)
            rows = conn.execute(f"SELECT id, kind, status, created_at, summary FROM jobs WHERE created_at < ? AND status IN ({placeholders}) ORDER BY created_at", [cutoff, *statuses]).fetchall()
            jobs = [self._job_dict(row) for row in rows]
            if apply and jobs:
                conn.executemany("DELETE FROM jobs WHERE id = ?", [(job["id"],) for job in jobs])
            conn.commit()
        return {"apply": apply, "cutoff": cutoff, "count": len(jobs), "jobs": jobs}

    def _job_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["resumable"] = bool(data.get("resumable"))
        data["cancelable"] = bool(data.get("cancelable"))
        data["options"] = _load_json(data.pop("options_json", None)) or {}
        data["result"] = _load_json(data.pop("result_json", None))
        return data

    def _step_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["details"] = _load_json(data.pop("details_json", None)) or []
        data["warnings"] = _load_json(data.pop("warnings_json", None)) or []
        data["errors"] = _load_json(data.pop("errors_json", None)) or []
        return data

    def _event_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["data"] = _load_json(data.pop("data_json", None)) or {}
        return data


class JobContext:
    def __init__(self, store: JobStore, job_id: str, *, safety_context: SafetyContext | None = None) -> None:
        self.store = store
        self.job_id = job_id
        self.safety_context = safety_context or SafetyContext()

    def update_progress(self, current: int, total: int, label: str = "") -> None:
        self.store.update_progress(self.job_id, current, total, label)

    def add_event(self, event_type: str, message: str = "", data: dict[str, Any] | None = None) -> None:
        self.store.add_event(self.job_id, event_type, message, data)

    def start_step(self, name: str) -> None:
        job = self.store.get_job(self.job_id)
        index = len(self.store.get_steps(self.job_id)) + 1
        total = int(job.get("progress_total") or index) if job else index
        self.store.upsert_step(self.job_id, StepResult(index, total, name, Status.OK, "started"), started_at=utc_now())
        self.add_event("step_started", name, {"step_index": index})

    def finish_step(self, step: StepResult) -> None:
        self.store.upsert_step(self.job_id, step, finished_at=utc_now())
        self.update_progress(step.index, step.total, step.name)
        self.add_event("step_finished", step.name, {"step_index": step.index, "status": step.status.value})

    def check_canceled(self) -> None:
        job = self.store.get_job(self.job_id)
        if job and job.get("status") == JobStatus.CANCELED.value:
            raise JobCanceled(f"Job {self.job_id} was canceled")

    def mark_canceled(self) -> None:
        self.store.cancel(self.job_id)

    def save_result(self, result: WorkflowResult) -> None:
        self.store.save_workflow_result(self.job_id, result)


def run_workflow_as_job(store: JobStore, options: JobOptions, run) -> WorkflowResult:
    job_id = store.create_job(options)
    store.mark_running(job_id)
    try:
        result = run(JobContext(store, job_id, safety_context=SafetyContext(target_path=Path(options.target) if options.target else None)))
    except JobCanceled:
        store.cancel(job_id)
        return WorkflowResult(Status.FAIL, [], workflow=options.kind, command=options.kind, target=Path(options.target) if options.target else None, target_type=options.target_type, mode=options.mode, errors=["job canceled"], job={"job_id": job_id})
    except Exception as exc:
        store.mark_failed(job_id, str(exc))
        raise
    store.save_workflow_result(job_id, result)
    return result


def resume_job(config: dict[str, Any], job_id: str) -> tuple[int, str]:
    store = JobStore(config)
    job = store.get_job(job_id)
    if not job:
        return 1, f"Job not found: {job_id}"
    if not job.get("resumable"):
        return 1, f"Job {job_id} is not resumable"
    if job.get("kind") != "job-test-resume":
        return 1, f"Job {job_id} has no resume handler for kind {job.get('kind')}"
    store.mark_running(job_id)
    completed = {step["name"] for step in store.get_steps(job_id) if step["status"] in {"OK", "completed"}}
    steps = ["Prepare", "Resume"]
    for index, name in enumerate(steps, 1):
        if name in completed:
            continue
        store.upsert_step(job_id, StepResult(index, len(steps), name, Status.OK, "resumed"), finished_at=utc_now())
        store.update_progress(job_id, index, len(steps), name)
    result = WorkflowResult(Status.OK, [StepResult(index, len(steps), name, Status.OK, "completed") for index, name in enumerate(steps, 1)], workflow="job-test-resume", command="job-test-resume", summary={"message": "resumed 1 job"})
    store.save_workflow_result(job_id, result)
    return 0, f"Job {job_id} resumed"
