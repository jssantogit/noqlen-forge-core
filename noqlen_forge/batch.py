from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .audio import is_audio_file
from .audit import audit_path


@dataclass(slots=True)
class BatchItem:
    path: Path
    status: str = "FAILED"
    code: int = 0


@dataclass(slots=True)
class BatchResult:
    items: list[BatchItem] = field(default_factory=list)
    stopped: bool = False
    cancelled: bool = False
    targets: list[Path] = field(default_factory=list)


def batch_targets(path: Path, recursive: bool = False) -> list[Path]:
    if _in_quarantine(path):
        return []
    if is_audio_file(path):
        return [path]
    if not path.is_dir():
        return []
    if recursive:
        return _recursive_targets(path)
    targets: list[Path] = []
    for child in sorted(path.iterdir()):
        if _in_quarantine(child):
            continue
        if is_audio_file(child):
            targets.append(child)
        elif child.is_dir() and _direct_audio_files(child):
            targets.append(child)
    return targets


def run_batch(path: Path, process: Callable[[Path, bool], int], apply: bool = False, recursive: bool = False, yes: bool = False, continue_on_review: bool = False) -> tuple[int, str]:
    result = run_batch_result(path, process=process, apply=apply, recursive=recursive, yes=yes, continue_on_review=continue_on_review)
    if not result.targets:
        return 1, "No batch targets found"
    final_code = 1 if result.cancelled or any(item.status in {"FAILED", "REVIEW"} for item in result.items) else 0
    return final_code, render_batch_summary(result, result.targets)


def run_batch_result(path: Path, process: Callable[[Path, bool], int], apply: bool = False, recursive: bool = False, yes: bool = False, continue_on_review: bool = False) -> BatchResult:
    targets = batch_targets(path, recursive=recursive)
    if not targets:
        return BatchResult(targets=[])
    if recursive and apply and len(targets) > 20 and not yes:
        return BatchResult(cancelled=True, targets=targets)
    result = BatchResult(targets=targets)
    for target in targets:
        code = process(target, apply)
        status = "FAILED"
        if code == 0:
            status = audit_path(target).status
        item = BatchItem(path=target, status=status, code=code)
        result.items.append(item)
        if status == "REVIEW" and not continue_on_review:
            result.stopped = True
            break
    return result


def render_batch_summary(result: BatchResult, targets: list[Path]) -> str:
    counts = {"OK": 0, "WARN": 0, "REVIEW": 0, "FAILED": 0}
    for item in result.items:
        counts[item.status] = counts.get(item.status, 0) + 1
    lines = ["Batch summary", f"Targets: {len(targets)}", f"OK: {counts['OK']}", f"WARN: {counts['WARN']}", f"REVIEW: {counts['REVIEW']}", f"FAILED: {counts['FAILED']}"]
    problems = [item for item in result.items if item.status in {"REVIEW", "FAILED"}]
    if problems:
        lines.append("Problem items:")
        for item in problems:
            lines.append(f"- {item.path}: {item.status}")
    if result.stopped:
        lines.append("Stopped on REVIEW. Use --continue-on-review to continue.")
    if result.cancelled:
        lines.append("Cancelled: recursive apply requires confirmation or --yes.")
    return "\n".join(lines)


def _recursive_targets(path: Path) -> list[Path]:
    targets: list[Path] = []
    seen_dirs: set[Path] = set()
    for child in sorted(path.rglob("*")):
        if _in_quarantine(child):
            continue
        if is_audio_file(child):
            if child.parent == path:
                targets.append(child)
            elif child.parent not in seen_dirs:
                targets.append(child.parent)
                seen_dirs.add(child.parent)
    return sorted(targets)


def _direct_audio_files(path: Path) -> list[Path]:
    return [child for child in path.iterdir() if is_audio_file(child)] if path.is_dir() else []


def _in_quarantine(path: Path) -> bool:
    return any(part == "Quarantine" for part in path.parts)
