from __future__ import annotations

import os
from pathlib import Path

import pytest

from noqlen_forge import cli
from noqlen_forge.dev import build_affected_plan, build_dev_check_steps, dev_command, render_affected_plan, run_dev_step
from noqlen_forge.safety import AUTOMATED_VALIDATION_ENV


def _displays(argv: list[str]) -> list[str]:
    args = cli.build_parser().parse_args(argv)
    return [step.display for step in build_dev_check_steps(args)]


def test_dev_check_help_works(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["dev", "check", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "--quick" in output
    assert "--smoke" in output
    assert "--full" in output
    assert "--unit" in output
    assert "--contract" in output
    assert "--integration" in output
    assert "--lab" in output
    assert "--lab-quick" in output


def test_dev_check_defaults_to_quick_sequence() -> None:
    assert _displays(["dev", "check"]) == ["python -m py_compile noqlen_forge/*.py", "noqlen-forge --help", "noqlen-forge db --help", "noqlen-forge dev lab --help", "noqlen-forge dev --help", 'pytest -q -m "unit or contract"']


def test_dev_check_quick_runs_expected_sequence(capsys) -> None:
    args = cli.build_parser().parse_args(["dev", "check", "--quick"])
    calls = []

    code = dev_command(args, runner=lambda step: calls.append(step.display) or 0)

    assert code == 0
    assert calls == ["python -m py_compile noqlen_forge/*.py", "noqlen-forge --help", "noqlen-forge db --help", "noqlen-forge dev lab --help", "noqlen-forge dev --help", 'pytest -q -m "unit or contract"']
    assert "Development check: quick" in capsys.readouterr().out


def test_dev_check_full_runs_complete_commit_sequence() -> None:
    assert _displays(["dev", "check", "--full"]) == [
        "python -m py_compile noqlen_forge/*.py",
        'pytest -q -m "not lab"',
        "noqlen-forge dev lab reset",
        "noqlen-forge dev lab run --full",
        "noqlen-forge dev lab run --full --timing",
    ]


def test_dev_check_unit_runs_compile_and_pytest() -> None:
    assert _displays(["dev", "check", "--unit"]) == ['pytest -q -m "unit"']


def test_dev_check_smoke_runs_compile_and_help() -> None:
    assert _displays(["dev", "check", "--smoke"]) == ["python -m py_compile noqlen_forge/*.py", "noqlen-forge --help", "noqlen-forge db --help", "noqlen-forge dev lab --help", "noqlen-forge dev --help"]


def test_dev_check_contract_and_integration_modes() -> None:
    assert _displays(["dev", "check", "--contract"]) == ['pytest -q -m "contract"']
    assert _displays(["dev", "check", "--integration"]) == ['pytest -q -m "integration and not slow and not lab"']


def test_dev_check_area_uses_marker_without_lab() -> None:
    assert _displays(["dev", "check", "--area", "lyrics"]) == ['pytest -q -m "lyrics and not lab"']
    assert _displays(["dev", "check", "--area", "navidrome"]) == ['pytest -q -m "navidrome and not lab"']
    assert _displays(["dev", "check", "--area", "playlists"]) == ['pytest -q -m "playlist and not lab"']


def test_dev_check_lab_runs_musiclab_sequence() -> None:
    assert _displays(["dev", "check", "--lab"]) == ["noqlen-forge dev lab reset", "noqlen-forge dev lab run --full"]


def test_dev_check_lab_quick_runs_subset() -> None:
    assert _displays(["dev", "check", "--lab-quick"]) == ["noqlen-forge dev lab run --quick"]


def test_dev_check_lab_timing_passes_timing_to_lab_run() -> None:
    assert _displays(["dev", "check", "--lab", "--timing"]) == ["noqlen-forge dev lab reset", "noqlen-forge dev lab run --full --timing"]


def test_dev_check_stops_on_failed_step() -> None:
    args = cli.build_parser().parse_args(["dev", "check", "--full"])
    calls = []

    def runner(step):
        calls.append(step.display)
        return 7 if step.display == 'pytest -q -m "not lab"' else 0

    assert dev_command(args, runner=runner) == 7
    assert calls == ["python -m py_compile noqlen_forge/*.py", 'pytest -q -m "not lab"']


def test_dev_check_full_excludes_lab_pytest_before_external_lab() -> None:
    args = cli.build_parser().parse_args(["dev", "check", "--full"])
    pytest_steps = [step for step in build_dev_check_steps(args) if step.name == "pytest"]

    assert len(pytest_steps) == 1
    assert pytest_steps[0].command[-2:] == ("-m", "not lab")
    assert "noqlen-forge dev lab run --full" in _displays(["dev", "check", "--full"])


def test_run_dev_step_sets_automated_validation(monkeypatch) -> None:
    args = cli.build_parser().parse_args(["dev", "check", "--lab"])
    step = build_dev_check_steps(args)[0]
    seen = {}

    def fake_run(command, env, check):
        seen["command"] = command
        seen["env"] = env
        seen["check"] = check

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr("noqlen_forge.dev.subprocess.run", fake_run)

    assert run_dev_step(step) == 0
    assert seen["env"][AUTOMATED_VALIDATION_ENV] == "1"
    assert os.environ.get(AUTOMATED_VALIDATION_ENV) is None
    assert seen["check"] is False


def test_dev_check_commands_do_not_target_real_library_or_apply() -> None:
    for argv in (["dev", "check"], ["dev", "check", "--quick"], ["dev", "check", "--full"], ["dev", "check", "--unit"], ["dev", "check", "--lab", "--timing"], ["dev", "check", "--lab-quick"]):
        args = cli.build_parser().parse_args(argv)
        for step in build_dev_check_steps(args):
            assert "--apply" not in step.command


def test_docs_state_quick_never_replaces_full_before_commit() -> None:
    text = Path("docs/development/testing-and-musiclab.md").read_text(encoding="utf-8")

    assert "noqlen-forge dev check --quick" in text
    assert "noqlen-forge dev check --full" in text
    assert "Quick check never replaces full check before automatic commits." in text


def test_pytest_markers_exist() -> None:
    text = Path("pytest.ini").read_text(encoding="utf-8")

    assert "slow: slow integration tests" in text
    assert "lab: MusicLab integration tests" in text
    assert "integration: integration tests" in text
    assert "provider: provider integration or provider decision tests" in text
    assert "db: SQLite database tests" in text
    assert "unit: pure" in text
    assert "contract: interface" in text
    assert "lyrics: lyrics" in text
    assert "navidrome: Navidrome" in text
    assert "playlist: playlist" in text
    assert "network_fake: fake/mock" in text


def test_dev_affected_suggests_areas() -> None:
    plan = build_affected_plan(("noqlen_forge/lyrics.py", "noqlen_forge/services/lyrics_service.py"))

    assert plan.areas == ("lyrics", "provider", "service", "contract")
    rendered = render_affected_plan(plan)
    assert "noqlen-forge dev check --area lyrics" in rendered
    assert "noqlen-forge dev check --contract" in rendered
    assert "Before commit:" in rendered


def test_dev_affected_command_outputs_suggestions(capsys) -> None:
    args = cli.build_parser().parse_args(["dev", "affected", "noqlen_forge/navidrome.py"])

    assert dev_command(args) == 0
    output = capsys.readouterr().out
    assert "Changed areas:" in output
    assert "navidrome" in output
