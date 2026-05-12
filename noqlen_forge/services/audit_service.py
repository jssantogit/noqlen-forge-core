from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..audit import AuditResult, audit_path
from ..workflow import OperationContext, Status, StepResult, WorkflowRunner


@dataclass(slots=True, frozen=True)
class AuditOptions:
    path: Path
    config: dict[str, Any] | None = None
    verbose: bool = False
    advanced: bool = False


def run_audit_service(options: AuditOptions):
    state: dict[str, AuditResult] = {}
    context = OperationContext.from_flags("audit", options.path, apply=False, verbose=options.verbose, config=options.config)

    def read_tags(_: OperationContext, index: int, total: int) -> StepResult:
        audit = audit_path(options.path)
        state["audit"] = audit
        return StepResult(index, total, "Read tags", Status(audit.status), f"{len(audit.tracks)} files")

    workflow = WorkflowRunner(context).run([read_tags])
    audit = state.get("audit", AuditResult(tracks=[], bad_fields=[]))
    workflow.mode = "read-only"
    workflow.summary = {"files": len(audit.tracks), "bad_fields": len(audit.bad_fields), "status": audit.status}
    workflow.counts = {"files": len(audit.tracks), "bad_fields": len(audit.bad_fields)}
    workflow.details = {"audit": {"status": audit.status, "tracks": len(audit.tracks), "bad_fields": len(audit.bad_fields)}, "_audit_result": audit}
    workflow.safe_details = {"audit": {"status": audit.status, "tracks": len(audit.tracks), "bad_fields": len(audit.bad_fields)}}
    return workflow


def audit_result_from_workflow(result) -> AuditResult:
    raw = result.details.get("_audit_result") if result.details else None
    if isinstance(raw, AuditResult):
        return raw
    return AuditResult(tracks=[], bad_fields=[])
