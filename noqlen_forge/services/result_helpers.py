from __future__ import annotations

import json
from typing import Any

from ..workflow import Artifact, Status, WorkflowResult


def first_line(output: str) -> str:
    return output.splitlines()[0] if output else ""


def status_from_text_output(code: int, output: str, *, default: Status = Status.OK) -> Status:
    if code:
        return Status.FAIL
    marker = "Status: "
    for line in reversed(output.splitlines()):
        if line.startswith(marker):
            try:
                return Status(line.removeprefix(marker).strip().upper())
            except ValueError:
                return default
    return default


def status_from_result(value: str | Status, *, code: int = 0, default: Status = Status.OK) -> Status:
    if code:
        return Status.FAIL
    try:
        return value if isinstance(value, Status) else Status(str(value).upper())
    except ValueError:
        return default


def finish_text_result(workflow: WorkflowResult, *, code: int | None = None, output: str = "", mode: str = "read-only", status: Status | None = None) -> WorkflowResult:
    exit_code = int(code if code is not None else (1 if workflow.status == Status.FAIL else 0))
    final_status = status or status_from_text_output(exit_code, output)
    workflow.status = final_status
    workflow.mode = mode
    workflow.summary = {"status": final_status.value, "exit_code": exit_code}
    payload: Any = None
    if output.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            workflow.summary.update({key: value for key, value in payload.get("summary", {}).items() if isinstance(payload.get("summary"), dict)})
            if "count" in payload:
                workflow.counts["items"] = payload["count"]
    workflow.details = {"exit_code": exit_code, "output_text": output}
    if isinstance(payload, dict):
        workflow.details["result"] = payload
        workflow.summary.update({key: payload[key] for key in ("status", "type", "scope", "query", "count") if key in payload})
    elif isinstance(payload, list):
        workflow.details["result"] = payload
        workflow.counts["items"] = len(payload)
    workflow.safe_details = {"exit_code": exit_code}
    if exit_code:
        workflow.errors = [output]
    return workflow


def add_output_artifact(workflow: WorkflowResult, output_path: Any, *, artifact_type: str, output_format: str, description: str) -> WorkflowResult:
    if output_path is not None:
        workflow.artifacts.append(Artifact(artifact_type, path=output_path, format=output_format, description=description))
    return workflow


def finish_object_result(workflow: WorkflowResult, result: Any, *, mode: str) -> WorkflowResult:
    code = int(getattr(result, "code", 1 if workflow.status == Status.FAIL else 0))
    status = status_from_result(getattr(result, "status", "FAIL" if code else "OK"), code=code, default=Status.FAIL if code else Status.OK)
    return finish_text_result(workflow, code=code, output=str(getattr(result, "output", "")), mode=mode, status=status)
