from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..jobs import JobStatus, JobStore, resume_job
from ..workflow import Status, StepResult, WorkflowResult


@dataclass(slots=True, frozen=True)
class JobsOptions:
    config: dict[str, Any]
    command: str
    job_id: str = ""
    status: str | None = None
    limit: int = 20
    apply: bool = False
    verbose: bool = False


def run_jobs_service(options: JobsOptions) -> WorkflowResult:
    store = JobStore(options.config)
    command = options.command
    if command == "list":
        jobs = store.list_jobs(status=options.status, limit=options.limit)
        return _result("jobs.list", Status.OK, {"jobs": jobs, "count": len(jobs)}, counts={"jobs": len(jobs)})
    if command in {"status", "show"}:
        result = store.get_result(options.job_id)
        if result is None:
            return _result(f"jobs.{command}", Status.FAIL, {"job_id": options.job_id}, errors=[f"Job not found: {options.job_id}"])
        events = result.events if command == "show" or options.verbose else []
        return _result(f"jobs.{command}", Status.OK, {"job": result.job, "steps": result.steps, "events": events}, summary={"job_id": options.job_id, "status": result.job.get("status")})
    if command == "cancel":
        try:
            ok = store.cancel(options.job_id)
        except ValueError as exc:
            return _result("jobs.cancel", Status.FAIL, {"job_id": options.job_id}, errors=[str(exc)])
        if not ok:
            return _result("jobs.cancel", Status.FAIL, {"job_id": options.job_id}, errors=[f"Job not found: {options.job_id}"])
        return _result("jobs.cancel", Status.OK, {"job_id": options.job_id, "status": JobStatus.CANCELED.value}, summary={"job_id": options.job_id, "canceled": True})
    if command == "resume":
        code, message = resume_job(options.config, options.job_id)
        status = Status.OK if code == 0 else Status.FAIL
        return _result("jobs.resume", status, {"job_id": options.job_id, "status": "resumed" if code == 0 else "failed", "message": message}, errors=[] if code == 0 else [message])
    if command == "prune":
        payload = store.prune(apply=options.apply)
        return _result("jobs.prune", Status.OK, payload, counts={"jobs": int(payload.get("count", 0))}, mode="apply" if options.apply else "dry-run")
    return _result("jobs", Status.FAIL, {"command": command}, errors=[f"Unknown jobs command: {command}"])


def _result(workflow: str, status: Status, payload: dict[str, Any], *, summary: dict[str, Any] | None = None, counts: dict[str, int | float | str] | None = None, errors: list[str] | None = None, mode: str = "read-only") -> WorkflowResult:
    step = StepResult(1, 1, workflow, status, (errors or ["ok"])[0])
    return WorkflowResult(status, [step], workflow=workflow, command=workflow, mode=mode, summary=summary or {"status": status.value}, counts=counts or {}, details=payload, safe_details=payload, errors=errors or [])
