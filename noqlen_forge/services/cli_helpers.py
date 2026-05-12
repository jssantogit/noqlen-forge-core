from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import load_config
from ..workflow import OperationContext, SafetyContext, Status, WorkflowResult
from .types import workflow_result_to_json


def load_cli_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return config or load_config()


def build_safety_context(target: Path | None = None, library: Path | None = None) -> SafetyContext:
    return SafetyContext(target_path=target, library_path=library)


def build_operation_context(command: str, target: Path | None = None, *, apply: bool = False, config: dict[str, Any] | None = None, verbose: bool = False, debug: bool = False, library: Path | None = None) -> OperationContext:
    return OperationContext.from_flags(command, target, apply=apply, verbose=verbose, debug=debug, config=config, library_path=library)


def exit_code_from_status(status: Status | str) -> int:
    value = Status(str(status).upper()) if not isinstance(status, Status) else status
    if value in {Status.OK, Status.SKIP, Status.DRY, Status.APPLY, Status.WARN}:
        return 0
    if value == Status.REVIEW:
        return 2
    return 1


def render_workflow_result(result: WorkflowResult) -> str:
    return str(result.details.get("output_text", "")) if result.details else ""


def render_service_result(result: WorkflowResult) -> tuple[int, str]:
    code = result.details.get("exit_code") if result.details else None
    return int(code) if code is not None else exit_code_from_status(result.status), render_workflow_result(result)


def render_structured_service_result(result: WorkflowResult) -> tuple[int, str]:
    code = result.details.get("exit_code") if result.details else None
    return int(code) if code is not None else exit_code_from_status(result.status), workflow_result_to_json(result)


def parse_fields(values: list[str] | None = None, csv_values: str | None = None) -> list[str] | None:
    fields = list(values or [])
    if csv_values:
        fields.extend(item.strip() for item in csv_values.split(",") if item.strip())
    return fields or None


def parse_provider_list(values: list[str] | None) -> list[str] | None:
    return list(values) if values else None


def parse_output_format(value: str | None, default: str = "text") -> str:
    return value or default


def handle_cli_error(exc: Exception) -> tuple[int, str]:
    return 1, str(exc)
