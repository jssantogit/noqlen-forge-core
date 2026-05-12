from __future__ import annotations

from pathlib import Path

import pytest

from noqlen_forge.output import render_plan, render_step, render_warnings, safe_text
from noqlen_forge.safety import SafetyError
from noqlen_forge.workflow import ChangePlan, OperationContext, SafetyContext, Status, StepResult, WorkflowRunner, combine_status, status_is_blocking


def test_combine_status_uses_shared_priority() -> None:
    assert combine_status(Status.OK, Status.WARN, Status.REVIEW, Status.FAIL) == Status.FAIL
    assert combine_status(Status.OK, Status.SKIP) == Status.OK
    assert combine_status(Status.OK, Status.SKIP, skip_required=True) == Status.REVIEW


def test_step_result_renders_status_and_summary() -> None:
    output = render_step(StepResult(3, 8, "ReplayGain", Status.OK, "track 6/6, album 6/6"))

    assert output == "[3/8] ReplayGain         OK     track 6/6, album 6/6"


def test_workflow_runner_executes_steps_in_order() -> None:
    seen: list[int] = []
    context = OperationContext.from_flags("test")

    def first(_: OperationContext, index: int, total: int) -> StepResult:
        seen.append(1)
        return StepResult(index, total, "first", Status.OK)

    def second(_: OperationContext, index: int, total: int) -> StepResult:
        seen.append(2)
        return StepResult(index, total, "second", Status.OK)

    result = WorkflowRunner(context).run([first, second])

    assert seen == [1, 2]
    assert result.status == Status.OK


def test_workflow_runner_stops_on_fail() -> None:
    seen: list[str] = []
    context = OperationContext.from_flags("test")

    def fail(_: OperationContext, index: int, total: int) -> StepResult:
        seen.append("fail")
        return StepResult(index, total, "fail", Status.FAIL)

    def later(_: OperationContext, index: int, total: int) -> StepResult:
        seen.append("later")
        return StepResult(index, total, "later", Status.OK)

    result = WorkflowRunner(context).run([fail, later])

    assert seen == ["fail"]
    assert result.status == Status.FAIL
    assert result.stopped


def test_workflow_runner_propagates_review() -> None:
    context = OperationContext.from_flags("test")

    def review(_: OperationContext, index: int, total: int) -> StepResult:
        return StepResult(index, total, "review", Status.REVIEW)

    result = WorkflowRunner(context, stop_on_review=True).run([review])

    assert result.status == Status.REVIEW
    assert result.stopped
    assert status_is_blocking(result.status)


def test_operation_context_loads_basic_flags(tmp_path: Path) -> None:
    context = OperationContext.from_flags("cleanup", tmp_path, apply=True, verbose=True, debug=True, config={"x": 1})

    assert context.command == "cleanup"
    assert context.target == tmp_path
    assert context.apply
    assert context.verbose
    assert context.debug
    assert context.config == {"x": 1}


def test_safety_context_blocks_automated_apply_outside_musiclab(tmp_path: Path) -> None:
    context = SafetyContext(automated_validation=True, target_path=tmp_path / "Library")

    with pytest.raises(SafetyError):
        context.check_apply_allowed(True, context="test")


def test_safety_context_allows_automated_apply_inside_musiclab(tmp_path: Path) -> None:
    lab = tmp_path / "noqlen-forge-lab"
    target = lab / "Library"
    target.mkdir(parents=True)
    (lab / ".noqlen-forge-lab").write_text("noqlen-forge lab\n", encoding="utf-8")
    context = SafetyContext(automated_validation=True, target_path=target)

    context.check_apply_allowed(True, context="test")


def test_change_plan_records_actions_and_summary(tmp_path: Path) -> None:
    plan = ChangePlan()
    target = tmp_path / "song.flac"

    plan.add_write(target, "file", "STYLE", "", "K-pop", source="test", confidence="high")
    plan.add_remove(target, "file", "badtag", "")
    plan.add_skip(target, "file", "MOOD", "low confidence")
    plan.add_conflict(target, "file", "artist", "A", "B", "tag/db mismatch")

    assert plan.has_writes()
    assert plan.summary() == {"writes": 1, "removals": 1, "skips": 1, "conflicts": 1, "warnings": 0, "status": "REVIEW"}


def test_output_renderer_redacts_sensitive_payloads() -> None:
    assert "secret line" not in render_warnings(["lyrics secret line"])
    assert "fingerprint" not in render_plan(ChangePlan(), verbose=True).casefold()
    assert safe_text("api_key=secret-value") == "[redacted sensitive output]"
