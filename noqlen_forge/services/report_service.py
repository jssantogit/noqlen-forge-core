from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import get_config_value
from ..db import db_query
from ..duplicates import duplicates_path
from ..export import export_data
from ..reports import missing_files_report, missing_report, untracked_report
from ..workflow import OperationContext, Status, StepResult, WorkflowRunner
from .cli_helpers import parse_fields
from .result_helpers import add_output_artifact, finish_text_result, first_line


@dataclass(slots=True, frozen=True)
class QueryOptions:
    config: dict[str, Any]
    query: str
    target: str = "tracks"
    limit: int = 50
    output_format: str = "text"
    verbose: bool = False
    debug: bool = False


@dataclass(slots=True, frozen=True)
class ExportOptions:
    config: dict[str, Any]
    query: str | None = None
    export_format: str = "json"
    output: Path | None = None
    force: bool = False
    scope: str = "tracks"
    all_data: bool = False
    missing: bool = False
    duplicates: bool = False
    reviews: bool = False
    library: Path | None = None
    fields: str | None = None
    exclude_fields: str | None = None
    include_tags: bool = False
    include_audio: bool = False
    include_assets: bool = False
    include_provider_history: bool = False
    verbose: bool = False
    debug: bool = False


@dataclass(slots=True, frozen=True)
class DuplicatesOptions:
    config: dict[str, Any]
    target: Path | None = None
    scope: str = "tracks"
    by: str | None = None
    strategy: str = "safe"
    output_format: str = "text"
    verbose: bool = False
    debug: bool = False


@dataclass(slots=True, frozen=True)
class MissingOptions:
    config: dict[str, Any]
    fields: list[str] | None = None
    library: Path | None = None
    scope: str = "albums"
    output_format: str = "text"
    verbose: bool = False
    debug: bool = False


@dataclass(slots=True, frozen=True)
class UntrackedOptions:
    config: dict[str, Any]
    path: Path | None = None
    output_format: str = "text"
    verbose: bool = False


@dataclass(slots=True, frozen=True)
class MissingFilesOptions:
    config: dict[str, Any]
    output_format: str = "text"
    verbose: bool = False


def build_export_options(
    config: dict[str, Any],
    query: str | None,
    *,
    albums: bool = False,
    files: bool = False,
    export_format: str = "json",
    output: Path | None = None,
    force: bool = False,
    all_data: bool = False,
    missing: bool = False,
    duplicates: bool = False,
    reviews: bool = False,
    library: Path | None = None,
    fields: str | None = None,
    exclude_fields: str | None = None,
    include_tags: bool = False,
    include_audio: bool = False,
    include_assets: bool = False,
    include_provider_history: bool = False,
    verbose: bool = False,
    debug: bool = False,
) -> ExportOptions:
    scope = "albums" if albums else "files" if files else "tracks"
    return ExportOptions(config, query, export_format=export_format, output=output, force=force, scope=scope, all_data=all_data, missing=missing, duplicates=duplicates, reviews=reviews, library=library, fields=fields, exclude_fields=exclude_fields, include_tags=include_tags, include_audio=include_audio, include_assets=include_assets, include_provider_history=include_provider_history, verbose=verbose, debug=debug)


def build_duplicates_options(
    config: dict[str, Any],
    *,
    target: Path | None = None,
    albums: bool = False,
    tracks: bool = False,
    by: str | None = None,
    strategy: str | None = None,
    output_format: str = "text",
    verbose: bool = False,
    debug: bool = False,
) -> DuplicatesOptions:
    scope = "albums" if albums else "tracks" if tracks else str(get_config_value(config, "duplicates", "default_scope", "tracks"))
    resolved_strategy = strategy or str(get_config_value(config, "duplicates", "default_strategy", "safe"))
    return DuplicatesOptions(config, target=target, scope=scope, by=by, strategy=resolved_strategy, output_format=output_format, verbose=verbose, debug=debug)


def build_missing_options(
    config: dict[str, Any],
    *,
    field: str | None = None,
    field_option: str | None = None,
    fields_csv: str | None = None,
    library: Path | None = None,
    tracks: bool = False,
    output_format: str = "text",
    verbose: bool = False,
    debug: bool = False,
) -> MissingOptions:
    fields = parse_fields([item for item in (field, field_option) if item], fields_csv)
    scope = "tracks" if tracks else "albums"
    return MissingOptions(config, fields=fields, library=library, scope=scope, output_format=output_format, verbose=verbose, debug=debug)


def build_untracked_options(config: dict[str, Any], *, path: Path | None = None, library: Path | None = None, output_format: str = "text", verbose: bool = False) -> UntrackedOptions:
    return UntrackedOptions(config, path=library or path, output_format=output_format, verbose=verbose)


def build_missing_files_options(config: dict[str, Any], *, output_format: str = "text", verbose: bool = False) -> MissingFilesOptions:
    return MissingFilesOptions(config, output_format=output_format, verbose=verbose)


def run_query_service(options: QueryOptions):
    return _run_tuple("query", None, lambda: db_query(options.config, options.query, target=options.target, limit=options.limit, output_format=options.output_format, verbose=options.verbose, debug=options.debug), mode="read-only")


def run_export_service(options: ExportOptions):
    result = _run_tuple("export", options.output, lambda: export_data(options.config, options.query, export_format=options.export_format, output=options.output, force=options.force, scope=options.scope, all_data=options.all_data, missing=options.missing, duplicates=options.duplicates, reviews=options.reviews, library=options.library, fields=options.fields, exclude_fields=options.exclude_fields, include_tags=options.include_tags, include_audio=options.include_audio, include_assets=options.include_assets, include_provider_history=options.include_provider_history, verbose=options.verbose, debug=options.debug), mode="read-only")
    return add_output_artifact(result, options.output, artifact_type="file", output_format=options.export_format, description="Export output")


def run_duplicates_service(options: DuplicatesOptions):
    return _run_tuple("duplicates", options.target, lambda: duplicates_path(options.config, target=options.target, scope=options.scope, by=options.by, strategy=options.strategy, output_format=options.output_format, verbose=options.verbose, debug=options.debug), mode="read-only")


def run_missing_service(options: MissingOptions):
    return _run_tuple("missing", options.library, lambda: missing_report(options.config, fields=options.fields, library=options.library, scope=options.scope, output_format=options.output_format, verbose=options.verbose, debug=options.debug), mode="read-only")


def run_untracked_service(options: UntrackedOptions):
    return _run_tuple("untracked", options.path, lambda: untracked_report(options.config, path=options.path, output_format=options.output_format, verbose=options.verbose), mode="read-only")


def run_missing_files_service(options: MissingFilesOptions):
    return _run_tuple("missing-files", None, lambda: missing_files_report(options.config, output_format=options.output_format, verbose=options.verbose), mode="read-only")


def _run_tuple(command: str, target: Path | None, call, *, mode: str):
    context = OperationContext.from_flags(command, target, apply=False)
    state: dict[str, Any] = {}

    def process(_: OperationContext, index: int, total: int) -> StepResult:
        code, output = call()
        state.update({"code": code, "output": output})
        return StepResult(index, total, command.title(), Status.FAIL if code else Status.OK, first_line(output))

    workflow = WorkflowRunner(context).run([process])
    code = int(state.get("code", 1 if workflow.status == Status.FAIL else 0))
    return finish_text_result(workflow, code=code, output=state.get("output", ""), mode=mode, status=Status.FAIL if code else Status.OK)


def render_report_result(result, *, title: str, scope: str, output_format: str) -> tuple[int, str]:
    code = int(result.details.get("exit_code", 1 if result.status == Status.FAIL else 0)) if result.details else (1 if result.status == Status.FAIL else 0)
    output = str(result.details.get("output_text", "")) if result.details else ""
    if output_format == "json":
        return code, output
    return code, f"Report: {title}\nScope: {scope}\n\n{output}"


def report_scope_label(path: Path | None) -> str:
    return str(path) if path is not None else "library"


def missing_report_title(fields: list[str] | None) -> str:
    return "Missing " + ", ".join(_display_field(item) for item in fields) if fields else "Missing Metadata"


def _display_field(field: str) -> str:
    return field.replace("_", " ").replace("-", " ").title()
