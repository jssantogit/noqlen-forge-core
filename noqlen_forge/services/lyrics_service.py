from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..lyrics import lyrics_path, process_lyrics, read_tracks, render_lyrics_result
from ..workflow import OperationContext, Status, StepResult, WorkflowRunner

_DEFAULT_LYRICS_PATH = lyrics_path


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
        if lyrics_path is not _DEFAULT_LYRICS_PATH:
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
            state.update({"code": code, "output": output, "stats": None})
            status = Status.FAIL if code else Status.APPLY if options.apply else Status.DRY
            return StepResult(index, total, "Process lyrics", status, "apply" if options.apply else "dry-run")
        tracks = read_tracks(options.path)
        if not tracks:
            output = "No supported audio files found"
            state.update({"code": 1, "output": output, "stats": None})
            return StepResult(index, total, "Process lyrics", Status.FAIL, output)
        stats = process_lyrics(tracks, apply=options.apply, force=options.force, embed_lyrics=options.embed_lyrics, save_lrc=options.save_lrc, save_txt=options.save_txt, prefer_synced=options.prefer_synced, allow_unsynced=options.allow_unsynced, sources=options.sources, min_confidence=options.min_confidence, debug=options.debug, config=options.config, prefer_local=options.prefer_local, allow_instrumental=options.allow_instrumental, allow_empty=options.allow_empty)
        output = render_lyrics_result(stats, apply=options.apply, force=options.force, embed_lyrics=options.embed_lyrics, save_lrc=options.save_lrc, save_txt=options.save_txt, verbose=options.verbose, debug=options.debug)
        code = 1 if stats.status == "FAIL" else 0
        state.update({"code": code, "output": output, "stats": stats})
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
    stats = state.get("stats")
    if stats is not None:
        selected = list(stats.per_file.values())
        structured = {"files": stats.total, "selected": len(selected), "synced": sum(1 for item in selected if item.synced), "unsynced": sum(1 for item in selected if not item.synced), "embedded_existing": stats.embedded_existing, "embedded_written": stats.embedded_written, "sidecar_written": stats.sidecar_written, "missing": stats.missing, "skipped": stats.skipped, "conflicts": len(stats.conflicts), "providers": sorted({item.provider for item in selected if item.provider})}
        workflow.summary.update({"status": stats.status, **structured})
        workflow.counts.update({"warnings": len(stats.warnings) + len(stats.selection_warnings), "errors": len(stats.errors), "provider_attempts": sum(len(items) for items in stats.provider_attempts.values())})
        workflow.safe_details.update({"lyrics": structured, "provider_attempts": [{"file": path.name, "provider": attempt.provider, "status": attempt.status, "message": attempt.message} for path, attempts in stats.provider_attempts.items() for attempt in attempts]})
    if state.get("code"):
        workflow.errors = [state.get("output", "lyrics failed")]
    return workflow


def render_lyrics_service_result(result) -> tuple[int, str]:
    return (1 if result.status == Status.FAIL else 0), result.details.get("output_text", "")
