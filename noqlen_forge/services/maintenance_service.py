from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..repair import repair_path
from ..rewrite import rewrite_path
from ..sync import sync_path
from ..workflow import OperationContext, Status, StepResult, WorkflowRunner
from .result_helpers import finish_text_result, first_line, status_from_text_output


@dataclass(slots=True, frozen=True)
class SyncOptions:
    path: Path
    config: dict[str, Any]
    direction: str | None = None
    apply: bool = False
    force: bool = False
    fields: list[str] | None = None
    conflict_policy: str | None = None
    verbose: bool = False
    debug: bool = False


@dataclass(slots=True, frozen=True)
class RewriteOptions:
    path: Path
    config: dict[str, Any]
    apply: bool = False
    fields: list[str] | None = None
    db_only: bool = False
    tags_only: bool = False
    force: bool = False
    verbose: bool = False
    debug: bool = False


@dataclass(slots=True, frozen=True)
class RepairOptions:
    config: dict[str, Any]
    target: Path | None = None
    kind: str = "all"
    apply: bool = False
    verbose: bool = False
    debug: bool = False


def run_sync_service(options: SyncOptions):
    context = OperationContext.from_flags("sync", options.path, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config)
    context.safety_context.check_apply_allowed(options.apply, context="noqlen-forge sync service")
    state: dict[str, Any] = {}

    def process(_: OperationContext, index: int, total: int) -> StepResult:
        code, output = sync_path(options.path, config=options.config, direction=options.direction, apply=options.apply, force=options.force, fields=options.fields, conflict_policy=options.conflict_policy, verbose=options.verbose, debug=options.debug)
        state.update({"code": code, "output": output})
        return StepResult(index, total, "Sync", status_from_text_output(code, output), first_line(output))

    workflow = WorkflowRunner(context).run([process])
    code = int(state.get("code", 1 if workflow.status == Status.FAIL else 0))
    output = state.get("output", "")
    return finish_text_result(workflow, code=code, output=output, mode="apply" if options.apply else "dry-run", status=status_from_text_output(code, output))


def run_rewrite_service(options: RewriteOptions):
    context = OperationContext.from_flags("maintain rewrite", options.path, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config)
    context.safety_context.check_apply_allowed(options.apply, context="noqlen-forge rewrite service")
    state: dict[str, Any] = {}

    def process(_: OperationContext, index: int, total: int) -> StepResult:
        code, output = rewrite_path(options.path, config=options.config, apply=options.apply, fields=options.fields, db_only=options.db_only, tags_only=options.tags_only, force=options.force, verbose=options.verbose, debug=options.debug)
        state.update({"code": code, "output": output})
        return StepResult(index, total, "Rewrite", status_from_text_output(code, output), first_line(output))

    workflow = WorkflowRunner(context).run([process])
    code = int(state.get("code", 1 if workflow.status == Status.FAIL else 0))
    output = state.get("output", "")
    return finish_text_result(workflow, code=code, output=output, mode="apply" if options.apply else "dry-run", status=status_from_text_output(code, output))


def run_repair_service(options: RepairOptions):
    context = OperationContext.from_flags("maintain repair", options.target, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config)
    context.safety_context.check_apply_allowed(options.apply, context="noqlen-forge repair service")
    state: dict[str, Any] = {}

    def process(_: OperationContext, index: int, total: int) -> StepResult:
        code, output = repair_path(options.config, target=options.target, kind=options.kind, apply=options.apply, verbose=options.verbose, debug=options.debug)
        state.update({"code": code, "output": output})
        return StepResult(index, total, "Repair", status_from_text_output(code, output), first_line(output))

    workflow = WorkflowRunner(context).run([process])
    code = int(state.get("code", 1 if workflow.status == Status.FAIL else 0))
    output = state.get("output", "")
    return finish_text_result(workflow, code=code, output=output, mode="apply" if options.apply else "dry-run", status=status_from_text_output(code, output))
