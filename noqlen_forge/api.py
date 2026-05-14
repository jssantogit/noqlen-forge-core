from __future__ import annotations

import copy
import io
import os
import time
import tomllib
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import default_config, load_config, merge_config
from .jobs import JobOptions, JobStore
from .safety import AUTOMATED_VALIDATION_ENV, SafetyError
from .services import (
    AuditOptions,
    ApplyMBIDOptions,
    CandidatesOptions,
    CoverOptions,
    EnrichOptions,
    ImportOptions,
    BatchOptions,
    CleanupOptions,
    LyricsOptions,
    MetadataOptions,
    NavidromePlaylistsOptions,
    NavidromeRatingsOptions,
    OrganizeOptions,
    PlaylistExportOptions,
    RepairOptions,
    ReplayGainOptions,
    ReviewOptions,
    RewriteOptions,
    SyncOptions,
    build_export_options,
    run_audit_service,
    run_apply_mbid_service,
    run_candidates_service,
    run_cover_service,
    run_enrich_service,
    run_export_service,
    run_import_service,
    run_batch_service,
    run_cleanup_service,
    run_lyrics_service,
    run_metadata_service,
    run_navidrome_playlists_service,
    run_navidrome_ratings_service,
    run_organize_service,
    run_playlist_export_service,
    run_repair_service,
    run_replaygain_service,
    run_review_service,
    run_rewrite_service,
    run_sync_service,
)
from .services.job_service import JobsOptions, run_jobs_service
from .workflow import SafetyContext, Status, StepResult, WorkflowResult


class CoreAPIError(RuntimeError):
    """Base error for the internal Noqlen Forge/Core API."""


class ConfigError(CoreAPIError):
    pass


class ValidationError(CoreAPIError):
    pass


class ProviderError(CoreAPIError):
    pass


class DatabaseError(CoreAPIError):
    pass


class NotImplementedWorkflowError(CoreAPIError):
    pass



_WORKFLOWS: dict[str, dict[str, Any]] = {
    "audit": {"apply": False, "jobs": True, "implemented": True},
    "enrich": {"apply": True, "jobs": True, "implemented": True},
    "lyrics": {"apply": True, "jobs": True, "implemented": True},
    "cover": {"apply": True, "jobs": True, "implemented": True},
    "metadata": {"apply": True, "jobs": True, "implemented": True},
    "candidates": {"apply": False, "jobs": True, "implemented": True},
    "apply_mbid": {"apply": True, "jobs": True, "implemented": True},
    "replaygain": {"apply": True, "jobs": True, "implemented": True},
    "import_music": {"apply": True, "jobs": True, "implemented": True, "dangerous": True},
    "organize": {"apply": True, "jobs": True, "implemented": True, "dangerous": True},
    "cleanup": {"apply": True, "jobs": True, "implemented": True},
    "batch": {"apply": True, "jobs": True, "implemented": True},
    "sync": {"apply": True, "jobs": True, "implemented": True},
    "review": {"apply": True, "jobs": False, "implemented": True},
    "rewrite": {"apply": True, "jobs": True, "implemented": True},
    "repair": {"apply": True, "jobs": True, "implemented": True},
    "export": {"apply": False, "jobs": True, "implemented": True},
    "playlist_export": {"apply": False, "jobs": True, "implemented": True},
    "navidrome_ratings_backup": {"apply": True, "jobs": False, "implemented": True},
    "navidrome_ratings_diff": {"apply": False, "jobs": False, "implemented": True},
    "navidrome_ratings_restore": {"apply": True, "jobs": False, "implemented": True, "dangerous": True},
    "navidrome_playlists_list": {"apply": False, "jobs": False, "implemented": True},
    "navidrome_playlists_backup": {"apply": True, "jobs": False, "implemented": True},
    "navidrome_playlists_diff": {"apply": False, "jobs": False, "implemented": True},
    "navidrome_playlists_push": {"apply": True, "jobs": False, "implemented": True},
    "navidrome_playlists_push_smart": {"apply": True, "jobs": False, "implemented": True},
}


class NoqlenForgeCore:
    """Stable internal API for Noqlen Forge/Core clients.

    This class is intentionally not an HTTP server, daemon, Android app, or CLI
    facade. It adapts stable Noqlen Forge services into silent, structured
    ``WorkflowResult`` responses for future Noqlen clients.
    """

    def __init__(self, config: dict[str, Any] | None = None, config_path: str | Path | None = None, profile: str | None = None, automated_validation: bool | None = None) -> None:
        if config is not None and config_path is not None:
            raise ConfigError("Use either config or config_path, not both")
        self.config_path = Path(config_path).expanduser() if config_path is not None else None
        self.config = _load_api_config(config=config, config_path=self.config_path)
        self.profile = profile
        self.automated_validation = automated_validation

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": "Noqlen Forge Core",
            "package": "noqlen_forge",
            "version": "1",
            "schema": "core-api/v1",
            "supports_dry_run": True,
            "supports_apply": True,
            "supports_jobs": True,
            "supports_json": True,
            "dangerous_operations": sorted(name for name, item in _WORKFLOWS.items() if item.get("dangerous")),
            "workflows": {name: {"apply": bool(item.get("apply")), "jobs": bool(item.get("jobs")), "implemented": bool(item.get("implemented")), "supports_dry_run": True} for name, item in sorted(_WORKFLOWS.items())},
        }

    def audit(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("audit", target, options, lambda opts: run_audit_service(_option(AuditOptions, path=target, config=self.config, **opts)))

    def enrich(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("enrich", target, options, lambda opts: run_enrich_service(_option(EnrichOptions, path=target, config=self.config, **opts)))

    def lyrics(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("lyrics", target, options, lambda opts: run_lyrics_service(_option(LyricsOptions, path=target, config=self.config, **opts)))

    def cover(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("cover", target, options, lambda opts: run_cover_service(_option(CoverOptions, path=target, config=self.config, **opts)))

    def metadata(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("metadata", target, options, lambda opts: run_metadata_service(_option(MetadataOptions, path=target, config=self.config, **opts)))

    def candidates(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("candidates", target, options, lambda opts: run_candidates_service(_option(CandidatesOptions, path=target, **opts)))

    def apply_mbid(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("apply_mbid", target, options, lambda opts: run_apply_mbid_service(_option(ApplyMBIDOptions, path=target, **opts)))

    def replaygain(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("replaygain", target, options, lambda opts: run_replaygain_service(_option(ReplayGainOptions, path=target, config=self.config, **opts)))

    def import_music(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("import_music", target, options, lambda opts: run_import_service(_option(ImportOptions, path=target, config=self.config, **opts)))

    def organize(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("organize", target, options, lambda opts: run_organize_service(_option(OrganizeOptions, path=target, config=self.config, **opts)))

    def cleanup(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("cleanup", target, options, lambda opts: run_cleanup_service(_option(CleanupOptions, path=target, config=self.config, **opts)))

    def batch(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("batch", target, options, lambda opts: run_batch_service(_option(BatchOptions, path=target, config=self.config, **opts)))

    def sync(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("sync", target, options, lambda opts: run_sync_service(_option(SyncOptions, path=target, config=self.config, **opts)))

    def review(self, path: str | Path | None = None, **options: Any) -> WorkflowResult:
        target = _path(path) if path is not None else None
        review_args = list(options.pop("review_args", []))
        if target is not None and not review_args:
            review_args = [str(target)]
        return self._run("review", target, options, lambda opts: run_review_service(_option(ReviewOptions, config=self.config, review_args=review_args, **opts)))

    def rewrite(self, path: str | Path, **options: Any) -> WorkflowResult:
        target = _path(path)
        return self._run("rewrite", target, options, lambda opts: run_rewrite_service(_option(RewriteOptions, path=target, config=self.config, **opts)))

    def repair(self, path: str | Path | None = None, **options: Any) -> WorkflowResult:
        target = _path(path) if path is not None else None
        return self._run("repair", target, options, lambda opts: run_repair_service(_option(RepairOptions, config=self.config, target=target, **opts)))

    def export(self, query_or_target: str | Path | None = None, **options: Any) -> WorkflowResult:
        query = None if query_or_target is None else str(query_or_target)
        return self._run("export", None, options, lambda opts: run_export_service(build_export_options(self.config, query, **opts)))

    def playlist_export(self, query: str, **options: Any) -> WorkflowResult:
        return self._run("playlist_export", None, options, lambda opts: run_playlist_export_service(_option(PlaylistExportOptions, config=self.config, name=query, **opts)))

    def navidrome_ratings_backup(self, **options: Any) -> WorkflowResult:
        return self._run("navidrome_ratings_backup", None, options, lambda opts: run_navidrome_ratings_service(_option(NavidromeRatingsOptions, config=self.config, command="backup", **opts)))

    def navidrome_ratings_diff(self, **options: Any) -> WorkflowResult:
        return self._run("navidrome_ratings_diff", None, options, lambda opts: run_navidrome_ratings_service(_option(NavidromeRatingsOptions, config=self.config, command="diff", **opts)))

    def navidrome_ratings_restore(self, **options: Any) -> WorkflowResult:
        return self._run("navidrome_ratings_restore", None, options, lambda opts: run_navidrome_ratings_service(_option(NavidromeRatingsOptions, config=self.config, command="restore", **opts)))

    def navidrome_playlists_list(self, **options: Any) -> WorkflowResult:
        return self._run("navidrome_playlists_list", None, options, lambda opts: run_navidrome_playlists_service(_option(NavidromePlaylistsOptions, config=self.config, command="list", **opts)))

    def navidrome_playlists_backup(self, **options: Any) -> WorkflowResult:
        return self._run("navidrome_playlists_backup", None, options, lambda opts: run_navidrome_playlists_service(_option(NavidromePlaylistsOptions, config=self.config, command="backup", **opts)))

    def navidrome_playlists_diff(self, query: str, **options: Any) -> WorkflowResult:
        return self._run("navidrome_playlists_diff", None, options, lambda opts: run_navidrome_playlists_service(_option(NavidromePlaylistsOptions, config=self.config, command="diff", query=query, **opts)))

    def navidrome_playlists_push(self, query: str, **options: Any) -> WorkflowResult:
        return self._run("navidrome_playlists_push", None, options, lambda opts: run_navidrome_playlists_service(_option(NavidromePlaylistsOptions, config=self.config, command="push", query=query, **opts)))

    def navidrome_playlists_push_smart(self, name: str, **options: Any) -> WorkflowResult:
        return self._run("navidrome_playlists_push_smart", None, options, lambda opts: run_navidrome_playlists_service(_option(NavidromePlaylistsOptions, config=self.config, command="push-smart", smart_name=name, **opts)))

    def create_job(self, kind: str, target: str | Path | None = None, options: dict[str, Any] | None = None, **job_options: Any) -> WorkflowResult:
        target_text = "" if target is None else str(_path(target))
        mode = "apply" if bool((options or {}).get("apply")) else "read-only"
        job_id = self._job_store().create_job(JobOptions(kind=kind, target=target_text, target_type=str(job_options.get("target_type", "path")), mode=mode, options=options or {}, resumable=bool(job_options.get("resumable", False)), cancelable=bool(job_options.get("cancelable", True))))
        return self._simple_result("jobs.create", Status.OK, summary={"job_id": job_id, "kind": kind}, job={"job_id": job_id, "resumable": bool(job_options.get("resumable", False)), "cancelable": bool(job_options.get("cancelable", True)), "progress_current": 0, "progress_total": 0, "progress_label": "created"})

    def run_job(self, job_id: str, **_: Any) -> WorkflowResult:
        store = self._job_store()
        job = store.get_job(job_id)
        if not job:
            return self._simple_result("jobs.run", Status.FAIL, errors=[f"Job not found: {job_id}"])
        kind = str(job.get("kind", ""))
        if kind not in _WORKFLOWS or not _WORKFLOWS[kind].get("implemented"):
            return self._not_implemented(kind or "job", _path(job["target"]) if job.get("target") else None, f"No Core API runner is registered for job kind: {kind}")
        store.mark_running(job_id)
        try:
            method = getattr(self, kind)
            target = job.get("target")
            options = dict(job.get("options") or {})
            result = method(target, **options) if target else method(**options)
            store.save_workflow_result(job_id, result)
            return result
        except Exception as exc:
            store.mark_failed(job_id, str(exc))
            return self._simple_result("jobs.run", Status.FAIL, errors=[str(exc)], job={"job_id": job_id})

    def get_job(self, job_id: str, **_: Any) -> WorkflowResult:
        result = self._job_store().get_result(job_id)
        if result is None:
            return self._simple_result("jobs.status", Status.FAIL, errors=[f"Job not found: {job_id}"])
        return self._simple_result("jobs.status", Status.OK, summary={"job_id": job_id, "status": result.job.get("status")}, details={"job": result.job, "steps": result.steps, "events": result.events}, job={"job_id": job_id, "progress_current": int(result.job.get("progress_current") or 0), "progress_total": int(result.job.get("progress_total") or 0), "progress_label": str(result.job.get("progress_label") or ""), "resumable": bool(result.job.get("resumable")), "cancelable": bool(result.job.get("cancelable"))})

    def cancel_job(self, job_id: str, **_: Any) -> WorkflowResult:
        try:
            canceled = self._job_store().cancel(job_id)
        except ValueError as exc:
            return self._simple_result("jobs.cancel", Status.FAIL, errors=[str(exc)], job={"job_id": job_id})
        status = Status.OK if canceled else Status.FAIL
        return self._simple_result("jobs.cancel", status, summary={"job_id": job_id, "canceled": canceled}, errors=[] if canceled else [f"Job not found: {job_id}"], job={"job_id": job_id})

    def jobs_list(self, **options: Any) -> WorkflowResult:
        return self._call_silently("jobs.list", None, lambda: run_jobs_service(_option(JobsOptions, config=self.config, command="list", **options)))

    def jobs_status(self, job_id: str, **options: Any) -> WorkflowResult:
        return self._call_silently("jobs.status", None, lambda: run_jobs_service(_option(JobsOptions, config=self.config, command="status", job_id=job_id, **options)))

    def jobs_show(self, job_id: str, **options: Any) -> WorkflowResult:
        return self._call_silently("jobs.show", None, lambda: run_jobs_service(_option(JobsOptions, config=self.config, command="show", job_id=job_id, **options)))

    def jobs_resume(self, job_id: str, **options: Any) -> WorkflowResult:
        return self._call_silently("jobs.resume", None, lambda: run_jobs_service(_option(JobsOptions, config=self.config, command="resume", job_id=job_id, **options)))

    def jobs_prune(self, **options: Any) -> WorkflowResult:
        return self._call_silently("jobs.prune", None, lambda: run_jobs_service(_option(JobsOptions, config=self.config, command="prune", **options)))

    def _run(self, workflow: str, target: Path | None, options: dict[str, Any], runner: Callable[[dict[str, Any]], WorkflowResult]) -> WorkflowResult:
        call_options = dict(options)
        as_job = bool(call_options.pop("as_job", False))
        if as_job:
            return self.create_job(workflow, target, call_options)
        return self._call_silently(workflow, target, lambda: runner(call_options))

    def _call_silently(self, workflow: str, target: Path | None, call: Callable[[], WorkflowResult]) -> WorkflowResult:
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with _automated_validation_env(self.automated_validation), redirect_stdout(stdout), redirect_stderr(stderr):
                return call()
        except (CoreAPIError, SafetyError, TypeError, ValueError, OSError) as exc:
            return self._simple_result(workflow, Status.FAIL, target=target, errors=[str(exc)])

    def _not_implemented(self, workflow: str, target: Path | None, message: str, *, options: dict[str, Any] | None = None) -> WorkflowResult:
        if options and options.get("as_job"):
            queued_options = dict(options)
            queued_options.pop("as_job", None)
            return self.create_job(workflow, target, queued_options)
        error = NotImplementedWorkflowError(message)
        return self._simple_result(workflow, Status.FAIL, target=target, errors=[str(error)], summary={"implemented": False, "error_type": error.__class__.__name__})

    def _simple_result(self, workflow: str, status: Status, *, target: Path | None = None, summary: dict[str, Any] | None = None, counts: dict[str, int | float | str] | None = None, details: dict[str, Any] | None = None, errors: list[str] | None = None, job: dict[str, Any] | None = None) -> WorkflowResult:
        started = datetime.now(timezone.utc)
        began = time.perf_counter()
        step = StepResult(1, 1, workflow, status, (errors or [""])[0] if status == Status.FAIL else "ok")
        return WorkflowResult(status, [step], workflow=workflow, command=workflow, target=target, mode="read-only", started_at=started, finished_at=datetime.now(timezone.utc), summary=summary or {}, counts=counts or {}, details=details or {}, safe_details=details or {}, errors=errors or [], elapsed_seconds=time.perf_counter() - began, job=job or {})

    def _job_store(self) -> JobStore:
        return JobStore(self.config)




def _load_api_config(*, config: dict[str, Any] | None, config_path: Path | None) -> dict[str, Any]:
    if config is not None:
        return copy.deepcopy(config)
    if config_path is None:
        return load_config()
    try:
        defaults = default_config()
        if not config_path.exists():
            return defaults
        with config_path.open("rb") as handle:
            return merge_config(defaults, tomllib.load(handle))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(str(exc)) from exc
    except OSError as exc:
        raise ConfigError(str(exc)) from exc


def _path(value: str | Path) -> Path:
    return Path(value).expanduser()


def _option(cls: type, **values: Any) -> Any:
    allowed = {field.name for field in fields(cls)}
    filtered = {key: value for key, value in values.items() if key in allowed}
    return cls(**filtered)


@contextmanager
def _automated_validation_env(enabled: bool | None):
    if enabled is None:
        yield
        return
    previous = os.environ.get(AUTOMATED_VALIDATION_ENV)
    os.environ[AUTOMATED_VALIDATION_ENV] = "1" if enabled else "0"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(AUTOMATED_VALIDATION_ENV, None)
        else:
            os.environ[AUTOMATED_VALIDATION_ENV] = previous
