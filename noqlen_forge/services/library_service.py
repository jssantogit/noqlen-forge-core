from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..importer import ImportResult, import_path
from ..organize import OrganizeResult, organize_path
from ..workflow import OperationContext, StepResult, WorkflowRunner
from .result_helpers import finish_object_result, first_line, status_from_result

EnrichRunner = Callable[[Path, bool, bool, bool, bool, bool, bool, bool], int]


@dataclass(slots=True, frozen=True)
class OrganizeOptions:
    path: Path
    config: dict[str, Any]
    apply: bool = False
    mode: str | None = None
    library: Path | None = None
    template: str | None = None
    singleton_template: str | None = None
    conflict_policy: str | None = None
    verbose: bool = False
    debug: bool = False


@dataclass(slots=True, frozen=True)
class ImportOptions:
    path: Path
    config: dict[str, Any]
    apply: bool = False
    library: Path | None = None
    mode: str | None = None
    replaygain: bool = False
    skip_enrich: bool = False
    skip_cover: bool = False
    skip_lyrics: bool = False
    skip_organize: bool = False
    allow_review: bool = False
    force: bool = False
    verbose: bool = False
    debug: bool = False
    enrich_runner: EnrichRunner | None = None


def run_organize_service(options: OrganizeOptions):
    context = OperationContext.from_flags("organize", options.path, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config, library_path=options.library)
    context.safety_context.check_library_destination(options.apply, context="noqlen-forge organize service")
    state: dict[str, OrganizeResult] = {}

    def process(_: OperationContext, index: int, total: int) -> StepResult:
        result = organize_path(options.path, config=options.config, apply=options.apply, mode=options.mode, library=options.library, template=options.template, singleton_template=options.singleton_template, conflict_policy=options.conflict_policy, verbose=options.verbose, debug=options.debug)
        state["result"] = result
        return StepResult(index, total, "Organize", status_from_result(result.status), first_line(result.output))

    workflow = WorkflowRunner(context).run([process])
    return finish_object_result(workflow, state.get("result"), mode="apply" if options.apply else "dry-run")


def run_import_service(options: ImportOptions):
    context = OperationContext.from_flags("import", options.path, apply=options.apply, verbose=options.verbose, debug=options.debug, config=options.config, library_path=options.library)
    context.safety_context.check_library_destination(options.apply, context="noqlen-forge import service")
    state: dict[str, ImportResult] = {}

    def process(_: OperationContext, index: int, total: int) -> StepResult:
        result = import_path(options.path, config=options.config, apply=options.apply, library=options.library, mode=options.mode, replaygain=options.replaygain, skip_enrich=options.skip_enrich, skip_cover=options.skip_cover, skip_lyrics=options.skip_lyrics, skip_organize=options.skip_organize, allow_review=options.allow_review, force=options.force, verbose=options.verbose, debug=options.debug, enrich_runner=options.enrich_runner)
        state["result"] = result
        return StepResult(index, total, "Import", status_from_result(result.status), first_line(result.output))

    workflow = WorkflowRunner(context).run([process])
    return finish_object_result(workflow, state.get("result"), mode="apply" if options.apply else "dry-run")
