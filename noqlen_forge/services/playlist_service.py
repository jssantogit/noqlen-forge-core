from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..safety import SafetyError
from ..smart_playlists import _matching_rows, _render_playlist, _replace_definition, _require_definition, _write_output
from ..workflow import Artifact, OperationContext, Status, StepResult, WorkflowRunner


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
