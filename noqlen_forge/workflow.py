from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable

from .safety import automated_validation_enabled, is_dangerous_real_library_path, require_lab_path_for_automated_apply


class Status(StrEnum):
    OK = "OK"
    WARN = "WARN"
    REVIEW = "REVIEW"
    FAIL = "FAIL"
    SKIP = "SKIP"
    DRY = "DRY"
    APPLY = "APPLY"


_STATUS_PRIORITY = {Status.OK: 0, Status.DRY: 0, Status.APPLY: 0, Status.SKIP: 0, Status.WARN: 1, Status.REVIEW: 2, Status.FAIL: 3}


def coerce_status(status: Status | str) -> Status:
    if isinstance(status, Status):
        return status
    return Status(str(status).upper())


def combine_status(*statuses: Status | str, skip_required: bool = False) -> Status:
    """Combine step statuses using the shared Noqlen Forge severity model."""
    worst = Status.OK
    for raw in statuses:
        status = coerce_status(raw)
        if status == Status.SKIP and skip_required:
            status = Status.REVIEW
        if _STATUS_PRIORITY[status] > _STATUS_PRIORITY[worst]:
            worst = status
    return worst


def status_from_warnings(warnings: Iterable[WarningMessage | str], review: bool = False) -> Status:
    warning_list = list(warnings)
    if not warning_list:
        return Status.OK
    return Status.REVIEW if review else Status.WARN


def status_is_blocking(status: Status | str) -> bool:
    return coerce_status(status) in {Status.FAIL, Status.REVIEW}


@dataclass(slots=True, frozen=True)
class WarningMessage:
    message: str
    field: str = ""
    target_path: Path | None = None
    status: Status = Status.WARN


@dataclass(slots=True)
class SafetyContext:
    automated_validation: bool = field(default_factory=automated_validation_enabled)
    target_path: Path | None = None
    library_path: Path | None = None
    require_lab_for_apply: bool = True

    def check_apply_allowed(self, apply: bool, context: str = "noqlen-forge") -> None:
        if not apply or not self.automated_validation or not self.require_lab_for_apply:
            return
        target = self.target_path or self.library_path
        if target is not None:
            require_lab_path_for_automated_apply(target, context=context)

    def check_destructive_allowed(self, apply: bool, context: str = "noqlen-forge") -> None:
        self.check_apply_allowed(apply, context=context)
        target = self.target_path or self.library_path
        if apply and target is not None and is_dangerous_real_library_path(target):
            require_lab_path_for_automated_apply(target, context=context)

    def check_library_destination(self, apply: bool, context: str = "noqlen-forge") -> None:
        if not apply:
            return
        target = self.library_path or self.target_path
        if target is not None and self.automated_validation and self.require_lab_for_apply:
            require_lab_path_for_automated_apply(target, context=context)


@dataclass(slots=True)
class OperationContext:
    command: str
    target: Path | None = None
    target_type: str = "path"
    apply: bool = False
    verbose: bool = False
    debug: bool = False
    automated_validation: bool = field(default_factory=automated_validation_enabled)
    config: dict[str, Any] | None = None
    database_enabled: bool = True
    started_at: float = field(default_factory=time.perf_counter)
    safety_context: SafetyContext | None = None

    def __post_init__(self) -> None:
        if self.safety_context is None:
            self.safety_context = SafetyContext(automated_validation=self.automated_validation, target_path=self.target)

    @classmethod
    def from_flags(
        cls,
        command: str,
        target: Path | None = None,
        *,
        target_type: str = "path",
        apply: bool = False,
        verbose: bool = False,
        debug: bool = False,
        config: dict[str, Any] | None = None,
        database_enabled: bool = True,
        library_path: Path | None = None,
    ) -> OperationContext:
        safety = SafetyContext(target_path=target, library_path=library_path)
        return cls(command=command, target=target, target_type=target_type, apply=apply, verbose=verbose, debug=debug, config=config, database_enabled=database_enabled, safety_context=safety)


@dataclass(slots=True)
class StepResult:
    index: int
    total: int
    name: str
    status: Status | str
    summary: str = ""
    details: list[str] = field(default_factory=list)
    warnings: list[WarningMessage | str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    skipped_reason: str = ""

    def __post_init__(self) -> None:
        self.status = coerce_status(self.status)


@dataclass(slots=True)
class WorkflowResult:
    status: Status
    steps: list[StepResult]
    workflow: str = ""
    command: str = ""
    target: Path | None = None
    target_type: str = "path"
    mode: str = "read-only"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int | float | str] = field(default_factory=dict)
    planned_changes: Any = None
    applied_changes: Any = None
    artifacts: list[Any] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    safe_details: dict[str, Any] = field(default_factory=dict)
    warnings: list[WarningMessage | str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    stopped: bool = False
    job: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.status = coerce_status(self.status)
        if not self.workflow:
            self.workflow = self.command
        if self.started_at is None:
            self.started_at = datetime.now(timezone.utc)
        if not self.job:
            self.job = {
                "job_id": None,
                "resumable": False,
                "cancelable": False,
                "progress_current": len(self.steps),
                "progress_total": len(self.steps),
                "progress_label": self.steps[-1].name if self.steps else "",
            }


StepCallable = Callable[[OperationContext, int, int], StepResult]


class WorkflowRunner:
    """Run command pipeline steps when a flow needs shared status/timing behavior."""

    def __init__(self, context: OperationContext, *, stop_on_review: bool = False) -> None:
        self.context = context
        self.stop_on_review = stop_on_review

    def run(self, steps: list[StepCallable]) -> WorkflowResult:
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc)
        results: list[StepResult] = []
        warnings: list[WarningMessage | str] = []
        stopped = False
        total = len(steps)
        for position, step in enumerate(steps, 1):
            step_started = time.perf_counter()
            try:
                result = step(self.context, position, total)
            except Exception as exc:
                result = StepResult(position, total, getattr(step, "__name__", "step"), Status.FAIL, str(exc))
            result.elapsed_seconds = time.perf_counter() - step_started
            results.append(result)
            warnings.extend(result.warnings)
            if result.status == Status.FAIL or (self.stop_on_review and result.status == Status.REVIEW):
                stopped = True
                break
        finished_at = datetime.now(timezone.utc)
        return WorkflowResult(status=combine_status(*(step.status for step in results)), steps=results, workflow=self.context.command, command=self.context.command, target=self.context.target, target_type=self.context.target_type, mode="apply" if self.context.apply else "read-only", started_at=started_at, finished_at=finished_at, warnings=warnings, elapsed_seconds=time.perf_counter() - started, stopped=stopped)


@dataclass(slots=True, frozen=True)
class PlannedChange:
    target_path: Path
    target_type: str
    field: str
    old_value: Any = None
    new_value: Any = None
    action: str = "write"
    source: str = ""
    confidence: str = ""
    reason: str = ""
    target_id: str = ""
    safe_preview: str = ""


@dataclass(slots=True, frozen=True)
class AppliedChange:
    target_type: str
    target_id: str = ""
    target_path: Path | None = None
    field: str = ""
    old_value: Any = None
    new_value: Any = None
    action: str = "write"
    source: str = ""
    confidence: str = ""
    reason: str = ""
    safe_preview: str = ""
    applied_at: datetime | None = None
    status: Status | str = Status.OK


@dataclass(slots=True, frozen=True)
class Artifact:
    type: str
    path: Path | None = None
    format: str = ""
    description: str = ""
    created: datetime | None = None
    size_bytes: int | None = None
    safe_to_show: bool = True


@dataclass(slots=True)
class ApplyResult:
    status: Status
    applied: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ChangePlan:
    """Collect planned writes/removals/skips/conflicts before dry-run or apply."""

    changes: list[PlannedChange] = field(default_factory=list)
    removals: list[PlannedChange] = field(default_factory=list)
    skips: list[PlannedChange] = field(default_factory=list)
    conflicts: list[PlannedChange] = field(default_factory=list)
    warnings: list[WarningMessage | str] = field(default_factory=list)
    status: Status = Status.OK

    def add_write(self, target_path: Path, target_type: str, field: str, old_value: Any, new_value: Any, *, source: str = "", confidence: str = "", reason: str = "") -> PlannedChange:
        change = PlannedChange(target_path, target_type, field, old_value, new_value, "write", source, confidence, reason)
        self.changes.append(change)
        self._refresh_status()
        return change

    def add_remove(self, target_path: Path, target_type: str, field: str, old_value: Any, *, source: str = "", confidence: str = "", reason: str = "") -> PlannedChange:
        change = PlannedChange(target_path, target_type, field, old_value, None, "remove", source, confidence, reason)
        self.removals.append(change)
        self._refresh_status()
        return change

    def add_skip(self, target_path: Path, target_type: str, field: str, reason: str, *, old_value: Any = None, new_value: Any = None, source: str = "") -> PlannedChange:
        change = PlannedChange(target_path, target_type, field, old_value, new_value, "skip", source, "", reason)
        self.skips.append(change)
        self._refresh_status()
        return change

    def add_conflict(self, target_path: Path, target_type: str, field: str, old_value: Any, new_value: Any, reason: str, *, source: str = "", confidence: str = "") -> PlannedChange:
        change = PlannedChange(target_path, target_type, field, old_value, new_value, "conflict", source, confidence, reason)
        self.conflicts.append(change)
        self._refresh_status()
        return change

    def has_writes(self) -> bool:
        return bool(self.changes or self.removals)

    def summary(self) -> dict[str, int | str]:
        self._refresh_status()
        return {"writes": len(self.changes), "removals": len(self.removals), "skips": len(self.skips), "conflicts": len(self.conflicts), "warnings": len(self.warnings), "status": self.status.value}

    def _refresh_status(self) -> None:
        self.status = combine_status(Status.REVIEW if self.conflicts else Status.OK, status_from_warnings(self.warnings))
