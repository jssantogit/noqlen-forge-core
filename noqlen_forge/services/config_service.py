from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import config_path, load_config, masked_config, render_config, save_default_config
from ..workflow import Status, StepResult, WorkflowResult


@dataclass(frozen=True)
class ConfigOptions:
    command: str
    config: dict[str, Any] | None = None
    force: bool = False
    path: Path | None = None


def run_config_service(options: ConfigOptions) -> WorkflowResult:
    target = options.path or config_path()
    if options.command == "path":
        return _result("config.path", target, Status.OK, "Config path resolved", {"path": target}, details={"path": target})
    if options.command == "init":
        if target.exists() and not options.force:
            return _result("config.init", target, Status.FAIL, "Config already exists", {"path": target, "created": False, "requires_force": True}, errors=[f"Config already exists: {target}"])
        saved = save_default_config(target)
        return _result("config.init", saved, Status.OK, "Config created", {"path": saved, "created": True, "overwritten": bool(options.force)})
    if options.command == "show":
        safe_config = masked_config(options.config or load_config())
        rendered = render_config(safe_config, mask_secrets=False)
        return _result("config.show", target, Status.OK, "Config loaded", {"path": target, "sections": len(safe_config)}, details={"config": safe_config, "rendered": rendered}, safe_details={"config": safe_config})
    return _result(f"config.{options.command}", target, Status.FAIL, "Unknown config command", {"path": target}, errors=[f"Unknown config command: {options.command}"])


def render_config_service_result(result: WorkflowResult) -> tuple[int, str]:
    if result.command == "config.path":
        return 0 if result.status == Status.OK else 1, str(result.summary.get("path", ""))
    if result.command == "config.init":
        path = result.summary.get("path", "")
        if result.status == Status.OK:
            return 0, f"Created config: {path}"
        return 1, f"Config already exists: {path}\nUse --force to overwrite."
    if result.command == "config.show":
        return 0 if result.status == Status.OK else 1, str(result.details.get("rendered", ""))
    return 1, "\n".join(result.errors or [result.steps[-1].summary if result.steps else "Config command failed"])


def _result(command: str, target: Path, status: Status, step_summary: str, summary: dict[str, Any], *, details: dict[str, Any] | None = None, safe_details: dict[str, Any] | None = None, errors: list[str] | None = None) -> WorkflowResult:
    return WorkflowResult(status, [StepResult(1, 1, command, status, step_summary)], workflow=command, command=command, target=target, target_type="config", mode="apply" if command == "config.init" and status == Status.OK else "read-only", summary=summary, details=details or summary, safe_details=safe_details or summary, errors=errors or [])
