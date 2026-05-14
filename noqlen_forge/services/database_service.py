from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import database_path, db_explain, db_query, db_status, execute_query, init_db, parse_query, render_status, scan_library
from ..db import connect_readonly, normalize_path
from ..workflow import Status, StepResult, WorkflowResult


@dataclass(frozen=True)
class DatabaseOptions:
    command: str
    config: dict[str, Any]
    path: Path | None = None
    query: str = ""
    target: str = "tracks"
    missing_field: str | None = None
    limit: int = 50
    output_format: str = "text"
    apply: bool = False
    field: str | None = None
    verbose: bool = False
    debug: bool = False


def run_database_service(options: DatabaseOptions) -> WorkflowResult:
    if options.command == "path":
        path = database_path(options.config)
        return _result("db.path", path, Status.OK, "Database path resolved", {"path": path})
    if options.command == "init":
        path = init_db(options.config)
        return _result("db.init", path, Status.OK, "Database initialized", {"path": path, "initialized": True}, mode="apply")
    if options.command == "status":
        status = db_status(options.config)
        path = Path(status["path"])
        summary = {"path": path, "schema_version": status["version"], "last_operation": status.get("last_operation")}
        return _result("db.status", path, Status.OK, "Database status loaded", summary, counts=dict(status["counts"]), details={"status": status})
    if options.command == "scan":
        return _scan_result(options)
    if options.command == "query":
        return _query_result(options)
    if options.command == "explain":
        return _explain_result(options)
    return _result(f"db.{options.command}", database_path(options.config), Status.FAIL, "Unknown database command", {}, errors=[f"Unknown database command: {options.command}"])


def render_database_service_result(result: WorkflowResult, *, output_format: str = "text") -> tuple[int, str]:
    code = 0 if result.status in {Status.OK, Status.DRY, Status.APPLY, Status.WARN} else 1
    if result.command == "db.path":
        return code, str(result.summary.get("path", ""))
    if result.command == "db.init":
        return code, f"Initialized database: {result.summary.get('path', '')}"
    if result.command == "db.status":
        return code, render_status(result.details["status"])
    if result.command in {"db.scan", "db.query", "db.explain"}:
        return code, str(result.details.get("rendered", "\n".join(result.errors)))
    return code, "\n".join(result.errors or [result.steps[-1].summary if result.steps else "Database command failed"])


def _scan_result(options: DatabaseOptions) -> WorkflowResult:
    if options.path is None:
        return _result("db.scan", database_path(options.config), Status.FAIL, "No scan path provided", {}, errors=["No scan path provided"])
    code, output = scan_library(options.config, options.path, apply=options.apply, verbose=options.verbose)
    status = Status.FAIL if code else Status.APPLY if options.apply else Status.DRY
    counts = _scan_counts(output)
    summary = {"path": database_path(options.config), "target": options.path, "apply": options.apply, "files_discovered": _scan_metric(output, "Discover files"), "files_read": _scan_read_metric(output)}
    return _result("db.scan", options.path, status, "Database scan completed", summary, counts=counts, details={"rendered": output, "summary": summary}, mode="apply" if options.apply else "dry-run", errors=[output] if code else [])


def _query_result(options: DatabaseOptions) -> WorkflowResult:
    path = database_path(options.config)
    conn = connect_readonly(options.config)
    if conn is None:
        message = f"Database not initialized: {path}"
        return _result("db.query", path, Status.FAIL, message, {"path": path, "query": options.query, "scope": options.target}, errors=[message])
    with conn:
        try:
            expression = options.query.strip()
            if options.missing_field:
                expression = " ".join(part for part in [expression, f"missing:{options.missing_field}"] if part)
            plan = parse_query(expression)
            rows = execute_query(conn, plan, options.target, options.limit)
        except ValueError as exc:
            message = str(exc)
            if not message.startswith("Unknown field:"):
                message = f"Invalid query: {message}"
            return _result("db.query", path, Status.FAIL, message, {"path": path, "query": options.query, "scope": options.target}, errors=[message])
        except sqlite3.DatabaseError as exc:
            message = f"Query failed: {exc}"
            return _result("db.query", path, Status.FAIL, message, {"path": path, "query": options.query, "scope": options.target}, errors=[message])
    code, rendered = db_query(options.config, options.query, target=options.target, missing_field=options.missing_field, limit=options.limit, output_format=options.output_format, verbose=options.verbose, debug=options.debug)
    safe_rows = _safe_query_rows(options.target, rows)
    status = Status.FAIL if code else Status.OK
    summary = {"path": path, "query": plan.raw, "scope": options.target, "limit": options.limit}
    return _result("db.query", path, status, f"{len(rows)} result(s)", summary, counts={"results": len(rows)}, details={"rendered": rendered, "rows": safe_rows}, safe_details={"rows": safe_rows}, errors=[rendered] if code else [])


def _explain_result(options: DatabaseOptions) -> WorkflowResult:
    path = database_path(options.config)
    if options.path is None:
        return _result("db.explain", path, Status.FAIL, "No explain path provided", {"path": path}, errors=["No explain path provided"])
    code, output = db_explain(options.config, options.path, field=options.field, verbose=options.verbose, debug=options.debug)
    status = Status.FAIL if code else Status.OK
    summary = {"path": path, "target": normalize_path(options.path), "field": options.field or "", "found": code == 0}
    return _result("db.explain", options.path, status, "Database explain completed" if code == 0 else output, summary, details={"rendered": output, "diagnostic": summary}, safe_details={"diagnostic": summary}, errors=[output] if code else [])


def _result(command: str, target: Path, status: Status, step_summary: str, summary: dict[str, Any], *, counts: dict[str, int] | None = None, details: dict[str, Any] | None = None, safe_details: dict[str, Any] | None = None, errors: list[str] | None = None, mode: str = "read-only") -> WorkflowResult:
    return WorkflowResult(status, [StepResult(1, 1, command, status, step_summary)], workflow=command, command=command, target=target, target_type="database", mode=mode, summary=summary, counts=counts or {}, details=details or summary, safe_details=safe_details or summary, errors=errors or [])


def _safe_query_rows(scope: str, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if scope == "albums":
            safe.append({"album": item.get("album") or "", "albumartist": item.get("albumartist") or "", "tracks": int(item.get("tracks") or 0)})
        elif scope == "files":
            safe.append({"path": item.get("path") or "", "title": item.get("title") or "", "artist": item.get("artist") or "", "album": item.get("album") or "", "status": item.get("status") or ""})
        else:
            safe.append({"title": item.get("title") or "", "artist": item.get("artist") or "", "album": item.get("album") or "", "albumartist": item.get("albumartist") or "", "path": item.get("path") or ""})
    return safe


def _scan_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, key in (("Albums:", "albums"), ("Tracks:", "tracks"), ("Files:", "files")):
        for line in output.splitlines():
            if line.startswith(label):
                try:
                    counts[key] = int(line.split("+", 1)[1].strip())
                except (IndexError, ValueError):
                    counts[key] = 0
    return counts


def _scan_metric(output: str, marker: str) -> int:
    for line in output.splitlines():
        if marker in line:
            words = line.split()
            for word in reversed(words):
                if word.isdigit():
                    return int(word)
    return 0


def _scan_read_metric(output: str) -> dict[str, int]:
    for line in output.splitlines():
        if "Read tags" in line:
            for word in line.split():
                if "/" in word:
                    left, _, right = word.partition("/")
                    if left.isdigit() and right.isdigit():
                        return {"read": int(left), "total": int(right)}
    return {"read": 0, "total": 0}
