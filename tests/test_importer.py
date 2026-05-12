from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from noqlen_forge.audio import Track
from noqlen_forge.config import default_config
from noqlen_forge.db import connect, get_counts, init_db
from noqlen_forge.importer import import_path
from noqlen_forge.organize import OrganizeItem, OrganizeResult


def _config(tmp_path: Path) -> dict:
    config = default_config()
    config["database"]["path"] = str(tmp_path / "library.db")
    config["import"]["library_path"] = str(tmp_path / "Library")
    return config


def _audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    return path


def _track(path: Path) -> Track:
    return Track(path, "mp3", "Import Album", "Import Artist", "Import Artist", path.stem, 1, "2026", 1.0, {"mb_album_id": ["a"], "mb_track_id": ["t"], "mb_release_group_id": ["g"], "genre": ["Rock"]})


def _ok_audit():
    return SimpleNamespace(status="OK", bad_fields=[])


def test_import_dry_run_does_not_write_copy_or_db(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    library = tmp_path / "Library"
    monkeypatch.setattr("noqlen_forge.importer.audit_path", lambda path: _ok_audit())
    monkeypatch.setattr("noqlen_forge.importer.read_tracks", lambda path: [_track(source)])
    monkeypatch.setattr("noqlen_forge.importer.organize_path", lambda *args, **kwargs: OrganizeResult(0, "", items=[OrganizeItem(source, library / "song.mp3", _track(source))]))

    result = import_path(source.parent, config, apply=False, library=library)

    assert result.code == 0
    assert "Mode: DRY-RUN" in result.output
    assert "DB update" in result.output
    assert not list(library.rglob("*.mp3"))
    assert not Path(config["database"]["path"]).exists()


def test_import_apply_uses_organize_and_records_import(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    init_db(config)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    destination = tmp_path / "Library" / "song.mp3"
    calls: list[str] = []
    monkeypatch.setattr("noqlen_forge.importer.audit_path", lambda path: _ok_audit())
    monkeypatch.setattr("noqlen_forge.importer.read_tracks", lambda path: [_track(source)])
    monkeypatch.setattr("noqlen_forge.importer.cover_path", lambda *args, **kwargs: (0, "Cover: OK"))
    monkeypatch.setattr("noqlen_forge.importer.lyrics_path", lambda *args, **kwargs: (0, "Lyrics: OK"))
    monkeypatch.setattr("noqlen_forge.importer.scan_library", lambda *args, **kwargs: (0, "scan ok"))

    def organize(*args, **kwargs):
        if kwargs.get("apply"):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            calls.append("apply")
            return OrganizeResult(0, "", copied=1, status="OK", items=[OrganizeItem(source, destination, _track(source))])
        calls.append("dry")
        return OrganizeResult(0, "", items=[OrganizeItem(source, destination, _track(source))])

    monkeypatch.setattr("noqlen_forge.importer.organize_path", organize)

    result = import_path(source.parent, config, apply=True, library=tmp_path / "Library", skip_enrich=True)

    assert result.code == 0
    assert destination.exists()
    assert calls == ["dry", "apply"]
    with connect(config) as conn:
        operation = conn.execute("SELECT operation, mode, status FROM operations ORDER BY id DESC LIMIT 1").fetchone()
    assert dict(operation) == {"operation": "import", "mode": "copy", "status": "ok"}


def test_import_apply_move_removes_source_via_organize(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    destination = tmp_path / "Library" / "song.mp3"
    monkeypatch.setattr("noqlen_forge.importer.audit_path", lambda path: _ok_audit())
    monkeypatch.setattr("noqlen_forge.importer.read_tracks", lambda path: [_track(source)])
    monkeypatch.setattr("noqlen_forge.importer.cover_path", lambda *args, **kwargs: (0, "Cover: OK"))
    monkeypatch.setattr("noqlen_forge.importer.lyrics_path", lambda *args, **kwargs: (0, "Lyrics: OK"))
    monkeypatch.setattr("noqlen_forge.importer.scan_library", lambda *args, **kwargs: (0, "scan ok"))

    def organize(*args, **kwargs):
        if kwargs.get("apply"):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            source.unlink()
            return OrganizeResult(0, "", moved=1, status="OK", items=[OrganizeItem(source, destination, _track(destination))])
        return OrganizeResult(0, "", items=[OrganizeItem(source, destination, _track(source))])

    monkeypatch.setattr("noqlen_forge.importer.organize_path", organize)

    result = import_path(source.parent, config, apply=True, library=tmp_path / "Library", mode="move", skip_enrich=True)

    assert result.code == 0
    assert not source.exists()
    assert destination.exists()
    assert "Moved: 1" in result.output


def test_import_stops_on_review_before_organize(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    monkeypatch.setattr("noqlen_forge.importer.read_tracks", lambda path: [_track(source)])
    monkeypatch.setattr("noqlen_forge.importer.audit_path", lambda path: SimpleNamespace(status="REVIEW", bad_fields=["BPM=0"]))
    called = False

    def organize(*args, **kwargs):
        nonlocal called
        called = True
        return OrganizeResult(0, "")

    monkeypatch.setattr("noqlen_forge.importer.organize_path", organize)

    result = import_path(source.parent, config, apply=True, library=tmp_path / "Library", skip_enrich=True)

    assert result.code == 1
    assert result.status == "REVIEW"
    assert not called


def test_import_allow_review_continues_when_safe(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    destination = tmp_path / "Library" / "song.mp3"
    monkeypatch.setattr("noqlen_forge.importer.read_tracks", lambda path: [_track(source)])
    monkeypatch.setattr("noqlen_forge.importer.audit_path", lambda path: SimpleNamespace(status="REVIEW", bad_fields=["BPM=0"]))
    monkeypatch.setattr("noqlen_forge.importer.scan_library", lambda *args, **kwargs: (0, "scan ok"))
    monkeypatch.setattr("noqlen_forge.importer.organize_path", lambda *args, **kwargs: OrganizeResult(0, "", copied=1 if kwargs.get("apply") else 0, status="OK", items=[OrganizeItem(source, destination, _track(source))]))

    result = import_path(source.parent, config, apply=True, library=tmp_path / "Library", skip_enrich=True, skip_cover=True, skip_lyrics=True, allow_review=True)

    assert result.code == 0


def test_import_skip_flags_and_replaygain(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    calls: list[str] = []
    monkeypatch.setattr("noqlen_forge.importer.audit_path", lambda path: _ok_audit())
    monkeypatch.setattr("noqlen_forge.importer.read_tracks", lambda path: [_track(source)])
    monkeypatch.setattr("noqlen_forge.importer.cover_path", lambda *args, **kwargs: calls.append("cover") or (0, "Cover: OK"))
    monkeypatch.setattr("noqlen_forge.importer.lyrics_path", lambda *args, **kwargs: calls.append("lyrics") or (0, "Lyrics: OK"))
    monkeypatch.setattr("noqlen_forge.importer.replaygain_path", lambda *args, **kwargs: calls.append("replaygain") or (0, "ReplayGain: OK"))
    monkeypatch.setattr("noqlen_forge.importer.scan_library", lambda *args, **kwargs: (0, "scan ok"))
    monkeypatch.setattr("noqlen_forge.importer.organize_path", lambda *args, **kwargs: OrganizeResult(0, "", copied=1 if kwargs.get("apply") else 0, status="OK", items=[OrganizeItem(source, tmp_path / "Library" / "song.mp3", _track(source))]))

    result = import_path(source.parent, config, apply=True, library=tmp_path / "Library", skip_enrich=True, skip_cover=True, skip_lyrics=True, replaygain=True)

    assert result.code == 0
    assert calls == ["replaygain"]


def test_import_blocks_empty_library_on_apply(tmp_path: Path) -> None:
    config = default_config()

    result = import_path(tmp_path / "Incoming", config, apply=True)

    assert result.code == 1
    assert "requires a library destination" in result.output


def test_import_blocks_destination_equal_source(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = tmp_path / "Incoming"

    result = import_path(source, config, apply=True, library=source)

    assert result.code == 1
    assert "destination equals source" in result.output


def test_import_blocks_automated_apply_outside_musiclab(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = tmp_path / "Incoming"
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")

    result = import_path(source, config, apply=True, library=tmp_path / "Library")

    assert result.code == 1
    assert "Refusing automated --apply outside MusicLab" in result.output


def test_import_conflict_is_review(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    destination = tmp_path / "Library" / "song.mp3"
    monkeypatch.setattr("noqlen_forge.importer.audit_path", lambda path: _ok_audit())
    monkeypatch.setattr("noqlen_forge.importer.read_tracks", lambda path: [_track(source)])
    monkeypatch.setattr("noqlen_forge.importer.organize_path", lambda *args, **kwargs: OrganizeResult(1, "", conflicts=1, status="REVIEW", items=[OrganizeItem(source, destination, _track(source), action="conflict", reason="duplicate destination in run")]))

    result = import_path(source.parent, config, apply=True, library=tmp_path / "Library", skip_enrich=True, skip_cover=True, skip_lyrics=True)

    assert result.code == 1
    assert result.status == "REVIEW"


def test_import_repeated_existing_destination_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    init_db(config)
    source = _audio(tmp_path / "Incoming" / "song.mp3")
    destination = _audio(tmp_path / "Library" / "song.mp3")
    with connect(config) as conn:
        conn.execute("INSERT INTO files(path, status) VALUES (?, 'active')", (str(destination.resolve()),))
        conn.commit()
    monkeypatch.setattr("noqlen_forge.importer.audit_path", lambda path: _ok_audit())
    monkeypatch.setattr("noqlen_forge.importer.read_tracks", lambda path: [_track(source)])
    monkeypatch.setattr("noqlen_forge.importer.scan_library", lambda *args, **kwargs: (0, "scan ok"))
    monkeypatch.setattr("noqlen_forge.importer.organize_path", lambda *args, **kwargs: OrganizeResult(1, "", conflicts=1, status="REVIEW", items=[OrganizeItem(source, destination, _track(source), action="conflict", reason="destination exists")]))

    result = import_path(source.parent, config, apply=True, library=tmp_path / "Library", skip_enrich=True, skip_cover=True, skip_lyrics=True)

    assert result.code == 0
    assert "already organized / already in library" in result.output
    with connect(config) as conn:
        assert get_counts(conn)["files"] == 1
