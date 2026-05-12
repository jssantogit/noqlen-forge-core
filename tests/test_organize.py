from __future__ import annotations

from pathlib import Path

import pytest

from noqlen_forge.audio import Track
from noqlen_forge.config import default_config
from noqlen_forge.db import connect, get_counts, init_db, normalize_path, scan_library
from noqlen_forge.organize import build_destination, organize_path


def _config(tmp_path: Path) -> dict:
    config = default_config()
    config["database"]["path"] = str(tmp_path / "library.db")
    return config


def _track(path: Path, title: str = "Song/One", albumartist: str = "Artist", genre: str = "Metal", track: int | None = 1) -> Track:
    return Track(
        path=path,
        format=path.suffix.lower().lstrip("."),
        album="Album",
        albumartist=albumartist,
        artist="Artist",
        title=title,
        tracknumber=track,
        date="2026-05-05",
        duration=1.0,
        tags={"genre": [genre], "tracktotal": ["2"], "disc": ["1"], "disctotal": ["1"], "label": ["Label"], "release_type": ["Album"]},
    )


def _audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    return path


def test_template_basic_generates_safe_path(tmp_path: Path) -> None:
    source = _audio(tmp_path / "in" / "song.mp3")

    path = build_destination(_track(source), "$genre/$albumartist/$album/$track $title", "$genre/$artist/Singles/$title", "Compilations/$album/$track $title", album_file_count=2)

    assert path == Path("Metal/Artist/Album/01 Song One.mp3")


def test_template_missing_fields_are_unknown_and_track_is_padded(tmp_path: Path) -> None:
    source = _audio(tmp_path / "song.flac")
    track = _track(source, title="", albumartist="", genre="", track=3)
    track.tags = {}

    path = build_destination(track, "$genre/$albumartist/$album/$track $title", "$genre/$artist/Singles/$track $title", "Compilations/$album/$track $title", album_file_count=2)

    assert path == Path("Unknown/Artist/Album/03 song.flac")


def test_template_blocks_path_traversal(tmp_path: Path) -> None:
    source = _audio(tmp_path / "song.mp3")

    with pytest.raises(Exception, match="path traversal"):
        build_destination(_track(source), "../../$artist/$title", "$artist/$title", "Compilations/$title", album_file_count=2)


def test_dry_run_does_not_copy_or_write_db(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    library = tmp_path / "Library"
    monkeypatch.setattr("noqlen_forge.organize.read_track", lambda path: _track(path))

    result = organize_path(source.parent, config, apply=False, library=library)

    assert result.code == 0
    assert "Mode: DRY-RUN" in result.output
    assert not list(library.rglob("*.mp3"))
    assert not Path(config["database"]["path"]).exists()


def test_apply_copy_copies_and_updates_db(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    init_db(config)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    library = tmp_path / "Library"
    monkeypatch.setattr("noqlen_forge.organize.read_track", lambda path: _track(path))

    result = organize_path(source.parent, config, apply=True, mode="copy", library=library)

    destination = library / "Metal" / "Artist" / "Singles" / "Song One.mp3"
    assert result.code == 0
    assert source.exists()
    assert destination.exists()
    with connect(config) as conn:
        counts = get_counts(conn)
        row = conn.execute("SELECT path FROM files").fetchone()
        operation = conn.execute("SELECT operation, mode, status FROM operations ORDER BY id DESC LIMIT 1").fetchone()
    assert counts["files"] == 1
    assert row["path"] == normalize_path(destination)
    assert dict(operation) == {"operation": "organize", "mode": "copy", "status": "ok"}


def test_apply_move_moves_and_updates_db(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    init_db(config)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    library = tmp_path / "Library"
    monkeypatch.setattr("noqlen_forge.organize.read_track", lambda path: _track(path))

    result = organize_path(source.parent, config, apply=True, mode="move", library=library)

    destination = library / "Metal" / "Artist" / "Singles" / "Song One.mp3"
    assert result.code == 0
    assert not source.exists()
    assert destination.exists()
    with connect(config) as conn:
        assert conn.execute("SELECT path FROM files").fetchone()["path"] == normalize_path(destination)


def test_existing_conflict_is_review_by_default(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    existing = _audio(tmp_path / "Library" / "Metal" / "Artist" / "Singles" / "Song One.mp3")
    existing.write_bytes(b"existing")
    monkeypatch.setattr("noqlen_forge.organize.read_track", lambda path: _track(path))

    result = organize_path(source.parent, config, apply=True, library=tmp_path / "Library")

    assert result.code == 1
    assert "Status: REVIEW" in result.output
    assert existing.read_bytes() == b"existing"


def test_conflict_skip_and_rename(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    existing = _audio(tmp_path / "Library" / "Metal" / "Artist" / "Singles" / "Song One.mp3")
    existing.write_bytes(b"existing")
    monkeypatch.setattr("noqlen_forge.organize.read_track", lambda path: _track(path))

    skipped = organize_path(source.parent, config, apply=True, library=tmp_path / "Library", conflict_policy="skip")
    renamed = organize_path(source.parent, config, apply=True, library=tmp_path / "Library", conflict_policy="rename")

    assert skipped.code == 0
    assert "Skipped: 1" in skipped.output
    assert (tmp_path / "Library" / "Metal" / "Artist" / "Singles" / "Song One (1).mp3").exists()
    assert existing.read_bytes() == b"existing"


def test_duplicate_destination_in_same_run_is_review(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = _audio(tmp_path / "Incoming" / "a.mp3")
    second = _audio(tmp_path / "Incoming" / "b.mp3")
    monkeypatch.setattr("noqlen_forge.organize.read_track", lambda path: _track(path, title="Same"))

    result = organize_path(first.parent, config, apply=False, library=tmp_path / "Library")

    assert second.exists()
    assert result.code == 1
    assert "duplicate destination in run" in result.output


def test_destination_equal_source_is_blocked(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Library" / "Metal" / "Artist" / "Singles" / "Song One.mp3")
    monkeypatch.setattr("noqlen_forge.organize.read_track", lambda path: _track(path))

    result = organize_path(source, config, apply=True, library=tmp_path / "Library")

    assert result.code == 1
    assert "destination equals source" in result.output


def test_automated_apply_outside_musiclab_is_blocked(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")

    result = organize_path(source, config, apply=True, library=tmp_path / "Library")

    assert result.code == 1
    assert "Refusing automated --apply outside MusicLab" in result.output


def test_musiclab_apply_inside_marker_is_allowed(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    lab = tmp_path / "lab"
    (lab / ".noqlen-forge-lab").parent.mkdir(parents=True)
    (lab / ".noqlen-forge-lab").write_text("noqlen-forge lab\n", encoding="utf-8")
    source = _audio(lab / "Incoming" / "song.mp3")
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")
    monkeypatch.setattr("noqlen_forge.organize.read_track", lambda path: _track(path))

    result = organize_path(source, config, apply=True, library=lab / "Library")

    assert result.code == 0


def test_db_does_not_duplicate_after_scan(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    init_db(config)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    library = tmp_path / "Library"
    monkeypatch.setattr("noqlen_forge.organize.read_track", lambda path: _track(path))
    monkeypatch.setattr("noqlen_forge.db.read_track", lambda path: _track(path))

    organize_path(source.parent, config, apply=True, mode="copy", library=library)
    scan_library(config, library, apply=True)

    with connect(config) as conn:
        assert get_counts(conn)["files"] == 1
