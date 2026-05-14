from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..safety import SafetyError
from ..smart_playlists import _matching_rows, _render_playlist, _replace_definition, _require_definition, _write_output, smart_create, smart_delete, smart_list, smart_rename, smart_show
from ..workflow import Artifact, OperationContext, Status, StepResult, WorkflowResult, WorkflowRunner


@dataclass(slots=True, frozen=True)
class PlaylistExportOptions:
    config: dict[str, Any]
    name: str
    export_format: str | None = None
    output: Path | None = None
    force: bool = False
    path_mode: str | None = None
    library_root: Path | None = None
    verbose: bool = False
    debug: bool = False
    command: str = "playlist smart export"


@dataclass(slots=True, frozen=True)
class PlaylistOptions:
    config: dict[str, Any]
    command: str
    name: str = ""
    query: str = ""
    new_name: str = ""
    apply: bool = False
    default_format: str = "m3u8"
    sort: str | None = None
    reverse: bool = False
    limit: int | None = None
    path_mode: str = "absolute"
    library_root: Path | None = None
    force: bool = False
    output_format: str = "text"
    verbose: bool = False
    debug: bool = False


def run_playlist_service(options: PlaylistOptions) -> WorkflowResult:
    command = options.command
    if command == "create":
        return _run_legacy_playlist(
            options,
            lambda: smart_create(options.config, options.name, options.query, apply=options.apply, default_format=options.default_format, sort=options.sort, reverse=options.reverse, limit=options.limit, path_mode=options.path_mode, library_root=options.library_root, force=options.force, output_format=options.output_format, verbose=options.verbose, debug=options.debug),
            summary={"name": options.name, "mode": "APPLY" if options.apply else "DRY-RUN"},
        )
    if command == "list":
        return _run_legacy_playlist(options, lambda: smart_list(options.config, output_format=options.output_format, verbose=options.verbose, debug=options.debug))
    if command == "show":
        return _run_legacy_playlist(options, lambda: smart_show(options.config, options.name, output_format=options.output_format, verbose=options.verbose, debug=options.debug), summary={"name": options.name})
    if command == "delete":
        return _run_legacy_playlist(options, lambda: smart_delete(options.config, options.name, apply=options.apply, output_format=options.output_format, verbose=options.verbose, debug=options.debug), summary={"name": options.name, "mode": "APPLY" if options.apply else "DRY-RUN"})
    if command == "rename":
        return _run_legacy_playlist(options, lambda: smart_rename(options.config, options.name, options.new_name, apply=options.apply, force=options.force, output_format=options.output_format, verbose=options.verbose, debug=options.debug), summary={"old_name": options.name, "new_name": options.new_name, "mode": "APPLY" if options.apply else "DRY-RUN"})
    return _playlist_result(command, Status.FAIL, {"command": command}, errors=[f"Unknown playlist command: {command}"])


def render_playlist_service_result(result: WorkflowResult) -> tuple[int, str]:
    code = int(result.details.get("exit_code", 1 if result.status == Status.FAIL else 0)) if result.details else (1 if result.status == Status.FAIL else 0)
    return code, str(result.details.get("output_text", "")) if result.details else ""


def run_playlist_export_service(options: PlaylistExportOptions):
    state: dict[str, Any] = {}
    context = OperationContext.from_flags(options.command, options.output, target_type="playlist", apply=False, verbose=options.verbose, debug=options.debug, config=options.config)

    def load_definition(_: OperationContext, index: int, total: int) -> StepResult:
        definition = _require_definition(options.config, options.name)
        if options.export_format:
            definition = _replace_definition(definition, default_format=options.export_format)
        if options.path_mode or options.library_root:
            changes: dict[str, Any] = {}
            if options.path_mode:
                changes["path_mode"] = options.path_mode
            if options.library_root:
                changes["library_root"] = str(options.library_root.expanduser())
            definition = _replace_definition(definition, **changes)
        state["definition"] = definition
        return StepResult(index, total, "Load definition", Status.OK, definition.name)

    def query_tracks(_: OperationContext, index: int, total: int) -> StepResult:
        rows = _matching_rows(options.config, state["definition"])
        state["rows"] = rows
        return StepResult(index, total, "Query tracks", Status.OK, f"{len(rows)} tracks")

    def render_output(_: OperationContext, index: int, total: int) -> StepResult:
        rendered = _render_playlist(state["definition"], state["rows"], output=options.output)
        state["rendered"] = rendered
        return StepResult(index, total, "Render output", Status.OK, state["definition"].default_format.upper())

    def write_artifact(_: OperationContext, index: int, total: int) -> StepResult:
        if options.output is None:
            return StepResult(index, total, "Write artifact", Status.OK, "stdout")
        written = _write_output(options.output, state["rendered"], state["definition"].default_format, force=options.force)
        state["written"] = written
        return StepResult(index, total, "Write artifact", Status.OK, str(written))

    try:
        workflow = WorkflowRunner(context).run([load_definition, query_tracks, render_output, write_artifact])
    except (ValueError, sqlite3.DatabaseError, OSError, SafetyError) as exc:
        workflow = WorkflowRunner(context).run([])
        workflow.status = Status.FAIL
        workflow.errors = [str(exc)]
    if workflow.status == Status.FAIL and not workflow.errors:
        workflow.errors = [workflow.steps[-1].summary] if workflow.steps else ["playlist export failed"]
    definition = state.get("definition")
    rows = state.get("rows", [])
    written = state.get("written")
    workflow.mode = "read-only"
    workflow.summary = {"name": options.name, "format": getattr(definition, "default_format", options.export_format or ""), "tracks": len(rows), "output": str(written) if written else "stdout" if "rendered" in state else ""}
    workflow.counts = {"tracks": len(rows)}
    if written:
        workflow.artifacts = [Artifact("playlist", path=written, format=getattr(definition, "default_format", options.export_format or ""), description=f"Smart playlist export: {options.name}")]
    workflow.details = {"output_text": state.get("rendered", ""), "definition": {"name": options.name, "format": getattr(definition, "default_format", options.export_format or "")}}
    workflow.safe_details = {"definition": {"name": options.name, "format": getattr(definition, "default_format", options.export_format or "")}}
    return workflow


def render_playlist_export_result(result, *, name: str) -> tuple[int, str]:
    if result.status == Status.FAIL:
        error = result.errors[0] if result.errors else "unknown error"
        return 1, f"Smart playlist export failed: {error}"
    output_text = result.details.get("output_text", "") if result.details else ""
    if not result.artifacts:
        return 0, output_text
    summary = result.summary
    return 0, "\n".join(["Smart playlist export", f"Name: {name}", f"Format: {str(summary.get('format', '')).upper()}", f"Tracks: {summary.get('tracks', 0)}", f"Output: {summary.get('output', '')}", "Status: OK"])


def _run_legacy_playlist(options: PlaylistOptions, call, *, summary: dict[str, Any] | None = None) -> WorkflowResult:
    try:
        code, output = call()
    except (ValueError, sqlite3.DatabaseError, OSError, SafetyError) as exc:
        code, output = 1, f"Smart playlist {options.command} failed: {exc}"
    payload = _json_payload(output)
    status = Status.FAIL if code else _playlist_status(payload)
    details: dict[str, Any] = {"exit_code": code, "output_text": output}
    if payload is not None:
        details["result"] = payload
    merged_summary = {"status": status.value, "command": options.command, **(summary or {})}
    counts = _playlist_counts(payload, output)
    if payload is not None:
        merged_summary.update(_playlist_summary(payload))
    if code and output:
        errors = [output]
    else:
        errors = []
    return _playlist_result(options.command, status, details, summary=merged_summary, counts=counts, errors=errors, mode="apply" if options.apply and code == 0 else "dry-run" if options.command in {"create", "delete", "rename"} else "read-only")


def _playlist_result(command: str, status: Status, details: dict[str, Any], *, summary: dict[str, Any] | None = None, counts: dict[str, int] | None = None, errors: list[str] | None = None, mode: str = "read-only") -> WorkflowResult:
    step = StepResult(1, 1, f"playlist smart {command}", status, (errors or ["ok"])[0])
    safe_details = {key: value for key, value in details.items() if key != "output_text"}
    return WorkflowResult(status, [step], workflow=f"playlist.smart.{command}", command=f"playlist smart {command}", mode=mode, summary=summary or {"status": status.value}, counts=counts or {}, details=details, safe_details=safe_details, errors=errors or [])


def _json_payload(output: str) -> dict[str, Any] | None:
    if not output.lstrip().startswith("{"):
        return None
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _playlist_status(payload: dict[str, Any] | None) -> Status:
    if not payload:
        return Status.OK
    try:
        return Status(str(payload.get("status", "OK")).upper())
    except ValueError:
        return Status.OK


def _playlist_counts(payload: dict[str, Any] | None, output: str) -> dict[str, int]:
    if payload is not None:
        if "count" in payload:
            return {"playlists": int(payload.get("count") or 0)}
        if "tracks_now" in payload:
            return {"tracks": int(payload.get("tracks_now") or 0)}
    return {}


def _playlist_summary(payload: dict[str, Any]) -> dict[str, Any]:
    keys = ("count", "tracks_now", "saved", "deleted", "renamed", "name", "old_name", "new_name", "mode")
    return {key: payload[key] for key in keys if key in payload}
