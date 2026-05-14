from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..analyze import analyze_bpm_path, analyze_features_path, analyze_key_path
from ..audio import get_tag, mb_album_ids, read_tracks, target_kind
from ..audit import audit_path, render_final_audit
from ..cleanup import apply_cleanup, plan_cleanup, summarize_cleanup
from ..config import config_path, get_config_value, load_config
from ..cover import CoverResult, process_cover
from ..db import database_path, scan_library
from ..lastfm import analyze_lastfm_tags
from ..lyrics import LyricsStats, has_embedded_lyrics, process_lyrics
from ..metadata_providers import acoustid_plans_from_candidate, build_context, fetch_metadata_with_providers, merge_ambiguous_discogs_common_fields, merge_candidate, metadata_status, plans_from_decisions, render_metadata_output, resolve_metadata_providers
from ..mood import analyze_mood_path
from ..musicbrainz import get_release, hydrate_releases, search_releases
from ..replaygain import replaygain_path
from ..scoring import rank_releases
from ..workflow import Status, StepResult, WorkflowResult, combine_status
from ..writers import apply_musicbrainz_writes, plan_musicbrainz_writes, plan_partial_musicbrainz_repair, summarize_partial_repair, summarize_plans


EnrichEventHandler = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class EnrichOptions:
    path: Path
    config: dict[str, Any] | None = None
    apply: bool = False
    force: bool = False
    acoustid_identify: bool = False
    skip_acoustid_identify: bool = False
    analyze_bpm: bool = False
    analyze_key: bool = False
    analyze_features: bool = False
    full: bool = False
    skip_bpm: bool = False
    skip_key: bool = False
    skip_features: bool = False
    force_bpm: bool = False
    force_key: bool = False
    force_features: bool = False
    with_lastfm: bool = False
    with_mood: bool = False
    skip_lastfm: bool = False
    skip_mood: bool = False
    cover: bool = False
    skip_cover: bool = False
    lyrics: bool = False
    skip_lyrics: bool = False
    metadata_providers: bool = False
    skip_metadata_providers: bool = False
    replaygain: bool = False
    skip_replaygain: bool = False
    force_lastfm: bool = False
    force_mood: bool = False
    force_cover: bool = False
    force_lyrics: bool = False
    force_acoustid: bool = False
    force_identity: bool = False
    metadata_provider_sources: list[str] | None = None
    allow_more_providers: bool = False
    min_metadata_confidence: str | None = None
    cover_sources: list[str] | None = None
    lyrics_sources: list[str] | None = None
    min_cover_confidence: str | None = None
    min_lyrics_confidence: str | None = None
    bpm_range: tuple[float, float] = (70, 180)
    bpm_round: str = "1dp"
    feature_confidence: str = "medium"
    lastfm_min_count: int = 3
    lastfm_max_tags: int = 10
    lastfm_debug: bool = False
    lastfm_raw: bool = False
    lastfm_no_fallback: bool = False
    verbose: bool = False
    debug: bool = False
    advanced: bool = False
    explicit_flags: set[str] = field(default_factory=set)
    confirm_medium_confidence: bool = False
    event_handler: EnrichEventHandler | None = None


def run_enrich_service(options: EnrichOptions) -> WorkflowResult:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    config_provided = options.config is not None
    config = options.config or load_config()
    if not config_provided and not config_path().exists():
        config = {**config, "enrich": {**config.get("enrich", {}), "full_includes_key": True}}
    selected = resolve_enrich_options(
        config,
        full=options.full,
        analyze_bpm=options.analyze_bpm,
        analyze_key=options.analyze_key,
        analyze_features=options.analyze_features,
        with_lastfm=options.with_lastfm,
        with_mood=options.with_mood,
        acoustid_identify=options.acoustid_identify,
        skip_acoustid_identify=options.skip_acoustid_identify,
        skip_bpm=options.skip_bpm,
        skip_key=options.skip_key,
        skip_features=options.skip_features,
        skip_lastfm=options.skip_lastfm,
        skip_mood=options.skip_mood,
        cover=options.cover,
        skip_cover=options.skip_cover,
        lyrics=options.lyrics,
        skip_lyrics=options.skip_lyrics,
        metadata_providers=options.metadata_providers,
        skip_metadata_providers=options.skip_metadata_providers,
        replaygain=options.replaygain,
        skip_replaygain=options.skip_replaygain,
        explicit_flags=options.explicit_flags,
    )
    kind = target_kind(options.path)
    if kind == "empty":
        return _failure(options.path, "No supported audio files found", started, started_at)
    targets = _enrichment_targets(options.path, kind)
    if not targets:
        return _failure(options.path, "No album/file targets found", started, started_at)

    steps: list[StepResult] = []
    target_details: list[dict[str, Any]] = []
    planned_writes = 0
    applied_writes = 0
    errors: list[str] = []
    warnings: list[str] = []
    stop = False
    for target in targets:
        target_result = _run_target(target, options, config, selected, len(targets), event_handler=options.event_handler)
        target_details.append(target_result["details"])
        steps.extend(target_result["steps"])
        planned_writes += int(target_result["planned_writes"])
        applied_writes += int(target_result["applied_writes"])
        errors.extend(target_result["errors"])
        warnings.extend(target_result["warnings"])
        if target_result["stop"]:
            stop = True
            break
    total_steps = len(steps)
    for index, step in enumerate(steps, 1):
        step.index = index
        step.total = total_steps
    status = combine_status(*(step.status for step in steps)) if steps else Status.OK
    if errors:
        status = Status.FAIL
    return WorkflowResult(
        status,
        steps,
        workflow="enrich",
        command="enrich",
        target=options.path,
        target_type=kind,
        mode="apply" if options.apply else "dry-run",
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        summary={"status": status.value, "targets": len(target_details), "planned_writes": planned_writes, "applied_writes": applied_writes, "stages": _stage_selection_summary(selected)},
        counts={"targets": len(target_details), "steps": len(steps), "planned_writes": planned_writes, "applied_writes": applied_writes},
        details={"targets": target_details, "stage_selection": selected},
        safe_details={"targets": [_safe_target_details(item) for item in target_details], "stage_selection": selected},
        warnings=warnings,
        errors=errors,
        elapsed_seconds=time.perf_counter() - started,
        stopped=stop,
    )


def resolve_enrich_options(config: dict[str, Any], full: bool, analyze_bpm: bool, analyze_key: bool, analyze_features: bool, with_lastfm: bool, with_mood: bool, acoustid_identify: bool = False, skip_acoustid_identify: bool = False, skip_bpm: bool = False, skip_key: bool = False, skip_features: bool = False, skip_lastfm: bool = False, skip_mood: bool = False, cover: bool = False, skip_cover: bool = False, lyrics: bool = False, skip_lyrics: bool = False, metadata_providers: bool = False, skip_metadata_providers: bool = False, replaygain: bool = False, skip_replaygain: bool = False, explicit_flags: set[str] | None = None) -> dict[str, bool]:
    explicit_flags = explicit_flags or set()

    def include(name: str, default: bool = True) -> bool:
        return bool(get_config_value(config, "enrich", f"full_includes_{name}", default))

    run_bpm = analyze_bpm or (full and include("bpm"))
    run_key = analyze_key or (full and include("key", False))
    run_features = analyze_features or (full and include("features"))
    run_lastfm = with_lastfm or (full and include("lastfm"))
    run_mood = with_mood or (full and include("mood"))
    run_cover = cover or (full and include("cover", False) and bool(get_config_value(config, "cover", "enabled", False)))
    run_lyrics = lyrics or (full and include("lyrics", False) and bool(get_config_value(config, "lyrics", "enabled", False)))
    run_metadata_providers = metadata_providers or (full and include("metadata_providers") and bool(get_config_value(config, "metadata_providers", "enabled", True)))
    run_replaygain = replaygain or (full and include("replaygain", False) and bool(get_config_value(config, "audio", "replaygain_enabled", True)))
    run_acoustid_identify = acoustid_identify or (full and include("acoustid_identification") and bool(get_config_value(config, "metadata_providers", "enabled", True)))
    run_cleanup = not (full and not include("cleanup"))
    if "--skip-bpm" in explicit_flags or skip_bpm:
        run_bpm = False
    if "--skip-key" in explicit_flags or skip_key:
        run_key = False
    if "--skip-features" in explicit_flags or skip_features:
        run_features = False
    if "--skip-lastfm" in explicit_flags or skip_lastfm:
        run_lastfm = False
    if "--skip-mood" in explicit_flags or skip_mood:
        run_mood = False
    if "--cover" in explicit_flags or cover:
        run_cover = True
    if "--skip-cover" in explicit_flags or skip_cover:
        run_cover = False
    if "--lyrics" in explicit_flags or lyrics:
        run_lyrics = True
    if "--skip-lyrics" in explicit_flags or skip_lyrics:
        run_lyrics = False
    if "--metadata-providers" in explicit_flags or metadata_providers:
        run_metadata_providers = True
    if "--skip-metadata-providers" in explicit_flags or skip_metadata_providers:
        run_metadata_providers = False
    if "--replaygain" in explicit_flags or replaygain:
        run_replaygain = True
    if "--skip-replaygain" in explicit_flags or skip_replaygain:
        run_replaygain = False
    if "--acoustid-identify" in explicit_flags or acoustid_identify:
        run_acoustid_identify = True
    if "--skip-acoustid-identify" in explicit_flags or skip_acoustid_identify:
        run_acoustid_identify = False
    return {"run_bpm": run_bpm, "run_key": run_key, "run_features": run_features, "run_lastfm": run_lastfm, "run_mood": run_mood, "run_cover": run_cover, "run_lyrics": run_lyrics, "run_metadata_providers": run_metadata_providers, "run_replaygain": run_replaygain, "run_acoustid_identify": run_acoustid_identify, "skip_acoustid_identify": skip_acoustid_identify, "run_cleanup": run_cleanup}


def _run_target(target: Path, options: EnrichOptions, config: dict[str, Any], selected: dict[str, bool], target_count: int, *, event_handler: EnrichEventHandler | None) -> dict[str, Any]:
    tracks = read_tracks(target)
    stage_total = 2 + sum(1 for enabled in (selected["run_metadata_providers"], selected["run_acoustid_identify"], selected["run_bpm"], selected["run_features"], selected["run_replaygain"], selected["run_lastfm"], selected["run_mood"], selected["run_cover"], selected["run_lyrics"], selected["run_key"] and (selected["run_cover"] or selected["run_lyrics"])) if enabled)
    verbose_output = options.verbose or options.debug
    target_detail: dict[str, Any] = {"target_name": target.name if target_count > 1 else "", "album": _common_track_value(tracks, "album"), "artist": _common_track_value(tracks, "albumartist") or _common_track_value(tracks, "artist"), "files": len(tracks), "mode": "APPLY" if options.apply else "DRY-RUN", "stages": [], "warnings": [], "final_audit": ""}
    _emit(event_handler, "target_start", 0, stage_total, "target", target=target_detail)
    steps: list[StepResult] = []
    errors: list[str] = []
    planned_writes = 0
    applied_writes = 0
    stage_index = 1

    release_date = ""
    existing_mb_album_ids = mb_album_ids(tracks)
    musicbrainz_plans = []
    musicbrainz_output = ""
    if not existing_mb_album_ids or options.force:
        _emit(event_handler, "stage_start", stage_index, stage_total, "MusicBrainz")
        musicbrainz_plans, mb_errors, mb_review = _apply_best_musicbrainz(target, tracks, apply=options.apply, force=options.force, confirm_medium_confidence=options.confirm_medium_confidence)
        if musicbrainz_plans:
            musicbrainz_output = summarize_plans(musicbrainz_plans, apply=options.apply, verbose=True)
        if mb_errors:
            status, summary = "FAIL", mb_errors[0]
        elif mb_review:
            status, summary = "REVIEW", mb_review
        else:
            status, summary = _musicbrainz_status(musicbrainz_plans, len(tracks))
    elif len(existing_mb_album_ids) == 1:
        if _musicbrainz_identity_complete(tracks):
            status, summary = "SKIP", "IDs already present"
        else:
            _emit(event_handler, "stage_start", stage_index, stage_total, "MusicBrainz")
            musicbrainz_plans, mb_errors, mb_review = _repair_partial_musicbrainz(tracks, next(iter(existing_mb_album_ids)), apply=options.apply)
            if musicbrainz_plans:
                musicbrainz_output = summarize_partial_repair(musicbrainz_plans, apply=options.apply)
            status, summary = ("FAIL", mb_errors[0]) if mb_errors else (("REVIEW", mb_review) if mb_review else _musicbrainz_status(musicbrainz_plans, len(tracks), skipped=not musicbrainz_plans, existing_ids=True))
    else:
        _emit(event_handler, "stage_start", stage_index, stage_total, "MusicBrainz")
        status, summary = "REVIEW", "album id inconsistent; use --force"
    planned_writes += len(musicbrainz_plans)
    applied_writes += len(musicbrainz_plans) if options.apply else 0
    _add_stage(target_detail, steps, stage_index, stage_total, "MusicBrainz", status, summary, musicbrainz_output if verbose_output else "", event_handler)
    if status == "FAIL":
        errors.append(summary)
        return _target_result(target_detail, steps, planned_writes, applied_writes, errors, True)
    stage_index += 1

    stage_specs = [
        ("run_metadata_providers", "Metadata providers", lambda: _metadata_stage(target, options, config, selected)),
        ("run_acoustid_identify", "AcoustID Identify", lambda: _acoustid_stage(target, options, config)),
        ("run_cleanup", "Cleanup", lambda: _cleanup_stage(target, options, release_date, musicbrainz_plans, verbose_output)),
        ("run_bpm", "BPM", lambda: _text_stage(analyze_bpm_path(target, apply=options.apply, force=options.force_bpm, bpm_range=options.bpm_range, bpm_round=options.bpm_round), _bpm_status, len(tracks))),
        ("run_features", "Features", lambda: _text_stage(analyze_features_path(target, apply=options.apply, force=options.force_features, minimum_confidence=options.feature_confidence, bpm_range=options.bpm_range, bpm_round=options.bpm_round), _features_status, len(tracks))),
        ("run_replaygain", "ReplayGain", lambda: _replaygain_stage(target, options, config, tracks)),
        ("run_lastfm", "Last.fm", lambda: _text_stage(analyze_lastfm_tags(target, apply=options.apply, force=options.force_lastfm, min_count=options.lastfm_min_count, max_tags=options.lastfm_max_tags, debug=options.lastfm_debug or options.debug, raw=options.lastfm_raw or options.debug, allow_fallback=not options.lastfm_no_fallback), _lastfm_status, len(tracks))),
        ("run_mood", "Mood", lambda: _text_stage(analyze_mood_path(target, apply=options.apply, force=options.force_mood, with_lastfm=selected["run_lastfm"]), _mood_status, len(tracks))),
        ("run_cover", "Cover", lambda: _cover_stage(target, options, config)),
        ("run_lyrics", "Lyrics", lambda: _lyrics_stage(target, options, config)),
    ]
    for flag, name, runner in stage_specs:
        if not selected[flag]:
            if flag == "run_cleanup":
                _add_stage(target_detail, steps, stage_index, stage_total, name, "SKIP", "disabled by config", "", event_handler)
                stage_index += 1
            continue
        _emit(event_handler, "stage_start", stage_index, stage_total, name)
        status, summary, detail, count = runner()
        planned_writes += count
        applied_writes += count if options.apply else 0
        _add_stage(target_detail, steps, stage_index, stage_total, name, status, summary, detail if verbose_output or name == "Lyrics" and status == "FAIL" else "", event_handler)
        if status == "FAIL":
            errors.append(summary)
            return _target_result(target_detail, steps, planned_writes, applied_writes, errors, True)
        stage_index += 1
    if selected["run_key"]:
        numbered_key = selected["run_cover"] or selected["run_lyrics"]
        key_index = stage_index if numbered_key else 0
        _emit(event_handler, "stage_start", key_index or 0, stage_total, "Key", optional=not numbered_key)
        code, output = analyze_key_path(target, apply=options.apply, force=options.force_key, config=config)
        if code != 0:
            status, summary = "FAIL", _first_line(output)
        elif output.startswith("KEY: skipped"):
            status, summary = "SKIP", "optional backend unavailable"
        else:
            status, summary = _key_status(output, len(tracks))
        _add_stage(target_detail, steps, key_index or len(steps) + 1, stage_total if numbered_key else len(steps) + 1, "Key", status, summary, output if verbose_output else "", event_handler, optional=not numbered_key)
        if status == "FAIL":
            errors.append(summary)
            return _target_result(target_detail, steps, planned_writes, applied_writes, errors, True)
    audit = audit_path(target)
    target_warnings: list[str] = []
    if audit.tracks and not any(get_tag(track, "style") for track in audit.tracks):
        target_warnings.append("Style missing: no reliable style found from configured metadata sources")
    if not options.apply and (options.full or options.analyze_bpm or options.analyze_key or options.analyze_features or selected["run_lastfm"] or selected["run_mood"] or selected["run_metadata_providers"] or selected["run_replaygain"] or selected["run_cover"] or selected["run_lyrics"]):
        target_warnings.append("Audit reflects current files; planned dry-run changes are not applied")
    if options.apply and selected["run_lastfm"] and audit.tracks and not any(get_tag(track, "lastfm_tags") for track in audit.tracks):
        target_warnings.append("Last.fm Tags missing: no Last.fm tags found")
    if options.apply and selected["run_mood"] and audit.tracks and not any(get_tag(track, "mood") for track in audit.tracks):
        target_warnings.append("Mood missing: no high-confidence mood found")
    target_detail["warnings"] = target_warnings
    target_detail["final_audit"] = render_final_audit(audit, verbose=verbose_output, advanced=options.advanced)
    return _target_result(target_detail, steps, planned_writes, applied_writes, errors, False)


def _metadata_stage(path: Path, options: EnrichOptions, config: dict[str, Any], selected: dict[str, bool]) -> tuple[str, str, str, int]:
    tracks = read_tracks(path)
    if not tracks:
        return "SKIP", "no supported audio files found", "", 0
    context = build_context(path, tracks)
    selection = resolve_metadata_providers(config, providers=options.metadata_provider_sources, allow_more_providers=options.allow_more_providers)
    if "musicbrainz" in selection.active:
        selection.active = [source for source in selection.active if source != "musicbrainz"]
        selection.skipped.append(("musicbrainz", "identity handled by MusicBrainz stage"))
    if (selected["run_acoustid_identify"] or selected["skip_acoustid_identify"]) and "acoustid" in selection.active:
        selection.active = [source for source in selection.active if source != "acoustid"]
        selection.skipped.append(("acoustid", "identifier handled by AcoustID Identify stage"))
    if not selection.active:
        detail = render_metadata_output(context, [], [], apply=options.apply, status="WARN", verbose=options.verbose, debug=options.debug, selection=selection)
        return "SKIP", "no active catalog/fallback providers", detail, 0
    attempts = fetch_metadata_with_providers(context, selection.active, config=config, debug=options.debug)
    selected_candidate = _metadata_selected_candidate(attempts, options.min_metadata_confidence or str(get_config_value(config, "metadata_providers", "min_confidence", "medium")))
    min_confidence = options.min_metadata_confidence or str(get_config_value(config, "metadata_providers", "min_confidence", "medium"))
    decisions = merge_candidate(context, selected_candidate, min_confidence=min_confidence, force=options.force) if selected_candidate else merge_ambiguous_discogs_common_fields(context, attempts, min_confidence=min_confidence, force=options.force)
    plans = acoustid_plans_from_candidate(tracks, selected_candidate, force=False) if selected_candidate and selected_candidate.provider == "acoustid" else plans_from_decisions(tracks, decisions)
    errors = apply_musicbrainz_writes(plans, apply=options.apply)
    if errors:
        return "FAIL", errors[0], "\n".join(errors), len(plans)
    status = metadata_status(attempts, decisions, selected_candidate)
    detail = render_metadata_output(context, attempts, decisions, apply=options.apply, status=status, verbose=options.verbose, debug=options.debug, selection=selection)
    return status, _metadata_provider_summary(selection.active, attempts, decisions), detail, len(plans)


def _acoustid_stage(path: Path, options: EnrichOptions, config: dict[str, Any]) -> tuple[str, str, str, int]:
    tracks = read_tracks(path)
    if not tracks:
        return "SKIP", "no supported audio files found", "", 0
    context = build_context(path, tracks)
    attempts = fetch_metadata_with_providers(context, ["acoustid"], config=config, debug=options.debug)
    selected = attempts[0].candidates[0] if attempts and attempts[0].candidates else None
    min_confidence = options.min_metadata_confidence or str(get_config_value(config, "metadata_providers", "min_confidence", "medium"))
    decisions = merge_candidate(context, selected, min_confidence=min_confidence, force=False) if selected else []
    plans = acoustid_plans_from_candidate(tracks, selected, force=False, force_acoustid=options.force_acoustid, force_identity=options.force_identity)
    errors = apply_musicbrainz_writes(plans, apply=options.apply)
    if errors:
        return "FAIL", errors[0], "\n".join(errors), len(plans)
    status = metadata_status(attempts, decisions, selected)
    if attempts and attempts[0].status == "WARN" and "fpcalc not found" in attempts[0].message:
        status = "SKIP"
    detail = render_metadata_output(context, attempts, decisions, apply=options.apply, status=status, verbose=options.verbose, debug=options.debug)
    return status, _acoustid_identify_summary(attempts, selected), detail, len(plans)


def _cleanup_stage(target: Path, options: EnrichOptions, release_date: str, musicbrainz_plans: list[Any], verbose_output: bool) -> tuple[str, str, str, int]:
    tracks = read_tracks(target)
    cleanup_plans = plan_cleanup(tracks, release_date=release_date)
    apply_cleanup(cleanup_plans, apply=options.apply)
    output = summarize_cleanup(cleanup_plans, apply=options.apply, verbose=verbose_output, repaired_fields_by_path=_musicbrainz_repaired_fields(musicbrainz_plans) if not options.apply else {})
    return "OK", _cleanup_summary(cleanup_plans), output, len(cleanup_plans)


def _replaygain_stage(target: Path, options: EnrichOptions, config: dict[str, Any], tracks: list[Any]) -> tuple[str, str, str, int]:
    code, output = replaygain_path(target, apply=options.apply, force=options.force, target_lufs=float(get_config_value(config, "audio", "target_lufs", -18.0)), write_track_gain=bool(get_config_value(config, "audio", "write_track_gain", True)), write_track_peak=bool(get_config_value(config, "audio", "write_track_peak", True)), write_album_gain=bool(get_config_value(config, "audio", "write_album_gain", True)), write_album_peak=bool(get_config_value(config, "audio", "write_album_peak", True)), write_loudness=bool(get_config_value(config, "audio", "write_loudness", True)), skip_existing=bool(get_config_value(config, "audio", "skip_existing", True)), verbose=options.verbose, debug=options.debug)
    if code != 0:
        return "FAIL", _first_line(output), output, 0
    if options.apply and (bool(get_config_value(config, "database", "auto_scan", False)) or database_path(config).exists()):
        scan_library(config, target, apply=True)
    status, summary = _replaygain_status(output, len(tracks))
    return status, summary, output, _count_actions(output)


def _cover_stage(target: Path, options: EnrichOptions, config: dict[str, Any]) -> tuple[str, str, str, int]:
    result = process_cover(target, tracks=read_tracks(target), apply=options.apply, force=options.force_cover, embed_cover=bool(get_config_value(config, "cover", "embed", True)), save_folder_cover=bool(get_config_value(config, "cover", "save_folder_cover", False)), folder_cover_filename=str(get_config_value(config, "cover", "filename", "cover")), sources=options.cover_sources or list(get_config_value(config, "cover", "sources", ["local", "musicbrainz", "itunes", "deezer"])), min_confidence=options.min_cover_confidence or str(get_config_value(config, "cover", "min_confidence", "medium")), prefer_front=bool(get_config_value(config, "cover", "prefer_front", True)), max_size_mb=int(get_config_value(config, "cover", "max_size_mb", 10)), debug=options.debug)
    status, summary = _cover_status(result, apply=options.apply, force=options.force_cover)
    count = result.total if status in {"DRY", "OK"} else 0
    return status, summary, "", count


def _lyrics_stage(target: Path, options: EnrichOptions, config: dict[str, Any]) -> tuple[str, str, str, int]:
    configured = get_config_value(config, "lyrics", "providers", None)
    if isinstance(configured, list) and configured:
        sources = list(configured) if any(provider in {"local", "embedded", "sidecar"} for provider in configured) else ["local", *[provider for provider in configured if provider != "local"]]
    else:
        sources = list(get_config_value(config, "lyrics", "sources", ["lrclib"]))
    result = process_lyrics(read_tracks(target), apply=options.apply, force=options.force_lyrics, embed_lyrics=bool(get_config_value(config, "lyrics", "embed_lyrics", get_config_value(config, "lyrics", "embed", True))), save_lrc=bool(get_config_value(config, "lyrics", "write_sidecar_lrc", get_config_value(config, "lyrics", "save_lrc", False))), save_txt=bool(get_config_value(config, "lyrics", "save_txt", False)), prefer_synced=bool(get_config_value(config, "lyrics", "prefer_synced", True)), allow_unsynced=bool(get_config_value(config, "lyrics", "allow_unsynced", True)), sources=options.lyrics_sources or sources, min_confidence=options.min_lyrics_confidence or str(get_config_value(config, "lyrics", "min_confidence", "medium")), debug=options.debug, config=config)
    status, summary = _lyrics_status(result, apply=options.apply, force=options.force_lyrics)
    return status, summary, "\n".join(result.errors), len(result.per_file)


def _text_stage(result: tuple[int, str], status_fn: Callable[[str, int], tuple[str, str]], total: int) -> tuple[str, str, str, int]:
    code, output = result
    if code != 0:
        return "FAIL", _first_line(output), output, 0
    status, summary = status_fn(output, total)
    return status, summary, output, _count_actions(output)


def _apply_best_musicbrainz(path: Path, tracks: list[Any], apply: bool, force: bool, confirm_medium_confidence: bool) -> tuple[list[Any], list[str], str]:
    ranked = rank_releases(tracks, hydrate_releases(search_releases(tracks)))
    if not ranked:
        return [], [], "No matching release candidates were found. Try --release-id UUID or check artist/album/title tags."
    scored = ranked[0]
    if scored.score < 80:
        return [], [], "Score below 80; review required before applying MusicBrainz IDs."
    if 80 <= scored.score < 95 and apply and not confirm_medium_confidence:
        return [], [], "medium-confidence match requires explicit confirmation"
    plans = plan_musicbrainz_writes(tracks, scored.release, force=force)
    errors = apply_musicbrainz_writes(plans, apply=apply)
    return plans, errors, ""


def _repair_partial_musicbrainz(tracks: list[Any], release_id: str, apply: bool) -> tuple[list[Any], list[str], str]:
    try:
        release = get_release(release_id)
    except Exception as exc:
        return [], [], f"could not fetch existing release {release_id}: {exc}"
    release_tracks = sum(len(medium.get("tracks", []) or []) for medium in release.get("media", []) or [])
    if release_tracks != len(tracks):
        return [], [], f"existing release {release_id} has {release_tracks} tracks, local target has {len(tracks)}"
    plans = plan_partial_musicbrainz_repair(tracks, release)
    errors = apply_musicbrainz_writes(plans, apply=apply)
    return plans, errors, ""


def _failure(path: Path, message: str, started: float, started_at: datetime) -> WorkflowResult:
    return WorkflowResult(Status.FAIL, [StepResult(1, 1, "Enrich", Status.FAIL, message)], workflow="enrich", command="enrich", target=path, mode="dry-run", started_at=started_at, finished_at=datetime.now(timezone.utc), summary={"status": "FAIL"}, errors=[message], elapsed_seconds=time.perf_counter() - started)


def _add_stage(target_detail: dict[str, Any], steps: list[StepResult], index: int, total: int, name: str, status: str, summary: str, detail: str, event_handler: EnrichEventHandler | None, *, optional: bool = False) -> None:
    stage = {"index": index, "total": total, "name": name, "status": status, "summary": summary, "detail": detail, "optional": optional}
    target_detail["stages"].append(stage)
    steps.append(StepResult(index, total, name, Status(status), summary, details=[detail] if detail else [], skipped_reason=summary if status == "SKIP" else ""))
    _emit(event_handler, "stage_done", index, total, name, status=status, summary=summary, detail=detail, optional=optional)


def _emit(handler: EnrichEventHandler | None, event: str, index: int, total: int, name: str, **extra: Any) -> None:
    if handler is not None:
        handler({"event": event, "index": index, "total": total, "name": name, **extra})


def _target_result(details: dict[str, Any], steps: list[StepResult], planned_writes: int, applied_writes: int, errors: list[str], stop: bool) -> dict[str, Any]:
    return {"details": details, "steps": steps, "planned_writes": planned_writes, "applied_writes": applied_writes, "errors": errors, "warnings": list(details.get("warnings", [])), "stop": stop}


def _safe_target_details(details: dict[str, Any]) -> dict[str, Any]:
    return {"target_name": details.get("target_name", ""), "album": details.get("album", ""), "artist": details.get("artist", ""), "files": details.get("files", 0), "mode": details.get("mode", ""), "stages": [{key: stage.get(key) for key in ("index", "total", "name", "status", "summary", "optional")} for stage in details.get("stages", [])], "warnings": list(details.get("warnings", []))}


def _stage_selection_summary(selected: dict[str, bool]) -> dict[str, list[str]]:
    names = {"run_metadata_providers": "metadata_providers", "run_acoustid_identify": "acoustid_identify", "run_cleanup": "cleanup", "run_bpm": "bpm", "run_features": "features", "run_replaygain": "replaygain", "run_lastfm": "lastfm", "run_mood": "mood", "run_cover": "cover", "run_lyrics": "lyrics", "run_key": "key"}
    enabled = [label for key, label in names.items() if selected.get(key)]
    skipped = [label for key, label in names.items() if not selected.get(key)]
    return {"enabled": enabled, "skipped": skipped}


def _enrichment_targets(path: Path, kind: str) -> list[Path]:
    if kind in {"single", "album"}:
        return [path]
    targets: list[Path] = []
    for child in sorted(path.iterdir()):
        child_kind = target_kind(child)
        if child_kind in {"single", "album"}:
            targets.append(child)
    return targets


def _common_track_value(tracks: list[Any], attr: str) -> str:
    values = [getattr(track, attr, "") for track in tracks if getattr(track, attr, "")]
    return max(set(values), key=values.count) if values else "unknown"


def _first_line(output: str) -> str:
    return next((line.strip() for line in output.splitlines() if line.strip()), "failed")


def _musicbrainz_status(plans: list[Any], total: int, skipped: bool = False, existing_ids: bool = False) -> tuple[str, str]:
    if skipped:
        return "SKIP", "IDs already present"
    if not plans:
        return "WARN", "no IDs written"
    written = sum(1 for plan in plans if plan.changes)
    fields = sorted({field for plan in plans for field in plan.changes})
    original_date = sum(1 for plan in plans if "Original Date" in plan.changes)
    if existing_ids and original_date:
        extra_fields = [field for field in fields if field != "Original Date"]
        summary = f"existing IDs, repaired Original Date {original_date}/{total}"
        if extra_fields:
            names = ", ".join(field.removeprefix("MusicBrainz ").lower() for field in extra_fields[:3])
            if len(extra_fields) > 3:
                names += ", ..."
            summary += f", repaired {names}"
        return "OK", summary
    names = ", ".join(field.removeprefix("MusicBrainz ").lower() for field in fields[:4])
    if len(fields) > 4:
        names += ", ..."
    return "OK", f"{written}/{total} files, wrote {names}" if names else f"{written}/{total} files"


def _musicbrainz_identity_complete(tracks: list[Any]) -> bool:
    return bool(tracks) and all(get_tag(track, "mb_album_id") and get_tag(track, "mb_track_id") and get_tag(track, "mb_release_group_id") for track in tracks)


def _cleanup_summary(plans: list[Any]) -> str:
    removed = 0
    normalized = 0
    for plan in plans:
        removed += len(getattr(plan, "remove", []))
        removed += sum(len(values) for values in getattr(plan, "remove_values", {}).values())
        normalized += len(getattr(plan, "set_values", {}))
    if normalized:
        return f"removed {removed} empty/bad fields, normalized {normalized} tags"
    return f"removed {removed} empty/bad fields"


def _bpm_status(output: str, total: int) -> tuple[str, str]:
    written = _count_actions(output)
    existing = _count_matching_lines(output, "skipped existing BPM")
    warnings = _count_matching_lines(output, "warning=")
    final = existing + written
    status = "WARN" if warnings or final < total else "OK"
    summary = f"existing {existing}/{total}, written {written}" if existing else f"{written}/{total} written"
    if warnings:
        summary += f", {warnings} half-time warnings"
    return status, summary


def _features_status(output: str, total: int) -> tuple[str, str]:
    energy = _count_feature_actions(output, "ENERGY")
    danceability = _count_feature_actions(output, "DANCEABILITY")
    low = _count_matching_lines(output, "action=skipped")
    return ("WARN" if low else "OK"), f"energy {energy}/{total}, danceability {danceability}/{total}"


def _replaygain_status(output: str, total: int) -> tuple[str, str]:
    lower = output.lower()
    if "ffmpeg not found" in lower:
        return "SKIP", "optional backend unavailable"
    if "status: warn" in lower:
        return "WARN", _first_line(output)
    match = re.search(r"ReplayGain Track:\s*(\d+)/(\d+).*ReplayGain Album:\s*(\d+)/(\d+)", output, flags=re.DOTALL)
    if match:
        return "OK", f"track {match.group(1)}/{match.group(2)}, album {match.group(3)}/{match.group(4)}"
    return "OK", f"{total}/{total} tracks"


def _lastfm_status(output: str, total: int) -> tuple[str, str]:
    if "LASTFM_API_KEY not set" in output:
        return "SKIP", "optional backend unavailable"
    written = sum(1 for line in output.splitlines() if " tags=" in line and "action=" in line)
    existing = _count_matching_lines(output, "skipped existing LASTFM_TAGS")
    sources = re.findall(r"source=([^\s]+)", output)
    source_summary = ""
    if sources:
        unique = sorted(set(sources))
        source_summary = f", source={unique[0]}" if len(unique) == 1 else ", sources: " + ", ".join(f"{source} {sources.count(source)}" for source in unique)
    final = existing + written
    status = "OK" if final == total else "WARN"
    summary = f"existing {existing}/{total}, written {written}" if existing else f"tags {written}/{total}"
    return status, f"{summary}{source_summary}"


def _mood_status(output: str, total: int) -> tuple[str, str]:
    written = sum(1 for line in output.splitlines() if "mood=" in line and "mood=none" not in line and "action=" in line and "skipped" not in line)
    existing = _count_matching_lines(output, "skipped existing MOOD")
    low = _count_matching_lines(output, "confidence=low")
    final = existing + written
    status = "WARN" if low or final < total else "OK"
    summary = f"existing {existing}/{total}, written {written}" if existing else f"mood {written}/{total}"
    if low:
        summary += f", low confidence {low}/{total}"
    return status, summary


def _cover_status(result: CoverResult, apply: bool, force: bool) -> tuple[str, str]:
    total = result.total
    folder = "folder cover found" if result.local_cover else "folder cover skipped"
    if result.save_folder_cover:
        folder = f"saved {result.saved_path.name}" if result.saved_path else "folder cover missing"
    if result.embedded_existing == total and not force and not result.save_folder_cover:
        return "SKIP", f"embedded cover already present {total}/{total}"
    if result.image is None:
        return "WARN", "no cover found"
    if apply:
        return ("WARN" if result.errors or result.existing_after < total else "OK"), f"embedded {result.existing_after}/{total}, {folder}"
    write_count = total if force else max(0, total - result.embedded_existing)
    return "DRY", f"would write embedded {write_count}/{total}, {folder}"


def _lyrics_status(result: LyricsStats, apply: bool, force: bool) -> tuple[str, str]:
    total = result.total
    if result.embedded_existing == total and not force:
        return "SKIP", f"existing lyrics already present {total}/{total}"
    if result.errors:
        return "FAIL", result.errors[0]
    if not result.per_file:
        return "WARN", "no lyrics found"
    synced = result.synced_found
    if apply:
        status = "OK" if result.lyrics_after == total else "WARN"
        return status, f"embedded {result.lyrics_after}/{total}, synced {synced}/{total}"
    write_count = sum(1 for track in result.tracks if track.path in result.per_file and (force or not (has_embedded_lyrics(track.path) or get_tag(track, "lyrics"))))
    status = "DRY" if write_count else "SKIP"
    return status, f"would write embedded {write_count}/{total}, synced {synced}/{total}"


def _key_status(output: str, total: int) -> tuple[str, str]:
    written = _count_actions(output)
    skipped = _count_matching_lines(output, "action=skipped")
    return ("WARN" if skipped else "OK"), f"key {written}/{total}"


def _count_actions(output: str) -> int:
    return sum(1 for line in output.splitlines() if "action=wrote" in line or "action=would write" in line)


def _count_feature_actions(output: str, name: str) -> int:
    return sum(1 for line in output.splitlines() if name in line and ("action=wrote" in line or "action=would write" in line))


def _count_matching_lines(output: str, needle: str) -> int:
    return sum(1 for line in output.splitlines() if needle in line)


def _metadata_selected_candidate(attempts: list[Any], min_confidence: str) -> Any | None:
    allowed = {"high": 3, "medium": 2, "low": 1}
    minimum = allowed.get(min_confidence, 2)
    candidates = [candidate for attempt in attempts if attempt.status == "OK" for candidate in attempt.candidates]
    candidates = [candidate for candidate in candidates if candidate.provider != "musicbrainz" and allowed.get(candidate.confidence, 0) >= minimum]
    return max(candidates, key=lambda item: item.score, default=None)


def _metadata_provider_summary(active: list[str], attempts: list[Any], decisions: list[Any]) -> str:
    if any(attempt.status == "REVIEW" for attempt in attempts):
        writes = sum(1 for decision in decisions if decision.action == "write")
        return "discogs ambiguous editions, wrote safe fields only" if writes else "discogs ambiguous editions"
    warnings = [attempt for attempt in attempts if attempt.status in {"WARN", "SKIP"}]
    if warnings:
        return ", ".join(f"{attempt.provider} {attempt.message}" for attempt in warnings[:2])
    selected_fields = [decision.field.replace("_", " ") for decision in decisions if decision.action == "write"]
    roles = {"discogs": "catalog", "deezer": "fallback", "itunes": "fallback", "musicbrainz": "identity"}
    provider_summary = ", ".join(f"{source} {roles.get(source, 'fallback')}" for source in active)
    if selected_fields:
        return f"{provider_summary}, selected {'/'.join(selected_fields)}"
    return provider_summary


def _acoustid_identify_summary(attempts: list[Any], candidate: Any | None) -> str:
    total = 0
    if candidate:
        decisions = candidate.extra.get("decisions", [])
        total = len(decisions) if isinstance(decisions, list) else 0
    attempt = attempts[0] if attempts else None
    if attempt and "fpcalc not found" in attempt.message:
        return "fpcalc not found"
    fingerprint_count = int(candidate.extra.get("fingerprint_count", 0)) if candidate else 0
    match_count = int(candidate.extra.get("match_count", 0)) if candidate else 0
    if attempt and "lookup skipped" in attempt.message:
        return f"fingerprints {fingerprint_count}/{total}, lookup skipped no API key"
    if candidate and candidate.extra.get("conflicts"):
        return "recording IDs conflict with existing MBIDs"
    return f"fingerprints {fingerprint_count}/{total}, matches {match_count}/{total}"


def _musicbrainz_repaired_fields(plans: list[Any]) -> dict[Path, set[str]]:
    fields_by_path: dict[Path, set[str]] = {}
    field_names = {"MusicBrainz Album Id": "mb_album_id", "MusicBrainz Release Group Id": "mb_release_group_id", "MusicBrainz Track Id": "mb_track_id", "MusicBrainz Release Track Id": "mb_release_track_id", "MusicBrainz Album Artist Id": "mb_album_artist_id", "Original Date": "originaldate", "Label": "label"}
    for plan in plans:
        fields = {field_names[field] for field in plan.changes if field in field_names}
        if fields:
            fields_by_path[plan.path] = fields
    return fields_by_path
