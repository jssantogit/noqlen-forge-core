from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import get_config_value
from ..cover import cover_path
from ..db import database_path, scan_library
from ..replaygain import replaygain_path
from ..workflow import OperationContext, Status, StepResult, WorkflowRunner
from .result_helpers import finish_text_result, first_line


@dataclass(slots=True, frozen=True)
class CoverOptions:
    path: Path
    config: dict[str, Any]
    apply: bool = False
    force: bool = False
    embed_cover: bool = True
    save_folder_cover: bool = False
    folder_cover_filename: str = "cover"
    force_folder_cover: bool = False
    remove_folder_cover: bool = False
    sources: list[str] | None = None
    min_confidence: str = "medium"
    prefer_front: bool = True
    max_size_mb: int = 10
    verbose: bool = False
    debug: bool = False


@dataclass(slots=True, frozen=True)
class ReplayGainOptions:
    path: Path
    config: dict[str, Any]
    apply: bool = False
    force: bool = False
    album: bool = True
    tracks: bool = True
    verbose: bool = False
    debug: bool = False


def run_cover_service(options: CoverOptions):
    context = OperationContext.from_flags("cover", options.path, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config)
    context.safety_context.check_apply_allowed(options.apply, context="noqlen-forge cover service")
    state: dict[str, Any] = {}

    def process(_: OperationContext, index: int, total: int) -> StepResult:
        code, output = cover_path(options.path, apply=options.apply, force=options.force, embed_cover=options.embed_cover, save_folder_cover=options.save_folder_cover, folder_cover_filename=options.folder_cover_filename, force_folder_cover=options.force_folder_cover, remove_folder_cover=options.remove_folder_cover, sources=options.sources, min_confidence=options.min_confidence, prefer_front=options.prefer_front, max_size_mb=options.max_size_mb, verbose=options.verbose, debug=options.debug)
        state.update({"code": code, "output": output})
        return StepResult(index, total, "Process cover", _status_from_code(code, apply=options.apply), first_line(output))

    result = WorkflowRunner(context).run([process])
    code = int(state.get("code", 1 if result.status == Status.FAIL else 0))
    finish_text_result(result, code=code, output=state.get("output", ""), mode="apply" if options.apply else "dry-run", status=_status_from_code(code, apply=options.apply))
    return result


def run_replaygain_service(options: ReplayGainOptions):
    context = OperationContext.from_flags("replaygain", options.path, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config)
    context.safety_context.check_apply_allowed(options.apply, context="noqlen-forge replaygain service")
    state: dict[str, Any] = {}

    def process(_: OperationContext, index: int, total: int) -> StepResult:
        code, output = replaygain_path(options.path, apply=options.apply, force=options.force, album=options.album, tracks=options.tracks, target_lufs=float(get_config_value(options.config, "audio", "target_lufs", -18.0)), write_track_gain=bool(get_config_value(options.config, "audio", "write_track_gain", True)), write_track_peak=bool(get_config_value(options.config, "audio", "write_track_peak", True)), write_album_gain=bool(get_config_value(options.config, "audio", "write_album_gain", True)), write_album_peak=bool(get_config_value(options.config, "audio", "write_album_peak", True)), write_loudness=bool(get_config_value(options.config, "audio", "write_loudness", True)), skip_existing=bool(get_config_value(options.config, "audio", "skip_existing", True)), verbose=options.verbose, debug=options.debug)
        if options.apply and code == 0 and (bool(get_config_value(options.config, "database", "auto_scan", False)) or database_path(options.config).exists()):
            scan_library(options.config, options.path, apply=True)
        state.update({"code": code, "output": output})
        return StepResult(index, total, "ReplayGain", _status_from_code(code, apply=options.apply), first_line(output))

    result = WorkflowRunner(context).run([process])
    code = int(state.get("code", 1 if result.status == Status.FAIL else 0))
    finish_text_result(result, code=code, output=state.get("output", ""), mode="apply" if options.apply else "dry-run", status=_status_from_code(code, apply=options.apply))
    return result


def _status_from_code(code: int, *, apply: bool = False) -> Status:
    if code:
        return Status.FAIL
    return Status.APPLY if apply else Status.OK
