from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from .lab_registry import LabScenario


@dataclass(slots=True)
class LabStep:
    index: int
    name: str
    status: str
    detail: str = ""
    duration: float | None = None


class LabRunRecorder:
    def __init__(self, scenarios: tuple[LabScenario, ...], *, mode: str, timing: bool = False) -> None:
        self.scenarios = scenarios
        self.mode = mode
        self.timing = timing
        self.steps: list[LabStep] = []
        self.started_at = perf_counter()
        self._last_step_at = self.started_at

    def add_step(self, index: int, name: str, status: str, detail: str = "") -> None:
        now = perf_counter()
        self.steps.append(LabStep(index, name, status, detail, now - self._last_step_at if self.timing else None))
        self._last_step_at = now

    @property
    def elapsed(self) -> float:
        return perf_counter() - self.started_at

    def render_success(self) -> str:
        lines = ["MusicLab validation", f"Scenarios: {len(self.scenarios)}", f"Mode: {self.mode}", ""]
        lines.extend(self._render_steps())
        passed = sum(1 for step in self.steps if step.status == "OK")
        skipped = sum(1 for step in self.steps if step.status == "SKIP")
        lines.extend(["", "Final:", f"Passed: {passed}", "Failed: 0", f"Skipped: {skipped}", f"Elapsed: {self.elapsed:.1f}s", "Status: OK", "", "MusicLab: OK"])
        return "\n".join(lines)

    def render_failure(self, failure_step: str, failure_command: str, log: str) -> str:
        lines = ["MusicLab validation", f"Scenarios: {len(self.scenarios)}", f"Mode: {self.mode}", ""]
        lines.extend(self._render_steps())
        skipped = sum(1 for step in self.steps if step.status == "SKIP")
        lines.extend(["", "Final:", f"Passed: {sum(1 for step in self.steps if step.status == 'OK')}", "Failed: 1", f"Skipped: {skipped}", f"Elapsed: {self.elapsed:.1f}s", "Status: FAIL", "", "MusicLab: FAIL", "Failure:", f"- {failure_step}: {failure_command}", "", "Logs:", log])
        return "\n".join(lines)

    def _render_steps(self) -> list[str]:
        total = len(self.steps)
        return [render_step(LabStep(index, step.name, step.status, step.detail, step.duration), total) for index, step in enumerate(self.steps, 1)]


def render_step(step: LabStep, total: int) -> str:
    return f"[{step.index}/{total}] {step.name:<22} {step.status:<6} {step.detail}{duration_suffix(step)}".rstrip()


def duration_suffix(step: LabStep) -> str:
    if step.duration is None:
        return ""
    return f"   {step.duration:.1f}s"
