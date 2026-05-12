from __future__ import annotations

from pathlib import Path

import pytest

from noqlen_forge.cli import main
from noqlen_forge.safety import SafetyError, is_dangerous_real_library_path, is_noqlen_forge_lab_path, require_lab_path_for_automated_apply


REAL_LIBRARY = Path("/mnt/sdcard/Music/Biblioteca de Musicas")


def test_dangerous_real_library_path_blocks_real_root() -> None:
    assert is_dangerous_real_library_path(REAL_LIBRARY)


def test_dangerous_real_library_path_blocks_real_subpaths() -> None:
    assert is_dangerous_real_library_path(REAL_LIBRARY / "Musicas" / "Artist" / "Album")


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


def test_require_lab_path_for_automated_apply_blocks_real_library() -> None:
    with pytest.raises(SafetyError) as error:
        require_lab_path_for_automated_apply(REAL_LIBRARY / "Musicas" / "Artist", context="test")

    message = str(error.value)
    assert "Refusing automated --apply outside MusicLab" in message
    assert "Target appears to be inside the real music library" in message
    assert "noqlen-forge dev lab run" in message


def test_cli_automated_apply_blocks_real_library(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")

    code = main(["replaygain", str(REAL_LIBRARY / "Musicas" / "Artist"), "--apply"])

    output = capsys.readouterr().out
    assert code == 1
    assert "Refusing automated --apply outside MusicLab" in output


def test_no_documented_real_library_apply_examples() -> None:
    root = Path.cwd()
    real_library_text = "/mnt/sdcard/Music" + "/Biblioteca de Musicas"
    checked_suffixes = {".md", ".py", ".sh"}
    ignored_parts = {".git", ".venv", ".pytest_cache", "__pycache__"}
    offenders: list[str] = []
    for path in root.rglob("*"):
        if path.is_dir() or path.suffix not in checked_suffixes or ignored_parts.intersection(path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if real_library_text in line and "--apply" in line:
                offenders.append(f"{path}:{number}: {line.strip()}")

    assert offenders == []
