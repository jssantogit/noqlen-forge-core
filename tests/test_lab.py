from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from noqlen_forge.lab import LAB_MARKER, LAB_SCENARIOS, lab_create, lab_list, lab_reset, lab_run


pytestmark = [pytest.mark.integration, pytest.mark.lab, pytest.mark.slow]


def test_lab_create_writes_marker_and_config(tmp_path: Path) -> None:
    lab = tmp_path / "noqlen-forge-lab"

    code, output = lab_create(lab)

    assert code == 0
    assert "Create: OK" in output
    assert (lab / LAB_MARKER).is_file()
    assert (lab / "config.toml").is_file()


def test_lab_create_writes_main_fixtures(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab fixtures")
    lab = tmp_path / "noqlen-forge-lab"

    code, output = lab_create(lab)

    assert code == 0
    assert "28 targets" in output
    for relative in (
        "Library/MusicLab Artist/Clean Album",
        "Library/MusicLab Artist/Dirty Album",
        "Library/MusicLab Artist/Partial Metadata",
        "Library/MusicLab Artist/Ambiguous Album",
        "Library/MusicLab Artist/Fallback Provider",
        "Library/MusicLab Artist/Existing Cover Lyrics",
        "Library/MusicLab Artist/AcoustID Cases",
        "Library/MusicLab Artist/Rewrite Album",
        "Library/MusicLab Duplicates/MB Track Duplicate",
        "Library/MusicLab Duplicates/AcoustID Duplicate",
        "Library/MusicLab Duplicates/Duration Duplicate",
        "Library/MusicLab Duplicates/Album Duplicate A",
        "Library/MusicLab Duplicates/Album Duplicate B",
        "Library/MusicLab Singles",
        "Incoming/Organize Copy",
        "Incoming/Organize Move",
        "Incoming/Organize Conflict",
        "Incoming/Organize Missing",
        "Incoming/Import Copy",
        "Incoming/Import Move",
        "Incoming/Import ReplayGain",
        "Incoming/Import Complete",
        "Incoming/Import Existing Cover Lyrics",
        "Incoming/Import Conflict",
        "Incoming/Import Review",
        "Incoming/Import Single",
    ):
        assert (lab / relative).exists()


def test_lab_reset_refuses_directory_without_marker(tmp_path: Path) -> None:
    unsafe = tmp_path / "not-lab"
    unsafe.mkdir()
    (unsafe / "file.txt").write_text("keep\n", encoding="utf-8")

    code, output = lab_reset(unsafe)

    assert code != 0
    assert "without .noqlen-forge-lab" in output
    assert unsafe.exists()


def test_lab_reset_allows_missing_safe_default_path(tmp_path: Path) -> None:
    missing = tmp_path / "noqlen-forge-lab"

    code, output = lab_reset(missing)

    assert code == 0
    assert "not present" in output


@pytest.mark.parametrize("path", [Path("/"), Path.home(), Path("/mnt/sdcard/Music"), Path("/mnt/sdcard/Music/Biblioteca de Musicas")])
def test_lab_safety_guard_blocks_dangerous_paths(path: Path) -> None:
    code, output = lab_create(path)

    assert code != 0
    assert "Refusing dangerous MusicLab path" in output


def test_lab_run_creates_isolated_database_and_fixtures(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab run")
    lab = tmp_path / "noqlen-forge-lab"

    code, output = lab_run(lab, quick=True)

    assert code == 0
    assert "MusicLab: OK" in output
    assert (lab / LAB_MARKER).is_file()
    assert (lab / "library.db").is_file()
    assert (lab / "Library").is_dir()
    assert not (Path.home() / "library.db").exists()


def test_lab_run_covers_real_world_scenarios(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab run")
    lab = tmp_path / "noqlen-forge-lab"

    code, output = lab_run(lab, quick=True)

    assert code == 0
    assert "Clean album" in output
    assert "Enrich dirty album" in output
    assert "Lyrics" in output
    assert "Navidrome" in output
    assert "Smart playlists" in output
    assert "Idempotency" in output


def test_lab_run_sets_automated_validation_for_internal_steps(monkeypatch, tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab run")
    lab = tmp_path / "noqlen-forge-lab"
    seen: dict[str, object] = {}

    def replaygain_check(path, report_dir, config, commands, stdout_chunks, stderr_chunks):
        seen["env"] = os.environ.get("NOQLEN_FORGE_AUTOMATED_VALIDATION")
        seen["path"] = path
        assert any((parent / LAB_MARKER).is_file() for parent in [path, *path.parents])
        return "SKIP: test replaygain"

    monkeypatch.setattr("noqlen_forge.lab._replaygain_check", replaygain_check)

    code, output = lab_run(lab, scenario="replaygain")

    assert code == 0
    assert "ReplayGain" in output
    assert seen["env"] == "1"
    assert str(seen["path"]).startswith(str(lab))
    assert os.environ.get("NOQLEN_FORGE_AUTOMATED_VALIDATION") is None


def test_lab_run_reports_are_created(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab run")
    lab = tmp_path / "noqlen-forge-lab"

    code, _ = lab_run(lab, scenario="lyrics")

    assert code == 0
    assert (lab / "Reports" / "latest-success.md").is_file()
    reports = [path for path in (lab / "Reports").iterdir() if path.is_dir()]
    assert reports
    latest = max(reports)
    assert (latest / "command.log").is_file()
    assert (latest / "stdout.log").is_file()
    assert (latest / "stderr.log").is_file()
    assert (latest / "summary.md").is_file()


def test_lab_run_is_idempotent_when_repeated(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab run")
    lab = tmp_path / "noqlen-forge-lab"

    first_code, first_output = lab_run(lab, quick=True)
    second_code, second_output = lab_run(lab, quick=True)

    assert first_code == 0
    assert second_code == 0
    assert "0 unexpected writes" in first_output
    assert "0 unexpected writes" in second_output


def test_lab_run_timing_adds_compact_step_durations(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab run")
    lab = tmp_path / "noqlen-forge-lab"

    code, output = lab_run(lab, scenario="lyrics", timing=True)

    assert code == 0
    assert "Create fixtures" in output
    assert "s" in output


def test_lab_run_returns_nonzero_and_report_on_simulated_failure(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab run")
    lab = tmp_path / "noqlen-forge-lab"

    code, output = lab_run(lab, simulate_failure=True)

    assert code != 0
    assert "MusicLab: FAIL" in output
    assert "Logs:" in output
    assert any((lab / "Reports").glob("*/enrich_dirty_album.log"))
    assert any((lab / "Reports").glob("*/command.log"))
    assert any((lab / "Reports").glob("*/stdout.log"))
    assert any((lab / "Reports").glob("*/stderr.log"))
    assert any((lab / "Reports").glob("*/summary.md"))


def test_lab_list_declares_scenarios() -> None:
    code, output = lab_list()

    assert code == 0
    assert "MusicLab scenarios" in output
    assert "lyrics" in output
    assert "navidrome" in output
    assert all(scenario.area for scenario in LAB_SCENARIOS)


def test_lab_run_scenario_runs_named_subset(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab run")
    lab = tmp_path / "noqlen-forge-lab"

    code, output = lab_run(lab, scenario="lyrics")

    assert code == 0
    assert "Lyrics" in output
    assert "Navidrome" not in output


def test_lab_run_area_runs_area_subset(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for full MusicLab run")
    lab = tmp_path / "noqlen-forge-lab"

    code, output = lab_run(lab, area="lyrics")

    assert code == 0
    assert "Lyrics" in output
    assert "Lyrics providers" in output
    assert "Navidrome" not in output


def test_lab_run_unknown_scenario_fails_fast(tmp_path: Path) -> None:
    code, output = lab_run(tmp_path / "noqlen-forge-lab", scenario="missing")

    assert code != 0
    assert "Unknown MusicLab scenario" in output
