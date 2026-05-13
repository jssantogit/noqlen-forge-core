from __future__ import annotations

from pathlib import Path
from typing import Any

PROTECTED_PATH_MARKERS = ("/mnt/", "/media/", "/storage/", "/sdcard/")


def assert_command_status(output: str, expected_status: str) -> None:
    """Assert a compact `Status: X` line without snapshotting full output."""
    expected = f"Status: {expected_status}"
    assert expected in output


def assert_status(result_or_output: Any, status: str) -> None:
    if isinstance(result_or_output, str):
        assert f"Status: {status}" in result_or_output or f"MusicLab: {status}" in result_or_output
        return
    summary = getattr(result_or_output, "summary", None)
    if isinstance(summary, dict):
        assert summary.get("status") == status
        return
    assert getattr(result_or_output, "status", None) == status


def assert_step(output: str, step_name: str, status: str) -> None:
    normalized = " ".join(output.split())
    assert step_name in output
    assert f"{step_name} {status}" in normalized


def assert_no_db_change(before: dict[str, int], after: dict[str, int]) -> None:
    assert after == before


def assert_no_file_change(before: dict[Path, tuple[int, str]], after: dict[Path, tuple[int, str]]) -> None:
    assert after == before


def assert_no_secrets(output: str) -> None:
    lowered = output.casefold()
    for token in ("password", "token", "salt", "api_key", "secret"):
        assert token not in lowered


def assert_json_stdout_clean(output: str) -> None:
    stripped = output.strip()
    assert stripped.startswith("{") or stripped.startswith("[")
    assert "Status:" not in stripped


def assert_json_clean(output: str) -> None:
    assert_json_stdout_clean(output)


def assert_machine_output_clean(output: str) -> None:
    stripped = output.strip()
    assert "Status:" not in stripped
    assert not stripped.startswith("Warnings:")


def assert_playlist_file_valid(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U") or path.suffix.lower() in {".json", ".csv"}


def assert_musiclab_safe_paths(*paths: Path) -> None:
    for path in paths:
        assert any((parent / ".noqlen-forge-lab").is_file() for parent in [path, *path.parents])


def assert_no_real_library_path(output: str) -> None:
    assert not any(marker in output for marker in PROTECTED_PATH_MARKERS)


def temp_db_config(path: Path) -> dict:
    return {"database": {"path": str(path)}}
