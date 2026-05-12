from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..lyrics import lyrics_path
from ..workflow import OperationContext, Status, StepResult, WorkflowRunner


@dataclass(slots=True, frozen=True)
class LyricsOptions:
    path: Path
    config: dict[str, Any] | None = None
    apply: bool = False
    force: bool = False
    embed_lyrics: bool = True
    save_lrc: bool = True
    save_txt: bool = False
    prefer_synced: bool = True
    allow_unsynced: bool = True
    sources: list[str] | None = None
    min_confidence: str = "medium"
    verbose: bool = False
    debug: bool = False
    prefer_local: bool | None = None
    allow_instrumental: bool | None = None
    allow_empty: bool | None = None


def run_lyrics_service(options: LyricsOptions):
    state: dict[str, Any] = {}
    context = OperationContext.from_flags("lyrics", options.path, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config)
    context.safety_context.check_apply_allowed(options.apply, context="noqlen-forge lyrics service")

    def process(_: OperationContext, index: int, total: int) -> StepResult:
        code, output = lyrics_path(
            options.path,
            apply=options.apply,
            force=options.force,
            embed_lyrics=options.embed_lyrics,
            save_lrc=options.save_lrc,
            save_txt=options.save_txt,
            prefer_synced=options.prefer_synced,
            allow_unsynced=options.allow_unsynced,
            sources=options.sources,
            min_confidence=options.min_confidence,
            verbose=options.verbose,
            debug=options.debug,
            config=options.config,
            prefer_local=options.prefer_local,
            allow_instrumental=options.allow_instrumental,
            allow_empty=options.allow_empty,
        )
        state["code"] = code
        state["output"] = output
        status = Status.FAIL if code else Status.APPLY if options.apply else Status.DRY
        return StepResult(index, total, "Process lyrics", status, "apply" if options.apply else "dry-run")

    workflow = WorkflowRunner(context).run([process])
    if workflow.status != Status.FAIL:
        workflow.status = Status.APPLY if options.apply else Status.DRY
    workflow.mode = "apply" if options.apply else "dry-run"
    workflow.summary = {"target": str(options.path), "mode": workflow.mode, "status": workflow.status.value}
    workflow.counts = {"warnings": 0, "errors": 1 if state.get("code") else 0}
    workflow.details = {"output_text": state.get("output", "")}
    workflow.safe_details = {"mode": workflow.mode, "target": str(options.path)}
    if state.get("code"):
        workflow.errors = [state.get("output", "lyrics failed")]
    return workflow


def render_lyrics_service_result(result) -> tuple[int, str]:
    return (1 if result.status == Status.FAIL else 0), result.details.get("output_text", "")
