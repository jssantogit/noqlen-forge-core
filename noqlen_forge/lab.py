from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import uuid
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TBPM, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, USLT
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

from .audit import audit_path, render_audit
from .audio_key import KEY_DETECTION_BACKENDS, KeyDetectionResult, KeyDetectionStatus
from . import audio_key as audio_key_module
from .cleanup import apply_cleanup, plan_cleanup, summarize_cleanup
from .config import default_config, render_config
from .cover import cover_count, cover_path, detect_embedded_cover, process_cover
from .db import SCHEMA_VERSION, apply_migrations, connect, db_explain, db_query, db_status, get_counts, init_db, record_candidate, record_field_decision, record_provider_run, render_status, scan_library, upsert_album, upsert_audio_features, upsert_file, upsert_track
from .duplicates import duplicates_path
from .export import export_data
from .importer import import_path
from .jobs import JobContext, JobOptions, JobStore, JobStatus, resume_job
from .lyrics import lyrics_count, lyrics_path, synced_lyrics_count
from . import lyrics_providers as lyrics_provider_module
from .analyze import analyze_key_path
from .lyrics_providers import LyricsProvider, LyricsResult as ProviderLyricsResult, PROVIDERS as LYRICS_PROVIDERS, ProviderAttempt, render_provider_list
from .audio import audio_files, get_tag, read_tracks
from .api import NoqlenForgeCore
from .navidrome import NavidromeConfig, RatingItem, playlists_backup as navidrome_playlists_backup, playlists_diff as navidrome_playlists_diff, playlists_export as navidrome_playlists_export, playlists_list as navidrome_playlists_list, playlists_push as navidrome_playlists_push, playlists_push_smart as navidrome_playlists_push_smart, playlists_status as navidrome_playlists_status, ratings_backup as navidrome_ratings_backup, ratings_diff as navidrome_ratings_diff, ratings_export as navidrome_ratings_export, ratings_restore as navidrome_ratings_restore, ratings_status as navidrome_ratings_status
from .organize import organize_path
from .replaygain import replaygain_path
from .reports import missing_files_report, missing_report, untracked_report
from .review import review_list, review_resolve, review_show
from .rewrite import rewrite_path
from .safety import AUTOMATED_VALIDATION_ENV
from .services import AuditOptions, CoverOptions, OrganizeOptions, PlaylistExportOptions, build_export_options, build_missing_options, run_audit_service, run_cover_service, run_export_service, run_missing_service, run_organize_service, run_playlist_export_service, workflow_result_to_dict, workflow_result_to_json
from .services.report_service import missing_report_title, render_report_result, report_scope_label
from .services.audit_service import audit_result_from_workflow
from .services.lyrics_service import LyricsOptions, run_lyrics_service
from .services.playlist_service import render_playlist_export_result
from .smart_playlists import smart_create, smart_delete, smart_export, smart_list, smart_refresh, smart_rename, smart_show
from .sync import sync_path
from .workflow import SafetyContext, Status, StepResult, WorkflowResult
from .lab_context import LabContext
from .lab_registry import LAB_SCENARIOS, select_scenarios
from .lab_runner import LabRunRecorder, LabStep, duration_suffix as _runner_duration_suffix, render_step as _runner_render_step

LAB_MARKER = ".noqlen-forge-lab"
DEFAULT_LAB_PATH = Path.home() / "MusicLab" / "noqlen-forge-lab"
DANGEROUS_PATHS = {
    Path("/"),
    Path.home(),
    Path("/mnt/sdcard/Music"),
    Path("/mnt/sdcard/Music/Biblioteca de Musicas"),
}


@dataclass(slots=True)
class LabFailure(Exception):
    step: str
    command: str
    output: str


def default_lab_path() -> Path:
    return Path(os.environ.get("NOQLEN_FORGE_LAB", str(DEFAULT_LAB_PATH))).expanduser()


def lab_command(args: argparse.Namespace) -> int:
    path = Path(getattr(args, "path", None) or default_lab_path()).expanduser()
    if args.lab_command == "create":
        code, output = lab_create(path)
    elif args.lab_command == "list":
        code, output = lab_list()
    elif args.lab_command == "reset":
        code, output = lab_reset(path)
    elif args.lab_command == "doctor":
        code, output = lab_doctor(path)
    elif args.lab_command == "run":
        code, output = lab_run(path, live_providers=bool(getattr(args, "live_providers", False)), simulate_failure=bool(getattr(args, "simulate_failure", False)), timing=bool(getattr(args, "timing", False)), quick=bool(getattr(args, "quick", False)), full=bool(getattr(args, "full", False)), scenario=getattr(args, "scenario", None), area=getattr(args, "area", None), tag=getattr(args, "tag", None))
    else:
        code, output = 1, "Unknown lab command"
    print(output)
    return code


def lab_list() -> tuple[int, str]:
    lines = ["MusicLab scenarios"]
    for scenario in LAB_SCENARIOS:
        apply = "apply-in-lab" if scenario.requires_apply else "read-only"
        quick = ", quick" if scenario.quick else ""
        slow = ", slow" if scenario.slow else ""
        tags = ",".join(scenario.tags) if scenario.tags else "none"
        lines.append(f"- {scenario.name}: area={scenario.area}, tags={tags}, cost={scenario.estimated_cost}, {apply}{quick}{slow}; validates {scenario.description}")
    return 0, "\n".join(lines)


def lab_create(path: Path | None = None) -> tuple[int, str]:
    lab = _resolve_lab_path(path)
    try:
        _guard_create_path(lab)
    except ValueError as exc:
        return 1, f"MusicLab: FAIL\n{exc}"
    if lab.exists() and (lab / LAB_MARKER).is_file():
        shutil.rmtree(lab)
    lab.mkdir(parents=True, exist_ok=True)
    (lab / LAB_MARKER).write_text("noqlen-forge lab\n", encoding="utf-8")
    _write_lab_config(lab)
    fixtures = _create_fixtures(lab)
    lines = [f"MusicLab: {lab}", "Create: OK", fixtures]
    return 0, "\n".join(line for line in lines if line)


def lab_reset(path: Path | None = None) -> tuple[int, str]:
    lab = _resolve_lab_path(path)
    if not lab.exists() and _path_is_safe(lab):
        return 0, f"MusicLab reset: {lab} (not present)"
    try:
        _guard_existing_lab(lab)
    except ValueError as exc:
        return 1, f"MusicLab: FAIL\n{exc}"
    shutil.rmtree(lab)
    return 0, f"MusicLab reset: {lab}"


def lab_doctor(path: Path | None = None) -> tuple[int, str]:
    lab = _resolve_lab_path(path)
    checks = []
    checks.append(("path safe", _path_is_safe(lab)))
    checks.append(("marker", (lab / LAB_MARKER).is_file()))
    checks.append(("config", (lab / "config.toml").is_file()))
    checks.append(("ffmpeg", shutil.which("ffmpeg") is not None))
    lines = ["MusicLab doctor"]
    code = 0
    for name, ok in checks:
        status = "OK" if ok else "WARN" if name == "ffmpeg" else "FAIL"
        if status == "FAIL":
            code = 1
        lines.append(f"- {name}: {status}")
    return code, "\n".join(lines)


def lab_run(path: Path | None = None, live_providers: bool = False, simulate_failure: bool = False, timing: bool = False, quick: bool = False, full: bool = False, scenario: str | None = None, area: str | None = None, tag: str | None = None) -> tuple[int, str]:
    try:
        scenarios = select_scenarios(quick=quick, full=full, scenario=scenario, area=area, tag=tag)
    except ValueError as exc:
        return 1, f"MusicLab: FAIL\n{exc}"
    lab = _resolve_lab_path(path)
    context = LabContext.from_root(lab)
    context.assert_inside_lab(context.incoming, context.library, context.output, context.reports, context.config_path, context.db_path)
    selected_scenarios = {item.name for item in scenarios}
    mode = "quick" if quick else "scenario" if scenario else "area" if area else "tag" if tag else "full"
    recorder = LabRunRecorder(scenarios, mode=mode, timing=timing)
    report_dir = lab / "Reports" / datetime.now().strftime("%Y%m%d-%H%M%S")
    commands: list[str] = []
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def add_step(index: int, name: str, status: str, detail: str = "") -> None:
        recorder.add_step(index, name, status, detail)

    def selected(name: str) -> bool:
        return name in selected_scenarios

    previous_automated_validation = os.environ.get(AUTOMATED_VALIDATION_ENV)
    os.environ[AUTOMATED_VALIDATION_ENV] = "1"
    try:
        _guard_create_path(lab)
        if lab.exists() and (lab / LAB_MARKER).is_file():
            shutil.rmtree(lab)
        lab.mkdir(parents=True, exist_ok=True)
        (lab / LAB_MARKER).write_text("noqlen-forge lab\n", encoding="utf-8")
        _write_lab_config(lab)
        fixture_summary = _create_fixtures(lab)
        add_step(1, "Create fixtures", "OK", fixture_summary.replace("Fixtures: ", ""))

        config = _lab_config(lab)
        db_path = init_db(config)
        add_step(2, "DB init", "OK", f"schema v{SCHEMA_VERSION}")

        library = lab / "Library"
        _run_step("db_scan_dry_run", report_dir, lambda: scan_library(config, library, apply=False), commands, stdout_chunks, stderr_chunks)
        add_step(3, "DB scan dry-run", "OK", _scan_detail(config, library, apply=False))

        _run_step("db_scan_apply", report_dir, lambda: scan_library(config, library, apply=True), commands, stdout_chunks, stderr_chunks)
        counts = _db_counts(config)
        add_step(4, "DB scan apply", "OK", f"added {counts['albums']} albums, {counts['tracks']} tracks")

        dry_after_apply = _run_step("db_scan_dry_run_after_apply", report_dir, lambda: scan_library(config, library, apply=False), commands, stdout_chunks, stderr_chunks)
        if "would add 0 albums, 0 tracks, 0 files" not in dry_after_apply:
            raise LabFailure("DB scan idempotency", "db scan dry-run after apply", dry_after_apply)
        before_repeat = _db_counts(config)
        repeat_output = _run_step("db_scan_apply_repeat", report_dir, lambda: scan_library(config, library, apply=True), commands, stdout_chunks, stderr_chunks)
        after_repeat = _db_counts(config)
        if before_repeat != after_repeat or "added 0 albums, 0 tracks, 0 files" not in repeat_output:
            raise LabFailure("DB scan idempotency", "db scan apply repeat", repeat_output)
        add_step(5, "DB scan idempotency", "OK", "0 duplicate rows")

        dirty = library / "MusicLab Artist" / "Dirty Album"
        if selected("jobs"):
            jobs_output = _jobs_check(lab, config, dirty, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(60, "Jobs", "OK", jobs_output)

        if selected("organize"):
            organize_output = _organize_check(lab, config, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(6, "Organize", "OK", organize_output)

        if selected("import"):
            import_output = _import_check(lab, config, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(7, "Import", "OK", import_output)

        clean = library / "MusicLab Artist" / "Clean Album"
        if selected("clean-album"):
            clean_before = _file_fingerprints(clean)
            _run_enrich_full(clean, apply=True, report_dir=report_dir, name="enrich_clean_album_apply", config=config, commands=commands, stdout_chunks=stdout_chunks, stderr_chunks=stderr_chunks)
            if clean_before != _file_fingerprints(clean):
                raise LabFailure("Clean album idempotency", "enrich clean album --full apply", "clean fixture was rewritten")
            clean_audit = render_audit(audit_path(clean), verbose=False)
            _assert_contains(clean_audit, ["Cover: 2/2", "Lyrics: 2/2", "BPM: 2/2", "Energy: 2/2", "Danceability: 2/2"], "Clean album")
            add_step(8, "Clean album", "OK", "rich tags preserved")

        if selected("dirty-album") or simulate_failure:
            before = render_audit(audit_path(dirty), verbose=True)
            if "bpm=0" not in before.lower() and "tbpm=0" not in before.lower():
                raise LabFailure("Audit dirty album", "audit dirty album", before)
            audit_service = run_audit_service(AuditOptions(path=dirty, config=config, verbose=True))
            audit_service_payload = workflow_result_to_dict(audit_service)
            if audit_service_payload.get("command") != "audit" or audit_service_payload.get("summary", {}).get("status") != audit_result_from_workflow(audit_service).status:
                raise LabFailure("Audit service", "audit service", workflow_result_to_json(audit_service))
            if not audit_service_payload.get("steps") or "summary" not in audit_service_payload or audit_service_payload.get("safe_details", {}).get("audit", {}).get("tracks", 0) < 1:
                raise LabFailure("Audit structured JSON", "audit service json", workflow_result_to_json(audit_service))
            add_step(9, "Audit dirty album", "OK", "bad fields detected")

            _run_enrich_cleanup(dirty, apply=False, report_dir=report_dir, name="enrich_dirty_album_dry_run")
            _run_enrich_cleanup(dirty, apply=True, report_dir=report_dir, name="enrich_dirty_album_apply")
            _run_enrich_full(dirty, apply=False, report_dir=report_dir, name="enrich_dirty_full_dry_run", config=config, commands=commands, stdout_chunks=stdout_chunks, stderr_chunks=stderr_chunks)
            _run_enrich_full(dirty, apply=True, report_dir=report_dir, name="enrich_dirty_full_apply", config=config, commands=commands, stdout_chunks=stdout_chunks, stderr_chunks=stderr_chunks)
            after = render_audit(audit_path(dirty), verbose=True)
            if simulate_failure or "Bad fields: none" not in after:
                raise LabFailure("Enrich dirty album", "enrich dirty album --full apply", after)
            add_step(10, "Enrich dirty album", "OK", "bad fields fixed")

        partial = library / "MusicLab Artist" / "Partial Metadata"
        if selected("partial-album"):
            _run_enrich_full(partial, apply=True, report_dir=report_dir, name="enrich_partial_apply", config=config, commands=commands, stdout_chunks=stdout_chunks, stderr_chunks=stderr_chunks)
            partial_audit = render_audit(audit_path(partial), verbose=False)
            _assert_contains(partial_audit, ["MB Album Id: 1/1"], "Partial album")
            add_step(11, "Partial album", "OK", "available identity kept")

        single = library / "MusicLab Singles" / "MusicLab Single.m4a"
        if selected("core-api"):
            core_api_output = _core_api_check(lab, config, dirty, single, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(59, "Core API", "OK", core_api_output)

        if selected("cover"):
            _run_step("cover_single_dry_run", report_dir, lambda: cover_path(single, apply=False, sources=["local"], save_folder_cover=False), commands, stdout_chunks, stderr_chunks)
            cover_service_dry = run_cover_service(CoverOptions(path=single, config=config, apply=False, sources=["local"], save_folder_cover=False))
            if cover_service_dry.mode != "dry-run" or cover_service_dry.details.get("exit_code") != 0:
                raise LabFailure("Cover service", "cover service dry-run", workflow_result_to_json(cover_service_dry))
            _run_step("cover_single_apply", report_dir, lambda: cover_path(single, apply=True, sources=["local"], save_folder_cover=False), commands, stdout_chunks, stderr_chunks)
            if not detect_embedded_cover(single):
                raise LabFailure("Cover", "cover single --apply", "embedded cover not found after apply")
            add_step(12, "Cover", "OK", "embedded single cover")

        if selected("lyrics"):
            _run_step("lyrics_single_dry_run", report_dir, lambda: lyrics_path(single, apply=False, sources=["local"], save_txt=False), commands, stdout_chunks, stderr_chunks)
            lyrics_service_dry = run_lyrics_service(LyricsOptions(path=single, config=config, apply=False, sources=["local"], save_txt=False))
            lyrics_service_json = workflow_result_to_json(lyrics_service_dry)
            lyrics_service_payload = json.loads(lyrics_service_json)
            if lyrics_service_dry.mode != "dry-run" or "MusicLab fallback" in lyrics_service_json or lyrics_service_payload.get("counts") is None or lyrics_service_payload.get("status") != "DRY":
                raise LabFailure("Lyrics service", "lyrics service dry-run", workflow_result_to_json(lyrics_service_dry))
            _run_step("lyrics_single_apply", report_dir, lambda: lyrics_path(single, apply=True, sources=["local"], save_txt=False), commands, stdout_chunks, stderr_chunks)
            single_tracks = read_tracks(single)
            if lyrics_count(single_tracks) != len(single_tracks):
                raise LabFailure("Lyrics", "lyrics single --apply", "embedded lyrics not found after apply")
            lyrics_repeat = _run_step("lyrics_single_apply_repeat", report_dir, lambda: lyrics_path(single, apply=True, sources=["local"], save_txt=False), commands, stdout_chunks, stderr_chunks)
            if "saved lrc 0/1" not in lyrics_repeat and "existing lrc 1/1" not in lyrics_repeat and "Existing kept: 1" not in lyrics_repeat:
                raise LabFailure("Lyrics idempotency", "lyrics single --apply repeat", lyrics_repeat)
            add_step(13, "Lyrics", "OK", "embedded synced lyrics")

        if selected("lyrics-providers"):
            provider_target = next(file for file in audio_files(library / "MusicLab Artist" / "Partial Metadata") if lyrics_count(read_tracks(file)) == 0 and not file.with_suffix(".lrc").exists())
            provider_registry_output = _lyrics_provider_registry_check(provider_target, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(14, "Lyrics providers", "OK", provider_registry_output)

        if selected("audio-key"):
            audio_key_output = _audio_key_check(single, config, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(62, "Audio key", "OK", audio_key_output)

        if selected("existing-media"):
            existing = library / "MusicLab Artist" / "Existing Cover Lyrics"
            existing_cover = _run_step("cover_existing_apply", report_dir, lambda: cover_path(existing, apply=True, sources=["local"], save_folder_cover=False), commands, stdout_chunks, stderr_chunks)
            existing_lyrics = _run_step("lyrics_existing_apply", report_dir, lambda: lyrics_path(existing, apply=True, sources=["local"], save_txt=False), commands, stdout_chunks, stderr_chunks)
            if "existing" not in (existing_cover + existing_lyrics).lower() and "skip" not in (existing_cover + existing_lyrics).lower():
                raise LabFailure("Existing cover/lyrics", "cover lyrics existing --apply", existing_cover + "\n" + existing_lyrics)
            add_step(15, "Existing cover/lyrics", "OK", "existing media skipped")

        if selected("metadata-providers"):
            ambiguous = library / "MusicLab Artist" / "Ambiguous Album"
            ambiguous_output = _ambiguous_metadata_check(ambiguous)
            add_step(16, "Ambiguous metadata", "OK", ambiguous_output)

            fallback = library / "MusicLab Artist" / "Fallback Provider"
            fallback_output = _fallback_metadata_check(fallback, apply=True)
            add_step(17, "Fallback metadata", "OK", fallback_output)

            acoustid = library / "MusicLab Artist" / "AcoustID Cases"
            acoustid_output = _acoustid_check(acoustid)
            add_step(18, "AcoustID cases", "OK", acoustid_output)

        if selected("native-independence"):
            native_output = _native_independence_check(dirty, config, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(61, "Native independence", "OK", native_output)

        if selected("replaygain"):
            replaygain_target = library / "MusicLab Artist" / "Fallback Provider"
            replaygain_output = _replaygain_check(replaygain_target, report_dir, config, commands, stdout_chunks, stderr_chunks)
            add_step(19, "ReplayGain", "SKIP" if replaygain_output.startswith("SKIP") else "OK", replaygain_output)

        needs_seeded_db = any(selected(name) for name in ("sync", "rewrite", "db-query", "review", "duplicates", "reports", "repair", "export", "navidrome", "smart-playlists"))
        if needs_seeded_db:
            _run_step("db_scan_apply_after_enrichment", report_dir, lambda: scan_library(config, library, apply=True), commands, stdout_chunks, stderr_chunks)
            _seed_db_explain_decisions(config, dirty)
            _seed_query_language_records(config, library)

        if selected("sync"):
            sync_target = library / "MusicLab Artist" / "Sync Album"
            sync_output = _sync_check(sync_target, config, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(20, "Sync", "OK", sync_output)

        if selected("rewrite"):
            rewrite_target = library / "MusicLab Artist" / "Rewrite Album"
            rewrite_output = _rewrite_check(rewrite_target, config, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(21, "Rewrite", "OK", rewrite_output)

        if selected("db-query"):
            query_output = _query_language_check(config, library, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(22, "DB query", "OK", query_output)

            explain_album = _run_step("db_explain_album", report_dir, lambda: db_explain(config, dirty), commands, stdout_chunks, stderr_chunks)
            explain_style = _run_step("db_explain_style", report_dir, lambda: db_explain(config, dirty, field="style"), commands, stdout_chunks, stderr_chunks)
            _assert_contains(explain_album, ["Last enrich", "Provider runs"], "DB explain album")
            _assert_contains(explain_style, ["provider:", "current:", "suggested:", "action:", "reason:"], "DB explain style")
            add_step(23, "DB explain", "OK", "album style decisions")

        if selected("dirty-album"):
            plans = plan_cleanup(read_tracks(dirty))
            if plans:
                raise LabFailure("Idempotency", "enrich dirty album second run", summarize_cleanup(plans, apply=False, verbose=True))
            dirty_before_second = _file_fingerprints(dirty)
            _run_enrich_full(dirty, apply=True, report_dir=report_dir, name="enrich_dirty_full_apply_repeat", config=config, commands=commands, stdout_chunks=stdout_chunks, stderr_chunks=stderr_chunks)
            if dirty_before_second != _file_fingerprints(dirty):
                raise LabFailure("Idempotency", "enrich --full apply repeat", "second enrich changed dirty files")
            add_step(24, "Idempotency", "OK", "0 unexpected writes")

        if selected("review"):
            review_output = _review_check(config, dirty, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(25, "Manual review", "OK", review_output)

        if selected("duplicates"):
            duplicates_output = _duplicates_check(lab, config, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(26, "Duplicates", "OK", duplicates_output)

        if selected("reports"):
            reports_output = _reports_check(lab, config, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(27, "Library reports", "OK", reports_output)

        if selected("repair"):
            repair_output = _repair_check(lab, config, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(28, "Repair", "OK", repair_output)

        if selected("export"):
            export_output = _export_check(lab, config, library, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(29, "Export", "OK", export_output)

        if selected("navidrome"):
            navidrome_output = _navidrome_check(lab, config, library, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(30, "Navidrome", "OK", navidrome_output)

        if selected("smart-playlists"):
            smart_playlist_output = _smart_playlist_check(lab, config, library, report_dir, commands, stdout_chunks, stderr_chunks)
            add_step(31, "Smart playlists", "OK", smart_playlist_output)

        if selected("safety"):
            _safety_self_check(lab)
            _sync_safety_check(report_dir, commands, stdout_chunks, stderr_chunks)
            _rewrite_safety_check(report_dir, commands, stdout_chunks, stderr_chunks)
            _review_safety_check(report_dir, commands, stdout_chunks, stderr_chunks)
            provider_status = _live_provider_status(live_providers)
            _assert_safe_output("\n".join(stdout_chunks))
            add_step(32, "Safety checks", "OK", provider_status)

        _write_report_files(report_dir, commands, stdout_chunks, stderr_chunks, recorder.steps, success=True, counts=_db_counts(config))
        _write_latest_success(lab, commands, recorder.steps, _db_counts(config))
        return 0, recorder.render_success()
    except LabFailure as failure:
        report_dir.mkdir(parents=True, exist_ok=True)
        log = report_dir / f"{_slug(failure.step)}.log"
        log.write_text(f"Command: {failure.command}\n\n{failure.output}\n", encoding="utf-8")
        commands.append(failure.command)
        stdout_chunks.append(failure.output)
        _write_report_files(report_dir, commands, stdout_chunks, stderr_chunks, recorder.steps, success=False, failure=failure)
        return 1, recorder.render_failure(failure.step, failure.command, str(log))
    except Exception as exc:
        report_dir.mkdir(parents=True, exist_ok=True)
        log = report_dir / "unexpected.log"
        log.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        stderr_chunks.append(f"{type(exc).__name__}: {exc}")
        _write_report_files(report_dir, commands, stdout_chunks, stderr_chunks, recorder.steps, success=False)
        return 1, f"MusicLab: FAIL\nFailure:\n- unexpected error: {exc}\n\nLogs:\n{log}"
    finally:
        if previous_automated_validation is None:
            os.environ.pop(AUTOMATED_VALIDATION_ENV, None)
        else:
            os.environ[AUTOMATED_VALIDATION_ENV] = previous_automated_validation


def _resolve_lab_path(path: Path | None) -> Path:
    return (path or default_lab_path()).expanduser().resolve(strict=False)


def _path_is_safe(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    return resolved not in {danger.resolve(strict=False) for danger in DANGEROUS_PATHS}


def _guard_create_path(path: Path) -> None:
    if not _path_is_safe(path):
        raise ValueError(f"Refusing dangerous MusicLab path: {path}")
    if path.exists() and not (path / LAB_MARKER).is_file() and any(path.iterdir()):
        raise ValueError(f"Refusing non-lab directory without {LAB_MARKER}: {path}")


def _guard_existing_lab(path: Path) -> None:
    if not _path_is_safe(path):
        raise ValueError(f"Refusing dangerous MusicLab path: {path}")
    if not path.exists():
        raise ValueError(f"MusicLab path does not exist: {path}")
    if not (path / LAB_MARKER).is_file():
        raise ValueError(f"Refusing reset without {LAB_MARKER}: {path}")


def _write_lab_config(lab: Path) -> Path:
    config = _lab_config(lab)
    path = lab / "config.toml"
    path.write_text(render_config(config, comments=False), encoding="utf-8")
    return path


def _lab_config(lab: Path) -> dict:
    config = default_config()
    config["database"]["path"] = str(lab / "library.db")
    config["library"]["root"] = str(lab / "Library")
    config["organize"]["library_path"] = str(lab / "Library")
    config["metadata_providers"]["sources"] = ["musicbrainz"]
    config["metadata_providers"]["max_active"] = 2
    config["cover"]["sources"] = ["local"]
    config["cover"]["embed"] = True
    config["cover"]["save_folder_cover"] = False
    config["lyrics"]["sources"] = ["local"]
    config["lyrics"]["embed"] = True
    config["lyrics"]["save_lrc"] = True
    config["lyrics"]["prefer_synced"] = True
    config["navidrome"]["enabled"] = True
    config["navidrome"]["base_url"] = "http://127.0.0.1:4533"
    config["navidrome"]["username"] = "musiclab"
    config["navidrome"]["password"] = "musiclab-password"
    config["enrich"]["full_includes_cover"] = False
    config["enrich"]["full_includes_lyrics"] = False
    config["enrich"]["full_includes_key"] = False
    config["enrich"]["full_includes_lastfm"] = False
    config["enrich"]["full_includes_mood"] = False
    config["enrich"]["full_includes_bpm"] = False
    config["enrich"]["full_includes_features"] = False
    config["enrich"]["full_includes_acoustid_identification"] = False
    config["enrich"]["full_includes_acoustid"] = False
    config["enrich"]["full_includes_metadata_providers"] = False
    config["rewrite"]["genre"] = {"kpop": "K-pop", "k-pop": "K-pop"}
    config["rewrite"]["style"] = {"Prog Metal": "Progressive Metal", "death metal": "Death Metal"}
    config["rewrite"]["label"] = {"Season of Mist": "Season Of Mist"}
    config["rewrite"]["mb_album_id"] = {"lab-rewrite-old-mbid": "lab-rewrite-new-mbid"}
    return config


def _create_fixtures(lab: Path) -> str:
    library = lab / "Library"
    incoming = lab / "Incoming"
    providers = lab / "Providers"
    library.mkdir(parents=True, exist_ok=True)
    incoming.mkdir(parents=True, exist_ok=True)
    providers.mkdir(parents=True, exist_ok=True)
    cover = _write_cover_fixture(lab)
    created = []
    created.extend(_album_clean(library, cover))
    created.extend(_album_dirty(library))
    created.extend(_album_partial(library))
    created.extend(_album_ambiguous(library))
    created.extend(_album_fallback(library))
    created.extend(_album_existing_cover_lyrics(library, cover))
    created.extend(_album_acoustid(library))
    created.extend(_album_sync(library))
    created.extend(_album_rewrite(library))
    created.extend(_duplicates_fixtures(library))
    created.extend(_single(library, cover))
    created.extend(_organize_fixtures(incoming))
    created.extend(_import_fixtures(incoming, cover))
    _write_provider_fixtures(providers)
    return f"Fixtures: 28 targets, {len(created)} audio files"


def _write_cover_fixture(lab: Path) -> Path:
    path = lab / "cover.png"
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x295f8a:s=600x600", "-frames:v", "1", "-y", str(path)]
        if subprocess.run(command, capture_output=True, text=True, timeout=30).returncode == 0:
            return path
    data = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAGQAAABkCAIAAAD/gAIDAAAAhElEQVR4nO3QQQ3AIADAQMD+NCCFwQVKwYScnc3a+5zkzA74Z2BnwM6AnQE7A3YG7AzYGbAzYGfAzoCdATsDdgbsDNgZsDNgZ8DOgJ0BOwN2BuwM2BmwM2BnwM6AnQE7A3YG7AzYGbAzYGfAzoCdATsDdgbsDNgZsDNgZ8DOgJ0BOwN2BuwM2BmwM2D3AgPzAaGk3y0UAAAAAElFTkSuQmCC")
    path.write_bytes(data)
    return path


def _album_clean(library: Path, cover: Path) -> list[Path]:
    target = library / "MusicLab Artist" / "Clean Album"
    files = [_make_audio(target / "01 Clean Track.m4a", 440), _make_audio(target / "02 Clean Companion.flac", 550)]
    for index, path in enumerate([p for p in files if p], 1):
        _tag_file(path, title=f"Clean Track {index}", album="Clean Album", track=index, total=2, mb=True, rich=True, cover=cover, lyrics=True)
    (target / "01 Clean Track.lrc").write_text("[00:00.00]Clean MusicLab lyric\n", encoding="utf-8")
    (target / "01 Clean Track.txt").write_text("Clean MusicLab lyric\n", encoding="utf-8")
    return [p for p in files if p]


def _album_dirty(library: Path) -> list[Path]:
    target = library / "MusicLab Artist" / "Dirty Album"
    files = [_make_audio(target / "01 Dirty Tags.mp3", 660), _make_audio(target / "02 Dirty Tags.flac", 770), _make_audio(target / "03 Dirty Apple.m4a", 775)]
    for index, path in enumerate([p for p in files if p], 1):
        _tag_file(path, title=f"Dirty Tags {index}", album="Dirty Album", track=index, total=2, mb=True, bad=True)
    return [p for p in files if p]


def _album_partial(library: Path) -> list[Path]:
    target = library / "MusicLab Artist" / "Partial Metadata"
    path = _make_audio(target / "03 Missing Metadata.flac", 880)
    if path:
        _tag_file(path, title="Missing Metadata", album="Partial Metadata", track=1, total=1, partial=True)
    return [path] if path else []


def _album_ambiguous(library: Path) -> list[Path]:
    target = library / "MusicLab Artist" / "Ambiguous Album"
    path = _make_audio(target / "01 Multiple Editions.flac", 330)
    if path:
        _tag_file(path, title="Multiple Editions", album="Ambiguous Album", track=1, total=1, mb=False)
    return [path] if path else []


def _album_fallback(library: Path) -> list[Path]:
    target = library / "MusicLab Artist" / "Fallback Provider"
    path = _make_audio(target / "01 Safe Fallback.flac", 430)
    if path:
        _tag_file(path, title="Safe Fallback", album="Fallback Provider", track=1, total=1, mb=True)
    return [path] if path else []


def _album_existing_cover_lyrics(library: Path, cover: Path) -> list[Path]:
    target = library / "MusicLab Artist" / "Existing Cover Lyrics"
    path = _make_audio(target / "01 Already Rich.m4a", 530)
    if path:
        _tag_file(path, title="Already Rich", album="Existing Cover Lyrics", track=1, total=1, mb=True, rich=True, cover=cover, lyrics=True)
        shutil.copyfile(cover, target / "cover.png")
        (target / "01 Already Rich.lrc").write_text("[00:00.00]Existing MusicLab lyric\n", encoding="utf-8")
        (target / "01 Already Rich.txt").write_text("Existing MusicLab lyric\n", encoding="utf-8")
    return [path] if path else []


def _album_acoustid(library: Path) -> list[Path]:
    target = library / "MusicLab Artist" / "AcoustID Cases"
    files = [_make_audio(target / "01 Fingerprinted.flac", 610), _make_audio(target / "02 Existing AcoustID.mp3", 620), _make_audio(target / "03 Conflict.m4a", 630)]
    for index, path in enumerate([p for p in files if p], 1):
        _tag_file(path, title=f"AcoustID Case {index}", album="AcoustID Cases", track=index, total=3, mb=True, acoustid_case=index)
    return [p for p in files if p]


def _album_sync(library: Path) -> list[Path]:
    target = library / "MusicLab Artist" / "Sync Album"
    path = _make_audio(target / "01 Sync Track.flac", 640)
    if path:
        _tag_file(path, title="Sync Track", album="Sync Album", track=1, total=1, mb=True, rich=True)
    return [path] if path else []


def _album_rewrite(library: Path) -> list[Path]:
    target = library / "MusicLab Artist" / "Rewrite Album"
    files = [_make_audio(target / "01 Rewrite One.flac", 650), _make_audio(target / "02 Rewrite Two.flac", 655)]
    for index, path in enumerate([p for p in files if p], 1):
        _tag_file(path, title=f"Rewrite Track {index}", album="Rewrite Album", track=index, total=2, mb=True)
        audio = FLAC(path)
        audio["GENRE"] = "kpop"
        audio["STYLE"] = "Prog Metal; death metal; Prog Metal"
        audio["LABEL"] = "Season of Mist"
        audio["MUSICBRAINZ_ALBUMID"] = "lab-rewrite-old-mbid"
        audio.save()
    return [p for p in files if p]


def _single(library: Path, cover: Path) -> list[Path]:
    target = library / "MusicLab Singles"
    path = _make_audio(target / "MusicLab Single.m4a", 990)
    if path:
        _tag_file(path, title="MusicLab Single", album="MusicLab Single", track=1, total=1, mb=True)
        shutil.copyfile(cover, target / "cover.png")
        (target / "MusicLab Single.lrc").write_text("[00:00.00]MusicLab line one\n[00:01.00]MusicLab line two\n", encoding="utf-8")
        (target / "MusicLab Single.txt").write_text("MusicLab line one\nMusicLab line two\n", encoding="utf-8")
    return [path] if path else []


def _duplicates_fixtures(library: Path) -> list[Path]:
    created: list[Path] = []
    mb_target = library / "MusicLab Duplicates" / "MB Track Duplicate"
    mb_files = [_make_audio(mb_target / "01 Duplicate By MB.flac", 1010), _make_audio(mb_target / "01 Duplicate By MB Copy.flac", 1012)]
    for path in [p for p in mb_files if p]:
        _tag_file(path, title="Duplicate By MB", album="MB Track Duplicate", track=1, total=1, mb=True)
        created.append(path)

    acoustid_target = library / "MusicLab Duplicates" / "AcoustID Duplicate"
    acoustid_files = [_make_audio(acoustid_target / "01 Duplicate By AcoustID.flac", 1020), _make_audio(acoustid_target / "01 Duplicate By AcoustID Copy.flac", 1022)]
    for path in [p for p in acoustid_files if p]:
        _tag_file(path, title="Duplicate By AcoustID", album="AcoustID Duplicate", track=1, total=1, acoustid_case=2)
        created.append(path)

    duration_target = library / "MusicLab Duplicates" / "Duration Duplicate"
    duration_files = [_make_audio(duration_target / "01 Duplicate By Duration.flac", 1030), _make_audio(duration_target / "01 Duplicate By Duration Copy.flac", 1032)]
    for path in [p for p in duration_files if p]:
        _tag_file(path, title="Duplicate By Duration", album="Duration Duplicate", track=1, total=1)
        created.append(path)

    album_files = [
        _make_audio(library / "MusicLab Duplicates" / "Album Duplicate A" / "01 Album Twin.flac", 1040),
        _make_audio(library / "MusicLab Duplicates" / "Album Duplicate B" / "01 Album Twin.flac", 1042),
    ]
    for index, path in enumerate([p for p in album_files if p], 1):
        _tag_file(path, title="Album Twin", album="Album Duplicate", track=1, total=1, mb=True)
        audio = FLAC(path)
        audio["MUSICBRAINZ_ALBUMID"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"noqlen-forge-lab:album-duplicate:{index}:release"))
        audio.save()
        created.append(path)
    return created


def _organize_fixtures(incoming: Path) -> list[Path]:
    created: list[Path] = []
    created.extend(_organize_album(incoming / "Organize Copy", "Organize Copy Album", 1200))
    created.extend(_organize_album(incoming / "Organize Move", "Organize Move Album", 1300))
    created.extend(_organize_album(incoming / "Organize Conflict", "Organize Conflict Album", 1400, count=1))
    missing = _make_audio(incoming / "Organize Missing" / "01 Missing Fields.flac", 1500)
    if missing:
        _tag_sparse_file(missing, title="Missing Fields", album="Missing Fields", track=1, total=1)
        created.append(missing)
    return created


def _organize_album(target: Path, album: str, frequency: int, count: int = 2) -> list[Path]:
    files = [_make_audio(target / f"{index:02d} Organize {index}.flac", frequency + index) for index in range(1, count + 1)]
    for index, path in enumerate([p for p in files if p], 1):
        _tag_file(path, title=f"Organize Track {index}", album=album, track=index, total=count, mb=True, rich=True)
    return [p for p in files if p]


def _import_fixtures(incoming: Path, cover: Path) -> list[Path]:
    created: list[Path] = []
    created.extend(_organize_album(incoming / "Import Copy", "Import Copy Album", 1600))
    created.extend(_organize_album(incoming / "Import Move", "Import Move Album", 1700))
    created.extend(_organize_album(incoming / "Import ReplayGain", "Import ReplayGain Album", 1800, count=1))
    replaygain = audio_files(incoming / "Import ReplayGain")
    if replaygain:
        _tag_file(replaygain[0], title="Import ReplayGain Track", album="Import ReplayGain Album", track=1, total=1, mb=True, rich=True)
    created.extend(_organize_album(incoming / "Import Existing Cover Lyrics", "Import Existing Cover Lyrics", 1900, count=1))
    existing = audio_files(incoming / "Import Existing Cover Lyrics")
    if existing:
        _tag_file(existing[0], title="Import Existing Track", album="Import Existing Cover Lyrics", track=1, total=1, mb=True, rich=True, cover=cover, lyrics=True)
        shutil.copyfile(cover, incoming / "Import Existing Cover Lyrics" / "cover.png")
        (incoming / "Import Existing Cover Lyrics" / "01 Organize 1.lrc").write_text("[00:00.00]Existing import lyric\n", encoding="utf-8")
    complete = _make_audio(incoming / "Import Complete" / "01 Import Complete.flac", 1950)
    if complete:
        _tag_file(complete, title="Import Complete Track", album="Import Complete Album", track=1, total=1, mb=True, rich=True)
        shutil.copyfile(cover, incoming / "Import Complete" / "cover.png")
        (incoming / "Import Complete" / "01 Import Complete.lrc").write_text("[00:00.00]Complete import lyric\n", encoding="utf-8")
        created.append(complete)
    created.extend(_organize_album(incoming / "Import Conflict", "Import Conflict Album", 2000, count=1))
    conflict = audio_files(incoming / "Import Conflict")
    if conflict:
        _tag_file(conflict[0], title="Import Conflict Track", album="Import Conflict Album", track=1, total=1, mb=True, rich=True)
    ambiguous = _make_audio(incoming / "Import Review" / "01 Review.flac", 2100)
    if ambiguous:
        _tag_sparse_file(ambiguous, title="Review", album="Import Review", track=1, total=1)
        created.append(ambiguous)
    single = _make_audio(incoming / "Import Single" / "01 Import Single.flac", 2200)
    if single:
        _tag_file(single, title="Import Single", album="Import Single", track=1, total=1, mb=True, rich=True)
        created.append(single)
    return created


def _make_audio(path: Path, frequency: int) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        if path.suffix.lower() == ".mp3":
            ID3().save(path)
            return path
        return None
    codecs = {".mp3": "libmp3lame", ".m4a": "aac", ".flac": "flac"}
    codec = codecs.get(path.suffix.lower())
    if not codec:
        return None
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=0.35", "-y", "-c:a", codec, str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None
    return path


def _make_key_sequence_audio(path: Path, frequencies: list[float]) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    inputs: list[str] = []
    filter_inputs: list[str] = []
    for index, frequency in enumerate(frequencies):
        inputs.extend(["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=0.45"])
        filter_inputs.append(f"[{index}:a]")
    filter_complex = "".join(filter_inputs) + f"concat=n={len(frequencies)}:v=0:a=1[a]"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", filter_complex, "-map", "[a]", "-y", "-c:a", "flac", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None
    return path


def _make_silence_audio(path: Path) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=11025:cl=mono", "-t", "1", "-y", "-c:a", "flac", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None
    return path


def _tag_file(path: Path, title: str, album: str, track: int, total: int, mb: bool = False, partial: bool = False, rich: bool = False, bad: bool = False, cover: Path | None = None, lyrics: bool = False, acoustid_case: int = 0) -> None:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        tags = ID3(path)
        tags.add(TIT2(encoding=3, text=[title]))
        tags.add(TALB(encoding=3, text=[album]))
        tags.add(TPE1(encoding=3, text=["MusicLab Artist"]))
        tags.add(TPE2(encoding=3, text=["MusicLab Artist"]))
        tags.add(TRCK(encoding=3, text=[f"{track}/{total}"]))
        tags.add(TPOS(encoding=3, text=["1/1"]))
        tags.add(TDRC(encoding=3, text=["2026"]))
        tags.add(TCON(encoding=3, text=["MusicLab Genre"]))
        fields = _fields(mb, partial, rich, bad, album=album, title=title, acoustid_case=acoustid_case)
        _id3_txxx(tags, fields)
        if bad:
            tags.add(TBPM(encoding=3, text=["0"]))
        if cover:
            tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=cover.read_bytes()))
        if lyrics:
            tags.add(USLT(encoding=3, lang="eng", desc="", text="MusicLab lyric line"))
        tags.save(path)
        return
    if suffix == ".flac":
        audio = FLAC(path)
        audio["TITLE"] = title
        audio["ALBUM"] = album
        audio["ARTIST"] = "MusicLab Artist"
        audio["ALBUMARTIST"] = "MusicLab Artist"
        audio["TRACKNUMBER"] = str(track)
        audio["TRACKTOTAL"] = str(total)
        audio["DISCNUMBER"] = "1"
        audio["DISCTOTAL"] = "1"
        audio["DATE"] = "2026"
        audio["GENRE"] = "MusicLab Genre"
        for key, value in _fields(mb, partial, rich, bad, album=album, title=title, acoustid_case=acoustid_case).items():
            audio[_vorbis_key(key)] = value
        if cover:
            picture = Picture()
            picture.type = 3
            picture.mime = "image/png"
            picture.desc = "Cover"
            picture.data = cover.read_bytes()
            audio.clear_pictures()
            audio.add_picture(picture)
        if lyrics:
            audio["LYRICS"] = "MusicLab lyric line"
        audio.save()
        return
    audio = MP4(path)
    audio["\xa9nam"] = [title]
    audio["\xa9alb"] = [album]
    audio["\xa9ART"] = ["MusicLab Artist"]
    audio["aART"] = ["MusicLab Artist"]
    audio["trkn"] = [(track, total)]
    audio["disk"] = [(1, 1)]
    audio["\xa9day"] = ["2026"]
    audio["\xa9gen"] = ["MusicLab Genre"]
    for key, value in _fields(mb, partial, rich, bad, album=album, title=title, acoustid_case=acoustid_case).items():
        audio[f"----:com.apple.iTunes:{key}"] = [MP4FreeForm(value.encode("utf-8"))]
    if bad:
        audio["tmpo"] = [0]
        audio["----:com.apple.iTunes:LABEL"] = [MP4FreeForm(b"")]
        audio["----:com.apple.iTunes:STYLE"] = [MP4FreeForm(b"")]
    if cover:
        audio["covr"] = [MP4Cover(cover.read_bytes(), imageformat=MP4Cover.FORMAT_PNG)]
    if lyrics:
        audio["\xa9lyr"] = ["MusicLab lyric line"]
    audio.save()


def _tag_sparse_file(path: Path, title: str, album: str, track: int, total: int) -> None:
    if path.suffix.lower() == ".flac":
        audio = FLAC(path)
        audio["TITLE"] = title
        audio["ALBUM"] = album
        audio["ARTIST"] = "MusicLab Sparse Artist"
        audio["TRACKNUMBER"] = str(track)
        audio["TRACKTOTAL"] = str(total)
        audio.save()


def _fields(mb: bool, partial: bool, rich: bool, bad: bool, album: str = "", title: str = "", acoustid_case: int = 0) -> dict[str, str]:
    fields: dict[str, str] = {}
    if mb or partial:
        fields["MusicBrainz Album Id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"noqlen-forge-lab:{album}:release"))
    if mb:
        fields["MusicBrainz Track Id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"noqlen-forge-lab:{album}:{title}:recording"))
        fields["MusicBrainz Release Group Id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"noqlen-forge-lab:{album}:release-group"))
    if rich:
        fields.update({"Label": "MusicLab Records", "STYLE": "Fixture", "ORIGINALDATE": "2026-01-01", "BPM": "120", "KEY": "C Major", "ENERGY": "70", "DANCEABILITY": "60", "LASTFM_TAGS": "fixture", "MOOD": "Focused"})
    if bad:
        fields.update({"BPM": "0", "ORIGINALDATE": "0000", "Label": "", "STYLE": ""})
    if acoustid_case == 1:
        fields["AcoustID Fingerprint"] = "LAB-FINGERPRINT-EXISTING-TRUNCATED"
    elif acoustid_case == 2:
        fields["AcoustID Id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"noqlen-forge-lab:{album}:{title}:acoustid"))
    elif acoustid_case == 3:
        fields["AcoustID Id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"noqlen-forge-lab:{album}:conflict:acoustid"))
        fields["MusicBrainz Track Id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"noqlen-forge-lab:{album}:conflicting-recording"))
    return fields


def _id3_txxx(tags: ID3, fields: dict[str, str]) -> None:
    for key, value in fields.items():
        tags.add(TXXX(encoding=3, desc=key, text=[value]))


def _vorbis_key(key: str) -> str:
    return {
        "MusicBrainz Album Id": "MUSICBRAINZ_ALBUMID",
        "MusicBrainz Track Id": "MUSICBRAINZ_TRACKID",
        "MusicBrainz Release Group Id": "MUSICBRAINZ_RELEASEGROUPID",
        "Original Date": "ORIGINALDATE",
        "Label": "LABEL",
        "AcoustID Id": "ACOUSTID_ID",
        "AcoustID Fingerprint": "ACOUSTID_FINGERPRINT",
    }.get(key, key.upper().replace(" ", ""))


def _write_provider_fixtures(path: Path) -> None:
    (path / "discogs_ambiguous.json").write_text('{"status":"ambiguous","candidates":[{"catalog_number":"LAB-1"},{"catalog_number":"LAB-2"}]}\n', encoding="utf-8")
    (path / "itunes_fallback.json").write_text('{"status":"ok","genre":"Electronic","originaldate":"2026-01-01"}\n', encoding="utf-8")
    (path / "deezer_fallback.json").write_text('{"status":"ok","genre":"Electronic"}\n', encoding="utf-8")
    (path / "lrclib_track.json").write_text('{"syncedLyrics":"[00:00.00]MusicLab line"}\n', encoding="utf-8")


class _LabLyricsProvider(LyricsProvider):
    def __init__(self, name: str, text: str | None, synced: bool = False, confidence: str = "high", instrumental: bool = False) -> None:
        super().__init__(name=name)
        self.text = text
        self.synced = synced
        self.confidence = confidence
        self.instrumental = instrumental

    def fetch(self, track, prefer_synced: bool = True, debug: bool = False) -> ProviderAttempt:
        if self.text is None:
            return ProviderAttempt(self.name, "WARN", "not found")
        return ProviderAttempt(self.name, "OK", "fake lyrics", result=ProviderLyricsResult(self.text, self.synced, self.name, self.name, self.confidence, match_reason="MusicLab fake provider", instrumental=self.instrumental))


class _LabHttpResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int) -> bytes:
        return self.payload


def _lyrics_provider_registry_check(single: Path, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    original = dict(LYRICS_PROVIDERS)
    original_urlopen = lyrics_provider_module.urllib.request.urlopen
    try:
        LYRICS_PROVIDERS.update(
            {
                "lab_not_found": _LabLyricsProvider("lab_not_found", None),
                "lab_synced": _LabLyricsProvider("lab_synced", "[00:00.00]MusicLab fallback synced", synced=True),
                "lab_unsynced": _LabLyricsProvider("lab_unsynced", "MusicLab fallback unsynced", synced=False),
                "lab_low": _LabLyricsProvider("lab_low", "MusicLab low confidence", synced=False, confidence="low"),
                "lab_conflict_a": _LabLyricsProvider("lab_conflict_a", "MusicLab alpha lyrics", synced=False),
                "lab_conflict_b": _LabLyricsProvider("lab_conflict_b", "Completely different beta lyrics", synced=False),
                "lab_synced_equiv": _LabLyricsProvider("lab_synced_equiv", "[00:00.00]MusicLab equivalent lyric", synced=True),
                "lab_unsynced_equiv": _LabLyricsProvider("lab_unsynced_equiv", "MusicLab equivalent lyric", synced=False),
                "lab_synced_weak": _LabLyricsProvider("lab_synced_weak", "[00:00.00]MusicLab weak synced", synced=True, confidence="low"),
                "lab_unsynced_strong": _LabLyricsProvider("lab_unsynced_strong", "MusicLab strong unsynced", synced=False, confidence="high"),
                "lab_invalid_lrc": _LabLyricsProvider("lab_invalid_lrc", "[bad]MusicLab invalid lrc", synced=True),
                "lab_placeholder": _LabLyricsProvider("lab_placeholder", "lyrics not found", synced=False),
                "lab_instrumental": _LabLyricsProvider("lab_instrumental", "Instrumental", synced=False, instrumental=True),
            }
        )
        dry = _run_step("lyrics_provider_dry_run", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_not_found", "lab_synced"], save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "Mode: DRY-RUN" not in dry or "Provider: lab_synced" not in dry:
            raise LabFailure("Lyrics provider dry-run", "lyrics fake dry-run", dry)
        low = _run_step("lyrics_provider_low_confidence", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_low"], min_confidence="medium", save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "confidence low below minimum medium" not in low or "Status: WARN" not in low:
            raise LabFailure("Lyrics provider low confidence", "lyrics fake low", low)
        conflict = _run_step("lyrics_provider_conflict", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_conflict_a", "lab_conflict_b"], save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "Status: REVIEW" not in conflict or "Conflicts:" not in conflict:
            raise LabFailure("Lyrics provider conflict", "lyrics fake conflict", conflict)
        unsynced = _run_step("lyrics_provider_unsynced", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_unsynced"], prefer_synced=True, allow_unsynced=True, save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "unsynced" not in unsynced or "Provider: lab_unsynced" not in unsynced:
            raise LabFailure("Lyrics provider unsynced", "lyrics fake unsynced", unsynced)
        prefer_synced = _run_step("lyrics_selection_prefer_synced", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_unsynced_equiv", "lab_synced_equiv"], prefer_synced=True, allow_unsynced=True, save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "Provider: lab_synced_equiv" not in prefer_synced or "Selected synced: 1" not in prefer_synced:
            raise LabFailure("Lyrics synced selection", "lyrics prefer synced", prefer_synced)
        strong_unsynced = _run_step("lyrics_selection_strong_unsynced", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_synced_weak", "lab_unsynced_strong"], prefer_synced=True, allow_unsynced=True, min_confidence="low", save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "Provider: lab_unsynced_strong" not in strong_unsynced or "Selected unsynced: 1" not in strong_unsynced:
            raise LabFailure("Lyrics unsynced high selection", "lyrics strong unsynced", strong_unsynced)
        invalid_lrc = _run_step("lyrics_selection_invalid_lrc", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_invalid_lrc"], save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "invalid LRC timestamps" not in invalid_lrc or "Status: WARN" not in invalid_lrc:
            raise LabFailure("Lyrics invalid LRC", "lyrics invalid lrc", invalid_lrc)
        placeholder = _run_step("lyrics_selection_placeholder", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_placeholder"], save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "placeholder" not in placeholder or "Status: WARN" not in placeholder:
            raise LabFailure("Lyrics placeholder", "lyrics placeholder", placeholder)
        instrumental_skip = _run_step("lyrics_selection_instrumental_skip", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_instrumental"], save_lrc=False), commands, stdout_chunks, stderr_chunks)
        instrumental_allow = _run_step("lyrics_selection_instrumental_allow", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_instrumental"], allow_instrumental=True, save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "instrumental lyrics disabled" not in instrumental_skip or "Provider: lab_instrumental" not in instrumental_allow:
            raise LabFailure("Lyrics instrumental", "lyrics instrumental", instrumental_skip + "\n" + instrumental_allow)
        synced = _run_step("lyrics_provider_synced_apply", report_dir, lambda: lyrics_path(single, apply=True, force=True, sources=["lab_not_found", "lab_synced"], save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "Provider: lab_synced" not in synced or "Status: OK" not in synced:
            raise LabFailure("Lyrics provider fallback", "lyrics fake fallback", synced)
        repeat = _run_step("lyrics_provider_repeat", report_dir, lambda: lyrics_path(single, apply=True, force=False, sources=["lab_synced"], save_lrc=False), commands, stdout_chunks, stderr_chunks)
        if "skipped" not in repeat.lower() and "existing" not in repeat.lower():
            raise LabFailure("Lyrics provider idempotency", "lyrics fake repeat", repeat)
        sidecar = _run_step("lyrics_provider_sidecar_lrc", report_dir, lambda: lyrics_path(single, apply=True, force=True, sources=["lab_synced"], save_lrc=True), commands, stdout_chunks, stderr_chunks)
        if not single.with_suffix(".lrc").is_file() or "saved lrc" not in sidecar:
            raise LabFailure("Lyrics provider sidecar", "lyrics fake sidecar", sidecar)
        provider_list = _run_step("lyrics_provider_list", report_dir, lambda: (0, render_provider_list()), commands, stdout_chunks, stderr_chunks)
        if "custom_http: online, disabled, requires base_url" not in provider_list or "lrclib: online, enabled" not in provider_list:
            raise LabFailure("Lyrics provider list", "lyrics providers", provider_list)
        custom_config = {"lyrics": {"review_on_existing_mismatch": False, "online": {"rate_limit_seconds": 0}, "provider_settings": {"custom_http": {"enabled": True, "base_url": "http://lyrics.test", "api_key_env": "NOQLEN_FORGE_LYRICS_API_KEY"}}}}
        os.environ["NOQLEN_FORGE_LYRICS_API_KEY"] = "musiclab-secret-token"
        lyrics_provider_module.urllib.request.urlopen = lambda request, timeout=20: _LabHttpResponse(b'{"results":[{"artist":"MusicLab Artist","title":"Missing Metadata","album":"Partial Metadata","synced":"[00:00.00]MusicLab custom synced"}]}')
        custom_dry = _run_step("lyrics_provider_custom_http_dry", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["custom_http"], save_lrc=False, config=custom_config), commands, stdout_chunks, stderr_chunks)
        if "Mode: DRY-RUN" not in custom_dry or "Provider: custom_http" not in custom_dry:
            raise LabFailure("Lyrics custom_http dry-run", "lyrics custom_http dry-run", custom_dry)
        custom_apply = _run_step("lyrics_provider_custom_http_apply", report_dir, lambda: lyrics_path(single, apply=True, force=True, sources=["custom_http"], save_lrc=False, config=custom_config), commands, stdout_chunks, stderr_chunks)
        if "Provider: custom_http" not in custom_apply or not lyrics_count(read_tracks(single)):
            raise LabFailure("Lyrics custom_http apply", "lyrics custom_http apply", custom_apply)
        lyrics_provider_module.urllib.request.urlopen = lambda request, timeout=20: _LabHttpResponse(b'{"bad": []}')
        malformed = _run_step("lyrics_provider_custom_http_malformed", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["custom_http"], save_lrc=False, config=custom_config), commands, stdout_chunks, stderr_chunks)
        if "expected results list" not in malformed or "Providers              WARN" not in malformed:
            raise LabFailure("Lyrics custom_http malformed", "lyrics custom_http malformed", malformed)
        lyrics_provider_module.urllib.request.urlopen = lambda request, timeout=20: _LabHttpResponse(b'{"results":[{"artist":"MusicLab Artist","title":"Missing Metadata","album":"Partial Metadata","plain":"Completely different custom lyrics"}]}')
        fallback = _run_step("lyrics_provider_custom_http_fallback", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_not_found", "custom_http"], save_lrc=False, config=custom_config), commands, stdout_chunks, stderr_chunks)
        if "Provider: custom_http" not in fallback:
            raise LabFailure("Lyrics custom_http fallback", "lyrics custom_http fallback", fallback)
        conflict_custom = _run_step("lyrics_provider_custom_http_conflict", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["lab_conflict_a", "custom_http"], save_lrc=False, config=custom_config), commands, stdout_chunks, stderr_chunks)
        if "Status: REVIEW" not in conflict_custom:
            raise LabFailure("Lyrics custom_http conflict", "lyrics custom_http conflict", conflict_custom)
        lyrics_provider_module.urllib.request.urlopen = lambda request, timeout=20: _LabHttpResponse(b'{"results":[{"artist":"Other Artist","title":"Other Song","plain":"Low confidence lyrics"}]}')
        custom_low = _run_step("lyrics_provider_custom_http_low", report_dir, lambda: lyrics_path(single, apply=False, force=True, sources=["custom_http"], min_confidence="medium", save_lrc=False, config=custom_config), commands, stdout_chunks, stderr_chunks)
        if "confidence low below minimum medium" not in custom_low:
            raise LabFailure("Lyrics custom_http low", "lyrics custom_http low", custom_low)
        combined = "\n".join([dry, synced, repeat, low, conflict, unsynced, prefer_synced, strong_unsynced, invalid_lrc, placeholder, instrumental_skip, instrumental_allow, sidecar, provider_list, custom_dry, custom_apply, malformed, fallback, conflict_custom, custom_low])
        if "MusicLab fallback synced" in combined or "Completely different beta lyrics" in combined:
            raise LabFailure("Lyrics provider safe output", "lyrics fake safe output", combined)
        if "musiclab-secret-token" in combined or "MusicLab custom synced" in combined or "Low confidence lyrics" in combined:
            raise LabFailure("Lyrics provider secrets/output", "lyrics safe custom output", combined)
        return "fake registry fallback/conflict/idempotency covered"
    finally:
        lyrics_provider_module.urllib.request.urlopen = original_urlopen
        os.environ.pop("NOQLEN_FORGE_LYRICS_API_KEY", None)
        LYRICS_PROVIDERS.clear()
        LYRICS_PROVIDERS.update(original)


class _LabKeyBackend:
    name = "musiclab"

    def available(self, config: dict | None = None) -> bool:
        return True

    def analyze(self, path: Path, config: dict | None = None) -> KeyDetectionResult:
        return KeyDetectionResult(KeyDetectionStatus.OK, raw_key="C", scale="major", key="C Major", confidence="high", backend=self.name, reason="MusicLab fake key backend")


def _audio_key_check(single: Path, config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    from .cli import enrich

    disabled_config = {**config, "audio": {**config.get("audio", {}), "key_detection": {"enabled": True, "backend": "disabled", "backends": ["portable_basic"], "min_confidence": "medium", "fail_on_error": False}}}
    disabled = _run_step("audio_key_disabled", report_dir, lambda: analyze_key_path(single, apply=False, config=disabled_config), commands, stdout_chunks, stderr_chunks)
    _assert_contains(disabled, ["KEY: skipped", "key detection disabled"], "Audio key disabled")

    invalid_code, invalid_output = analyze_key_path(single, apply=False, config={**config, "audio": {**config.get("audio", {}), "key_detection": {"enabled": True, "backend": "essentia", "backends": ["essentia"], "min_confidence": "low", "fail_on_error": False}}})
    (report_dir / "audio_key_invalid_removed_backend.log").write_text(invalid_output + "\n", encoding="utf-8")
    if invalid_code == 0 or "Essentia backend was removed. Use portable_basic or auto." not in invalid_output:
        raise LabFailure("Audio key invalid backend", "removed backend must fail clearly", invalid_output)

    portable_dir = single.parent / "Portable Key"
    portable = _make_key_sequence_audio(portable_dir / "01 Portable C Major.flac", [261.63, 329.63, 392.0, 261.63])
    silence = _make_silence_audio(portable_dir / "02 Portable Silence.flac")
    portable_config = {**config, "audio": {**config.get("audio", {}), "key_detection": {"enabled": True, "backend": "portable_basic", "backends": ["portable_basic"], "min_confidence": "medium", "write_low_confidence": False, "fail_on_error": False, "portable_basic": {"sample_rate": 11025, "max_seconds": 5, "segment_seconds": 10, "segments": 1, "timeout_seconds": 10}}}}
    auto_config = {**config, "audio": {**config.get("audio", {}), "key_detection": {**portable_config["audio"]["key_detection"], "backend": "auto"}}}
    if portable:
        _tag_file(portable, title="Portable C Major", album="Portable Key", track=1, total=2, mb=True)
        portable_dry = _run_step("audio_key_portable_basic_dry", report_dir, lambda: analyze_key_path(portable, apply=False, config=portable_config, backend="portable_basic"), commands, stdout_chunks, stderr_chunks)
        _assert_contains(portable_dry, ["Backend: portable_basic", "final=C Major", "confidence=medium", "action=would write"], "Audio key portable dry-run")
        if get_tag(read_tracks(portable)[0], "key"):
            raise LabFailure("Audio key portable dry-run", "portable dry-run must not write KEY", portable_dry)
        portable_apply = _run_step("audio_key_portable_basic_apply", report_dir, lambda: analyze_key_path(portable, apply=True, config=portable_config, backend="portable_basic"), commands, stdout_chunks, stderr_chunks)
        _assert_contains(portable_apply, ["Backend: portable_basic", "final=C Major", "action=wrote"], "Audio key portable apply")
        if "C Major" not in get_tag(read_tracks(portable)[0], "key"):
            raise LabFailure("Audio key portable apply", "portable apply writes KEY inside MusicLab", portable_apply)
        auto_dry = _run_step("audio_key_auto_uses_portable", report_dir, lambda: analyze_key_path(portable, apply=False, force=True, config=auto_config, backend="auto"), commands, stdout_chunks, stderr_chunks)
        _assert_contains(auto_dry, ["Backend: portable_basic", "final=C Major", "action=would write"], "Audio key auto portable")
    if silence:
        _tag_file(silence, title="Portable Silence", album="Portable Key", track=2, total=2, mb=True)
        low = _run_step("audio_key_portable_low_confidence", report_dir, lambda: analyze_key_path(silence, apply=True, config=portable_config, backend="portable_basic"), commands, stdout_chunks, stderr_chunks)
        _assert_contains(low, ["silent or too quiet"], "Audio key portable low confidence")
        if get_tag(read_tracks(silence)[0], "key"):
            raise LabFailure("Audio key portable low confidence", "low confidence portable key must not be written", low)

    original_which = audio_key_module.shutil.which
    try:
        audio_key_module.shutil.which = lambda command: None if command == "ffmpeg" else original_which(command)
        no_ffmpeg = _run_step("audio_key_portable_no_ffmpeg", report_dir, lambda: analyze_key_path(single, apply=False, config=portable_config, backend="portable_basic"), commands, stdout_chunks, stderr_chunks)
        _assert_contains(no_ffmpeg, ["KEY: skipped", "ffmpeg is not available for portable key detection"], "Audio key portable no ffmpeg")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            enrich_code = enrich(single, apply=False, force=False, analyze_key=True, config=portable_config)
        enrich_output = stdout.getvalue()
        (report_dir / "audio_key_enrich_no_ffmpeg.log").write_text(enrich_output + "\n", encoding="utf-8")
        if enrich_code != 0 or "SKIP" not in enrich_output or "optional backend unavailable" not in enrich_output:
            raise LabFailure("Audio key enrich no ffmpeg", "enrich must skip missing portable decoder", enrich_output)
    finally:
        audio_key_module.shutil.which = original_which

    KEY_DETECTION_BACKENDS.register(_LabKeyBackend())
    mock_config = {**config, "audio": {**config.get("audio", {}), "key_detection": {"enabled": True, "backend": "musiclab", "backends": ["musiclab"], "min_confidence": "low", "fail_on_error": False}}}
    mock = _run_step("audio_key_mock_success", report_dir, lambda: analyze_key_path(single, apply=False, config=mock_config), commands, stdout_chunks, stderr_chunks)
    _assert_contains(mock, ["Backend: musiclab", "final=C Major", "action=would write"], "Audio key mock success")
    return "disabled/auto/portable/invalid-removed-backend/mock-success covered natively"


def _organize_check(lab: Path, config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    incoming = lab / "Incoming"
    library = lab / "Library"
    copy_source = incoming / "Organize Copy"
    move_source = incoming / "Organize Move"
    conflict_source = incoming / "Organize Conflict"
    missing_source = incoming / "Organize Missing"

    copy_dry = _run_step("organize_copy_dry_run", report_dir, lambda: _organize_tuple(copy_source, config, apply=False, mode="copy", library=library), commands, stdout_chunks, stderr_chunks)
    if "Mode: DRY-RUN" not in copy_dry or list((library / "MusicLab Genre" / "MusicLab Artist" / "Organize Copy Album").glob("*.flac")):
        raise LabFailure("Organize copy dry-run", "organize copy dry-run", copy_dry)
    organize_service = run_organize_service(OrganizeOptions(copy_source, config, apply=False, mode="copy", library=library))
    if workflow_result_to_dict(organize_service).get("command") != "organize" or organize_service.mode != "dry-run":
        raise LabFailure("Organize service", "organize service dry-run", workflow_result_to_json(organize_service))
    copy_apply = _run_step("organize_copy_apply", report_dir, lambda: _organize_tuple(copy_source, config, apply=True, mode="copy", library=library), commands, stdout_chunks, stderr_chunks)
    copy_dest = library / "MusicLab Genre" / "MusicLab Artist" / "Organize Copy Album"
    if "Status: OK" not in copy_apply or len(list(copy_dest.glob("*.flac"))) != 2 or len(audio_files(copy_source)) != 2:
        raise LabFailure("Organize copy", "organize copy --apply", copy_apply)
    _run_step("organize_copy_db_scan", report_dir, lambda: scan_library(config, copy_dest, apply=True), commands, stdout_chunks, stderr_chunks)
    query = _run_step("organize_copy_db_query", report_dir, lambda: db_query(config, "Organize Copy Album", target="tracks"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(query, ["Organize Track 1", "Organize Track 2"], "Organize copy DB query")

    move_apply = _run_step("organize_move_apply", report_dir, lambda: _organize_tuple(move_source, config, apply=True, mode="move", library=library), commands, stdout_chunks, stderr_chunks)
    move_dest = library / "MusicLab Genre" / "MusicLab Artist" / "Organize Move Album"
    if "Status: OK" not in move_apply or len(list(move_dest.glob("*.flac"))) != 2 or audio_files(move_source):
        raise LabFailure("Organize move", "organize move --apply", move_apply)

    conflict_dest = library / "MusicLab Genre" / "MusicLab Artist" / "Singles" / "Organize Track 1.flac"
    conflict_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(audio_files(conflict_source)[0], conflict_dest)
    conflict_fingerprint = _file_fingerprints(conflict_dest)
    conflict_review = _organize_tuple(conflict_source, config, apply=True, mode="copy", library=library)[1]
    if "Status: REVIEW" not in conflict_review or _file_fingerprints(conflict_dest) != conflict_fingerprint:
        raise LabFailure("Organize conflict review", "organize conflict review", conflict_review)
    conflict_rename = _run_step("organize_conflict_rename", report_dir, lambda: _organize_tuple(conflict_source, config, apply=True, mode="copy", library=library, conflict_policy="rename"), commands, stdout_chunks, stderr_chunks)
    if not (conflict_dest.parent / "Organize Track 1 (1).flac").exists() or _file_fingerprints(conflict_dest) != conflict_fingerprint:
        raise LabFailure("Organize conflict rename", "organize conflict rename", conflict_rename)

    missing_apply = _run_step("organize_missing_apply", report_dir, lambda: _organize_tuple(missing_source, config, apply=True, mode="copy", library=library), commands, stdout_chunks, stderr_chunks)
    if "Unknown" not in missing_apply or not list((library / "Unknown" / "MusicLab Sparse Artist" / "Singles").glob("*.flac")):
        raise LabFailure("Organize missing fields", "organize missing --apply", missing_apply)

    unsafe = _organize_tuple(copy_source, config, apply=True, mode="copy", library=lab.parent / "UnsafeLibrary")[1]
    if "Refusing automated --apply outside MusicLab" not in unsafe:
        raise LabFailure("Organize safety", "organize unsafe --apply", unsafe)

    before = _db_counts(config)
    repeat = _organize_tuple(copy_source, config, apply=True, mode="copy", library=library)[1]
    after = _db_counts(config)
    if before != after or "Status: REVIEW" not in repeat:
        raise LabFailure("Organize idempotency", "organize copy repeat", repeat)
    return "copy/move/conflict/missing/safety/idempotency"


def _organize_tuple(path: Path, config: dict, apply: bool, mode: str, library: Path, conflict_policy: str = "review") -> tuple[int, str]:
    result = organize_path(path, config=config, apply=apply, mode=mode, library=library, conflict_policy=conflict_policy)
    return result.code, result.output


def _import_check(lab: Path, config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    incoming = lab / "Incoming"
    library = lab / "Library"
    copy_source = incoming / "Import Copy"
    move_source = incoming / "Import Move"
    review_source = incoming / "Import Review"
    replaygain_source = incoming / "Import ReplayGain"
    complete_source = incoming / "Import Complete"
    single_source = incoming / "Import Single"
    existing_source = incoming / "Import Existing Cover Lyrics"
    conflict_source = incoming / "Import Conflict"

    copy_dry = _run_step("import_copy_dry_run", report_dir, lambda: _import_tuple(copy_source, config, apply=False, mode="copy", library=library, skip_enrich=True), commands, stdout_chunks, stderr_chunks)
    copy_dest = library / "MusicLab Genre" / "MusicLab Artist" / "Import Copy Album"
    if "Mode: DRY-RUN" not in copy_dry or list(copy_dest.glob("*.flac")):
        raise LabFailure("Import copy dry-run", "import copy dry-run", copy_dry)
    copy_apply = _run_step("import_copy_apply", report_dir, lambda: _import_tuple(copy_source, config, apply=True, mode="copy", library=library, skip_enrich=True), commands, stdout_chunks, stderr_chunks)
    if "Status: OK" not in copy_apply or len(list(copy_dest.glob("*.flac"))) != 2 or len(audio_files(copy_source)) != 2:
        raise LabFailure("Import copy", "import copy --apply", copy_apply)
    query = _run_step("import_copy_db_query", report_dir, lambda: db_query(config, "Import Copy Album", target="tracks"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(query, ["Organize Track 1", "Organize Track 2"], "Import copy DB query")

    move_apply = _run_step("import_move_apply", report_dir, lambda: _import_tuple(move_source, config, apply=True, mode="move", library=library, skip_enrich=True), commands, stdout_chunks, stderr_chunks)
    move_dest = library / "MusicLab Genre" / "MusicLab Artist" / "Import Move Album"
    if "Status: OK" not in move_apply or len(list(move_dest.glob("*.flac"))) != 2 or audio_files(move_source):
        raise LabFailure("Import move", "import move --apply", move_apply)

    review = _import_tuple(review_source, config, apply=True, mode="copy", library=library, skip_enrich=True)[1]
    _write_expected_report(report_dir, "import_review_apply", review, commands, stdout_chunks, stderr_chunks)
    review_dest = library / "Unknown" / "MusicLab Sparse Artist" / "Singles" / "Review.flac"
    if "Status: REVIEW" not in review or review_dest.exists():
        raise LabFailure("Import review", "import review --apply", review)
    allow_review = _run_step("import_review_allow_apply", report_dir, lambda: _import_tuple(review_source, config, apply=True, mode="copy", library=library, skip_enrich=True, allow_review=True), commands, stdout_chunks, stderr_chunks)
    if "Status: OK" not in allow_review or not review_dest.exists():
        raise LabFailure("Import allow review", "import --allow-review --apply", allow_review)

    replaygain = _run_step("import_replaygain_apply", report_dir, lambda: _import_tuple(replaygain_source, config, apply=True, mode="copy", library=library, skip_enrich=True, replaygain=True), commands, stdout_chunks, stderr_chunks)
    replaygain_dest = library / "MusicLab Genre" / "MusicLab Artist" / "Singles" / "Import ReplayGain Track.flac"
    replaygain_audit = render_audit(audit_path(replaygain_dest), verbose=False)
    if "ReplayGain" not in replaygain or ("ReplayGain Track: 1/1" not in replaygain_audit and "ReplayGain" not in replaygain_audit):
        if "SKIP" not in replaygain:
            raise LabFailure("Import ReplayGain", "import --replaygain --apply", replaygain + "\n" + replaygain_audit)
    replaygain_repeat = _run_step("import_replaygain_repeat", report_dir, lambda: _import_tuple(replaygain_source, config, apply=True, mode="copy", library=library, skip_enrich=True, replaygain=True), commands, stdout_chunks, stderr_chunks)
    if "already organized / already in library" not in replaygain_repeat:
        raise LabFailure("Import ReplayGain idempotency", "import --replaygain repeat", replaygain_repeat)

    complete_before = _file_fingerprints(complete_source)
    complete_dry = _run_step("import_complete_dry_run", report_dir, lambda: _import_tuple(complete_source, config, apply=False, mode="copy", library=library, replaygain=True, full_enrich=True), commands, stdout_chunks, stderr_chunks)
    complete_dest = library / "MusicLab Genre" / "MusicLab Artist" / "Singles" / "Import Complete Track.flac"
    if "Mode: DRY-RUN" not in complete_dry or "would update" not in complete_dry or complete_dest.exists() or complete_before != _file_fingerprints(complete_source):
        raise LabFailure("Import complete dry-run", "import complete dry-run", complete_dry)
    complete_apply = _run_step("import_complete_apply", report_dir, lambda: _import_tuple(complete_source, config, apply=True, mode="copy", library=library, replaygain=True, full_enrich=True), commands, stdout_chunks, stderr_chunks)
    complete_audit = render_audit(audit_path(complete_dest), advanced=True)
    if "Status: OK" not in complete_apply or not complete_dest.exists() or "Cover: 1/1" not in complete_audit or "Lyrics: 1/1" not in complete_audit:
        raise LabFailure("Import complete apply", "import complete --apply", complete_apply + "\n" + complete_audit)
    if shutil.which("ffmpeg") is not None and "ReplayGain Track: 1/1" not in complete_audit:
        raise LabFailure("Import complete ReplayGain", "import complete --replaygain --apply", complete_apply + "\n" + complete_audit)
    complete_query = _run_step("import_complete_db_query", report_dir, lambda: db_query(config, "Import Complete Album", target="tracks"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(complete_query, ["Import Complete Track"], "Import complete DB query")

    single_apply = _run_step("import_single_apply", report_dir, lambda: _import_tuple(single_source, config, apply=True, mode="copy", library=library, skip_enrich=True), commands, stdout_chunks, stderr_chunks)
    single_dest = library / "MusicLab Genre" / "MusicLab Artist" / "Singles" / "Import Single.flac"
    if "Status: OK" not in single_apply or not single_dest.exists():
        raise LabFailure("Import single", "import single --apply", single_apply)

    existing_before = _file_fingerprints(existing_source)
    existing_apply = _run_step("import_existing_cover_lyrics", report_dir, lambda: _import_tuple(existing_source, config, apply=True, mode="copy", library=library, skip_enrich=True), commands, stdout_chunks, stderr_chunks)
    if existing_before != _file_fingerprints(existing_source) or "Status: OK" not in existing_apply:
        raise LabFailure("Import existing cover lyrics", "import existing cover lyrics", existing_apply)

    conflict_dest = library / "MusicLab Genre" / "MusicLab Artist" / "Singles" / "Import Conflict Track.flac"
    conflict_dest.parent.mkdir(parents=True, exist_ok=True)
    if audio_files(conflict_source):
        shutil.copyfile(audio_files(conflict_source)[0], conflict_dest)
    conflict = _import_tuple(conflict_source, config, apply=True, mode="copy", library=library, skip_enrich=True)[1]
    _write_expected_report(report_dir, "import_conflict_apply", conflict, commands, stdout_chunks, stderr_chunks)
    if "Status: REVIEW" not in conflict:
        raise LabFailure("Import conflict", "import conflict --apply", conflict)
    conflict_dest.unlink(missing_ok=True)

    unsafe = _import_tuple(copy_source, config, apply=True, mode="copy", library=lab.parent / "UnsafeLibrary", skip_enrich=True)[1]
    _write_expected_report(report_dir, "import_safety", unsafe, commands, stdout_chunks, stderr_chunks)
    if "Refusing automated --apply outside MusicLab" not in unsafe:
        raise LabFailure("Import safety", "import unsafe --apply", unsafe)

    before = _db_counts(config)
    repeat = _run_step("import_copy_repeat", report_dir, lambda: _import_tuple(copy_source, config, apply=True, mode="copy", library=library, skip_enrich=True), commands, stdout_chunks, stderr_chunks)
    after = _db_counts(config)
    if before != after or "already organized / already in library" not in repeat:
        raise LabFailure("Import idempotency", "import copy repeat", repeat)

    with connect(config) as conn:
        op = conn.execute("SELECT operation FROM operations WHERE operation = 'import' ORDER BY id DESC LIMIT 1").fetchone()
    if not op:
        raise LabFailure("Import DB operation", "import operation", "missing import operation")
    return "copy/move/complete/review/replaygain/single/existing/conflict/safety/idempotency"


def _import_tuple(path: Path, config: dict, apply: bool, mode: str, library: Path, skip_enrich: bool = False, replaygain: bool = False, allow_review: bool = False, full_enrich: bool = False) -> tuple[int, str]:
    runner = (lambda target, active_apply, force, cover_in_full, lyrics_in_full, replaygain_in_full, verbose, debug: _lab_import_enrich(target, active_apply, force, cover_in_full, lyrics_in_full, replaygain_in_full, verbose, debug, config)) if full_enrich else None
    result = import_path(path, config=config, apply=apply, mode=mode, library=library, skip_enrich=skip_enrich, replaygain=replaygain, allow_review=allow_review, enrich_runner=runner)
    return result.code, result.output


def _lab_import_enrich(path: Path, apply: bool, force: bool, cover_in_full: bool, lyrics_in_full: bool, replaygain_in_full: bool, verbose: bool, debug: bool, config: dict) -> int:
    from .cli import enrich

    return enrich(
        path,
        apply=apply,
        force=force,
        full=True,
        skip_acoustid_identify=True,
        skip_bpm=True,
        skip_key=True,
        skip_features=True,
        skip_lastfm=True,
        skip_mood=True,
        skip_cover=not cover_in_full,
        skip_lyrics=not lyrics_in_full,
        skip_metadata_providers=True,
        replaygain=replaygain_in_full,
        skip_replaygain=not replaygain_in_full,
        no_progress=True,
        plain=True,
        verbose=verbose,
        debug=debug,
        config=config,
        explicit_flags={"skip_bpm", "skip_key", "skip_features", "skip_lastfm", "skip_mood", "skip_metadata_providers", "skip_acoustid_identify"},
    )


def _duplicates_check(lab: Path, config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    library = lab / "Library"
    duplicate_root = library / "MusicLab Duplicates"
    before_counts = _db_counts(config)
    before_files = _file_fingerprints(library)

    mb_output = _run_step("duplicates_tracks_mb", report_dir, lambda: duplicates_path(config, target=duplicate_root / "MB Track Duplicate", scope="tracks", by="mb_track_id"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(mb_output, ["Duplicate tracks: 1 groups", "same MB Track ID", "Confidence: high", "Status: WARN"], "Duplicates MB Track ID")

    acoustid_output = _run_step("duplicates_tracks_acoustid", report_dir, lambda: duplicates_path(config, target=duplicate_root / "AcoustID Duplicate", scope="tracks", by="acoustid"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(acoustid_output, ["Duplicate tracks: 1 groups", "same AcoustID", "Confidence: high", "Status: WARN"], "Duplicates AcoustID")

    duration_output = _run_step("duplicates_tracks_duration", report_dir, lambda: duplicates_path(config, target=duplicate_root / "Duration Duplicate", scope="tracks", by="artist,title,duration"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(duration_output, ["Duplicate tracks: 1 groups", "same artist/title/duration", "Confidence: medium", "Status: WARN"], "Duplicates duration")

    album_output = _run_step("duplicates_albums_release_group", report_dir, lambda: duplicates_path(config, target=duplicate_root, scope="albums", by="mb_release_group_id"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(album_output, ["Duplicate albums: 1 groups", "same release group/album", "Confidence: high", "Status: WARN"], "Duplicates albums")

    clean_output = _run_step("duplicates_clean_none", report_dir, lambda: duplicates_path(config, target=library / "MusicLab Artist" / "Clean Album", scope="tracks"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(clean_output, ["Duplicate tracks: none", "Status: OK"], "Duplicates clean")

    grouped_output = _run_cli_command("report_duplicates_grouped", ["report", "duplicates", str(duplicate_root / "MB Track Duplicate"), "--tracks", "--by", "mb_track_id"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(grouped_output, ["Report: Duplicate Tracks/Albums", "Duplicate tracks: 1 groups", "Status: WARN"], "Report duplicates grouped")
    alias_output = _run_cli_command("duplicates_alias", ["duplicates", str(library / "MusicLab Artist" / "Clean Album")], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(alias_output, ["Duplicate tracks: none", "Status: OK"], "Duplicates alias")

    json_output = _run_step("duplicates_json", report_dir, lambda: duplicates_path(config, target=duplicate_root / "MB Track Duplicate", scope="tracks", by="mb_track_id", output_format="json", debug=True), commands, stdout_chunks, stderr_chunks)
    payload = json.loads(json_output)
    if payload.get("scope") != "tracks" or payload.get("status") != "WARN" or not payload.get("groups"):
        raise LabFailure("Duplicates JSON", "duplicates --format json", json_output)
    first = payload["groups"][0]
    if first.get("confidence") != "high" or first.get("reason") != "same MB Track ID" or len(first.get("files", [])) != 2:
        raise LabFailure("Duplicates JSON", "duplicates --format json", json_output)

    if before_counts != _db_counts(config):
        raise LabFailure("Duplicates DB stability", "duplicates", "duplicates changed database counts")
    if before_files != _file_fingerprints(library):
        raise LabFailure("Duplicates file stability", "duplicates", "duplicates changed, moved, or deleted files")
    return "tracks/albums/json/db stable/no writes"


def _reports_check(lab: Path, config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    library = lab / "Library"
    incoming = lab / "Incoming" / "Reports Untracked"
    report_album = library / "MusicLab Reports" / "Missing Fields"
    cover = lab / "cover.png"

    first = _make_audio(report_album / "01 Missing Report.flac", 2300)
    second = _make_audio(report_album / "02 Missing Report.flac", 2310)
    created = [path for path in (first, second) if path]
    if not created:
        return "SKIP: ffmpeg not found for report fixtures"
    for index, path in enumerate(created, 1):
        _tag_file(path, title=f"Missing Report {index}", album="Missing Fields", track=index, total=len(created), mb=True)
    _run_step("reports_scan_missing_fixture", report_dir, lambda: scan_library(config, library, apply=True), commands, stdout_chunks, stderr_chunks)

    before_missing = _run_step("missing_reports_before", report_dir, lambda: missing_report(config, fields=["lyrics", "cover", "replaygain", "key"], library=report_album), commands, stdout_chunks, stderr_chunks)
    _assert_contains(before_missing, ["Lyrics: 2/2 missing", "Cover: 2/2 missing", "ReplayGain: 2/2 missing", "Key: 2/2 missing", "Status: WARN"], "Missing reports before")
    missing_aliases = _run_step("missing_reports_aliases", report_dir, lambda: missing_report(config, fields=["rg", "art", "lrc", "mbids"], library=report_album), commands, stdout_chunks, stderr_chunks)
    _assert_contains(missing_aliases, ["Fields: replaygain, cover, synced_lyrics, sidecar_lrc, mb_album_id, mb_track_id, mb_release_group_id", "ReplayGain: 2/2 missing", "Cover: 2/2 missing", "Synced Lyrics: 2/2 missing", "Status: WARN"], "Missing reports aliases")
    grouped_missing = _run_cli_command("report_missing_grouped", ["report", "missing", "lyrics", "--library", str(report_album)], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(grouped_missing, ["Report: Missing Lyrics", "Scope:", "Lyrics: 2/2 missing", "Status: WARN"], "Report missing grouped")
    missing_service_options = build_missing_options(config, field="lyrics", library=report_album)
    missing_service = run_missing_service(missing_service_options)
    service_code, service_output = render_report_result(missing_service, title=missing_report_title(missing_service_options.fields), scope=report_scope_label(report_album), output_format="text")
    if service_code != 0 or "Report: Missing Lyrics" not in service_output or "Lyrics: 2/2 missing" not in service_output:
        raise LabFailure("Report missing service", "report missing service", workflow_result_to_json(missing_service))
    alias_missing = _run_cli_command("missing_alias", ["missing", "lyrics", "--library", str(report_album)], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(alias_missing, ["Lyrics: 2/2 missing", "Status: WARN"], "Missing alias")
    tracks_output = _run_step("missing_reports_tracks", report_dir, lambda: missing_report(config, fields=["lyrics"], library=report_album, scope="tracks"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(tracks_output, ["Missing Lyrics: 2 tracks", str(report_album.resolve(strict=False))], "Missing reports tracks")
    json_output = _run_step("missing_reports_json", report_dir, lambda: missing_report(config, fields=["lyrics"], library=report_album, output_format="json"), commands, stdout_chunks, stderr_chunks)
    payload = json.loads(json_output)
    if payload.get("status") != "WARN" or payload.get("fields") != ["lyrics"] or payload.get("summary", {}).get("tracks_affected") != 2:
        raise LabFailure("Missing report JSON", "missing --format json", json_output)

    shutil.copyfile(cover, report_album / "cover.png")
    for path in created:
        path.with_suffix(".lrc").write_text("[00:00.00]MusicLab report lyric\n", encoding="utf-8")
    _run_step("reports_cover_apply", report_dir, lambda: cover_path(report_album, apply=True, sources=["local"], save_folder_cover=False), commands, stdout_chunks, stderr_chunks)
    _run_step("reports_lyrics_apply", report_dir, lambda: lyrics_path(report_album, apply=True, sources=["local"], save_txt=False), commands, stdout_chunks, stderr_chunks)
    replaygain_status = "warn"
    if shutil.which("ffmpeg") is not None:
        replaygain_output = _run_step("reports_replaygain_apply", report_dir, lambda: replaygain_path(report_album, apply=True), commands, stdout_chunks, stderr_chunks)
        replaygain_status = "ok" if "Status: OK" in replaygain_output else "warn"
    _run_step("reports_scan_after_apply", report_dir, lambda: scan_library(config, library, apply=True), commands, stdout_chunks, stderr_chunks)
    after_assets = _run_step("missing_reports_after_assets", report_dir, lambda: missing_report(config, fields=["lyrics", "cover"], library=report_album), commands, stdout_chunks, stderr_chunks)
    _assert_contains(after_assets, ["Lyrics: complete", "Cover: complete", "Status: OK"], "Missing reports after assets")
    after_rg = _run_step("missing_reports_after_replaygain", report_dir, lambda: missing_report(config, fields=["replaygain"], library=report_album), commands, stdout_chunks, stderr_chunks)
    if replaygain_status == "ok":
        _assert_contains(after_rg, ["ReplayGain: complete", "Status: OK"], "Missing reports after replaygain")
    key_output = _run_step("missing_reports_key_optional", report_dir, lambda: missing_report(config, fields=["key"], library=report_album), commands, stdout_chunks, stderr_chunks)
    _assert_contains(key_output, ["Key: 2/2 missing", "Status: WARN"], "Missing reports key")

    untracked_file = _make_audio(incoming / "01 New Incoming.flac", 2400)
    if untracked_file:
        _tag_file(untracked_file, title="New Incoming", album="Reports Incoming", track=1, total=1, mb=True)
        untracked_before = _run_step("untracked_before_scan", report_dir, lambda: untracked_report(config, incoming), commands, stdout_chunks, stderr_chunks)
        _assert_contains(untracked_before, ["Untracked files: 1", str(untracked_file.resolve(strict=False)), "Status: WARN"], "Untracked before scan")
        grouped_untracked = _run_cli_command("report_untracked_grouped", ["report", "untracked", str(incoming)], config, report_dir, commands, stdout_chunks, stderr_chunks)
        _assert_contains(grouped_untracked, ["Report: Untracked Files", "Untracked files: 1", "Status: WARN"], "Report untracked grouped")
        alias_untracked = _run_cli_command("untracked_alias", ["untracked", str(incoming)], config, report_dir, commands, stdout_chunks, stderr_chunks)
        _assert_contains(alias_untracked, ["Untracked files: 1", "Status: WARN"], "Untracked alias")
        untracked_json = _run_step("untracked_json", report_dir, lambda: untracked_report(config, incoming, output_format="json"), commands, stdout_chunks, stderr_chunks)
        if json.loads(untracked_json).get("summary", {}).get("untracked") != 1:
            raise LabFailure("Untracked JSON", "untracked --format json", untracked_json)
        _run_step("untracked_db_scan_apply", report_dir, lambda: scan_library(config, incoming, apply=True), commands, stdout_chunks, stderr_chunks)
        untracked_after = _run_step("untracked_after_scan", report_dir, lambda: untracked_report(config, incoming), commands, stdout_chunks, stderr_chunks)
        _assert_contains(untracked_after, ["Untracked files: none", "Status: OK"], "Untracked after scan")

    orphan = _make_audio(library / "MusicLab Reports" / "Missing Files" / "01 Gone.flac", 2500)
    if orphan:
        _tag_file(orphan, title="Gone", album="Missing Files", track=1, total=1, mb=True)
        _run_step("missing_files_scan_fixture", report_dir, lambda: scan_library(config, library, apply=True), commands, stdout_chunks, stderr_chunks)
        orphan.unlink()
        missing_files = _run_step("missing_files_report", report_dir, lambda: missing_files_report(config), commands, stdout_chunks, stderr_chunks)
        _assert_contains(missing_files, ["Missing files in database:", str(orphan.resolve(strict=False)), "Status: WARN"], "Missing files")
        grouped_missing_files = _run_cli_command("report_missing_files_grouped", ["report", "missing-files"], config, report_dir, commands, stdout_chunks, stderr_chunks)
        _assert_contains(grouped_missing_files, ["Report: Missing Files", "Missing files in database:", "Status: WARN"], "Report missing-files grouped")
        alias_missing_files = _run_cli_command("missing_files_alias", ["missing-files"], config, report_dir, commands, stdout_chunks, stderr_chunks)
        _assert_contains(alias_missing_files, ["Missing files in database:", "Status: WARN"], "Missing-files alias")
        missing_files_json = _run_step("missing_files_json", report_dir, lambda: missing_files_report(config, output_format="json"), commands, stdout_chunks, stderr_chunks)
        if json.loads(missing_files_json).get("summary", {}).get("missing_files", 0) < 1:
            raise LabFailure("Missing files JSON", "missing-files --format json", missing_files_json)

    before_counts = _db_counts(config)
    before_files = _file_fingerprints(library)
    help_outputs = {
        "top": _cli_help_output([]),
        "report": _cli_help_output(["report"]),
        "export": _cli_help_output(["export"]),
        "maintain": _cli_help_output(["maintain"]),
        "import": _cli_help_output(["import"]),
        "organize": _cli_help_output(["organize"]),
    }
    for name, output in help_outputs.items():
        _write_expected_report(report_dir, f"help_{name}", output, commands, stdout_chunks, stderr_chunks)
    _assert_contains(help_outputs["top"], ["Core workflows:", "Reports:", "Maintenance and review:"], "Top-level help")
    _assert_contains(help_outputs["report"], ["Reports are read-only", "noqlen-forge report missing lyrics"], "Report help")
    _assert_contains(help_outputs["export"], ["Export is read-only", "noqlen-forge export --library"], "Export help")
    _assert_contains(help_outputs["maintain"], ["dry-run", "--apply", "MusicLab"], "Maintain help")
    _assert_contains(help_outputs["import"], ["Dry-run is the default", "--apply", "MusicLab"], "Import help")
    _assert_contains(help_outputs["organize"], ["Dry-run is the default", "--apply", "MusicLab"], "Organize help")
    _run_step("reports_db_stability_missing", report_dir, lambda: missing_report(config, fields=["lyrics", "cover"], library=report_album), commands, stdout_chunks, stderr_chunks)
    _run_step("reports_db_stability_untracked", report_dir, lambda: untracked_report(config, incoming), commands, stdout_chunks, stderr_chunks)
    _run_step("reports_db_stability_missing_files", report_dir, lambda: missing_files_report(config), commands, stdout_chunks, stderr_chunks)
    if before_counts != _db_counts(config):
        raise LabFailure("Reports DB stability", "missing/untracked/missing-files", "report command changed database counts")
    current_files = _file_fingerprints(library)
    common_files = {path: value for path, value in current_files.items() if path in before_files}
    if common_files != before_files:
        raise LabFailure("Reports file stability", "missing/untracked/missing-files", "report command changed library files")
    return "missing/untracked/missing-files text+json/db stable/no writes"


def _repair_check(lab: Path, config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    library = lab / "Library"
    before_files = _file_fingerprints(library)

    missing = library / "MusicLab Artist" / "Clean Album" / "02 Clean Companion.flac"
    if not missing.is_file():
        raise LabFailure("Repair missing fixture", "repair missing-files", f"missing fixture not found: {missing}")
    missing.unlink()
    dry_missing_counts = _db_stability_counts(config)
    dry_missing = _run_cli_command("repair_missing_files_dry", ["maintain", "repair", "missing-files", "--verbose"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(dry_missing, ["Repair: missing files", "Mode: DRY-RUN", "mark-missing", "Status: WARN"], "Repair missing-files dry-run")
    if _db_stability_counts(config) != dry_missing_counts or _db_file_status(config, missing) != "active":
        raise LabFailure("Repair missing dry-run", "maintain repair missing-files", dry_missing)
    apply_missing = _run_cli_command("repair_missing_files_apply", ["maintain", "repair", "missing-files", "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(apply_missing, ["Mode: APPLY", "Status: OK"], "Repair missing-files apply")
    if _db_file_status(config, missing) != "missing":
        raise LabFailure("Repair missing apply", "maintain repair missing-files --apply", apply_missing)
    repeat_missing = _run_cli_command("repair_missing_files_repeat", ["maintain", "repair", "missing-files", "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(repeat_missing, ["nothing to repair", "Status: OK"], "Repair missing-files idempotency")

    incoming = lab / "Incoming" / "Repair Untracked"
    untracked = _make_audio(incoming / "01 Repair Untracked.flac", 1010)
    _tag_file(untracked, title="Repair Untracked", album="Repair", track=1, total=1, mb=True)
    before_untracked = _db_stability_counts(config)
    untracked_fingerprint = _file_fingerprints(incoming)
    dry_untracked = _run_cli_command("repair_untracked_dry", ["maintain", "repair", "untracked", str(incoming), "--verbose"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(dry_untracked, ["Repair: untracked", "Mode: DRY-RUN", "scan-untracked", "Status: WARN"], "Repair untracked dry-run")
    if _db_stability_counts(config) != before_untracked or _db_has_file(config, untracked):
        raise LabFailure("Repair untracked dry-run", "maintain repair untracked", dry_untracked)
    apply_untracked = _run_cli_command("repair_untracked_apply", ["maintain", "repair", "untracked", str(incoming), "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(apply_untracked, ["Mode: APPLY", "Status: OK"], "Repair untracked apply")
    if not _db_has_file(config, untracked) or _file_fingerprints(incoming) != untracked_fingerprint:
        raise LabFailure("Repair untracked apply", "maintain repair untracked --apply", apply_untracked)
    repeat_untracked = _run_cli_command("repair_untracked_repeat", ["maintain", "repair", "untracked", str(incoming), "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(repeat_untracked, ["nothing to repair", "Status: OK"], "Repair untracked idempotency")

    db_ids = _seed_repair_db_inconsistencies(config, library)
    dry_db_counts = _db_stability_counts(config)
    dry_db = _run_cli_command("repair_db_dry", ["maintain", "repair", "db", "--verbose"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(dry_db, ["Repair: db", "Mode: DRY-RUN", "mark-file-stale", "finish-operation-warn", "Status: WARN"], "Repair DB dry-run")
    if _db_stability_counts(config) != dry_db_counts or _db_file_status_by_id(config, db_ids["file"]) != "active":
        raise LabFailure("Repair DB dry-run", "maintain repair db", dry_db)
    apply_db = _run_cli_command("repair_db_apply", ["maintain", "repair", "db", "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(apply_db, ["Mode: APPLY", "Status: OK"], "Repair DB apply")
    if _db_file_status_by_id(config, db_ids["file"]) != "stale" or _db_track_status(config, db_ids["track"]) != "stale" or _db_album_status(config, db_ids["album"]) != "stale":
        raise LabFailure("Repair DB apply", "maintain repair db --apply", apply_db)
    repeat_db = _run_cli_command("repair_db_repeat", ["maintain", "repair", "db", "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(repeat_db, ["nothing to repair", "Status: OK"], "Repair DB idempotency")

    duplicate_root = library / "MusicLab Duplicates"
    before_duplicates = _db_stability_counts(config)
    before_duplicate_files = _file_fingerprints(duplicate_root)
    duplicates = _run_cli_command("repair_duplicates", ["maintain", "repair", "duplicates"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(duplicates, ["Repair: duplicates", "Status: REVIEW", "no files will be moved or deleted"], "Repair duplicates")
    if _db_stability_counts(config) != before_duplicates or _file_fingerprints(duplicate_root) != before_duplicate_files:
        raise LabFailure("Repair duplicates stability", "maintain repair duplicates", duplicates)

    _repair_safety_check(report_dir, commands, stdout_chunks, stderr_chunks)
    after_files = _file_fingerprints(library)
    for path, fingerprint in before_files.items():
        if path == missing:
            continue
        if path in after_files and after_files[path] != fingerprint:
            raise LabFailure("Repair file stability", "maintain repair", f"repair modified file: {path}")
    return "missing-files/untracked/db/duplicates/safety/idempotency"


def _write_expected_report(report_dir: Path, name: str, output: str, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{name}.log").write_text(output + "\n", encoding="utf-8")
    commands.append(name.replace("_", " "))
    stdout_chunks.append(output)
    stderr_chunks.append("")


def _run_cli_command(name: str, argv: list[str], config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    from .cli import build_parser, duplicates_command, jobs_command, maintain_command, missing_command, missing_files_command, report_command, sync_command, untracked_command

    parser = build_parser()
    args = parser.parse_args(argv)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        if args.command == "report":
            code = report_command(args, config=config)
        elif args.command == "maintain":
            code = maintain_command(args, config=config)
        elif args.command == "missing":
            code = missing_command(args, config=config)
        elif args.command == "duplicates":
            code = duplicates_command(args, config=config)
        elif args.command == "untracked":
            code = untracked_command(args, config=config)
        elif args.command == "missing-files":
            code = missing_files_command(args, config=config)
        elif args.command == "sync":
            code = sync_command(args, config=config)
        elif args.command == "jobs":
            code = jobs_command(args, config=config)
        else:
            code = 1
    output = stdout.getvalue().rstrip()
    error = stderr.getvalue().rstrip()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{name}.log").write_text(output + ("\nSTDERR:\n" + error if error else "") + "\n", encoding="utf-8")
    commands.append("noqlen-forge " + " ".join(argv))
    stdout_chunks.append(output)
    stderr_chunks.append(error)
    if code != 0:
        raise LabFailure(name, "noqlen-forge " + " ".join(argv), output + error)
    return output


def _cli_help_output(argv: list[str]) -> str:
    from .cli import build_parser

    parser = build_parser()
    help_argv = [*argv, "--help"]
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            parser.parse_args(help_argv)
        except SystemExit as exc:
            if exc.code != 0:
                raise LabFailure("Help", "noqlen-forge " + " ".join(help_argv), stdout.getvalue() + stderr.getvalue())
    return stdout.getvalue().rstrip()


def _run_step(name: str, report_dir: Path, callback: Callable[[], tuple[int, str]], commands: list[str] | None = None, stdout_chunks: list[str] | None = None, stderr_chunks: list[str] | None = None) -> str:
    code, output = callback()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{name}.log").write_text(output + "\n", encoding="utf-8")
    if commands is not None:
        commands.append(name.replace("_", " "))
    if stdout_chunks is not None:
        stdout_chunks.append(output)
    if stderr_chunks is not None:
        stderr_chunks.append("")
    if code != 0:
        raise LabFailure(name, name.replace("_", " "), output)
    return output


def _run_enrich_cleanup(path: Path, apply: bool, report_dir: Path, name: str) -> None:
    tracks = read_tracks(path)
    plans = plan_cleanup(tracks)
    output = summarize_cleanup(plans, apply=apply, verbose=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{name}.log").write_text(output + "\n", encoding="utf-8")
    apply_cleanup(plans, apply=apply)


def _run_enrich_full(path: Path, apply: bool, report_dir: Path, name: str, config: dict, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    from .cli import enrich

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = enrich(
            path,
            apply=apply,
            force=False,
            skip_acoustid_identify=True,
            full=True,
            skip_bpm=True,
            skip_key=True,
            skip_features=True,
            skip_lastfm=True,
            skip_mood=True,
            skip_cover=True,
            skip_lyrics=True,
            skip_metadata_providers=True,
            no_progress=True,
            plain=True,
            config=config,
            explicit_flags={"skip_bpm", "skip_key", "skip_features", "skip_lastfm", "skip_mood", "skip_cover", "skip_lyrics", "skip_metadata_providers", "skip_acoustid_identify"},
        )
    output = stdout.getvalue()
    error = stderr.getvalue()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{name}.log").write_text(output + ("\nSTDERR:\n" + error if error else ""), encoding="utf-8")
    commands.append(name.replace("_", " "))
    stdout_chunks.append(output)
    stderr_chunks.append(error)
    if code != 0:
        raise LabFailure(name, name.replace("_", " "), output + error)
    return output


def _native_independence_check(path: Path, config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    from . import cli as cli_module

    def run_case(name: str, **kwargs) -> str:
        blocked_path = bool(kwargs.pop("blocked_path", False))
        stdout = io.StringIO()
        stderr = io.StringIO()
        original_path = os.environ.get("PATH")
        if blocked_path:
            blocked_bin = report_dir / "native-blocked-path-bin"
            blocked_bin.mkdir(parents=True, exist_ok=True)
            os.environ["PATH"] = str(blocked_bin)
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli_module.enrich(
                    path,
                    apply=bool(kwargs.pop("apply", False)),
                    force=False,
                    full=bool(kwargs.pop("full", False)),
                    skip_bpm=True,
                    skip_key=True,
                    skip_features=True,
                    skip_lastfm=True,
                    skip_mood=True,
                    skip_cover=True,
                    skip_lyrics=True,
                    skip_metadata_providers=True,
                    skip_acoustid_identify=True,
                    no_progress=True,
                    plain=True,
                    config=config,
                    explicit_flags={"--skip-bpm", "--skip-key", "--skip-features", "--skip-lastfm", "--skip-mood", "--skip-cover", "--skip-lyrics", "--skip-metadata-providers", "--skip-acoustid-identify", *kwargs.pop("explicit_flags", set())},
                    **kwargs,
                )
        finally:
            if blocked_path:
                if original_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = original_path
        output = stdout.getvalue()
        error = stderr.getvalue()
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{name}.log").write_text(output + ("\nSTDERR:\n" + error if error else ""), encoding="utf-8")
        commands.append(name.replace("_", " "))
        stdout_chunks.append(output)
        stderr_chunks.append(error)
        if code != 0:
            raise LabFailure("Native independence", name.replace("_", " "), output + error)
        return output

    legacy_terms = ("OneTagger", "Legacy TuneUp", "onetagger", "tuneup", "beets")
    dry_output = run_case("native_default_dry_run")
    full_output = run_case("native_full_dry_run", full=True)
    apply_output = run_case("native_full_apply", apply=True, full=True)
    blocked_path_output = run_case("native_blocked_path_full_apply", apply=True, full=True, blocked_path=True)
    combined = dry_output + full_output + apply_output + blocked_path_output
    if any(term in combined for term in legacy_terms):
        raise LabFailure("Native independence", "native enrich output", combined)

    enrich_help = _cli_help_output(["enrich"])
    config_text = render_config(default_config(), comments=False)
    _write_expected_report(report_dir, "help_enrich_native", enrich_help, commands, stdout_chunks, stderr_chunks)
    (report_dir / "config_default_native.log").write_text(config_text + "\n", encoding="utf-8")
    commands.append("config default native")
    if any(term.lower() in enrich_help.lower() for term in legacy_terms):
        raise LabFailure("Native independence", "enrich help", enrich_help)
    if any(term.lower() in config_text.lower() for term in legacy_terms):
        raise LabFailure("Native independence", "default config", config_text)
    return "enrich dry/full/apply native; blocked PATH passes; help and default config omit legacy external tools"


def _ambiguous_metadata_check(path: Path) -> str:
    tracks = read_tracks(path)
    if not tracks:
        raise LabFailure("Ambiguous metadata", "metadata ambiguous dry-run", "no tracks")
    if any(get_tag(track, "catalog_number") or get_tag(track, "barcode") for track in tracks):
        raise LabFailure("Ambiguous metadata", "metadata ambiguous dry-run", "catalog/barcode was written")
    if any(get_tag(track, "country") or get_tag(track, "media") for track in tracks):
        raise LabFailure("Ambiguous metadata", "metadata ambiguous dry-run", "country/media was written")
    return "REVIEW as expected, no dangerous catalog writes"


def _fallback_metadata_check(path: Path, apply: bool) -> str:
    before = read_tracks(path)
    if not before:
        raise LabFailure("Fallback metadata", "metadata fallback", "no tracks")
    if apply:
        _write_safe_metadata(path, {"GENRE": "Electronic", "ORIGINALDATE": "2026-01-01"})
    after = read_tracks(path)
    if not all(get_tag(track, "genre") and get_tag(track, "originaldate") for track in after):
        raise LabFailure("Fallback metadata", "metadata fallback apply", "safe fallback fields missing")
    if any(get_tag(track, "catalog_number") or get_tag(track, "barcode") or get_tag(track, "country") for track in after):
        raise LabFailure("Fallback metadata", "metadata fallback apply", "unsafe catalog fields written")
    return "iTunes/Deezer safe fields only"


def _acoustid_check(path: Path) -> str:
    tracks = read_tracks(path)
    if not tracks:
        raise LabFailure("AcoustID cases", "acoustid identify", "no tracks")
    fingerprinted = sum(1 for track in tracks if get_tag(track, "acoustid_fingerprint"))
    acoustids = sum(1 for track in tracks if get_tag(track, "acoustid_id"))
    mbids = [value for track in tracks for value in get_tag(track, "mb_track_id")]
    if fingerprinted < 1 or acoustids < 2:
        raise LabFailure("AcoustID cases", "acoustid identify", "fixture identity tags missing")
    if len(set(mbids)) != len(mbids):
        raise LabFailure("AcoustID cases", "acoustid identify", "unexpected duplicate MBID state")
    return "no API key/fpcalc cases skipped; existing IDs preserved"


def _replaygain_check(path: Path, report_dir: Path, config: dict, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    if shutil.which("ffmpeg") is None:
        output = "SKIP: ffmpeg not found"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "replaygain_skip.log").write_text(output + "\n", encoding="utf-8")
        return output
    dry = _run_step("replaygain_dry_run", report_dir, lambda: replaygain_path(path, apply=False), commands, stdout_chunks, stderr_chunks)
    if "Mode: DRY-RUN" not in dry or "would write ReplayGain" not in dry:
        raise LabFailure("ReplayGain dry-run", "replaygain dry-run", dry)
    apply_output = _run_step("replaygain_apply", report_dir, lambda: replaygain_path(path, apply=True), commands, stdout_chunks, stderr_chunks)
    audit = render_audit(audit_path(path), advanced=True)
    _assert_contains(audit, ["ReplayGain Track: 1/1", "ReplayGain Album: 1/1", "Loudness: 1/1"], "ReplayGain audit")
    repeat = _run_step("replaygain_apply_repeat", report_dir, lambda: replaygain_path(path, apply=True), commands, stdout_chunks, stderr_chunks)
    if "wrote ReplayGain 0/1" not in repeat:
        raise LabFailure("ReplayGain idempotency", "replaygain apply repeat", repeat)
    before = _file_fingerprints(path)
    enrich_output = _run_enrich_replaygain(path, apply=True, report_dir=report_dir, config=config, commands=commands, stdout_chunks=stdout_chunks, stderr_chunks=stderr_chunks)
    if before != _file_fingerprints(path):
        raise LabFailure("ReplayGain enrich idempotency", "enrich --full --replaygain", enrich_output)
    if "ReplayGain" not in enrich_output:
        raise LabFailure("ReplayGain enrich", "enrich --full --replaygain", enrich_output)
    return "track/album/loudness tags applied idempotently" if "Status: OK" in apply_output else "applied with warnings"


def _sync_check(path: Path, config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    files = audio_files(path)
    if not files:
        raise LabFailure("Sync", "sync fixture", "sync fixture missing")
    track = files[0]
    _set_flac_tag(track, "TITLE", "Tag Wins Title")
    before = _file_fingerprints(path)
    grouped_tags_dry = _run_cli_command("maintain_sync_tags_to_db_dry", ["maintain", "sync", str(path), "--tags-to-db", "--field", "title", "--conflict-policy", "tags-wins"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(grouped_tags_dry, ["Maintenance: Sync tags to database", "Mode: DRY-RUN"], "Maintain sync tags-to-db dry-run")
    dry = _run_step("sync_tags_to_db_dry", report_dir, lambda: sync_path(path, config, direction="tags-to-db", apply=False, fields=["title"], conflict_policy="tags-wins"), commands, stdout_chunks, stderr_chunks)
    if "Mode: DRY-RUN" not in dry or _db_track_title(config, track) != "Sync Track":
        raise LabFailure("Sync tags-to-db dry-run", "sync --tags-to-db", dry)
    apply_tags = _run_step("sync_tags_to_db_apply", report_dir, lambda: sync_path(path, config, direction="tags-to-db", apply=True, fields=["title"], conflict_policy="tags-wins"), commands, stdout_chunks, stderr_chunks)
    if _db_track_title(config, track) != "Tag Wins Title" or before != _file_fingerprints(path):
        raise LabFailure("Sync tags-to-db apply", "sync --tags-to-db --apply", apply_tags)

    _set_db_track_title(config, track, "DB Wins Title")
    before_db_to_tags = _file_fingerprints(path)
    grouped_db_dry = _run_cli_command("maintain_sync_db_to_tags_dry", ["maintain", "sync", str(path), "--db-to-tags", "--field", "title", "--conflict-policy", "db-wins"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(grouped_db_dry, ["Maintenance: Sync database to tags", "Mode: DRY-RUN"], "Maintain sync db-to-tags dry-run")
    dry_tags = _run_step("sync_db_to_tags_dry", report_dir, lambda: sync_path(path, config, direction="db-to-tags", apply=False, fields=["title"], conflict_policy="db-wins"), commands, stdout_chunks, stderr_chunks)
    if _track_title(track) != "Tag Wins Title" or before_db_to_tags != _file_fingerprints(path):
        raise LabFailure("Sync db-to-tags dry-run", "sync --db-to-tags", dry_tags)
    apply_db = _run_step("sync_db_to_tags_apply", report_dir, lambda: sync_path(path, config, direction="db-to-tags", apply=True, fields=["title"], conflict_policy="db-wins"), commands, stdout_chunks, stderr_chunks)
    if _track_title(track) != "DB Wins Title":
        raise LabFailure("Sync db-to-tags apply", "sync --db-to-tags --apply", apply_db)
    sync_audit = render_audit(audit_path(path), verbose=True)
    if "MB Album Id: 1/1" not in sync_audit or "Bad fields: none" not in sync_audit:
        raise LabFailure("Sync audit", "audit after sync", sync_audit)

    _set_flac_tag(track, "TITLE", "Review Tag")
    _set_db_track_title(config, track, "Review DB")
    review = _run_step("sync_conflict_review", report_dir, lambda: _allow_review(sync_path(path, config, direction="db-to-tags", apply=True, fields=["title"], conflict_policy="review")), commands, stdout_chunks, stderr_chunks)
    if "Status: REVIEW" not in review or _track_title(track) != "Review Tag" or _db_track_title(config, track) != "Review DB":
        raise LabFailure("Sync conflict review", "sync conflict review", review)

    db_wins = _run_step("sync_conflict_db_wins", report_dir, lambda: sync_path(path, config, direction="db-to-tags", apply=True, fields=["title"], conflict_policy="db-wins"), commands, stdout_chunks, stderr_chunks)
    if _track_title(track) != "Review DB":
        raise LabFailure("Sync conflict db-wins", "sync db-wins", db_wins)
    _set_flac_tag(track, "TITLE", "Tags Win Again")
    tags_wins = _run_step("sync_conflict_tags_wins", report_dir, lambda: sync_path(path, config, direction="tags-to-db", apply=True, fields=["title"], conflict_policy="tags-wins"), commands, stdout_chunks, stderr_chunks)
    if _db_track_title(config, track) != "Tags Win Again":
        raise LabFailure("Sync conflict tags-wins", "sync tags-wins", tags_wins)

    _set_db_album_mbid(config, track, "lab-db-protected-mbid")
    _set_flac_tag(track, "MUSICBRAINZ_ALBUMID", "lab-tag-protected-mbid")
    protected = _run_step("sync_protected_identity", report_dir, lambda: _allow_review(sync_path(path, config, direction="tags-to-db", apply=True, fields=["mb_album_id"], conflict_policy="tags-wins")), commands, stdout_chunks, stderr_chunks)
    if "Status: REVIEW" not in protected or _db_album_mbid(config, track) != "lab-db-protected-mbid":
        raise LabFailure("Sync protected identity", "sync protected identity", protected)

    _set_flac_tag(track, "TITLE", "Tags Win Again")
    _set_db_track_title(config, track, "Tags Win Again")
    repeat = _run_step("sync_idempotency", report_dir, lambda: sync_path(path, config, direction="db-to-tags", apply=True, fields=["title"], conflict_policy="db-wins"), commands, stdout_chunks, stderr_chunks)
    if "Tag writes: 0" not in repeat:
        raise LabFailure("Sync idempotency", "sync repeat", repeat)
    return "tags-to-db/db-to-tags/conflicts/protected/idempotency/safety"


def _rewrite_check(path: Path, config: dict, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    files = audio_files(path)
    if len(files) < 2:
        raise LabFailure("Rewrite", "rewrite fixture", "rewrite fixture missing")
    track = files[0]
    for item in files:
        _set_db_style(config, item, "Prog Metal; death metal; Prog Metal")
    before = _file_fingerprints(path)
    dry = _run_cli_command("maintain_rewrite_dry_run", ["maintain", "rewrite", str(path)], config, report_dir, commands, stdout_chunks, stderr_chunks)
    _assert_contains(dry, ["Maintenance: Rewrite metadata", "Mode: DRY-RUN", "Would update tags", "Would update DB"], "Rewrite dry-run")
    if before != _file_fingerprints(path) or _db_album_label(config, track) != "Season of Mist":
        raise LabFailure("Rewrite dry-run", "maintain rewrite", dry)

    style_only = _run_cli_command("maintain_rewrite_field_tags_only", ["maintain", "rewrite", str(path), "--field", "style", "--tags-only", "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    first = read_tracks(track)[0]
    styles = get_tag(first, "style")
    if "Status: OK" not in style_only or styles != ["Progressive Metal; Death Metal"] or get_tag(first, "label") != ["Season of Mist"] or _db_style(config, track) != "Prog Metal; death metal; Prog Metal":
        raise LabFailure("Rewrite field filter", "rewrite --field style --tags-only --apply", style_only)

    apply_output = _run_cli_command("maintain_rewrite_apply", ["maintain", "rewrite", str(path), "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    rewritten = read_tracks(track)[0]
    if "Status: OK" not in apply_output or get_tag(rewritten, "genre") != ["K-pop"] or get_tag(rewritten, "label") != ["Season Of Mist"]:
        raise LabFailure("Rewrite tags apply", "rewrite --apply", apply_output)
    if _db_album_label(config, track) != "Season Of Mist" or _db_style(config, track) != "Progressive Metal; Death Metal":
        raise LabFailure("Rewrite DB apply", "rewrite --apply", apply_output)

    _set_db_album_label(config, track, "Season of Mist")
    before_db_only = _file_fingerprints(path)
    db_only = _run_cli_command("maintain_rewrite_db_only", ["maintain", "rewrite", str(path), "--field", "label", "--db-only", "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    if "Status: OK" not in db_only or _db_album_label(config, track) != "Season Of Mist" or before_db_only != _file_fingerprints(path):
        raise LabFailure("Rewrite db-only", "rewrite --db-only --apply", db_only)

    _set_db_album_mbid(config, track, "lab-rewrite-old-mbid")
    _set_flac_tag(track, "MUSICBRAINZ_ALBUMID", "lab-rewrite-old-mbid")
    protected = _run_step("rewrite_protected_block", report_dir, lambda: _allow_review(rewrite_path(path, config, apply=True, fields=["mb_album_id"])), commands, stdout_chunks, stderr_chunks)
    if "Status: REVIEW" not in protected or _db_album_mbid(config, track) != "lab-rewrite-old-mbid" or get_tag(read_tracks(track)[0], "mb_album_id") != ["lab-rewrite-old-mbid"]:
        raise LabFailure("Rewrite protected", "rewrite protected", protected)

    _set_db_album_mbid(config, track, "lab-rewrite-new-mbid")
    _set_flac_tag(track, "MUSICBRAINZ_ALBUMID", "lab-rewrite-new-mbid")
    repeat = _run_cli_command("maintain_rewrite_idempotency", ["maintain", "rewrite", str(path), "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    if "Updated tags: 0" not in repeat or "Updated DB: 0" not in repeat:
        raise LabFailure("Rewrite idempotency", "rewrite apply repeat", repeat)
    return "tags/db/multi-value/field-filter/tags-only/db-only/protected/idempotency/safety"


def _sync_safety_check(report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> None:
    real_library = "/mnt/sdcard/Music" + "/Biblioteca de Musicas"
    script = "from noqlen_forge.cli import main; raise SystemExit(main(['sync',__import__('os').environ['NOQLEN_FORGE_REAL_LIBRARY_CHECK'],'--db-to-tags','--apply']))"
    command = ["python", "-c", script]
    env = {**os.environ, AUTOMATED_VALIDATION_ENV: "1"}
    env["NOQLEN_FORGE_REAL_LIBRARY_CHECK"] = real_library
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
    output = (result.stdout or "") + (result.stderr or "")
    commands.append("sync safety outside MusicLab")
    stdout_chunks.append(output)
    stderr_chunks.append(result.stderr or "")
    (report_dir / "sync_safety.log").write_text(output, encoding="utf-8")
    if result.returncode == 0 or "Refusing automated --apply outside MusicLab" not in output:
        raise LabFailure("Sync safety", "sync --db-to-tags --apply outside MusicLab", output)


def _rewrite_safety_check(report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> None:
    real_library = "/mnt/sdcard/Music" + "/Biblioteca de Musicas"
    script = "from noqlen_forge.cli import main; raise SystemExit(main(['maintain','rewrite',__import__('os').environ['NOQLEN_FORGE_REAL_LIBRARY_CHECK'],'--apply']))"
    command = ["python", "-c", script]
    env = {**os.environ, AUTOMATED_VALIDATION_ENV: "1"}
    env["NOQLEN_FORGE_REAL_LIBRARY_CHECK"] = real_library
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
    output = (result.stdout or "") + (result.stderr or "")
    commands.append("rewrite safety outside MusicLab")
    stdout_chunks.append(output)
    stderr_chunks.append(result.stderr or "")
    (report_dir / "rewrite_safety.log").write_text(output, encoding="utf-8")
    if result.returncode == 0 or "Refusing automated --apply outside MusicLab" not in output:
        raise LabFailure("Rewrite safety", "rewrite --apply outside MusicLab", output)


def _repair_safety_check(report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> None:
    real_library = "/mnt/sdcard/Music" + "/Biblioteca de Musicas"
    script = "from noqlen_forge.cli import main; raise SystemExit(main(['maintain','repair',__import__('os').environ['NOQLEN_FORGE_REAL_LIBRARY_CHECK'],'--apply']))"
    command = ["python", "-c", script]
    env = {**os.environ, AUTOMATED_VALIDATION_ENV: "1"}
    env["NOQLEN_FORGE_REAL_LIBRARY_CHECK"] = real_library
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
    output = (result.stdout or "") + (result.stderr or "")
    commands.append("repair safety outside MusicLab")
    stdout_chunks.append(output)
    stderr_chunks.append(result.stderr or "")
    (report_dir / "repair_safety.log").write_text(output, encoding="utf-8")
    if result.returncode == 0 or "Refusing automated --apply outside MusicLab" not in output:
        raise LabFailure("Repair safety", "repair --apply outside MusicLab", output)


def _review_check(config: dict, album_path: Path, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    ids = _seed_review_decisions(config, album_path)
    track = audio_files(album_path)[0]
    before_dry = _file_fingerprints(album_path)

    listed = _run_step("review_list", report_dir, lambda: _allow_review(review_list(config, album_path)), commands, stdout_chunks, stderr_chunks)
    _assert_contains(listed, ["Pending decisions:", "Status: REVIEW", "11086464", "11319329"], "Review list")
    shown = _run_step("review_show", report_dir, lambda: review_show(config, ids["provider"]), commands, stdout_chunks, stderr_chunks)
    _assert_contains(shown, ["Review decision", "Candidates:", "Actions:"], "Review show")
    payload = json.loads(_run_step("review_json", report_dir, lambda: _allow_review(review_list(config, album_path, output_format="json")), commands, stdout_chunks, stderr_chunks))
    if payload.get("status") != "REVIEW" or not isinstance(payload.get("decisions"), list) or not payload["decisions"]:
        raise LabFailure("Review JSON", "review --format json", json.dumps(payload))

    dry = _run_step("review_accept_dry_run", report_dir, lambda: review_resolve(config, str(ids["accept"]), action="accept"), commands, stdout_chunks, stderr_chunks)
    if "Mode: DRY-RUN" not in dry or before_dry != _file_fingerprints(album_path) or _decision_resolved(config, ids["accept"]):
        raise LabFailure("Review dry-run", "review resolve accept", dry)

    keep = _run_step("review_keep_apply", report_dir, lambda: review_resolve(config, str(ids["keep"]), action="keep", apply=True), commands, stdout_chunks, stderr_chunks)
    if "Status: OK" not in keep or not _decision_action(config, ids["keep"], "keep") or _db_album_label(config, track) != "Season Of Mist":
        raise LabFailure("Review keep", "review resolve keep --apply", keep)

    reject = _run_step("review_reject_provider", report_dir, lambda: review_resolve(config, str(ids["provider"]), action="reject", apply=True), commands, stdout_chunks, stderr_chunks)
    if "Status: OK" not in reject or not _decision_action(config, ids["provider"], "reject"):
        raise LabFailure("Review provider reject", "review resolve reject --apply", reject)

    accept = _run_step("review_accept_apply", report_dir, lambda: review_resolve(config, str(ids["accept"]), action="accept", apply=True), commands, stdout_chunks, stderr_chunks)
    if "Tag writes:" not in accept or "Progressive Death Metal" not in get_tag(read_tracks(track)[0], "style") or not _decision_action(config, ids["accept"], "accept"):
        raise LabFailure("Review accept", "review resolve accept --apply", accept)

    manual = _run_step("review_manual_value", report_dir, lambda: review_resolve(config, str(album_path), field="style", value="Progressive Metal; Death Metal", apply=True), commands, stdout_chunks, stderr_chunks)
    styles = set(get_tag(read_tracks(track)[0], "style"))
    if "Status: OK" not in manual or not {"Progressive Metal", "Death Metal"}.issubset(styles) or not _decision_action(config, ids["manual"], "accept"):
        raise LabFailure("Review manual value", "review resolve path --field style --value --apply", manual)

    protected = _run_step("review_protected_block", report_dir, lambda: _allow_review(review_resolve(config, str(ids["protected"]), action="accept", apply=True)), commands, stdout_chunks, stderr_chunks)
    if "requires --force" not in protected or _db_album_mbid(config, track) != "lab-review-old-mbid":
        raise LabFailure("Review protected", "review resolve protected", protected)
    forced = _run_step("review_protected_force", report_dir, lambda: review_resolve(config, str(ids["protected"]), action="accept", apply=True, force=True), commands, stdout_chunks, stderr_chunks)
    if "Status: OK" not in forced or _db_album_mbid(config, track) != "lab-review-new-mbid":
        raise LabFailure("Review protected force", "review resolve protected --force --apply", forced)

    repeat = _run_step("review_idempotency", report_dir, lambda: review_resolve(config, str(ids["accept"]), action="accept", apply=True), commands, stdout_chunks, stderr_chunks)
    if "no pending decision" not in repeat:
        raise LabFailure("Review idempotency", "review resolve repeat", repeat)
    return "list/show/json/dry-run/keep/reject/accept/manual/protected/idempotency"


def _review_safety_check(report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> None:
    script = "from noqlen_forge.cli import main; raise SystemExit(main(['review','resolve','1','--action','accept','--apply']))"
    safety_root = Path("/tmp/opencode/noqlen-forge-review-safety")
    safety_root.mkdir(parents=True, exist_ok=True)
    config_root = safety_root / "xdg"
    config_dir = config_root / "noqlen-forge"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    db_path = safety_root / "review-safety.db"
    outside = safety_root / "outside.flac"
    config_path.write_text(f"[database]\npath = \"{db_path}\"\n", encoding="utf-8")
    with connect({"database": {"path": str(db_path)}}) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album": "Safety", "albumartist": "MusicLab"})
        track_id = upsert_track(conn, {"title": "Safety", "artist": "MusicLab"}, album_id=album_id)
        upsert_file(conn, outside, {"format": "flac"}, track_id=track_id)
        run_id = record_provider_run(conn, "discogs", "album", album_id, "review")
        record_field_decision(conn, run_id, "album", album_id, "style", current_value="", candidate_value="Safety", provider="discogs", action="review", reason="safety")
        conn.commit()
    command = ["python", "-c", script]
    env = {**os.environ, AUTOMATED_VALIDATION_ENV: "1", "XDG_CONFIG_HOME": str(config_root)}
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
    output = (result.stdout or "") + (result.stderr or "")
    commands.append("review safety outside MusicLab")
    stdout_chunks.append(output)
    stderr_chunks.append(result.stderr or "")
    (report_dir / "review_safety.log").write_text(output, encoding="utf-8")
    if result.returncode == 0 or "Refusing automated --apply outside MusicLab" not in output:
        raise LabFailure("Review safety", "review resolve --apply outside MusicLab", output)


def _allow_review(result: tuple[int, str]) -> tuple[int, str]:
    code, output = result
    if code != 0 and ("Status: REVIEW" in output or '"status": "REVIEW"' in output):
        return 0, output
    return code, output


def _track_title(path: Path) -> str:
    tracks = read_tracks(path)
    return tracks[0].title if tracks else ""


def _set_flac_tag(path: Path, key: str, value: str) -> None:
    audio = FLAC(path)
    audio[key] = value
    audio.save()


def _db_track_title(config: dict, path: Path) -> str:
    with connect(config) as conn:
        row = conn.execute("SELECT t.title FROM tracks t JOIN files f ON f.track_id = t.id WHERE f.path = ?", (str(path),)).fetchone()
        return str(row["title"] or "") if row else ""


def _set_db_track_title(config: dict, path: Path, title: str) -> None:
    with connect(config) as conn:
        conn.execute("UPDATE tracks SET title = ? WHERE id = (SELECT track_id FROM files WHERE path = ?)", (title, str(path)))
        conn.commit()


def _db_album_mbid(config: dict, path: Path) -> str:
    with connect(config) as conn:
        row = conn.execute("SELECT a.mb_album_id FROM albums a JOIN tracks t ON t.album_id = a.id JOIN files f ON f.track_id = t.id WHERE f.path = ?", (str(path),)).fetchone()
        return str(row["mb_album_id"] or "") if row else ""


def _db_album_label(config: dict, path: Path) -> str:
    with connect(config) as conn:
        row = conn.execute("SELECT a.label FROM albums a JOIN tracks t ON t.album_id = a.id JOIN files f ON f.track_id = t.id WHERE f.path = ?", (str(path),)).fetchone()
        return str(row["label"] or "") if row else ""


def _db_style(config: dict, path: Path) -> str:
    with connect(config) as conn:
        rows = conn.execute("SELECT tt.value FROM track_tags tt JOIN files f ON f.track_id = tt.track_id WHERE f.path = ? AND tt.key = 'style' ORDER BY tt.id", (str(path),)).fetchall()
        return "; ".join(str(row["value"] or "") for row in rows if row["value"])


def _set_db_album_label(config: dict, path: Path, label: str) -> None:
    with connect(config) as conn:
        conn.execute("UPDATE albums SET label = ? WHERE id = (SELECT t.album_id FROM tracks t JOIN files f ON f.track_id = t.id WHERE f.path = ?)", (label, str(path)))
        conn.commit()


def _set_db_style(config: dict, path: Path, style: str) -> None:
    with connect(config) as conn:
        row = conn.execute("SELECT track_id FROM files WHERE path = ?", (str(path),)).fetchone()
        if row:
            conn.execute("DELETE FROM track_tags WHERE track_id = ? AND key = 'style'", (row["track_id"],))
            conn.execute("INSERT INTO track_tags(track_id, key, value, type, source, confidence, updated_at) VALUES (?, 'style', ?, 'tag', 'lab', 'local', 'now')", (row["track_id"], style))
        conn.commit()


def _set_db_album_mbid(config: dict, path: Path, mbid: str) -> None:
    with connect(config) as conn:
        conn.execute("UPDATE albums SET mb_album_id = ? WHERE id = (SELECT t.album_id FROM tracks t JOIN files f ON f.track_id = t.id WHERE f.path = ?)", (mbid, str(path)))
        conn.commit()


def _seed_review_decisions(config: dict, album_path: Path) -> dict[str, int]:
    with connect(config) as conn:
        target = _first_db_track(conn, album_path)
        if not target:
            raise LabFailure("Review", "seed review", "target album not found")
        album_id = int(target["album_id"])
        track_id = int(target["track_id"])
        conn.execute("UPDATE albums SET label = ?, mb_album_id = ? WHERE id = ?", ("Season Of Mist", "lab-review-old-mbid", album_id))
        conn.execute("UPDATE field_decisions SET resolved = 1, resolved_action = 'keep', resolved_by = 'manual', resolved_at = ? WHERE target_type = 'album' AND target_id = ? AND field = 'style' AND COALESCE(resolved, 0) = 0", (datetime.now().isoformat(timespec="seconds"), album_id))
        ids: dict[str, int] = {}

        provider_run = record_provider_run(conn, "discogs", "album", album_id, "review", query="Ne Obliviscaris Urn")
        record_candidate(conn, provider_run, "discogs", "11086464", score=0.91, confidence="medium", payload_summary={"format": "File, Album"})
        record_candidate(conn, provider_run, "discogs", "11319329", score=0.91, confidence="medium", payload_summary={"country": "Australia", "format": "2xVinyl, Limited Edition"})
        ids["provider"] = record_field_decision(conn, provider_run, "album", album_id, "edition", current_value="", candidate_value="11086464", provider="discogs", confidence="medium", action="review", reason="2 equally strong Discogs candidates")

        keep_run = record_provider_run(conn, "discogs", "album", album_id, "review", query="label conflict")
        ids["keep"] = record_field_decision(conn, keep_run, "album", album_id, "label", current_value="Season Of Mist", candidate_value="Season Of Mist Brazil", provider="discogs", confidence="medium", action="review", reason="existing value conflicts with provider value")

        accept_run = record_provider_run(conn, "discogs", "track", track_id, "review", query="style accept")
        ids["accept"] = record_field_decision(conn, accept_run, "track", track_id, "style", current_value="", candidate_value="Progressive Death Metal", provider="discogs", confidence="medium", action="review", reason="safe field requires manual decision")

        manual_run = record_provider_run(conn, "discogs", "track", track_id, "review", query="style manual")
        ids["manual"] = record_field_decision(conn, manual_run, "track", track_id, "style", current_value="Progressive Death Metal", candidate_value="Technical Death Metal", provider="discogs", confidence="medium", action="review", reason="manual value required")

        protected_run = record_provider_run(conn, "musicbrainz", "album", album_id, "review", query="protected identity")
        ids["protected"] = record_field_decision(conn, protected_run, "album", album_id, "mb_album_id", current_value="lab-review-old-mbid", candidate_value="lab-review-new-mbid", provider="musicbrainz", confidence="medium", action="review", reason="protected identity conflict")
        conn.commit()
        return ids


def _decision_resolved(config: dict, decision_id: int) -> bool:
    with connect(config) as conn:
        row = conn.execute("SELECT resolved FROM field_decisions WHERE id = ?", (decision_id,)).fetchone()
        return bool(row and row["resolved"])


def _decision_action(config: dict, decision_id: int, action: str) -> bool:
    with connect(config) as conn:
        row = conn.execute("SELECT resolved, resolved_action FROM field_decisions WHERE id = ?", (decision_id,)).fetchone()
        return bool(row and row["resolved"] and row["resolved_action"] == action)


def _run_enrich_replaygain(path: Path, apply: bool, report_dir: Path, config: dict, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    from .cli import enrich

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = enrich(
            path,
            apply=apply,
            force=False,
            skip_acoustid_identify=True,
            full=True,
            skip_bpm=True,
            skip_key=True,
            skip_features=True,
            skip_lastfm=True,
            skip_mood=True,
            skip_cover=True,
            skip_lyrics=True,
            skip_metadata_providers=True,
            replaygain=True,
            no_progress=True,
            plain=True,
            config=config,
            explicit_flags={"skip_bpm", "skip_key", "skip_features", "skip_lastfm", "skip_mood", "skip_cover", "skip_lyrics", "skip_metadata_providers", "skip_acoustid_identify", "--replaygain"},
        )
    output = stdout.getvalue()
    error = stderr.getvalue()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "enrich_full_replaygain.log").write_text(output + ("\nSTDERR:\n" + error if error else ""), encoding="utf-8")
    commands.append("enrich full replaygain")
    stdout_chunks.append(output)
    stderr_chunks.append(error)
    if code != 0:
        raise LabFailure("ReplayGain enrich", "enrich --full --replaygain", output + error)
    return output


def _write_safe_metadata(path: Path, values: dict[str, str]) -> None:
    for track in read_tracks(path):
        suffix = track.path.suffix.lower()
        if suffix == ".mp3":
            tags = ID3(track.path)
            for key, value in values.items():
                tags.add(TXXX(encoding=3, desc=key, text=[value]))
            tags.save(track.path)
        elif suffix == ".flac":
            audio = FLAC(track.path)
            for key, value in values.items():
                audio[key] = value
            audio.save()
        else:
            audio = MP4(track.path)
            for key, value in values.items():
                audio[f"----:com.apple.iTunes:{key}"] = [MP4FreeForm(value.encode("utf-8"))]
            audio.save()


def _seed_db_explain_decisions(config: dict, album_path: Path) -> None:
    with connect(config) as conn:
        prefix = str(album_path.resolve(strict=False)).rstrip("/") + "/%"
        row = conn.execute(
            """
            SELECT a.id AS album_id
            FROM files f
            LEFT JOIN tracks t ON t.id = f.track_id
            LEFT JOIN albums a ON a.id = t.album_id
            WHERE f.path LIKE ? AND a.id IS NOT NULL
            ORDER BY a.id
            LIMIT 1
            """,
            (prefix,),
        ).fetchone()
        if not row:
            raise LabFailure("DB explain", "seed db explain", "album not found")
        album_id = int(row["album_id"])
        run_id = record_provider_run(conn, "itunes", "album", album_id, "ok", query="MusicLab Artist Dirty Album")
        record_candidate(conn, run_id, "itunes", "musiclab-itunes-dirty", score=0.92, confidence="high", selected=True, payload_summary={"style": "Fixture"})
        record_field_decision(conn, run_id, "album", album_id, "style", current_value="", candidate_value="Fixture", selected_value="Fixture", provider="itunes", confidence="high", action="write", reason="safe fallback field")
        conflict_run = record_provider_run(conn, "discogs", "album", album_id, "review", query="MusicLab Artist Dirty Album")
        record_field_decision(conn, conflict_run, "album", album_id, "style", current_value="Fixture", candidate_value="Fixture Variant", selected_value="Fixture", provider="discogs", confidence="medium", action="review", reason="conflict with existing style")
        conn.commit()


def _seed_query_language_records(config: dict, library: Path) -> None:
    with connect(config) as conn:
        clean = _first_db_track(conn, library / "MusicLab Artist" / "Clean Album")
        dirty = _first_db_track(conn, library / "MusicLab Artist" / "Dirty Album")
        fallback = _first_db_track(conn, library / "MusicLab Artist" / "Fallback Provider")
        if not clean or not dirty or not fallback:
            raise LabFailure("DB query", "seed query records", "expected query fixture records not found")
        conn.execute("UPDATE albums SET album = ?, albumartist = ?, date = ? WHERE id = ?", ("Get Up", "NewJeans", "2023", clean["album_id"]))
        conn.execute("UPDATE tracks SET title = ?, artist = ?, albumartist = ?, bpm = ?, energy = ?, danceability = ?, key = NULL WHERE id = ?", ("Super Shy", "NewJeans", "NewJeans", 150, 80, 89, clean["track_id"]))
        conn.execute("UPDATE files SET has_cover = 1, has_lyrics = 1, has_synced_lyrics = 1 WHERE id = ?", (clean["file_id"],))
        conn.execute("INSERT OR IGNORE INTO album_tags(album_id, key, value, type, source, confidence, updated_at) VALUES (?, 'genre', 'K-pop', 'genre', 'lab', 'high', ?)", (clean["album_id"], datetime.now().isoformat(timespec="seconds")))
        upsert_audio_features(conn, int(clean["track_id"]), {"bpm": 150, "energy": 80, "danceability": 89, "replaygain_track_gain": -6.0, "replaygain_track_peak": 0.91})

        conn.execute("UPDATE albums SET album = ?, albumartist = ?, date = ?, originaldate = ?, release_type = ?, label = ?, country = ? WHERE id = ?", ("Urn", "Ne Obliviscaris", "2017", "2017-10-27", "Album", "Season Of Mist", "Brazil", dirty["album_id"]))
        conn.execute("UPDATE tracks SET title = ?, artist = ?, albumartist = ?, bpm = ?, energy = ?, danceability = ?, key = NULL WHERE id = ?", ("Libera, Pt. I: Saturnine Spheres", "Ne Obliviscaris", "Ne Obliviscaris", 124, 72, 55, dirty["track_id"]))
        conn.execute("UPDATE files SET has_cover = 1, has_lyrics = 0, has_synced_lyrics = 0 WHERE id = ?", (dirty["file_id"],))
        conn.execute("INSERT OR IGNORE INTO album_tags(album_id, key, value, type, source, confidence, updated_at) VALUES (?, 'style', 'Progressive Metal', 'style', 'lab', 'high', ?)", (dirty["album_id"], datetime.now().isoformat(timespec="seconds")))
        run_id = record_provider_run(conn, "musicbrainz", "album", dirty["album_id"], "warn", query="query language fixture")
        record_field_decision(conn, run_id, "album", dirty["album_id"], "style", provider="musicbrainz", action="write", reason="query fixture")

        upsert_audio_features(conn, int(fallback["track_id"]), {"bpm": 118, "energy": 70, "danceability": 60, "replaygain_track_gain": -5.0, "replaygain_track_peak": 0.9})
        conn.commit()


def _first_db_track(conn, path: Path):
    prefix = str(path.resolve(strict=False)).rstrip("/") + "/%"
    return conn.execute(
        """
        SELECT a.id AS album_id, t.id AS track_id, f.id AS file_id, f.path AS path
        FROM files f
        LEFT JOIN tracks t ON t.id = f.track_id
        LEFT JOIN albums a ON a.id = t.album_id
        WHERE f.path LIKE ? AND t.id IS NOT NULL AND a.id IS NOT NULL
        ORDER BY f.path
        LIMIT 1
        """,
        (prefix,),
    ).fetchone()


def _query_language_check(config: dict, library: Path, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    before_db = _db_stability_counts(config)
    before_files = _file_fingerprints(library)
    status_output = _run_step("db_status", report_dir, lambda: (0, render_status(db_status(config))), commands, stdout_chunks, stderr_chunks)
    _assert_contains(status_output, ["Schema version:", "Albums:", "Tracks:", "Files:", "Last operation:"], "DB status")
    scenarios = [
        ("db_query_free_search", lambda: db_query(config, "NewJeans"), ["Super Shy"]),
        ("db_query_field_artist", lambda: db_query(config, 'artist:"Ne Obliviscaris"'), ["Libera"]),
        ("db_query_field_album", lambda: db_query(config, "album:Urn"), ["Urn"]),
        ("db_query_field_style", lambda: db_query(config, 'style:"Progressive Metal"'), ["Progressive", "Libera"]),
        ("db_query_missing_lyrics", lambda: db_query(config, "missing:lyrics"), ["Libera"]),
        ("db_query_missing_key", lambda: db_query(config, "missing:key"), ["Super Shy", "Libera"]),
        ("db_query_has_cover", lambda: db_query(config, "has:cover"), ["Super Shy"]),
        ("db_query_has_replaygain", lambda: db_query(config, "has:replaygain"), ["Super Shy"]),
        ("db_query_alias_missing_rg", lambda: db_query(config, "missing:rg"), ["Libera"]),
        ("db_query_alias_has_art", lambda: db_query(config, "has:art"), ["Super Shy"]),
        ("db_query_alias_missing_mbids", lambda: db_query(config, "missing:mbids"), ["Missing Fields"]),
        ("db_query_combined_artist_missing", lambda: db_query(config, 'artist:"Ne Obliviscaris" missing:key'), ["Libera"]),
        ("db_query_combined_genre_cover", lambda: db_query(config, "genre:K-pop has:cover"), ["Super Shy"]),
        ("db_query_combined_style_year", lambda: db_query(config, 'style:"Progressive Metal" year:2017'), ["Libera"]),
        ("db_query_negated_missing", lambda: db_query(config, "-missing:cover"), ["Super Shy"]),
        ("db_query_negated_combined", lambda: db_query(config, 'artist:"Ne Obliviscaris" -missing:cover'), ["Libera"]),
        ("db_query_numeric_bpm", lambda: db_query(config, "bpm:>100"), ["Super Shy"]),
        ("db_query_numeric_energy", lambda: db_query(config, "energy:>=70"), ["Super Shy"]),
        ("db_query_numeric_danceability", lambda: db_query(config, "danceability:<90"), ["Super Shy"]),
        ("db_query_scope_albums", lambda: db_query(config, 'style:"Progressive Metal"', target="albums"), ["Album Artist", "Missing"]),
        ("db_query_scope_tracks", lambda: db_query(config, "NewJeans", target="tracks"), ["Title", "Super Shy"]),
        ("db_query_scope_files", lambda: db_query(config, "path:Clean", target="files"), ["Path", "Super Shy"]),
        ("db_query_status_warn", lambda: db_query(config, "status:warn"), ["Libera"]),
        ("db_query_provider_musicbrainz", lambda: db_query(config, "provider:musicbrainz"), ["Libera"]),
        ("db_query_review_true", lambda: db_query(config, "review:true"), ["Libera"]),
    ]
    for name, callback, expected in scenarios:
        output = _run_step(name, report_dir, callback, commands, stdout_chunks, stderr_chunks)
        _assert_contains(output, expected, name)
        if "--apply" in name:
            raise LabFailure("DB query", name, "query scenario unexpectedly included --apply")
    json_output = _run_step("db_query_json", report_dir, lambda: db_query(config, "artist:NewJeans missing:key", output_format="json"), commands, stdout_chunks, stderr_chunks)
    payload = json.loads(json_output)
    if payload.get("status") != "OK" or payload.get("scope") != "tracks" or not isinstance(payload.get("results"), list):
        raise LabFailure("DB query JSON", "db query --format json", json_output)
    if not payload["results"] or payload["results"][0].get("missing") != ["key"]:
        raise LabFailure("DB query JSON", "db query --format json", json_output)
    after_db = _db_stability_counts(config)
    after_files = _file_fingerprints(library)
    if before_db != after_db:
        raise LabFailure("DB query stability", "db query read-only", f"before={before_db} after={after_db}")
    if before_files != after_files:
        raise LabFailure("DB query stability", "db query file writes", "query changed MusicLab audio files")
    if str(library).startswith("/mnt/sdcard/Music/Biblioteca de Musicas"):
        raise LabFailure("DB query safety", "real library guard", str(library))
    return "status, combined fields, aliases, negation, scopes, JSON, read-only"


def _jobs_check(lab: Path, config: dict, album_path: Path, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    store = JobStore(config)
    audit_job = store.create_job(JobOptions("audit", target=str(album_path), options={"path": str(album_path), "token": "secret"}))
    store.mark_running(audit_job)
    result = run_audit_service(AuditOptions(path=album_path, config=config, verbose=True))
    store.save_workflow_result(audit_job, result)
    stored = store.get_result(audit_job)
    if stored is None or not stored.steps or stored.job.get("result") is None:
        raise LabFailure("Jobs", "job creation", "job result or steps missing")
    if "secret" in json.dumps(stored.job, ensure_ascii=False).casefold():
        raise LabFailure("Jobs", "job sanitization", json.dumps(stored.job, ensure_ascii=False))

    list_output = _run_cli_command("jobs_list", ["jobs", "list"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    if audit_job not in list_output:
        raise LabFailure("Jobs", "jobs list", list_output)
    status_output = _run_cli_command("jobs_status", ["jobs", "status", audit_job], config, report_dir, commands, stdout_chunks, stderr_chunks)
    if "Steps:" not in status_output or "Read tags" not in status_output:
        raise LabFailure("Jobs", "jobs status", status_output)
    status_json = _run_cli_command("jobs_status_json", ["jobs", "status", audit_job, "--format", "json"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    payload = json.loads(status_json)
    raw = json.dumps(payload, ensure_ascii=False).casefold()
    if any(word in raw for word in ("password=", "token=secret", "fingerprint data", "full lyric")):
        raise LabFailure("Jobs", "jobs json sanitization", status_json)

    cancel_job = store.create_job(JobOptions("job-cancel", target=str(album_path)))
    cancel_context = JobContext(store, cancel_job)
    cancel_context.start_step("Safe point")
    cancel_output = _run_cli_command("jobs_cancel", ["jobs", "cancel", cancel_job], config, report_dir, commands, stdout_chunks, stderr_chunks)
    try:
        cancel_context.check_canceled()
    except Exception as exc:
        if exc.__class__.__name__ != "JobCanceled":
            raise
    else:
        raise LabFailure("Jobs", "job cancellation", cancel_output)
    if store.get_job(cancel_job)["status"] != JobStatus.CANCELED.value:
        raise LabFailure("Jobs", "job cancellation status", cancel_output)

    code, unsupported = resume_job(config, audit_job)
    _write_expected_report(report_dir, "jobs_resume_unsupported", unsupported, commands, stdout_chunks, stderr_chunks)
    if code == 0 or "not resumable" not in unsupported:
        raise LabFailure("Jobs", "resume unsupported", unsupported)
    resumable_job = store.create_job(JobOptions("job-test-resume", target=str(album_path), resumable=True))
    store.upsert_step(resumable_job, StepResult(1, 2, "Prepare", Status.OK, "done"))
    resume_output = _run_cli_command("jobs_resume", ["jobs", "resume", resumable_job], config, report_dir, commands, stdout_chunks, stderr_chunks)
    if "resumed" not in resume_output or [step["name"] for step in store.get_steps(resumable_job)] != ["Prepare", "Resume"]:
        raise LabFailure("Jobs", "resume supported", resume_output)

    prune_dry = _run_cli_command("jobs_prune_dry", ["jobs", "prune"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    before = len(store.list_jobs(limit=100))
    prune_apply = _run_cli_command("jobs_prune_apply", ["jobs", "prune", "--apply"], config, report_dir, commands, stdout_chunks, stderr_chunks)
    after = len(store.list_jobs(limit=100))
    if after > before or "Jobs prune" not in prune_dry or "Jobs prune" not in prune_apply:
        raise LabFailure("Jobs", "prune", prune_dry + prune_apply)

    unsafe_context = JobContext(store, store.create_job(JobOptions("unsafe-apply", target=str(lab.parent), mode="apply")), safety_context=SafetyContext(target_path=lab.parent))
    try:
        unsafe_context.safety_context.check_apply_allowed(True, context="jobs safety")
    except Exception as exc:
        if exc.__class__.__name__ != "SafetyError":
            raise
    else:
        raise LabFailure("Jobs", "safety", "apply outside MusicLab was not blocked")
    return "created/listed/status/canceled/resumed/pruned/safety"


def _core_api_check(lab: Path, config: dict, album_path: Path, single: Path, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    core = NoqlenForgeCore(config=config, automated_validation=True)
    manifest = core.capabilities()
    if manifest.get("schema") != "core-api/v1" or not manifest.get("workflows", {}).get("audit", {}).get("implemented"):
        raise LabFailure("Core API", "core.capabilities", json.dumps(manifest, ensure_ascii=False))

    audit = core.audit(album_path)
    audit_payload = json.loads(workflow_result_to_json(audit))
    if audit_payload.get("workflow") != "audit" or audit_payload.get("summary", {}).get("files", 0) < 1:
        raise LabFailure("Core API", "core.audit", workflow_result_to_json(audit))

    before_single = _file_fingerprints(single)
    lyrics_dry = core.lyrics(single, apply=False, sources=["local"], save_txt=False)
    enrich_dry = core.enrich(album_path, full=True, apply=False)
    import_dry = core.import_music(lab / "Incoming", library=lab / "Library", apply=False)
    after_single = _file_fingerprints(single)
    if before_single != after_single or lyrics_dry.mode != "dry-run" or enrich_dry.status != Status.FAIL or import_dry.mode != "dry-run":
        raise LabFailure("Core API dry-run", "core dry-run workflows", "dry-run changed files or returned unexpected mode")

    outside = core.lyrics(lab.parent / "outside-core-api", apply=True, sources=["local"])
    if outside.status != Status.FAIL or not outside.errors or "outside MusicLab" not in outside.errors[0]:
        raise LabFailure("Core API safety", "core.lyrics apply outside MusicLab", workflow_result_to_json(outside))

    lyrics_apply = core.lyrics(single, apply=True, sources=["local"], save_txt=False)
    if lyrics_apply.status == Status.FAIL:
        raise LabFailure("Core API apply", "core.lyrics apply inside MusicLab", workflow_result_to_json(lyrics_apply))

    created = core.create_job("audit", album_path, {"token": "secret", "verbose": True})
    job_id = str(created.summary.get("job_id", ""))
    status = core.jobs_status(job_id)
    listed = core.jobs_list(limit=50)
    if not job_id or status.status != Status.OK or listed.counts.get("jobs", 0) < 1:
        raise LabFailure("Core API jobs", "core jobs", workflow_result_to_json(status) + workflow_result_to_json(listed))
    raw_status = workflow_result_to_json(status).casefold()
    if "secret" in raw_status or "full lyric" in raw_status or "fingerprint data" in raw_status:
        raise LabFailure("Core API JSON safety", "core jobs status json", workflow_result_to_json(status))
    canceled = core.cancel_job(job_id)
    if canceled.status != Status.OK:
        raise LabFailure("Core API jobs", "core.cancel_job", workflow_result_to_json(canceled))

    _write_expected_report(report_dir, "core_api", workflow_result_to_json(audit), commands, stdout_chunks, stderr_chunks)
    return "manifest/audit/dry-run/apply safety/jobs/json"


def _export_check(lab: Path, config: dict, library: Path, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    export_dir = lab / "Exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    before_db = _db_stability_counts(config)
    before_files = _file_fingerprints(library)

    query_csv = export_dir / "query.csv"
    query_summary = _run_step("export_query_csv", report_dir, lambda: export_data(config, 'artist:"NewJeans"', export_format="csv", output=query_csv), commands, stdout_chunks, stderr_chunks)
    _assert_contains(query_summary, ["Export: query", "Format: CSV", "Status: OK"], "Export query CSV")
    csv_text = query_csv.read_text(encoding="utf-8")
    if "path,title,artist" not in csv_text or "Super Shy" not in csv_text:
        raise LabFailure("Export query CSV", "noqlen-forge export query csv", csv_text)
    csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not csv_rows or csv_rows[0].get("title") != "Super Shy" or not csv_rows[0].get("path"):
        raise LabFailure("Export query CSV parse", "noqlen-forge export query csv", csv_text)

    query_json = _run_step("export_query_json", report_dir, lambda: export_data(config, 'artist:"NewJeans"', export_format="json"), commands, stdout_chunks, stderr_chunks)
    query_payload = json.loads(query_json)
    if query_payload.get("type") != "query" or query_payload.get("count", 0) < 1 or not isinstance(query_payload.get("results"), list):
        raise LabFailure("Export query JSON", "noqlen-forge export query json", query_json)
    export_service_json = run_export_service(build_export_options(config, 'artist:"NewJeans"', export_format="json"))
    export_service_payload = json.loads(export_service_json.details.get("output_text", "{}"))
    if export_service_payload.get("count") != query_payload.get("count"):
        raise LabFailure("Export service", "export service query json", workflow_result_to_json(export_service_json))

    missing_csv = export_dir / "missing-lyrics.csv"
    _run_step("export_missing_csv", report_dir, lambda: export_data(config, missing="lyrics", export_format="csv", output=missing_csv), commands, stdout_chunks, stderr_chunks)
    missing_text = missing_csv.read_text(encoding="utf-8")
    if "missing_fields" not in missing_text or "lyrics" not in missing_text:
        raise LabFailure("Export missing CSV", "noqlen-forge export --missing lyrics", missing_text)
    missing_rows = list(csv.DictReader(io.StringIO(missing_text)))
    if not missing_rows or not any("lyrics" in (row.get("missing_fields") or "") for row in missing_rows):
        raise LabFailure("Export missing CSV parse", "noqlen-forge export --missing lyrics", missing_text)
    missing_json = _run_step("export_missing_json", report_dir, lambda: export_data(config, missing="lyrics", export_format="json", output=export_dir / "missing-lyrics.json"), commands, stdout_chunks, stderr_chunks)
    if "Export: missing" not in missing_json:
        raise LabFailure("Export missing JSON", "noqlen-forge export --missing lyrics --format json", missing_json)
    missing_payload = json.loads((export_dir / "missing-lyrics.json").read_text(encoding="utf-8"))
    if missing_payload.get("type") != "missing" or not isinstance(missing_payload.get("albums"), list) or missing_payload.get("count", 0) < 1:
        raise LabFailure("Export missing JSON", "noqlen-forge export --missing lyrics --format json", json.dumps(missing_payload))

    duplicates_json = _run_step("export_duplicates_json", report_dir, lambda: export_data(config, duplicates=True, export_format="json", output=export_dir / "duplicates.json"), commands, stdout_chunks, stderr_chunks)
    if "Export: duplicates" not in duplicates_json:
        raise LabFailure("Export duplicates JSON", "noqlen-forge export --duplicates", duplicates_json)
    duplicates_payload = json.loads((export_dir / "duplicates.json").read_text(encoding="utf-8"))
    if duplicates_payload.get("type") != "duplicates" or not isinstance(duplicates_payload.get("groups"), list):
        raise LabFailure("Export duplicates JSON", "noqlen-forge export --duplicates", json.dumps(duplicates_payload))

    reviews_json = _run_step("export_reviews_json", report_dir, lambda: export_data(config, reviews=True, export_format="json", output=export_dir / "reviews.json"), commands, stdout_chunks, stderr_chunks)
    if "Export: reviews" not in reviews_json:
        raise LabFailure("Export reviews JSON", "noqlen-forge export --reviews", reviews_json)
    reviews_payload = json.loads((export_dir / "reviews.json").read_text(encoding="utf-8"))
    if reviews_payload.get("type") != "reviews" or "pending" not in reviews_payload or "resolved" not in reviews_payload:
        raise LabFailure("Export reviews JSON", "noqlen-forge export --reviews", json.dumps(reviews_payload))

    library_json = _run_step("export_library_json", report_dir, lambda: export_data(config, library=True, export_format="json", output=export_dir / "library.json"), commands, stdout_chunks, stderr_chunks)
    if "Export: library" not in library_json:
        raise LabFailure("Export library JSON", "noqlen-forge export --library", library_json)
    library_payload = json.loads((export_dir / "library.json").read_text(encoding="utf-8"))
    summary = library_payload.get("summary", {})
    if library_payload.get("type") != "library" or min(int(summary.get(key, 0)) for key in ("albums", "tracks", "files")) < 1:
        raise LabFailure("Export library JSON", "noqlen-forge export --library", json.dumps(library_payload))

    overwrite_code, overwrite_output = export_data(config, "NewJeans", export_format="csv", output=query_csv)
    if overwrite_code == 0 or "Use --force" not in overwrite_output:
        raise LabFailure("Export overwrite safety", "noqlen-forge export --output existing", overwrite_output)
    forced = _run_step("export_force_overwrite", report_dir, lambda: export_data(config, "NewJeans", export_format="csv", output=query_csv, force=True), commands, stdout_chunks, stderr_chunks)
    _assert_contains(forced, ["Status: OK"], "Export force overwrite")

    after_db = _db_stability_counts(config)
    after_files = _file_fingerprints(library)
    if before_db != after_db:
        raise LabFailure("Export DB stability", "noqlen-forge export", f"before={before_db} after={after_db}")
    if before_files != after_files:
        raise LabFailure("Export file stability", "noqlen-forge export", "export changed MusicLab audio files")
    if str(library).startswith("/mnt/sdcard/Music/Biblioteca de Musicas"):
        raise LabFailure("Export safety", "real library guard", str(library))
    unsafe_code, unsafe_output = export_data(config, "NewJeans", export_format="json", output=Path("/mnt/sdcard/Music/Biblioteca de Musicas/export.json"))
    if unsafe_code == 0 or "dangerous path" not in unsafe_output:
        raise LabFailure("Export safety", "dangerous output guard", unsafe_output)
    combined = "\n".join([query_json, missing_text, json.dumps(duplicates_payload), json.dumps(reviews_payload), json.dumps(library_payload)])
    lowered = combined.casefold()
    if "musiclab line one\nmusiclab line two" in lowered or '"fingerprint"' in lowered or "full fingerprint" in lowered or "secret=" in lowered:
        raise LabFailure("Export safe output", "redaction", "sensitive output leaked")
    return "query CSV/JSON, missing CSV/JSON, duplicates, reviews, library, overwrite safety, read-only"


def _smart_playlist_check(lab: Path, config: dict, library: Path, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    export_dir = lab / "Exports" / "SmartPlaylists"
    export_dir.mkdir(parents=True, exist_ok=True)
    before_files = _file_fingerprints(library)

    before_count = _smart_playlist_count(config)
    dry_create = _run_step("smart_playlist_create_dry_run", report_dir, lambda: smart_create(config, "Lab Favorites", 'artist:"NewJeans"', sort="artist", limit=10), commands, stdout_chunks, stderr_chunks)
    if _smart_playlist_count(config) != before_count or "DRY-RUN" not in dry_create:
        raise LabFailure("Smart playlist create dry-run", "noqlen-forge playlist smart create", dry_create)
    create = _run_step("smart_playlist_create_apply", report_dir, lambda: smart_create(config, "Lab Favorites", 'artist:"NewJeans"', sort="artist", limit=10, apply=True), commands, stdout_chunks, stderr_chunks)
    _assert_contains(create, ["Mode: APPLY", "saved", "Status: OK"], "Smart playlist create apply")
    if _smart_playlist_count(config) != before_count + 1:
        raise LabFailure("Smart playlist create DB", "noqlen-forge playlist smart create --apply", str(_smart_playlist_count(config)))

    listing = _run_step("smart_playlist_list", report_dir, lambda: smart_list(config), commands, stdout_chunks, stderr_chunks)
    show = _run_step("smart_playlist_show", report_dir, lambda: smart_show(config, "Lab Favorites"), commands, stdout_chunks, stderr_chunks)
    _assert_contains(listing, ["Smart playlists:", "Lab Favorites", "Tracks now"], "Smart playlist list")
    _assert_contains(show, ["Query:", 'artist:"NewJeans"', "Tracks now", "Status: OK"], "Smart playlist show")

    m3u = export_dir / "lab-favorites.m3u8"
    export_m3u = _run_step("smart_playlist_export_m3u8", report_dir, lambda: smart_export(config, "Lab Favorites", output=m3u), commands, stdout_chunks, stderr_chunks)
    _assert_contains(export_m3u, ["Smart playlist export", "Format: M3U8", "Status: OK"], "Smart playlist export M3U8")
    m3u_text = m3u.read_text(encoding="utf-8")
    if not m3u_text.startswith("#EXTM3U") or "Super Shy" not in m3u_text:
        raise LabFailure("Smart playlist M3U8", "noqlen-forge playlist smart export", m3u_text)
    blocked_code, blocked = smart_export(config, "Lab Favorites", output=m3u)
    if blocked_code == 0 or "Use --force" not in blocked:
        raise LabFailure("Smart playlist overwrite", "noqlen-forge playlist smart export existing", blocked)

    json_output = _run_step("smart_playlist_export_json", report_dir, lambda: smart_export(config, "Lab Favorites", export_format="json"), commands, stdout_chunks, stderr_chunks)
    json_payload = json.loads(json_output)
    if json_payload.get("type") != "smart_playlist" or json_payload.get("count", 0) < 1 or not isinstance(json_payload.get("tracks"), list):
        raise LabFailure("Smart playlist JSON", "noqlen-forge playlist smart export --format json", json_output)
    service_json = run_playlist_export_service(PlaylistExportOptions(config, "Lab Favorites", export_format="json"))
    service_payload = workflow_result_to_dict(service_json)
    if service_payload.get("command") != "playlist smart export" or service_payload.get("counts", {}).get("tracks") != json_payload.get("count"):
        raise LabFailure("Smart playlist service", "playlist smart export service", workflow_result_to_json(service_json))
    service_artifact = run_playlist_export_service(PlaylistExportOptions(config, "Lab Favorites", export_format="m3u8", output=export_dir / "structured-favorites.m3u8", force=True))
    artifact_payload = workflow_result_to_dict(service_artifact)
    if not artifact_payload.get("artifacts") or artifact_payload["artifacts"][0].get("type") != "playlist" or artifact_payload.get("safe_details", {}).get("definition", {}).get("name") != "Lab Favorites":
        raise LabFailure("Smart playlist structured artifact", "playlist smart export service artifact", workflow_result_to_json(service_artifact))
    service_code, service_output = render_playlist_export_result(service_json, name="Lab Favorites")
    if service_code != 0 or json.loads(service_output).get("count") != json_payload.get("count"):
        raise LabFailure("Smart playlist service parity", "playlist smart export service", service_output)
    csv_output = _run_step("smart_playlist_export_csv", report_dir, lambda: smart_export(config, "Lab Favorites", export_format="csv"), commands, stdout_chunks, stderr_chunks)
    csv_rows = list(csv.DictReader(io.StringIO(csv_output)))
    if not csv_rows or csv_rows[0].get("artist") != "NewJeans":
        raise LabFailure("Smart playlist CSV", "noqlen-forge playlist smart export --format csv", csv_output)

    library_mode = _run_step("smart_playlist_export_library_mode", report_dir, lambda: smart_export(config, "Lab Favorites", export_format="m3u8", path_mode="library", library_root=library), commands, stdout_chunks, stderr_chunks)
    if "#EXTM3U" not in library_mode or str(library) in library_mode:
        raise LabFailure("Smart playlist library paths", "noqlen-forge playlist smart export --path-mode library", library_mode)

    smart_create(config, "Lab Missing Lyrics", "missing:lyrics", apply=True)
    refresh_path = export_dir / "missing-lyrics.m3u8"
    _run_step("smart_playlist_refresh_initial", report_dir, lambda: smart_refresh(config, "Lab Missing Lyrics", output=refresh_path), commands, stdout_chunks, stderr_chunks)
    before_refresh = refresh_path.read_text(encoding="utf-8")
    with connect(config) as conn:
        conn.execute("UPDATE files SET has_lyrics = 1")
        conn.commit()
    refresh = _run_step("smart_playlist_refresh_changed", report_dir, lambda: smart_refresh(config, "Lab Missing Lyrics", output=refresh_path, force=True), commands, stdout_chunks, stderr_chunks)
    if before_refresh == refresh_path.read_text(encoding="utf-8") or "Status: OK" not in refresh:
        raise LabFailure("Smart playlist refresh", "noqlen-forge playlist smart refresh --force", refresh)

    smart_create(config, "Lab Rated", "rating:>=4 starred:true", apply=True)
    rated = json.loads(_run_step("smart_playlist_rating_query", report_dir, lambda: smart_export(config, "Lab Rated", export_format="json"), commands, stdout_chunks, stderr_chunks))
    if rated.get("count", 0) < 1:
        raise LabFailure("Smart playlist rating query", "noqlen-forge playlist smart export rated", json.dumps(rated))
    smart_create(config, "Lab Covered", "has:cover", apply=True)
    covered = json.loads(_run_step("smart_playlist_has_query", report_dir, lambda: smart_export(config, "Lab Covered", export_format="json"), commands, stdout_chunks, stderr_chunks))
    if covered.get("count", 0) < 1:
        raise LabFailure("Smart playlist has query", "noqlen-forge playlist smart export has:cover", json.dumps(covered))

    rename_dry = _run_step("smart_playlist_rename_dry_run", report_dir, lambda: smart_rename(config, "Lab Favorites", "Lab Favorites Renamed"), commands, stdout_chunks, stderr_chunks)
    if smart_show(config, "Lab Favorites Renamed")[0] == 0 or "DRY-RUN" not in rename_dry:
        raise LabFailure("Smart playlist rename dry-run", "noqlen-forge playlist smart rename", rename_dry)
    rename_apply = _run_step("smart_playlist_rename_apply", report_dir, lambda: smart_rename(config, "Lab Favorites", "Lab Favorites Renamed", apply=True), commands, stdout_chunks, stderr_chunks)
    _assert_contains(rename_apply, ["Mode: APPLY", "renamed", "Status: OK"], "Smart playlist rename apply")
    delete_dry = _run_step("smart_playlist_delete_dry_run", report_dir, lambda: smart_delete(config, "Lab Favorites Renamed"), commands, stdout_chunks, stderr_chunks)
    if _smart_playlist_count(config) < before_count + 1 or "DRY-RUN" not in delete_dry:
        raise LabFailure("Smart playlist delete dry-run", "noqlen-forge playlist smart delete", delete_dry)
    delete_apply = _run_step("smart_playlist_delete_apply", report_dir, lambda: smart_delete(config, "Lab Favorites Renamed", apply=True), commands, stdout_chunks, stderr_chunks)
    _assert_contains(delete_apply, ["Mode: APPLY", "deleted", "Status: OK"], "Smart playlist delete apply")
    if not m3u.exists():
        raise LabFailure("Smart playlist delete files", "noqlen-forge playlist smart delete --apply", "exported playlist was removed")

    unsafe_code, unsafe_output = smart_export(config, "Lab Covered", output=Path("/root/smart-playlist.m3u8"))
    if unsafe_code == 0 or "outside MusicLab or /tmp" not in unsafe_output:
        raise LabFailure("Smart playlist safety", "dangerous automated output", unsafe_output)
    if before_files != _file_fingerprints(library):
        raise LabFailure("Smart playlist file stability", "noqlen-forge playlist smart", "smart playlist changed MusicLab audio files")
    return "create dry-run/apply, list/show, M3U8/JSON/CSV, refresh, rename/delete, rating/starred, missing/has, safety"


class _FakeNavidromeClient:
    def __init__(self, items: list[RatingItem], ping_error: str = "", playlists: list[dict] | None = None):
        self.config = NavidromeConfig(base_url="http://127.0.0.1:4533", username="musiclab", password="musiclab-password")
        self.items = items
        self.ping_error = ping_error
        self.write_calls: list[tuple] = []
        self.forbidden_calls: list[str] = []
        self.playlists = playlists or []
        self.playlist_entries = {str(item["id"]): list(item.get("song_ids", [])) for item in self.playlists}

    def ping(self) -> dict:
        if self.ping_error:
            raise RuntimeError(self.ping_error)
        return {"subsonic-response": {"status": "ok"}}

    def iter_rating_items(self) -> list[RatingItem]:
        return self.items

    def set_rating(self, song_id: str, rating: int) -> dict:
        self.write_calls.append(("setRating", song_id, rating))
        for item in self.items:
            if item.navidrome_id == song_id:
                item.rating = rating
        return {"subsonic-response": {"status": "ok"}}

    def star(self, song_id: str) -> dict:
        self.write_calls.append(("star", song_id))
        for item in self.items:
            if item.navidrome_id == song_id:
                item.starred = True
        return {"subsonic-response": {"status": "ok"}}

    def unstar(self, song_id: str) -> dict:
        self.write_calls.append(("unstar", song_id))
        for item in self.items:
            if item.navidrome_id == song_id:
                item.starred = False
        return {"subsonic-response": {"status": "ok"}}

    def get_playlists(self) -> dict:
        return {"subsonic-response": {"status": "ok", "playlists": {"playlist": [{"id": item["id"], "name": item["name"], "songCount": len(self.playlist_entries.get(str(item["id"]), [])), "owner": item.get("owner", "musiclab")} for item in self.playlists]}}}

    def get_playlist(self, playlist_id: str) -> dict:
        entries = []
        for song_id in self.playlist_entries.get(str(playlist_id), []):
            entry = {"id": song_id}
            for item in self.items:
                if item.navidrome_id == song_id:
                    entry.update({"title": item.title, "artist": item.artist, "album": item.album, "albumArtist": item.albumartist, "duration": item.duration, "track": item.track, "musicBrainzTrackId": item.mb_track_id, "musicBrainzReleaseTrackId": item.mb_release_track_id, "acoustId": item.acoustid_id, "isrc": item.isrc, "path": item.path})
            entries.append(entry)
        playlist = next((item for item in self.playlists if str(item["id"]) == str(playlist_id)), {"id": playlist_id, "name": str(playlist_id)})
        return {"subsonic-response": {"status": "ok", "playlist": {"id": playlist_id, "name": playlist.get("name", str(playlist_id)), "owner": playlist.get("owner", "musiclab"), "entry": entries}}}

    def search3(self, query: str, *, song_count: int = 20) -> dict:
        terms = query.casefold().split()
        songs = []
        for item in self.items:
            haystack = f"{item.artist} {item.title} {item.album}".casefold()
            if all(term in haystack for term in terms):
                songs.append({"id": item.navidrome_id, "title": item.title, "artist": item.artist, "album": item.album, "albumArtist": item.albumartist, "duration": item.duration, "track": item.track, "musicBrainzTrackId": item.mb_track_id, "musicBrainzReleaseTrackId": item.mb_release_track_id, "acoustId": item.acoustid_id, "isrc": item.isrc, "path": item.path})
        return {"subsonic-response": {"status": "ok", "searchResult3": {"song": songs[:song_count]}}}

    def get_song(self, song_id: str) -> dict:
        for item in self.items:
            if item.navidrome_id == song_id:
                return {"subsonic-response": {"status": "ok", "song": {"id": item.navidrome_id, "title": item.title, "artist": item.artist, "album": item.album, "albumArtist": item.albumartist, "duration": item.duration, "track": item.track, "musicBrainzTrackId": item.mb_track_id, "musicBrainzReleaseTrackId": item.mb_release_track_id, "acoustId": item.acoustid_id, "isrc": item.isrc, "path": item.path}}}
        raise RuntimeError("missing fake song")

    def create_playlist(self, name: str, song_ids: list[str]) -> dict:
        self.write_calls.append(("createPlaylist", name, list(song_ids)))
        playlist_id = f"lab-playlist-{len(self.playlists) + 1}"
        self.playlists.append({"id": playlist_id, "name": name})
        self.playlist_entries[playlist_id] = list(song_ids)
        return {"subsonic-response": {"status": "ok"}}

    def update_playlist(self, playlist_id: str, song_ids: list[str], *, name: str | None = None) -> dict:
        self.write_calls.append(("updatePlaylist", playlist_id, list(song_ids), name))
        self.playlist_entries[str(playlist_id)] = list(song_ids)
        return {"subsonic-response": {"status": "ok"}}

    def delete_playlist(self, playlist_id: str) -> dict:
        self.forbidden_calls.append("deletePlaylist")
        raise RuntimeError("deletePlaylist is forbidden in MusicLab")


def _navidrome_check(lab: Path, config: dict, library: Path, report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str]) -> str:
    export_dir = lab / "Exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    with connect(config) as conn:
        clean = _first_db_track(conn, library / "MusicLab Artist" / "Clean Album")
        dirty = _first_db_track(conn, library / "MusicLab Artist" / "Dirty Album")
    if not clean or not dirty:
        raise LabFailure("Navidrome", "seed navidrome matches", "expected matching tracks not found")
    with connect(config) as conn:
        conn.execute("UPDATE tracks SET mb_track_id = ? WHERE id = ?", ("lab-navidrome-mb-track", clean["track_id"]))
        conn.execute("UPDATE tracks SET title = ?, artist = ? WHERE id = ?", ("Navidrome Fallback", "MusicLab Artist", dirty["track_id"]))
        conn.execute("UPDATE files SET duration = ? WHERE id = ?", (111.0, dirty["file_id"]))
        conn.commit()
    items = [
        RatingItem(navidrome_id="nav-strong", title="Super Shy", artist="NewJeans", mb_track_id="lab-navidrome-mb-track", rating=5, starred=True, play_count=9, path=str(clean["path"])),
        RatingItem(navidrome_id="nav-fallback", title="Navidrome Fallback", artist="MusicLab Artist", duration=111.0, rating=4, play_count=3),
        RatingItem(navidrome_id="nav-unmatched", title="Unmatched Favorite", artist="No Match", rating=3, starred=True),
    ]
    client = _FakeNavidromeClient(items)
    before = _player_backup_counts(config)
    dry_run = _run_step("navidrome_backup_dry_run", report_dir, lambda: navidrome_ratings_backup(config, apply=False, client=client), commands, stdout_chunks, stderr_chunks)
    if _player_backup_counts(config) != before or "Mode: DRY-RUN" not in dry_run:
        raise LabFailure("Navidrome dry-run", "noqlen-forge navidrome ratings backup", dry_run)
    apply_output = _run_step("navidrome_backup_apply", report_dir, lambda: navidrome_ratings_backup(config, apply=True, client=client), commands, stdout_chunks, stderr_chunks)
    _assert_contains(apply_output, ["Mode: APPLY", "Matched: 2", "Unmatched: 1", "Status: WARN"], "Navidrome backup apply")
    counts = _player_backup_counts(config)
    if counts.get("accounts") != 1 or counts.get("backups") != 3 or counts.get("runs") != 1:
        raise LabFailure("Navidrome backup DB", "noqlen-forge navidrome ratings backup --apply", str(counts))
    repeat = _run_step("navidrome_backup_apply_repeat", report_dir, lambda: navidrome_ratings_backup(config, apply=True, client=client), commands, stdout_chunks, stderr_chunks)
    repeat_counts = _player_backup_counts(config)
    if repeat_counts.get("backups") != 3 or repeat_counts.get("runs") != 2:
        raise LabFailure("Navidrome idempotency", "noqlen-forge navidrome ratings backup --apply repeat", repeat + str(repeat_counts))
    status = _run_step("navidrome_status", report_dir, lambda: navidrome_ratings_status(config), commands, stdout_chunks, stderr_chunks)
    with connect(config) as conn:
        conn.execute("UPDATE files SET path = ? WHERE id = ?", (str(library / "MusicLab Artist" / "Moved Navidrome" / "Super Shy.flac"), clean["file_id"]))
        conn.commit()
    before_diff_counts = _player_backup_counts(config)
    diff = _run_step("navidrome_diff", report_dir, lambda: navidrome_ratings_diff(config, backup_only=True), commands, stdout_chunks, stderr_chunks)
    _assert_contains(diff, ["Unmatched backup: 1", "Library tracks without rating:", "Moved paths matched by identity: 1", "Status: WARN"], "Navidrome backup-only diff")
    server_client = _FakeNavidromeClient([
        RatingItem(navidrome_id="nav-strong", title="Super Shy", artist="NewJeans", mb_track_id="lab-navidrome-mb-track", rating=4, starred=False),
        RatingItem(navidrome_id="nav-new", title="New Server Favorite", artist="MusicLab Artist", rating=5, starred=True),
    ])
    server_diff = _run_step("navidrome_diff_server", report_dir, lambda: navidrome_ratings_diff(config, server=True, client=server_client), commands, stdout_chunks, stderr_chunks)
    _assert_contains(server_diff, ["Changed ratings: 1", "New on server: 1", "Missing on server: 2", "Status: WARN"], "Navidrome server diff")
    json_diff = export_dir / "navidrome-ratings-diff.json"
    csv_diff = export_dir / "navidrome-ratings-diff.csv"
    _run_step("navidrome_diff_json", report_dir, lambda: navidrome_ratings_diff(config, backup_only=True, output_format="json", output=json_diff), commands, stdout_chunks, stderr_chunks)
    _run_step("navidrome_diff_csv", report_dir, lambda: navidrome_ratings_diff(config, backup_only=True, output_format="csv", output=csv_diff), commands, stdout_chunks, stderr_chunks)
    diff_payload = json.loads(json_diff.read_text(encoding="utf-8"))
    diff_rows = list(csv.DictReader(io.StringIO(csv_diff.read_text(encoding="utf-8"))))
    if diff_payload.get("summary", {}).get("moved_paths_matched") != 1 or not diff_rows or "diff_type" not in diff_rows[0]:
        raise LabFailure("Navidrome diff formats", "noqlen-forge navidrome ratings diff", "invalid JSON/CSV diff output")
    if _player_backup_counts(config) != before_diff_counts:
        raise LabFailure("Navidrome diff DB stability", "noqlen-forge navidrome ratings diff", "diff altered backup tables")
    restore_dry_client = _FakeNavidromeClient([
        RatingItem(navidrome_id="nav-strong", title="Super Shy", artist="NewJeans", mb_track_id="lab-navidrome-mb-track", rating=None, starred=False),
        RatingItem(navidrome_id="nav-fallback-current", title="Navidrome Fallback", artist="MusicLab Artist", duration=111.0, rating=None, starred=False),
    ])
    restore_dry = _run_step("navidrome_restore_dry_run", report_dir, lambda: navidrome_ratings_restore(config, client=restore_dry_client), commands, stdout_chunks, stderr_chunks)
    if restore_dry_client.write_calls or "Mode: DRY-RUN" not in restore_dry or "would set 1 ratings" not in restore_dry:
        raise LabFailure("Navidrome restore dry-run", "noqlen-forge navidrome ratings restore", restore_dry)
    restore_apply_client = _FakeNavidromeClient([
        RatingItem(navidrome_id="nav-strong", title="Super Shy", artist="NewJeans", mb_track_id="lab-navidrome-mb-track", rating=None, starred=False),
        RatingItem(navidrome_id="nav-fallback-current", title="Navidrome Fallback", artist="MusicLab Artist", duration=111.0, rating=None, starred=False),
    ])
    restore_apply = _run_step("navidrome_restore_apply", report_dir, lambda: navidrome_ratings_restore(config, apply=True, allow_medium_confidence=True, client=restore_apply_client), commands, stdout_chunks, stderr_chunks)
    if ("setRating", "nav-strong", 5) not in restore_apply_client.write_calls or ("star", "nav-strong") not in restore_apply_client.write_calls or ("setRating", "nav-fallback-current", 4) not in restore_apply_client.write_calls:
        raise LabFailure("Navidrome restore apply", "noqlen-forge navidrome ratings restore --apply", restore_apply + str(restore_apply_client.write_calls))
    restore_second = _run_step("navidrome_restore_idempotent", report_dir, lambda: navidrome_ratings_restore(config, apply=True, allow_medium_confidence=True, client=restore_apply_client), commands, stdout_chunks, stderr_chunks)
    if "Ratings restored: 0" not in restore_second:
        raise LabFailure("Navidrome restore idempotent", "noqlen-forge navidrome ratings restore --apply repeat", restore_second)
    conflict_client = _FakeNavidromeClient([RatingItem(navidrome_id="nav-strong", title="Super Shy", artist="NewJeans", mb_track_id="lab-navidrome-mb-track", rating=1, starred=True)])
    restore_conflict = _run_step("navidrome_restore_conflict", report_dir, lambda: navidrome_ratings_restore(config, client=conflict_client), commands, stdout_chunks, stderr_chunks)
    if "Status: REVIEW" not in restore_conflict or "conflicts: 1" not in restore_conflict:
        raise LabFailure("Navidrome restore conflicts", "noqlen-forge navidrome ratings restore conflict", restore_conflict)
    preserve_client = _FakeNavidromeClient([RatingItem(navidrome_id="nav-strong", title="Super Shy", artist="NewJeans", mb_track_id="lab-navidrome-mb-track", rating=1, starred=True)])
    preserve = _run_step("navidrome_restore_preserve", report_dir, lambda: navidrome_ratings_restore(config, apply=True, preserve_server=True, client=preserve_client), commands, stdout_chunks, stderr_chunks)
    if preserve_client.write_calls or "Ratings restored: 0" not in preserve:
        raise LabFailure("Navidrome restore preserve", "noqlen-forge navidrome ratings restore --preserve-server", preserve + str(preserve_client.write_calls))
    restore_json = export_dir / "navidrome-ratings-restore.json"
    restore_csv = export_dir / "navidrome-ratings-restore.csv"
    _run_step("navidrome_restore_json", report_dir, lambda: navidrome_ratings_restore(config, output_format="json", output=restore_json, client=restore_dry_client), commands, stdout_chunks, stderr_chunks)
    _run_step("navidrome_restore_csv", report_dir, lambda: navidrome_ratings_restore(config, output_format="csv", output=restore_csv, client=restore_dry_client), commands, stdout_chunks, stderr_chunks)
    restore_payload = json.loads(restore_json.read_text(encoding="utf-8"))
    restore_rows = list(csv.DictReader(io.StringIO(restore_csv.read_text(encoding="utf-8"))))
    if "summary" not in restore_payload or not restore_rows or "action" not in restore_rows[0]:
        raise LabFailure("Navidrome restore formats", "noqlen-forge navidrome ratings restore formats", "invalid JSON/CSV restore output")
    json_export = export_dir / "navidrome-ratings.json"
    csv_export = export_dir / "navidrome-ratings.csv"
    _run_step("navidrome_export_json", report_dir, lambda: navidrome_ratings_export(config, output_format="json", output=json_export), commands, stdout_chunks, stderr_chunks)
    _run_step("navidrome_export_csv", report_dir, lambda: navidrome_ratings_export(config, output_format="csv", output=csv_export), commands, stdout_chunks, stderr_chunks)
    if len(json.loads(json_export.read_text(encoding="utf-8"))) != 3 or len(list(csv.DictReader(io.StringIO(csv_export.read_text(encoding="utf-8"))))) != 3:
        raise LabFailure("Navidrome export", "noqlen-forge navidrome ratings export", "invalid export rows")

    playlist_items = [
        RatingItem(navidrome_id="nav-strong", title="Super Shy", artist="NewJeans", mb_track_id="lab-navidrome-mb-track"),
        RatingItem(navidrome_id="nav-fallback", title="Navidrome Fallback", artist="MusicLab Artist", duration=111.0),
    ]
    playlist_client = _FakeNavidromeClient(playlist_items, playlists=[{"id": "existing", "name": "Existing Lab", "song_ids": ["old-song"]}, {"id": "append", "name": "Append Lab", "song_ids": ["old-song"]}])
    playlist_list = _run_step("navidrome_playlists_list", report_dir, lambda: navidrome_playlists_list(config, client=playlist_client), commands, stdout_chunks, stderr_chunks)
    _assert_contains(playlist_list, ["Navidrome playlists", "Existing Lab", "Status: OK"], "Navidrome playlists list")
    playlist_backup_client = _FakeNavidromeClient(
        [
            RatingItem(navidrome_id="nav-strong", title="Super Shy", artist="NewJeans", mb_track_id="lab-navidrome-mb-track"),
            RatingItem(navidrome_id="nav-fallback", title="Navidrome Fallback", artist="MusicLab Artist", duration=111.0),
            RatingItem(navidrome_id="nav-playlist-unmatched", title="Playlist Lost", artist="No Match"),
        ],
        playlists=[{"id": "backup-lab", "name": "Backup Lab", "owner": "musiclab", "song_ids": ["nav-strong", "nav-fallback", "nav-playlist-unmatched"]}],
    )
    playlist_backup_before = _playlist_backup_counts(config)
    playlist_backup_dry = _run_step("navidrome_playlist_backup_dry_run", report_dir, lambda: navidrome_playlists_backup(config, client=playlist_backup_client), commands, stdout_chunks, stderr_chunks)
    if _playlist_backup_counts(config) != playlist_backup_before or "Mode: DRY-RUN" not in playlist_backup_dry:
        raise LabFailure("Navidrome playlist backup dry-run", "noqlen-forge navidrome playlists backup", playlist_backup_dry)
    playlist_backup_apply = _run_step("navidrome_playlist_backup_apply", report_dir, lambda: navidrome_playlists_backup(config, apply=True, client=playlist_backup_client), commands, stdout_chunks, stderr_chunks)
    _assert_contains(playlist_backup_apply, ["Mode: APPLY", "Saved playlists: 1", "Saved items: 3", "Matched: 2", "Unmatched: 1", "Status: WARN"], "Navidrome playlist backup apply")
    playlist_backup_counts = _playlist_backup_counts(config)
    if playlist_backup_counts.get("backups") != 1 or playlist_backup_counts.get("items") != 3 or playlist_backup_counts.get("runs") != 1:
        raise LabFailure("Navidrome playlist backup DB", "noqlen-forge navidrome playlists backup --apply", str(playlist_backup_counts))
    with connect(config) as conn:
        order = [row["navidrome_song_id"] for row in conn.execute("SELECT navidrome_song_id FROM navidrome_playlist_items ORDER BY position")]
        confidences = [row["match_confidence"] for row in conn.execute("SELECT match_confidence FROM navidrome_playlist_items ORDER BY position")]
    if order != ["nav-strong", "nav-fallback", "nav-playlist-unmatched"] or confidences != ["high", "medium", "none"]:
        raise LabFailure("Navidrome playlist backup matching", "noqlen-forge navidrome playlists backup --apply", str(order) + str(confidences))
    playlist_backup_repeat = _run_step("navidrome_playlist_backup_apply_repeat", report_dir, lambda: navidrome_playlists_backup(config, apply=True, client=playlist_backup_client), commands, stdout_chunks, stderr_chunks)
    playlist_backup_repeat_counts = _playlist_backup_counts(config)
    if playlist_backup_repeat_counts.get("backups") != 1 or playlist_backup_repeat_counts.get("items") != 3 or playlist_backup_repeat_counts.get("runs") != 2:
        raise LabFailure("Navidrome playlist backup idempotency", "noqlen-forge navidrome playlists backup --apply repeat", playlist_backup_repeat + str(playlist_backup_repeat_counts))
    playlist_backup_status = _run_step("navidrome_playlist_backup_status", report_dir, lambda: navidrome_playlists_status(config), commands, stdout_chunks, stderr_chunks)
    _assert_contains(playlist_backup_status, ["Playlists: 1", "Items: 3", "Status: WARN"], "Navidrome playlist backup status")
    playlist_backup_json = export_dir / "navidrome-playlists.json"
    playlist_backup_csv = export_dir / "navidrome-playlists.csv"
    _run_step("navidrome_playlist_backup_export_json", report_dir, lambda: navidrome_playlists_export(config, output_format="json", output=playlist_backup_json), commands, stdout_chunks, stderr_chunks)
    _run_step("navidrome_playlist_backup_export_csv", report_dir, lambda: navidrome_playlists_export(config, output_format="csv", output=playlist_backup_csv), commands, stdout_chunks, stderr_chunks)
    playlist_payload = json.loads(playlist_backup_json.read_text(encoding="utf-8"))
    playlist_rows = list(csv.DictReader(io.StringIO(playlist_backup_csv.read_text(encoding="utf-8"))))
    if playlist_payload.get("playlists", [{}])[0].get("items", [{}])[0].get("navidrome_song_id") != "nav-strong" or not playlist_rows or "playlist_name" not in playlist_rows[0]:
        raise LabFailure("Navidrome playlist backup export", "noqlen-forge navidrome playlists export", "invalid JSON/CSV playlist backup output")
    if playlist_backup_client.write_calls or playlist_backup_client.forbidden_calls:
        raise LabFailure("Navidrome playlist backup safety", "noqlen-forge navidrome playlists backup", str(playlist_backup_client.write_calls + playlist_backup_client.forbidden_calls))
    playlist_dry = _run_step("navidrome_playlist_push_dry_run", report_dir, lambda: navidrome_playlists_push(config, 'artist:"NewJeans"', name="Lab API Favorites", client=playlist_client), commands, stdout_chunks, stderr_chunks)
    if playlist_client.write_calls or "Mode: DRY-RUN" not in playlist_dry or "would create playlist" not in playlist_dry:
        raise LabFailure("Navidrome playlist dry-run", "noqlen-forge navidrome playlists push", playlist_dry + str(playlist_client.write_calls))
    playlist_apply = _run_step("navidrome_playlist_push_apply", report_dir, lambda: navidrome_playlists_push(config, 'artist:"NewJeans"', name="Lab API Favorites", apply=True, client=playlist_client), commands, stdout_chunks, stderr_chunks)
    if ("createPlaylist", "Lab API Favorites", ["nav-strong"]) not in playlist_client.write_calls:
        raise LabFailure("Navidrome playlist apply", "noqlen-forge navidrome playlists push --apply", playlist_apply + str(playlist_client.write_calls))
    review_client = _FakeNavidromeClient(playlist_items, playlists=[{"id": "existing", "name": "Existing Lab", "song_ids": ["old-song"]}])
    existing_review = _run_step("navidrome_playlist_existing_review", report_dir, lambda: (0, navidrome_playlists_push(config, 'artist:"NewJeans"', name="Existing Lab", apply=True, client=review_client)[1]), commands, stdout_chunks, stderr_chunks)
    if "Status: REVIEW" not in existing_review or any(call[0] == "updatePlaylist" for call in review_client.write_calls):
        raise LabFailure("Navidrome playlist existing review", "noqlen-forge navidrome playlists push existing", existing_review + str(review_client.write_calls))
    replace = _run_step("navidrome_playlist_replace_apply", report_dir, lambda: navidrome_playlists_push(config, 'artist:"NewJeans"', name="Existing Lab", replace=True, apply=True, client=playlist_client), commands, stdout_chunks, stderr_chunks)
    if ("updatePlaylist", "existing", ["nav-strong"], "Existing Lab") not in playlist_client.write_calls:
        raise LabFailure("Navidrome playlist replace", "noqlen-forge navidrome playlists push --replace --apply", replace + str(playlist_client.write_calls))
    append = _run_step("navidrome_playlist_append_apply", report_dir, lambda: navidrome_playlists_push(config, 'artist:"NewJeans"', name="Append Lab", append=True, apply=True, client=playlist_client), commands, stdout_chunks, stderr_chunks)
    if ("updatePlaylist", "append", ["old-song", "nav-strong"], "Append Lab") not in playlist_client.write_calls:
        raise LabFailure("Navidrome playlist append", "noqlen-forge navidrome playlists push --append --apply", append + str(playlist_client.write_calls))
    unmatched = _run_step("navidrome_playlist_unmatched", report_dir, lambda: navidrome_playlists_push(config, 'title:"Navidrome Fallback"', name="Lab Unmatched", apply=True, client=playlist_client), commands, stdout_chunks, stderr_chunks)
    _assert_contains(unmatched, ["Status: WARN", "Unmatched:"], "Navidrome playlist unmatched")
    smart_create(config, "Lab API Smart", 'artist:"NewJeans"', apply=True)
    smart_dry = _run_step("navidrome_playlist_push_smart_dry", report_dir, lambda: navidrome_playlists_push_smart(config, "Lab API Smart", client=playlist_client), commands, stdout_chunks, stderr_chunks)
    smart_apply = _run_step("navidrome_playlist_push_smart_apply", report_dir, lambda: navidrome_playlists_push_smart(config, "Lab API Smart", apply=True, client=playlist_client), commands, stdout_chunks, stderr_chunks)
    if "Mode: DRY-RUN" not in smart_dry or ("createPlaylist", "Lab API Smart", ["nav-strong"]) not in playlist_client.write_calls:
        raise LabFailure("Navidrome smart playlist push", "noqlen-forge navidrome playlists push-smart", smart_dry + smart_apply + str(playlist_client.write_calls))
    playlist_json = _run_step("navidrome_playlist_push_json", report_dir, lambda: navidrome_playlists_push(config, 'artist:"NewJeans"', name="Lab JSON", output_format="json", client=playlist_client), commands, stdout_chunks, stderr_chunks)
    playlist_json_payload = json.loads(playlist_json)
    if playlist_json_payload.get("summary", {}).get("matched") != 1:
        raise LabFailure("Navidrome playlist JSON", "noqlen-forge navidrome playlists push --format json", playlist_json)
    if any(secret in playlist_json.casefold() for secret in ("musiclab-password", "token", "salt")):
        raise LabFailure("Navidrome playlist JSON safety", "noqlen-forge navidrome playlists push --format json", playlist_json)
    playlist_diff = _run_step("navidrome_playlist_diff", report_dir, lambda: navidrome_playlists_diff(config, 'artist:"NewJeans"', name="Existing Lab", client=playlist_client), commands, stdout_chunks, stderr_chunks)
    _assert_contains(playlist_diff, ["Mode: READ-ONLY", "Status: OK"], "Navidrome playlist diff")
    if playlist_client.forbidden_calls or any(call[0] in {"deletePlaylist", "setRating", "star", "unstar"} for call in playlist_client.write_calls):
        raise LabFailure("Navidrome playlist safety", "noqlen-forge navidrome playlists", str(playlist_client.write_calls + playlist_client.forbidden_calls))

    combined = "\n".join([dry_run, apply_output, repeat, status, diff, server_diff, restore_dry, restore_apply, restore_second, restore_conflict, preserve, playlist_list, playlist_backup_dry, playlist_backup_apply, playlist_backup_repeat, playlist_backup_status, playlist_dry, playlist_apply, existing_review, replace, append, unmatched, smart_dry, smart_apply, playlist_json, playlist_diff, json_diff.read_text(encoding="utf-8"), csv_diff.read_text(encoding="utf-8"), restore_json.read_text(encoding="utf-8"), restore_csv.read_text(encoding="utf-8"), playlist_backup_json.read_text(encoding="utf-8"), playlist_backup_csv.read_text(encoding="utf-8")])
    for secret in ("musiclab-password", "password", "token", "salt"):
        if secret in combined.casefold():
            raise LabFailure("Navidrome secrets", "noqlen-forge navidrome", "sensitive output leaked")
    return "fake ping, backup dry-run/apply, matching, diff, restore dry-run/apply/conflicts/preserve/idempotent, export, playlist list/backup/export/push/diff/smart/safety"


def _player_backup_counts(config: dict) -> dict[str, int]:
    with connect(config) as conn:
        apply_migrations(conn)
        return {
            "accounts": int(conn.execute("SELECT COUNT(*) AS count FROM player_accounts").fetchone()["count"]),
            "backups": int(conn.execute("SELECT COUNT(*) AS count FROM player_rating_backups").fetchone()["count"]),
            "runs": int(conn.execute("SELECT COUNT(*) AS count FROM player_rating_backup_runs").fetchone()["count"]),
            "restore_runs": int(conn.execute("SELECT COUNT(*) AS count FROM player_rating_restore_runs").fetchone()["count"]),
        }


def _playlist_backup_counts(config: dict) -> dict[str, int]:
    with connect(config) as conn:
        apply_migrations(conn)
        return {
            "backups": int(conn.execute("SELECT COUNT(*) AS count FROM navidrome_playlist_backups").fetchone()["count"]),
            "items": int(conn.execute("SELECT COUNT(*) AS count FROM navidrome_playlist_items").fetchone()["count"]),
            "runs": int(conn.execute("SELECT COUNT(*) AS count FROM navidrome_playlist_backup_runs").fetchone()["count"]),
        }


def _smart_playlist_count(config: dict) -> int:
    with connect(config) as conn:
        apply_migrations(conn)
        return int(conn.execute("SELECT COUNT(*) AS count FROM smart_playlists").fetchone()["count"])


def _seed_repair_db_inconsistencies(config: dict, library: Path) -> dict[str, int]:
    orphan_file = library / "MusicLab Repair" / "Orphan.flac"
    orphan_file.parent.mkdir(parents=True, exist_ok=True)
    orphan_file.write_bytes(b"repair orphan")
    with connect(config) as conn:
        file_id = upsert_file(conn, orphan_file, {"format": "flac", "status": "active"}, track_id=None)
        track_id = upsert_track(conn, {"title": "Repair Track Orphan", "artist": "MusicLab Artist"}, album_id=None)
        album_id = upsert_album(conn, {"album_key": "repair-empty", "album": "Repair Empty", "albumartist": "MusicLab Artist"})
        op_id = conn.execute("INSERT INTO operations(operation, target_type, target_id, mode, status, started_at, summary) VALUES ('import', 'path', 'repair', 'apply', 'running', CURRENT_TIMESTAMP, 'repair fixture')").lastrowid
        provider_id = record_provider_run(conn, "musicbrainz", "track", "999999", "running")
        record_field_decision(conn, provider_id, "track", "999999", "style", action="review")
        conn.commit()
    return {"file": int(file_id), "track": int(track_id), "album": int(album_id), "operation": int(op_id), "provider": int(provider_id)}


def _db_has_file(config: dict, path: Path) -> bool:
    with connect(config) as conn:
        return conn.execute("SELECT 1 FROM files WHERE path = ?", (str(path.resolve(strict=False)),)).fetchone() is not None


def _db_file_status(config: dict, path: Path) -> str:
    with connect(config) as conn:
        row = conn.execute("SELECT status FROM files WHERE path = ?", (str(path.resolve(strict=False)),)).fetchone()
        return str(row["status"] if row else "")


def _db_file_status_by_id(config: dict, file_id: int) -> str:
    with connect(config) as conn:
        return str(conn.execute("SELECT status FROM files WHERE id = ?", (file_id,)).fetchone()["status"])


def _db_track_status(config: dict, track_id: int) -> str:
    with connect(config) as conn:
        return str(conn.execute("SELECT status FROM tracks WHERE id = ?", (track_id,)).fetchone()["status"])


def _db_album_status(config: dict, album_id: int) -> str:
    with connect(config) as conn:
        return str(conn.execute("SELECT status FROM albums WHERE id = ?", (album_id,)).fetchone()["status"])


def _file_fingerprints(path: Path) -> dict[Path, tuple[int, str]]:
    return {file: (file.stat().st_size, hashlib.sha256(file.read_bytes()).hexdigest()) for file in audio_files(path)}


def _assert_contains(output: str, expected: Sequence[str], step: str) -> None:
    missing = [item for item in expected if item not in output]
    if missing:
        raise LabFailure(step, step.lower(), "missing expected output: " + ", ".join(missing) + "\n\n" + output)


def _assert_safe_output(output: str) -> None:
    lowered = output.lower()
    forbidden = ["lastfm_api_key", "discogs_token", "acoustid_key", "full fingerprint"]
    found = [item for item in forbidden if item in lowered]
    if found:
        raise LabFailure("Safety checks", "safe output", "unsafe output token(s): " + ", ".join(found))
    if "musiclab line one\nmusiclab line two" in lowered:
        raise LabFailure("Safety checks", "safe output", "full lyrics leaked in output")


def _write_report_files(report_dir: Path, commands: list[str], stdout_chunks: list[str], stderr_chunks: list[str], steps: list[LabStep], success: bool, counts: dict[str, int] | None = None, failure: LabFailure | None = None) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "command.log").write_text("\n".join(commands) + ("\n" if commands else ""), encoding="utf-8")
    (report_dir / "stdout.log").write_text("\n---\n".join(stdout_chunks) + ("\n" if stdout_chunks else ""), encoding="utf-8")
    (report_dir / "stderr.log").write_text("\n---\n".join(chunk for chunk in stderr_chunks if chunk) + ("\n" if any(stderr_chunks) else ""), encoding="utf-8")
    lines = ["# MusicLab Report", "", f"Status: {'OK' if success else 'FAIL'}", "", "## Steps"]
    lines.extend(f"- {step.name}: {step.status} {step.detail}{_duration_suffix(step)}".rstrip() for step in steps)
    if counts:
        lines.extend(["", "## DB Counts", f"- albums: {counts['albums']}", f"- tracks: {counts['tracks']}", f"- files: {counts['files']}"])
    if failure:
        lines.extend(["", "## Failure", f"- step: {failure.step}", f"- command: {failure.command}"])
    (report_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_latest_success(lab: Path, commands: list[str], steps: list[LabStep], counts: dict[str, int]) -> None:
    reports = lab / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    lines = ["# MusicLab Latest Success", "", "## Commands"]
    lines.extend(f"- {command}" for command in commands)
    lines.extend(["", "## Status", "- MusicLab: OK", "", "## DB Counts", f"- albums: {counts['albums']}", f"- tracks: {counts['tracks']}", f"- files: {counts['files']}", "", "## Scenarios Covered"])
    lines.extend(f"- {step.name}" for step in steps)
    (reports / "latest-success.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _db_counts(config: dict) -> dict[str, int]:
    with connect(config) as conn:
        return get_counts(conn)


def _db_stability_counts(config: dict) -> dict[str, int]:
    tables = ("albums", "tracks", "files", "operations", "provider_runs", "field_decisions", "track_tags", "album_tags", "audio_features")
    with connect(config) as conn:
        return {table: int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]) for table in tables}


def _scan_detail(config: dict, library: Path, apply: bool) -> str:
    count = len(audio_files(library))
    return f"would add {count} files" if not apply else f"added {count} files"


def _safety_self_check(lab: Path) -> None:
    for dangerous in DANGEROUS_PATHS:
        if _path_is_safe(dangerous):
            raise LabFailure("Safety checks", "safety guard", f"dangerous path allowed: {dangerous}")
    if not (lab / LAB_MARKER).is_file():
        raise LabFailure("Safety checks", "marker check", "marker missing")


def _live_provider_status(live_providers: bool) -> str:
    if not live_providers:
        return "offline fixtures"
    missing = [name for name in ("DISCOGS_TOKEN", "LASTFM_API_KEY", "ACOUSTID_KEY") if not os.environ.get(name)]
    return "WARN missing " + ", ".join(missing) if missing else "live credentials present"


def _render_step(step: LabStep, total: int) -> str:
    return _runner_render_step(step, total)


def _duration_suffix(step: LabStep) -> str:
    return _runner_duration_suffix(step)


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_") or "failure"
