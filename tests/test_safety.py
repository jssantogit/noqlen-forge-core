from __future__ import annotations

from pathlib import Path

import pytest

from noqlen_forge.cli import main
from noqlen_forge.safety import PROTECTED_LIBRARY_ROOTS_ENV, SafetyError, is_dangerous_real_library_path, is_noqlen_forge_lab_path, require_lab_path_for_automated_apply


FAKE_PROTECTED_LIBRARY = Path("/tmp/noqlen-forge-protected-library")


def test_dangerous_real_library_path_blocks_generic_mount_tree() -> None:
    assert is_dangerous_real_library_path(Path("/mnt/library"))


def test_dangerous_real_library_path_blocks_configured_protected_subpaths() -> None:
    assert is_dangerous_real_library_path(FAKE_PROTECTED_LIBRARY / "Artist" / "Album", protected_roots=(FAKE_PROTECTED_LIBRARY,))


def test_dangerous_real_library_path_uses_env_protected_roots(monkeypatch) -> None:
    monkeypatch.setenv(PROTECTED_LIBRARY_ROOTS_ENV, str(FAKE_PROTECTED_LIBRARY))

    assert is_dangerous_real_library_path(FAKE_PROTECTED_LIBRARY / "Artist")


def test_noqlen_forge_lab_path_detects_parent_marker(tmp_path: Path) -> None:
    lab = tmp_path / "noqlen-forge-lab"
    target = lab / "Library" / "Artist" / "Album"
    target.mkdir(parents=True)
    (lab / ".noqlen-forge-lab").write_text("noqlen-forge lab\n", encoding="utf-8")

    assert is_noqlen_forge_lab_path(target)


def test_require_lab_path_for_automated_apply_allows_musiclab(tmp_path: Path) -> None:
    lab = tmp_path / "noqlen-forge-lab"
    target = lab / "Library" / "Artist" / "Album"
    target.mkdir(parents=True)
    (lab / ".noqlen-forge-lab").write_text("noqlen-forge lab\n", encoding="utf-8")

    require_lab_path_for_automated_apply(target, context="test")


def test_require_lab_path_for_automated_apply_blocks_dangerous_root() -> None:
    with pytest.raises(SafetyError) as error:
        require_lab_path_for_automated_apply(Path("/mnt/library/Artist"), context="test")

    message = str(error.value)
    assert "Refusing automated --apply outside MusicLab" in message
    assert "dangerous filesystem root or protected library location" in message
    assert "noqlen-forge dev lab run" in message


def test_cli_automated_apply_blocks_real_library(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")
    monkeypatch.setenv(PROTECTED_LIBRARY_ROOTS_ENV, str(FAKE_PROTECTED_LIBRARY))

    code = main(["replaygain", str(FAKE_PROTECTED_LIBRARY / "Artist"), "--apply"])

    output = capsys.readouterr().out
    assert code == 1
    assert "Refusing automated --apply outside MusicLab" in output


def test_no_documented_real_library_apply_examples() -> None:
    root = Path.cwd()
    checked_suffixes = {".md", ".py", ".sh"}
    ignored_parts = {".git", ".venv", ".pytest_cache", "__pycache__"}
    prohibited_constants = ("REAL_LIBRARY" + "_ROOT", "REAL_LIBRARY" + "_PATH")
    offenders: list[str] = []
    for path in root.rglob("*"):
        if path.is_dir() or path.suffix not in checked_suffixes or ignored_parts.intersection(path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if any(name in line for name in prohibited_constants):
                offenders.append(f"{path}:{number}: {line.strip()}")

    assert offenders == []
