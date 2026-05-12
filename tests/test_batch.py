from pathlib import Path

from noqlen_forge.audit import AuditResult
from noqlen_forge.audio import Track
from noqlen_forge.batch import batch_targets, run_batch
from noqlen_forge.cli import build_parser


def test_batch_parser_defaults_to_dry_run() -> None:
    args = build_parser().parse_args(["batch", "Library"])

    assert args.command == "batch"
    assert args.path == Path("Library")
    assert args.apply is False


def test_batch_processes_direct_children(tmp_path, monkeypatch) -> None:
    album = tmp_path / "Album"
    single = tmp_path / "Single"
    album.mkdir()
    single.mkdir()
    (album / "01.mp3").touch()
    (single / "song.flac").touch()
    (tmp_path / "loose.mp3").touch()

    targets = batch_targets(tmp_path)

    assert targets == [album, single, tmp_path / "loose.mp3"]


def test_batch_does_not_enter_recursively_without_recursive(tmp_path) -> None:
    artist = tmp_path / "Artist"
    album = artist / "Album"
    album.mkdir(parents=True)
    (album / "01.mp3").touch()

    assert batch_targets(tmp_path) == []


def test_batch_recursive_finds_nested_album(tmp_path) -> None:
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    (album / "01.mp3").touch()

    assert batch_targets(tmp_path, recursive=True) == [album]


def test_batch_skips_quarantine(tmp_path) -> None:
    quarantine = tmp_path / "Quarantine"
    album = quarantine / "Album"
    album.mkdir(parents=True)
    (album / "01.mp3").touch()

    good = tmp_path / "Album"
    good.mkdir()
    (good / "01.mp3").touch()

    assert batch_targets(tmp_path, recursive=True) == [good]


def test_batch_dry_run_passes_apply_false(monkeypatch, tmp_path) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    (album / "01.mp3").touch()
    calls = []
    monkeypatch.setattr("noqlen_forge.batch.audit_path", lambda path: AuditResult([Track(path=Path("x.mp3"), format="mp3")], []))

    run_batch(tmp_path, process=lambda target, apply: calls.append((target, apply)) or 0)

    assert calls == [(album, False)]


def test_batch_stops_on_review_by_default(monkeypatch, tmp_path) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    first.mkdir()
    second.mkdir()
    (first / "01.mp3").touch()
    (second / "01.mp3").touch()
    calls = []
    monkeypatch.setattr("noqlen_forge.batch.audit_path", lambda path: AuditResult([Track(path=Path("x.mp3"), format="mp3")], []))

    code, output = run_batch(tmp_path, process=lambda target, apply: calls.append(target) or 0)

    assert code == 1
    assert calls == [first]
    assert "REVIEW: 1" in output
    assert "Stopped on REVIEW" in output


def test_batch_continue_on_review_shows_summary(monkeypatch, tmp_path) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    first.mkdir()
    second.mkdir()
    (first / "01.mp3").touch()
    (second / "01.mp3").touch()
    monkeypatch.setattr("noqlen_forge.batch.audit_path", lambda path: AuditResult([Track(path=Path("x.mp3"), format="mp3")], []))

    code, output = run_batch(tmp_path, process=lambda target, apply: 0, continue_on_review=True)

    assert code == 1
    assert "Targets: 2" in output
    assert "REVIEW: 2" in output


def test_batch_summary_counts_failed(monkeypatch, tmp_path) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    (album / "01.mp3").touch()

    code, output = run_batch(tmp_path, process=lambda target, apply: 1, continue_on_review=True)

    assert code == 1
    assert "FAILED: 1" in output
    assert "Problem items:" in output


def test_batch_recursive_apply_requires_confirmation(monkeypatch, tmp_path) -> None:
    for index in range(21):
        album = tmp_path / f"Album {index}"
        album.mkdir()
        (album / "01.mp3").touch()
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    code, output = run_batch(tmp_path, process=lambda target, apply: 0, apply=True, recursive=True)

    assert code == 1
    assert "Cancelled" in output
