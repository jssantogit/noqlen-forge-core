from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..audio import read_tracks
from ..batch import BatchResult, batch_targets, render_batch_summary, run_batch_result
from ..cleanup import CleanupPlan, apply_cleanup, plan_cleanup, summarize_cleanup
from ..workflow import OperationContext, PlannedChange, Status, StepResult, WorkflowResult
from .result_helpers import finish_text_result, status_from_text_output

BatchProcessor = Callable[[Path, bool], int]


@dataclass(slots=True, frozen=True)
class CleanupOptions:
    path: Path
    config: dict[str, Any] | None = None
    apply: bool = False
    verbose: bool = False
    debug: bool = False


@dataclass(slots=True, frozen=True)
class BatchOptions:
    path: Path
    config: dict[str, Any] | None = None
    apply: bool = False
    recursive: bool = False
    yes: bool = False
    continue_on_review: bool = False
    verbose: bool = False
    debug: bool = False
    process: BatchProcessor | None = None


def run_cleanup_service(options: CleanupOptions) -> WorkflowResult:
    context = OperationContext.from_flags("cleanup", options.path, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config or {})
    context.safety_context.check_apply_allowed(options.apply, context="noqlen-forge cleanup service")
    tracks = read_tracks(options.path)
    if not tracks:
        result = WorkflowResult(Status.FAIL, [StepResult(1, 1, "Cleanup", Status.FAIL, "no supported audio files")], workflow="cleanup", command="cleanup", target=options.path, mode="apply" if options.apply else "dry-run", summary={"status": "FAIL", "files": 0}, counts={"files": 0}, details={"exit_code": 1, "output_text": "No supported audio files found"}, safe_details={"exit_code": 1}, errors=["No supported audio files found"])
        return result
    plans = plan_cleanup(tracks)
    apply_cleanup(plans, apply=options.apply)
    output = summarize_cleanup(plans, apply=options.apply, verbose=options.verbose)
    workflow = WorkflowResult(Status.OK, [StepResult(1, 1, "Cleanup", Status.OK, f"{len(plans)} files with changes")], workflow="cleanup", command="cleanup", target=options.path, mode="apply" if options.apply else "dry-run")
    workflow = finish_text_result(workflow, code=0, output=output, mode="apply" if options.apply else "dry-run", status=Status.APPLY if options.apply else Status.DRY)
    workflow.counts = {"files": len(tracks), "planned_files": len(plans), "removals": sum(len(plan.remove) for plan in plans), "writes": sum(len(plan.set_values) for plan in plans)}
    workflow.summary.update({"files": len(tracks), "planned_files": len(plans), "apply": options.apply})
    workflow.planned_changes = _cleanup_changes(plans)
    workflow.safe_details.update({"plans": _cleanup_plan_summary(plans)})
    return workflow


def run_batch_service(options: BatchOptions) -> WorkflowResult:
    context = OperationContext.from_flags("batch", options.path, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config or {})
    context.safety_context.check_apply_allowed(options.apply, context="noqlen-forge batch service")
    processor = options.process or (lambda _target, _apply: 0)
    targets = batch_targets(options.path, recursive=options.recursive)
    if not targets:
        output = "No batch targets found"
        return finish_text_result(WorkflowResult(Status.FAIL, [StepResult(1, 1, "Batch", Status.FAIL, output)], workflow="batch", command="batch", target=options.path), code=1, output=output, mode="apply" if options.apply else "dry-run", status=Status.FAIL)
    result = run_batch_result(options.path, process=processor, apply=options.apply, recursive=options.recursive, yes=options.yes, continue_on_review=options.continue_on_review)
    output = render_batch_summary(result, result.targets)
    code = 1 if result.cancelled or any(item.status in {"FAILED", "REVIEW"} for item in result.items) else 0
    status = _batch_status(result, code)
    workflow = finish_text_result(WorkflowResult(status, [StepResult(1, 1, "Batch", status, f"{len(result.items)}/{len(result.targets)} targets")], workflow="batch", command="batch", target=options.path), code=code, output=output, mode="apply" if options.apply else "dry-run", status=status)
    workflow.counts = _batch_counts(result)
    workflow.summary.update({"targets": len(result.targets), "processed": len(result.items), "cancelled": result.cancelled, "stopped": result.stopped})
    workflow.safe_details.update({"targets": [str(target) for target in result.targets], "items": [{"path": str(item.path), "status": item.status, "code": item.code} for item in result.items]})
    return workflow


def _cleanup_changes(plans: list[CleanupPlan]) -> list[PlannedChange]:
    changes: list[PlannedChange] = []
    for plan in plans:
        for key in sorted(plan.remove):
            changes.append(PlannedChange(plan.path, "track", key, action="remove", source="cleanup", reason="metadata cleanup", safe_preview=f"remove {key}"))
        for key, values in sorted(plan.set_values.items()):
            changes.append(PlannedChange(plan.path, "track", key, old_value=plan.before_values.get(key), new_value=values, action="write", source="cleanup", reason="metadata cleanup", safe_preview=f"{key}={'; '.join(values)}"))
    return changes


def _cleanup_plan_summary(plans: list[CleanupPlan]) -> list[dict[str, Any]]:
    return [{"path": str(plan.path), "remove": sorted(plan.remove), "write": sorted(plan.set_values)} for plan in plans]


def _batch_status(result: BatchResult, code: int) -> Status:
    if result.cancelled or any(item.status == "FAILED" for item in result.items):
        return Status.FAIL
    if any(item.status == "REVIEW" for item in result.items):
        return Status.REVIEW
    if any(item.status == "WARN" for item in result.items):
        return Status.WARN
    return status_from_text_output(code, render_batch_summary(result, result.targets), default=Status.OK)


def _batch_counts(result: BatchResult) -> dict[str, int | str | float]:
    counts: dict[str, int | str | float] = {"targets": len(result.targets), "processed": len(result.items), "OK": 0, "WARN": 0, "REVIEW": 0, "FAILED": 0}
    for item in result.items:
        counts[item.status] = int(counts.get(item.status, 0)) + 1
    return counts
