from __future__ import annotations

from typing import Iterable

from .workflow import ChangePlan, StepResult, Status, WarningMessage, coerce_status


SENSITIVE_WORDS = ("lyrics", "lyric", "fingerprint", "secret", "token", "api_key", "apikey")


def render_status(status: Status | str) -> str:
    return coerce_status(status).value


def render_header(title: str, scope: str = "") -> str:
    return f"{title}\nScope: {_safe(scope)}" if scope else title


def render_steps(steps: Iterable[StepResult], *, verbose: bool = False, debug: bool = False) -> str:
    lines: list[str] = []
    for step in steps:
        lines.append(render_step(step))
        if verbose:
            lines.extend(f"  {_safe(detail, debug=debug)}" for detail in step.details)
        if debug and step.skipped_reason:
            lines.append(f"  skipped: {_safe(step.skipped_reason, debug=debug)}")
    return "\n".join(lines)


def render_step(step: StepResult) -> str:
    summary = _safe(step.summary)
    return f"[{step.index}/{step.total}] {step.name:<18} {render_status(step.status):<6} {summary}".rstrip()


def render_warnings(warnings: Iterable[WarningMessage | str], *, debug: bool = False) -> str:
    items = list(warnings)
    if not items:
        return ""
    lines = ["Warnings:"]
    for warning in items:
        text = warning.message if isinstance(warning, WarningMessage) else str(warning)
        lines.extend(format_warning(text, debug=debug).splitlines())
    return "\n".join(lines)


def format_warning(message: str, *, next_action: str = "", debug: bool = False, sanitize: bool = True) -> str:
    rendered_message = safe_text(message, debug=debug) if sanitize else _truncate(message)
    lines = [f"- {rendered_message}"]
    if next_action:
        rendered_action = safe_text(next_action, debug=debug) if sanitize else _truncate(next_action)
        lines.append(f"  Next: {rendered_action}")
    return "\n".join(lines)


def render_final_summary(status: Status | str, summary: dict[str, object] | None = None) -> str:
    lines = ["Final:"]
    for key, value in (summary or {}).items():
        lines.append(f"{str(key).replace('_', ' ').title()}: {safe_text(str(value))}")
    lines.append(f"Status: {render_status(status)}")
    return "\n".join(lines)


def render_plan(plan: ChangePlan, *, verbose: bool = False, debug: bool = False) -> str:
    summary = plan.summary()
    lines = ["Plan:", f"Writes: {summary['writes']}", f"Removals: {summary['removals']}", f"Skips: {summary['skips']}", f"Conflicts: {summary['conflicts']}"]
    if verbose:
        for change in [*plan.changes, *plan.removals, *plan.skips, *plan.conflicts]:
            lines.append(f"- {change.action} {change.target_path}: {change.field} ({safe_text(change.reason, debug=debug)})")
    lines.append(f"Status: {summary['status']}")
    return "\n".join(lines)


def safe_text(value: str, *, debug: bool = False) -> str:
    """Return compact human output without exposing sensitive payload classes."""
    text = str(value)
    lowered = text.casefold()
    if any(word in lowered for word in SENSITIVE_WORDS):
        return "[redacted sensitive output]" if not debug else _truncate(text)
    return _truncate(text)


def _safe(value: str, *, debug: bool = False) -> str:
    return safe_text(value, debug=debug)


def _truncate(value: str, limit: int = 300) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."
