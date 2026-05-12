from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..workflow import AppliedChange, ApplyResult, Artifact, ChangePlan, PlannedChange, Status, StepResult, WarningMessage, WorkflowResult

_SENSITIVE_KEYS = ("lyric", "lyrics", "fingerprint", "secret", "token", "api_key", "apikey", "password", "salt", "authorization", "cookie", "set-cookie", "headers", "output_text")
_SENSITIVE_VALUE_MARKERS = ("password=", "token=", "api_key=", "apikey=", "authorization:", "bearer ")
_MAX_STRING = 500


def workflow_result_to_dict(result: WorkflowResult) -> dict[str, Any]:
    return sanitize_result_for_json(result)


def workflow_result_to_json(result: WorkflowResult) -> str:
    return json.dumps(workflow_result_to_dict(result), ensure_ascii=False, indent=2, sort_keys=True)


def workflow_result_from_dict(data: dict[str, Any]) -> WorkflowResult:
    steps = [StepResult(int(item.get("index", 0)), int(item.get("total", 0)), str(item.get("name", "")), Status(str(item.get("status", "OK")).upper()), str(item.get("summary", "")), details=list(item.get("details", []) or []), warnings=list(item.get("warnings", []) or []), elapsed_seconds=float(item.get("elapsed_seconds", 0.0) or 0.0)) for item in data.get("steps", [])]
    return WorkflowResult(
        Status(str(data.get("status", "OK")).upper()),
        steps,
        workflow=str(data.get("workflow") or data.get("command") or ""),
        command=str(data.get("command") or ""),
        target=Path(data["target"]) if data.get("target") else None,
        target_type=str(data.get("target_type") or "path"),
        mode=str(data.get("mode") or "read-only"),
        summary=dict(data.get("summary") or {}),
        counts=dict(data.get("counts") or {}),
        planned_changes=data.get("planned_changes"),
        applied_changes=data.get("applied_changes"),
        artifacts=list(data.get("artifacts") or []),
        metadata=dict(data.get("metadata") or {}),
        safe_details=dict(data.get("safe_details") or {}),
        warnings=list(data.get("warnings") or []),
        errors=list(data.get("errors") or []),
        elapsed_seconds=float(data.get("elapsed_seconds", 0.0) or 0.0),
        stopped=bool(data.get("stopped", False)),
        job=dict(data.get("job") or {}),
    )


def sanitize_result_for_json(result: WorkflowResult) -> dict[str, Any]:
    return _clean(result)


def sanitize_value_for_output(value: Any) -> Any:
    return _clean(value)


def _clean(value: Any, *, key: str = "") -> Any:
    if key.startswith("_"):
        return None
    if _is_sensitive_key(key):
        return "[redacted]"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseException):
        return _clean_string(str(value))
    if isinstance(value, bytes):
        return f"[bytes:{len(value)}]"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _clean_string(value) if isinstance(value, str) else value
    if isinstance(value, WorkflowResult):
        safe_details = value.safe_details or _clean(value.details, key="details")
        return {
            "workflow": value.workflow or value.command,
            "command": value.command,
            "target": _clean(value.target, key="target"),
            "target_type": value.target_type,
            "mode": value.mode,
            "status": value.status.value,
            "started_at": _clean(value.started_at, key="started_at"),
            "finished_at": _clean(value.finished_at, key="finished_at"),
            "elapsed_seconds": round(value.elapsed_seconds, 6),
            "steps": _clean(value.steps, key="steps"),
            "summary": _clean(value.summary, key="summary"),
            "warnings": _clean(value.warnings, key="warnings"),
            "errors": _clean(value.errors, key="errors"),
            "planned_changes": _clean(value.planned_changes, key="planned_changes"),
            "applied_changes": _clean(value.applied_changes, key="applied_changes"),
            "artifacts": _clean(value.artifacts, key="artifacts"),
            "counts": _clean(value.counts, key="counts"),
            "metadata": _clean(value.metadata, key="metadata"),
            "details": safe_details,
            "safe_details": safe_details,
            "job": _clean(value.job, key="job"),
            "stopped": value.stopped,
        }
    if isinstance(value, WarningMessage):
        return {"message": _clean(value.message, key="message"), "field": value.field, "target_path": _clean(value.target_path, key="target_path"), "status": value.status.value}
    if isinstance(value, StepResult):
        return {
            "index": value.index,
            "total": value.total,
            "name": value.name,
            "status": value.status.value,
            "summary": _clean(value.summary, key="summary"),
            "details": _clean(value.details, key="details"),
            "warnings": _clean(value.warnings, key="warnings"),
            "elapsed_seconds": round(value.elapsed_seconds, 6),
            "skipped_reason": _clean(value.skipped_reason, key="skipped_reason"),
        }
    if isinstance(value, PlannedChange):
        return _change_dict(value)
    if isinstance(value, AppliedChange):
        data = _change_dict(value)
        data["applied_at"] = _clean(value.applied_at, key="applied_at")
        data["status"] = _clean(value.status, key="status")
        return data
    if isinstance(value, Artifact):
        data = {field.name: _clean(getattr(value, field.name), key=field.name) for field in fields(value)}
        if value.path is not None and value.size_bytes is None:
            try:
                data["size_bytes"] = value.path.stat().st_size
            except OSError:
                data["size_bytes"] = None
        return data
    if isinstance(value, ChangePlan):
        return {"summary": value.summary(), "changes": _clean(value.changes, key="changes"), "removals": _clean(value.removals, key="removals"), "skips": _clean(value.skips, key="skips"), "conflicts": _clean(value.conflicts, key="conflicts"), "warnings": _clean(value.warnings, key="warnings")}
    if isinstance(value, ApplyResult):
        return {"status": value.status.value, "applied": value.applied, "skipped": value.skipped, "errors": _clean(value.errors, key="errors")}
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for item_key, item_value in value.items():
            text_key = str(item_key)
            if text_key.startswith("_"):
                continue
            cleaned[text_key] = _clean(item_value, key=text_key)
        return cleaned
    if isinstance(value, (list, tuple, set)):
        return [_clean(item, key=key) for item in value]
    if is_dataclass(value):
        return {field.name: _clean(getattr(value, field.name), key=field.name) for field in fields(value) if not field.name.startswith("_")}
    return _clean_string(str(value))


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(word in lowered for word in _SENSITIVE_KEYS)


def _clean_string(value: str) -> str:
    lowered = value.casefold()
    if any(marker in lowered for marker in _SENSITIVE_VALUE_MARKERS):
        return "[redacted]"
    if any(word in lowered for word in _SENSITIVE_KEYS) and len(value) > 80:
        return "[redacted]"
    return value if len(value) <= _MAX_STRING else value[: _MAX_STRING - 3] + "..."


def _change_dict(value: PlannedChange | AppliedChange) -> dict[str, Any]:
    return {
        "target_type": _clean(value.target_type, key="target_type"),
        "target_id": _clean(value.target_id, key="target_id"),
        "target_path": _clean(value.target_path, key="target_path"),
        "field": _clean(value.field, key="field"),
        "old_value": _clean(value.old_value, key=value.field or "old_value"),
        "new_value": _clean(value.new_value, key=value.field or "new_value"),
        "action": _clean(value.action, key="action"),
        "source": _clean(value.source, key="source"),
        "confidence": _clean(value.confidence, key="confidence"),
        "reason": _clean(value.reason, key="reason"),
        "safe_preview": _clean(value.safe_preview or _preview_change_value(value.new_value), key="safe_preview"),
    }


def _preview_change_value(value: Any) -> str:
    cleaned = _clean(value, key="preview")
    if isinstance(cleaned, (dict, list)):
        return _clean_string(json.dumps(cleaned, ensure_ascii=False, sort_keys=True))
    return "" if cleaned is None else _clean_string(str(cleaned))
