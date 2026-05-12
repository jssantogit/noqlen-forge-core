from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .safety import AUTOMATED_VALIDATION_ENV


@dataclass(frozen=True, slots=True)
class DevCheckStep:
    name: str
    command: tuple[str, ...]
    display: str


@dataclass(frozen=True, slots=True)
class AffectedPlan:
    files: tuple[str, ...]
    areas: tuple[str, ...]
    suggestions: tuple[str, ...]


StepRunner = Callable[[DevCheckStep], int]


def build_dev_check_steps(args: argparse.Namespace) -> list[DevCheckStep]:
    mode = _dev_check_mode(args)
    timing = bool(getattr(args, "timing", False))
    area = getattr(args, "area", None)
    if mode == "changed":
        return _changed_steps(args)
    if area:
        return [_pytest_marker_step(area, AREA_MARKERS[area])]
    if mode == "smoke":
        return _smoke_steps()
    if mode == "quick":
        return [*_smoke_steps(), _pytest_marker_step("quick", "unit or contract")]
    if mode == "unit":
        return [_pytest_marker_step("unit", "unit")]
    if mode == "contract":
        return [_pytest_marker_step("contract", "contract")]
    if mode == "integration":
        return [_pytest_marker_step("integration", "integration and not slow and not lab")]
    if mode == "lab-quick":
        return [_lab_run_step(timing=timing, quick=True)]
    if mode == "lab":
        return _lab_steps(timing=timing)
    if mode == "release":
        return [*_full_steps(), _pytest_marker_step("release", "release")]
    if mode == "full":
        return _full_steps()
    return [*_smoke_steps(), _pytest_marker_step("quick", "unit or contract")]


def _full_steps() -> list[DevCheckStep]:
    return [_py_compile_step(), _pytest_step("full"), *_lab_steps(timing=False), _lab_run_step(timing=True, full=True)]


def dev_command(args: argparse.Namespace, runner: StepRunner | None = None) -> int:
    if args.dev_command == "affected":
        print(render_affected_plan(build_affected_plan(tuple(str(path) for path in getattr(args, "paths", ()) or ()), changed=False)))
        return 0
    if args.dev_command != "check":
        print("Unknown dev command")
        return 1
    if _dev_check_mode(args) == "changed" and runner is None:
        print(render_affected_plan(build_affected_plan(tuple(), changed=True)))
    runner = runner or run_dev_step
    steps = build_dev_check_steps(args)
    print(f"Development check: {_dev_check_mode(args)}")
    for step in steps:
        print(f"$ {step.display}")
        code = runner(step)
        if code != 0:
            print(f"FAIL: {step.name}")
            return code
    print("Development check: OK")
    return 0


def run_dev_step(step: DevCheckStep) -> int:
    env = os.environ.copy()
    if step.display.startswith("noqlen-forge dev lab ") or step.display.startswith("noqlen-forge lab "):
        env[AUTOMATED_VALIDATION_ENV] = "1"
    completed = subprocess.run(step.command, env=env, check=False)
    return completed.returncode


def _dev_check_mode(args: argparse.Namespace) -> str:
    if getattr(args, "area", None):
        return "area " + str(getattr(args, "area"))
    if getattr(args, "changed", False):
        return "changed"
    if getattr(args, "smoke", False):
        return "smoke"
    if getattr(args, "full", False):
        return "full"
    if getattr(args, "unit", False):
        return "unit"
    if getattr(args, "contract", False):
        return "contract"
    if getattr(args, "integration", False):
        return "integration"
    if getattr(args, "lab_quick", False):
        return "lab-quick"
    if getattr(args, "lab", False):
        return "lab"
    if getattr(args, "release", False):
        return "release"
    return "quick"


def _py_compile_step() -> DevCheckStep:
    files = tuple(str(path) for path in sorted(Path("noqlen_forge").glob("*.py")))
    return DevCheckStep(
        name="py_compile",
        command=(sys.executable, "-m", "py_compile", *files),
        display="python -m py_compile noqlen_forge/*.py",
    )


def _smoke_steps() -> list[DevCheckStep]:
    return [
        _py_compile_step(),
        _noqlen_forge_step("--help", "--help"),
        _noqlen_forge_step("db --help", "db", "--help"),
        _noqlen_forge_step("dev lab --help", "dev", "lab", "--help"),
        _noqlen_forge_step("dev --help", "dev", "--help"),
    ]


def _pytest_step(mode: str) -> DevCheckStep:
    command: tuple[str, ...]
    display: str
    if mode == "quick":
        command = (sys.executable, "-m", "pytest", "-q", "-m", "not slow")
        display = 'pytest -q -m "not slow"'
    elif mode == "full":
        command = (sys.executable, "-m", "pytest", "-q", "-m", "not lab")
        display = 'pytest -q -m "not lab"'
    else:
        command = (sys.executable, "-m", "pytest")
        display = "pytest"
    return DevCheckStep(name="pytest", command=command, display=display)


def _pytest_marker_step(name: str, marker: str) -> DevCheckStep:
    return DevCheckStep(
        name="pytest " + name,
        command=(sys.executable, "-m", "pytest", "-q", "-m", marker),
        display=f'pytest -q -m "{marker}"',
    )


def _lab_steps(timing: bool) -> list[DevCheckStep]:
    return [_lab_reset_step(), _lab_run_step(timing=timing, full=True)]


def _lab_reset_step() -> DevCheckStep:
    return _noqlen_forge_step("dev lab reset", "dev", "lab", "reset")


def _lab_run_step(timing: bool, quick: bool = False, full: bool = False) -> DevCheckStep:
    args = ["dev", "lab", "run"]
    display = "dev lab run"
    if quick:
        args.append("--quick")
        display += " --quick"
    if full:
        args.append("--full")
        display += " --full"
    if timing:
        args.append("--timing")
        display += " --timing"
    return _noqlen_forge_step(display, *args)


def _noqlen_forge_step(display: str, *args: str) -> DevCheckStep:
    return DevCheckStep(
        name="noqlen-forge " + display,
        command=(sys.executable, "-m", "noqlen_forge.cli", *args),
        display="noqlen-forge " + display,
    )


AREA_MARKERS = {
    "lyrics": "lyrics and not lab",
    "navidrome": "navidrome and not lab",
    "playlists": "playlist and not lab",
    "db": "db and not lab",
    "service": "service and not lab",
    "cli": "cli and not lab",
    "providers": "provider and not lab",
    "import": "integration and filesystem and not lab",
    "organize": "integration and filesystem and not lab",
    "sync": "integration and filesystem and not lab",
}


PATH_AREA_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("noqlen_forge/lyrics", ("lyrics", "provider")),
    ("noqlen_forge/navidrome", ("navidrome",)),
    ("noqlen_forge/smart_playlists", ("playlists",)),
    ("noqlen_forge/services", ("service", "contract")),
    ("noqlen_forge/db", ("db",)),
    ("noqlen_forge/cli", ("cli", "smoke")),
    ("noqlen_forge/importer", ("import",)),
    ("noqlen_forge/organize", ("organize",)),
    ("noqlen_forge/sync", ("sync",)),
    ("tests/test_lab", ("lab",)),
    ("tests/musiclab", ("lab",)),
)


def build_affected_plan(paths: tuple[str, ...], changed: bool = False) -> AffectedPlan:
    files = paths or (_git_changed_files() if changed else tuple())
    areas: list[str] = []
    for file in files:
        normalized = file.replace(os.sep, "/")
        for prefix, mapped in PATH_AREA_RULES:
            if normalized.startswith(prefix):
                areas.extend(mapped)
    unique_areas = tuple(dict.fromkeys(areas))
    suggestions = ["noqlen-forge dev check --smoke"]
    for area in unique_areas:
        if area == "smoke":
            continue
        if area == "contract":
            suggestions.append("noqlen-forge dev check --contract")
        elif area == "lab":
            suggestions.append("noqlen-forge dev check --lab-quick")
        elif area in AREA_MARKERS:
            suggestions.append(f"noqlen-forge dev check --area {area}")
    if not unique_areas:
        suggestions.append("noqlen-forge dev check --quick")
    suggestions.append("noqlen-forge dev check --full")
    return AffectedPlan(files=tuple(files), areas=unique_areas, suggestions=tuple(dict.fromkeys(suggestions)))


def render_affected_plan(plan: AffectedPlan) -> str:
    lines = ["Changed areas:"]
    lines.extend(f"- {area}" for area in plan.areas) if plan.areas else lines.append("- unknown")
    lines.extend(["", "Suggested:"])
    for suggestion in plan.suggestions[:-1]:
        lines.append(suggestion)
    lines.extend(["", "Before commit:", plan.suggestions[-1]])
    return "\n".join(lines)


def _changed_steps(args: argparse.Namespace) -> list[DevCheckStep]:
    plan = build_affected_plan(tuple(), changed=True)
    steps = [_smoke_steps()[0]]
    for area in plan.areas:
        if area == "contract":
            steps.append(_pytest_marker_step("contract", "contract"))
        elif area == "lab":
            steps.append(_lab_run_step(timing=bool(getattr(args, "timing", False)), quick=True))
        elif area in AREA_MARKERS:
            steps.append(_pytest_marker_step(area, AREA_MARKERS[area]))
    if len(steps) == 1:
        steps.append(_pytest_marker_step("quick", "unit or contract"))
    return steps


def _git_changed_files() -> tuple[str, ...]:
    completed = subprocess.run(("git", "diff", "--name-only", "HEAD"), check=False, capture_output=True, text=True)
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())
