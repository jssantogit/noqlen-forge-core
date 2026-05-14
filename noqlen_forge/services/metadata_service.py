from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..audio import mb_album_ids, read_tracks
from ..metadata_providers import _manual_discogs_selection_required, _select_acoustid_fingerprint_candidate, _select_candidate, acoustid_plans_from_candidate, build_context, fetch_metadata_with_providers, merge_ambiguous_discogs_common_fields, merge_candidate, metadata_status, plans_from_decisions, render_metadata_output, resolve_metadata_providers
from ..musicbrainz import get_release, hydrate_releases, search_releases
from ..review import review_command as run_review_command
from ..scoring import rank_releases, score_release
from ..workflow import Status, StepResult, WorkflowResult
from ..writers import apply_musicbrainz_writes, plan_musicbrainz_writes, summarize_plans
from .result_helpers import finish_text_result, status_from_text_output


@dataclass(slots=True)
class MetadataOptions:
    path: Path
    config: dict[str, Any] | None = None
    apply: bool = False
    force: bool = False
    providers: list[str] | None = None
    min_confidence: str = "medium"
    verbose: bool = False
    debug: bool = False
    allow_more_providers: bool = False
    discogs_release_id: str = ""
    candidate_index: int | None = None
    itunes_storefront: str = ""


@dataclass(slots=True)
class CandidatesOptions:
    path: Path


@dataclass(slots=True)
class ApplyMBIDOptions:
    path: Path
    release_id: str | None = None
    apply: bool = False
    force: bool = False
    confirm_medium_confidence: bool = False


@dataclass(slots=True)
class ReviewOptions:
    config: dict[str, Any]
    review_args: list[str]
    output_format: str = "text"
    verbose: bool = False
    action: str | None = None
    value: str | None = None
    field: str | None = None
    apply: bool = False
    force: bool = False


def run_metadata_service(options: MetadataOptions) -> WorkflowResult:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    tracks = read_tracks(options.path)
    if not tracks:
        return _text_result("metadata", options.path, 1, "No supported audio files found", started, started_at)
    context = build_context(options.path, tracks)
    selection = resolve_metadata_providers(options.config or {}, providers=options.providers, allow_more_providers=options.allow_more_providers)
    attempts = fetch_metadata_with_providers(context, selection.active, config=options.config, debug=options.debug, discogs_release_id=options.discogs_release_id, candidate_index=options.candidate_index, itunes_storefront=options.itunes_storefront)
    selected = _select_candidate(attempts, options.min_confidence)
    if selected is None:
        selected = _select_acoustid_fingerprint_candidate(attempts)
    decisions = merge_candidate(context, selected, min_confidence=options.min_confidence, force=options.force) if selected else merge_ambiguous_discogs_common_fields(context, attempts, min_confidence=options.min_confidence, force=options.force)
    plans = acoustid_plans_from_candidate(tracks, selected, force=options.force) if selected and selected.provider == "acoustid" else plans_from_decisions(tracks, decisions)
    manual_discogs_selection_required = _manual_discogs_selection_required(attempts)
    errors = apply_musicbrainz_writes(plans, apply=options.apply and not manual_discogs_selection_required)
    if errors:
        return _text_result("metadata", options.path, 1, "Metadata write verification failed:\n" + "\n".join(f"- {error}" for error in errors), started, started_at, errors=errors)
    status_text = metadata_status(attempts, decisions, selected)
    output = render_metadata_output(context, attempts, decisions, apply=options.apply, status=status_text, verbose=options.verbose, debug=options.debug, selection=selection, manual_discogs_selection_required=manual_discogs_selection_required)
    code = 0 if status_text != "REVIEW" else 1
    status = Status(status_text)
    step = StepResult(1, 1, "Metadata providers", status, f"{len(attempts)} providers, {len(decisions)} decisions")
    result = WorkflowResult(status, [step], workflow="metadata", command="metadata", target=options.path, target_type=context.target_type, mode="apply" if options.apply else "dry-run", started_at=started_at, finished_at=datetime.now(timezone.utc), summary={"status": status.value, "providers": len(attempts), "decisions": len(decisions), "planned_writes": len(plans)}, counts={"files": len(tracks), "providers": len(attempts), "decisions": len(decisions), "planned_writes": len(plans)}, details={"exit_code": code, "output_text": output, "providers": [_attempt_summary(item) for item in attempts], "decisions": [_decision_summary(item) for item in decisions], "selected_candidate": _candidate_summary(selected), "selection": {"active": selection.active, "skipped": selection.skipped, "roles": selection.roles}}, safe_details={"exit_code": code, "providers": [_attempt_summary(item) for item in attempts], "decisions": [_decision_summary(item) for item in decisions], "selected_candidate": _candidate_summary(selected), "selection": {"active": selection.active, "skipped": selection.skipped, "roles": selection.roles}}, elapsed_seconds=time.perf_counter() - started)
    return result


def run_candidates_service(options: CandidatesOptions) -> WorkflowResult:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    tracks = read_tracks(options.path)
    if not tracks:
        return _text_result("candidates", options.path, 1, "No supported audio files found", started, started_at)
    ranked = rank_releases(tracks, hydrate_releases(search_releases(tracks)))
    lines: list[str] = []
    for item in ranked:
        release = item.release
        lines.append(f"{item.score:3d} {release.get('id')} {release.get('title')} {release.get('date', '')} {release.get('country', '')}")
        lines.append("    " + "; ".join(item.reasons))
    if not ranked:
        return _text_result("candidates", options.path, 1, "No matching release candidates were found. Try --release-id UUID or check artist/album/title tags.", started, started_at)
    output = "\n".join(lines)
    details = {"exit_code": 0, "output_text": output, "candidates": [_release_summary(item) for item in ranked]}
    return WorkflowResult(Status.OK, [StepResult(1, 1, "Rank MusicBrainz releases", Status.OK, f"{len(ranked)} candidates")], workflow="candidates", command="candidates", target=options.path, mode="read-only", started_at=started_at, finished_at=datetime.now(timezone.utc), summary={"status": "OK", "candidates": len(ranked)}, counts={"files": len(tracks), "candidates": len(ranked)}, details=details, safe_details={key: value for key, value in details.items() if key != "output_text"}, elapsed_seconds=time.perf_counter() - started)


def run_apply_mbid_service(options: ApplyMBIDOptions) -> WorkflowResult:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    tracks = read_tracks(options.path)
    if not tracks:
        return _text_result("apply-mbid", options.path, 1, "No supported audio files found", started, started_at)
    existing = mb_album_ids(tracks)
    if existing and not options.force:
        output = f"Existing MusicBrainz Album Id found on all/some tracks: {', '.join(sorted(existing))}\nNot overwriting without --force"
        return _text_result("apply-mbid", options.path, 0, output, started, started_at, status=Status.OK)
    if options.release_id:
        release = get_release(options.release_id)
        scored = score_release(tracks, release)
    else:
        ranked = rank_releases(tracks, hydrate_releases(search_releases(tracks)))
        if not ranked:
            return _text_result("apply-mbid", options.path, 1, "No matching release candidates were found. Try --release-id UUID or check artist/album/title tags.", started, started_at)
        scored = ranked[0]
        release = scored.release
    selected_line = f"Selected score={scored.score} release={release.get('id')} title={release.get('title')}"
    if scored.score < 80 and not options.release_id:
        return _text_result("apply-mbid", options.path, 1, selected_line + "\nScore below 80; review required before applying MusicBrainz IDs.", started, started_at, status=Status.REVIEW)
    if 80 <= scored.score < 95 and options.apply and not options.release_id and not options.confirm_medium_confidence:
        output = selected_line + "\nScore below 95; explicit confirmation is required before applying MusicBrainz IDs."
        result = _text_result("apply-mbid", options.path, 1, output, started, started_at, status=Status.REVIEW)
        result.summary["requires_confirmation"] = True
        result.safe_details["requires_confirmation"] = True
        return result
    plans = plan_musicbrainz_writes(tracks, release, force=options.force)
    errors = apply_musicbrainz_writes(plans, apply=options.apply)
    if errors:
        output = "MusicBrainz write verification failed:\n" + "\n".join(f"- {error}" for error in errors)
        return _text_result("apply-mbid", options.path, 1, output, started, started_at, errors=errors)
    output = selected_line + "\n" + summarize_plans(plans, apply=options.apply)
    status = Status.APPLY if options.apply else Status.DRY
    details = {"exit_code": 0, "output_text": output, "selected_release": _scored_release_summary(scored), "planned_writes": len(plans)}
    return WorkflowResult(status, [StepResult(1, 1, "Plan MusicBrainz writes", status, f"{len(plans)} files")], workflow="apply-mbid", command="apply-mbid", target=options.path, mode="apply" if options.apply else "dry-run", started_at=started_at, finished_at=datetime.now(timezone.utc), summary={"status": status.value, "planned_writes": len(plans), "selected_release_id": str(release.get("id") or "")}, counts={"files": len(tracks), "planned_writes": len(plans)}, details=details, safe_details={key: value for key, value in details.items() if key != "output_text"}, elapsed_seconds=time.perf_counter() - started)


def run_review_service(options: ReviewOptions) -> WorkflowResult:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    code, output = run_review_command(options.config, options.review_args, output_format=options.output_format, verbose=options.verbose, action=options.action, value=options.value, field=options.field, apply=options.apply, force=options.force)
    status = status_from_text_output(code, output, default=Status.OK if code == 0 else Status.FAIL)
    if "Status: REVIEW" in output:
        status = Status.REVIEW
    result = WorkflowResult(status, [StepResult(1, 1, "Review", status, output.splitlines()[0] if output else "review")], workflow="review", command="review", mode="apply" if options.apply else "dry-run", started_at=started_at, finished_at=datetime.now(timezone.utc), elapsed_seconds=time.perf_counter() - started)
    return finish_text_result(result, code=code, output=output, mode="apply" if options.apply else "dry-run", status=status)


def _text_result(workflow: str, target: Path | None, code: int, output: str, started: float, started_at: datetime, *, status: Status | None = None, errors: list[str] | None = None) -> WorkflowResult:
    final_status = status or status_from_text_output(code, output, default=Status.FAIL if code else Status.OK)
    result = WorkflowResult(final_status, [StepResult(1, 1, workflow, final_status, output.splitlines()[0] if output else "")], workflow=workflow, command=workflow, target=target, mode="dry-run", started_at=started_at, finished_at=datetime.now(timezone.utc), elapsed_seconds=time.perf_counter() - started, errors=errors or ([] if code == 0 else [output]))
    return finish_text_result(result, code=code, output=output, mode="dry-run", status=final_status)


def _attempt_summary(attempt: Any) -> dict[str, Any]:
    return {"provider": attempt.provider, "status": attempt.status, "message": attempt.message, "candidates": [_candidate_summary(item) for item in attempt.candidates[:5]]}


def _candidate_summary(candidate: Any | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    fields = ("provider", "source_id", "confidence", "score", "match_reason", "album", "albumartist", "artist", "title", "date", "label", "genre", "style", "country", "barcode", "catalog_number", "media", "release_format", "release_type", "mb_album_id", "mb_release_group_id", "mb_track_id", "mb_release_track_id", "acoustid_id")
    return {field: str(getattr(candidate, field, "") or "") for field in fields if getattr(candidate, field, "")}


def _decision_summary(decision: Any) -> dict[str, str]:
    return {"field": decision.field, "current_value": decision.current_value, "candidate_value": decision.candidate_value, "provider": decision.provider, "confidence": decision.confidence, "action": decision.action, "reason": decision.reason}


def _release_summary(item: Any) -> dict[str, Any]:
    release = item.release
    return {"score": item.score, "release_id": str(release.get("id") or ""), "title": str(release.get("title") or ""), "date": str(release.get("date") or ""), "country": str(release.get("country") or ""), "reasons": list(item.reasons)}


def _scored_release_summary(item: Any) -> dict[str, Any]:
    return _release_summary(item)
