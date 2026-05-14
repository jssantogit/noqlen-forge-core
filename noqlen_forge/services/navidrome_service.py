from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..navidrome import NavidromeClient, playlists_backup, playlists_diff, playlists_list, playlists_push, playlists_push_smart, ratings_backup, ratings_diff, ratings_restore
from ..workflow import Status, StepResult, WorkflowResult


@dataclass(slots=True, frozen=True)
class NavidromeRatingsOptions:
    config: dict[str, Any]
    command: str
    apply: bool = False
    server: bool = False
    backup_only: bool = False
    restore_ratings: bool = True
    restore_starred: bool = True
    only_matched: bool = False
    allow_medium_confidence: bool = False
    force: bool = False
    preserve_server: bool = False
    output_format: str = "text"
    output: Path | None = None
    verbose: bool = False
    debug: bool = False
    client: NavidromeClient | None = None


@dataclass(slots=True, frozen=True)
class NavidromePlaylistsOptions:
    config: dict[str, Any]
    command: str
    query: str = ""
    smart_name: str = ""
    name: str | None = None
    playlist_id: str | None = None
    apply: bool = False
    replace: bool = False
    append: bool = False
    preserve_existing: bool = False
    allow_medium_confidence: bool = False
    force: bool = False
    sort: str | None = None
    reverse: bool = False
    limit: int | None = None
    path_mode: str = "absolute"
    library_root: Path | None = None
    output_format: str = "text"
    output: Path | None = None
    verbose: bool = False
    debug: bool = False
    client: NavidromeClient | None = None


def run_navidrome_ratings_service(options: NavidromeRatingsOptions) -> WorkflowResult:
    command = f"navidrome ratings {options.command}"
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    if options.command == "backup":
        code, output = ratings_backup(options.config, apply=options.apply, output=options.output, output_format=options.output_format, client=options.client)
    elif options.command == "diff":
        code, output = ratings_diff(options.config, server=options.server, backup_only=options.backup_only, output_format="json", output=options.output, verbose=options.verbose, debug=options.debug, client=options.client)
    elif options.command == "restore":
        code, output = ratings_restore(options.config, apply=options.apply, restore_ratings=options.restore_ratings, restore_starred=options.restore_starred, only_matched=options.only_matched, allow_medium_confidence=options.allow_medium_confidence, force=options.force, preserve_server=options.preserve_server, output_format="json", output=options.output, verbose=options.verbose, debug=options.debug, client=options.client)
    else:
        code, output = 1, f"Unsupported Navidrome ratings command: {options.command}"
    return _workflow(command, code, output, mode="apply" if options.apply else "read-only" if options.command == "diff" else "dry-run", started=started, started_at=started_at)


def run_navidrome_playlists_service(options: NavidromePlaylistsOptions) -> WorkflowResult:
    command = f"navidrome playlists {options.command}"
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    if options.command == "list":
        code, output = playlists_list(options.config, output_format="json", output=options.output, client=options.client, verbose=options.verbose, debug=options.debug)
    elif options.command == "backup":
        code, output = playlists_backup(options.config, apply=options.apply, playlist_id=options.playlist_id, name=options.name, output_format="json", output=options.output, client=options.client, verbose=options.verbose, debug=options.debug)
    elif options.command == "diff":
        code, output = playlists_diff(options.config, options.query, name=options.name, playlist_id=options.playlist_id, sort=options.sort, reverse=options.reverse, limit=options.limit, path_mode=options.path_mode, library_root=options.library_root, output_format="json", output=options.output, client=options.client, verbose=options.verbose, debug=options.debug)
    elif options.command == "push-smart":
        code, output = playlists_push_smart(options.config, options.smart_name, apply=options.apply, replace=options.replace, append=options.append, preserve_existing=options.preserve_existing, allow_medium_confidence=options.allow_medium_confidence, force=options.force, output_format="json", output=options.output, client=options.client, verbose=options.verbose, debug=options.debug)
    elif options.command == "push":
        code, output = playlists_push(options.config, options.query, name=options.name, playlist_id=options.playlist_id, apply=options.apply, replace=options.replace, append=options.append, preserve_existing=options.preserve_existing, allow_medium_confidence=options.allow_medium_confidence, force=options.force, sort=options.sort, reverse=options.reverse, limit=options.limit, path_mode=options.path_mode, library_root=options.library_root, output_format="json", output=options.output, client=options.client, verbose=options.verbose, debug=options.debug)
    else:
        code, output = 1, f"Unsupported Navidrome playlists command: {options.command}"
    return _workflow(command, code, output, mode="apply" if options.apply else "read-only" if options.command in {"list", "diff"} else "dry-run", started=started, started_at=started_at)


def _workflow(command: str, code: int, output: str, *, mode: str, started: float, started_at: datetime) -> WorkflowResult:
    payload = _payload_from_output(output)
    status = _status_from_payload(code, payload, output)
    summary = _summary_from_payload(payload, output)
    step = StepResult(1, 1, command, status, _step_summary(summary, output))
    return WorkflowResult(status, [step], workflow=command, command=command, mode=mode, started_at=started_at, finished_at=datetime.now(timezone.utc), summary=summary, counts=_counts_from_summary(summary), details={"exit_code": code, "output_text": output, "result": payload}, safe_details={"exit_code": code, "result": payload}, errors=[] if code == 0 else [summary.get("error") or output], elapsed_seconds=time.perf_counter() - started)


def _payload_from_output(output: str) -> dict[str, Any]:
    text = output.strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _status_from_payload(code: int, payload: dict[str, Any], output: str) -> Status:
    raw = str(payload.get("status") or _line_value(output, "Status") or ("FAIL" if code else "OK")).upper()
    if raw in Status.__members__:
        return Status[raw]
    return Status.FAIL if code else Status.OK


def _summary_from_payload(payload: dict[str, Any], output: str) -> dict[str, Any]:
    if payload:
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        return {"status": str(payload.get("status") or ""), "mode": str(payload.get("mode") or ""), **summary, "playlist": payload.get("playlist", {}) if isinstance(payload.get("playlist"), dict) else {}, "count": payload.get("count", summary.get("total_items", 0)), "error": str(payload.get("error") or payload.get("message") or "")}
    summary: dict[str, Any] = {"status": _line_value(output, "Status"), "mode": _line_value(output, "Mode")}
    for label, key in (("Items", "total_items"), ("Matched", "matched_items"), ("Unmatched", "unmatched_items"), ("Rated", "rated_items"), ("Starred", "starred_items"), ("Backups", "backups"), ("Playlists", "playlists")):
        value = _line_value(output, label)
        if value.isdigit():
            summary[key] = int(value)
    error = _line_value(output, "Error")
    if error:
        summary["error"] = error
    return summary


def _counts_from_summary(summary: dict[str, Any]) -> dict[str, int | float | str]:
    return {key: value for key, value in summary.items() if isinstance(value, (int, float, str)) and key not in {"status", "mode", "error"}}


def _step_summary(summary: dict[str, Any], output: str) -> str:
    if summary.get("error"):
        return str(summary["error"])
    for key in ("total_items", "local_tracks", "final_tracks", "count", "playlists"):
        if key in summary:
            return f"{summary[key]} {key.replace('_', ' ')}"
    return output.splitlines()[0] if output else "navidrome"


def _line_value(output: str, label: str) -> str:
    prefix = f"{label}:"
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""
