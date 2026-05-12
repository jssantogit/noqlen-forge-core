from __future__ import annotations

from pathlib import Path

from noqlen_forge import cli
from noqlen_forge.config import default_config
from noqlen_forge.db import apply_migrations, connect, get_counts, normalize_path, record_field_decision, record_provider_run, upsert_album, upsert_file, upsert_track
from noqlen_forge.repair import repair_path


def _config(path: Path, library: Path | None = None) -> dict:
    config = default_config()
    config["database"]["path"] = str(path)
    if library is not None:
        config["library"]["root"] = str(library)
    return config


def _seed(config: dict, root: Path) -> dict[str, Path | int]:
    present = root / "Artist" / "Album" / "01 Present.flac"
    missing = root / "Artist" / "Album" / "02 Missing.flac"
    duplicate = root / "Artist" / "Album" / "03 Duplicate.flac"
    present.parent.mkdir(parents=True)
    present.write_bytes(b"audio")
    duplicate.write_bytes(b"audio2")
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album_key": "album", "album": "Album", "albumartist": "Artist"})
        track_id = upsert_track(conn, {"title": "Present", "artist": "Artist", "mb_track_id": "mb-track"}, album_id=album_id)
        upsert_file(conn, present, {"format": "flac", "status": "active"}, track_id=track_id)
        missing_track_id = upsert_track(conn, {"title": "Missing", "artist": "Artist"}, album_id=album_id)
        upsert_file(conn, missing, {"format": "flac", "status": "active"}, track_id=missing_track_id)
        dup_track_id = upsert_track(conn, {"title": "Duplicate", "artist": "Artist", "mb_track_id": "mb-track"}, album_id=album_id)
        upsert_file(conn, duplicate, {"format": "flac", "status": "active"}, track_id=dup_track_id)
        conn.commit()
    return {"present": present, "missing": missing, "duplicate": duplicate, "album_id": album_id}


def _status(config: dict, path: Path) -> str:
    with connect(config) as conn:
        row = conn.execute("SELECT status FROM files WHERE path = ?", (normalize_path(path),)).fetchone()
        return str(row["status"])


def _op_count(config: dict) -> int:
    with connect(config) as conn:
        return int(conn.execute("SELECT COUNT(*) AS count FROM operations WHERE operation = 'repair'").fetchone()["count"])


def test_repair_missing_files_dry_run_does_not_alter_db(tmp_path: Path) -> None:
    library = tmp_path / "Library"
    config = _config(tmp_path / "library.db", library)
    paths = _seed(config, library)
    before = get_counts(connect(config))

    code, output = repair_path(config, kind="missing-files", apply=False, verbose=True)

    assert code == 0
    assert "Mode: DRY-RUN" in output
    assert "mark-missing" in output
    assert _status(config, paths["missing"]) == "active"
    assert get_counts(connect(config)) == before
    assert _op_count(config) == 0


def test_repair_missing_files_apply_marks_missing_and_records_operation(tmp_path: Path) -> None:
    library = tmp_path / "Library"
    config = _config(tmp_path / "library.db", library)
    paths = _seed(config, library)

    code, output = repair_path(config, kind="missing-files", apply=True)

    assert code == 0
    assert "Mode: APPLY" in output
    assert _status(config, paths["missing"]) == "missing"
    assert paths["present"].exists()
    assert _op_count(config) == 1


def test_repair_untracked_dry_run_and_apply_scan_without_file_writes(tmp_path: Path) -> None:
    library = tmp_path / "Library"
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    new_file = incoming / "01 New.flac"
    new_file.write_bytes(b"new audio")
    config = _config(tmp_path / "library.db", library)
    _seed(config, library)
    before_mtime = new_file.stat().st_mtime_ns

    _, dry = repair_path(config, target=incoming, kind="untracked", apply=False, verbose=True)
    assert "scan-untracked" in dry
    with connect(config) as conn:
        assert conn.execute("SELECT 1 FROM files WHERE path = ?", (normalize_path(new_file),)).fetchone() is None

    _, applied = repair_path(config, target=incoming, kind="untracked", apply=True)
    assert "Status: OK" in applied
    with connect(config) as conn:
        row = conn.execute("SELECT track_id, status FROM files WHERE path = ?", (normalize_path(new_file),)).fetchone()
    assert row is not None
    assert row["track_id"] is None
    assert row["status"] == "active"
    assert new_file.stat().st_mtime_ns == before_mtime


def test_repair_duplicates_reports_review_without_deleting(tmp_path: Path) -> None:
    library = tmp_path / "Library"
    config = _config(tmp_path / "library.db", library)
    _seed(config, library)
    before = get_counts(connect(config))

    code, output = repair_path(config, kind="duplicates", apply=True)

    assert code == 0
    assert "Status: REVIEW" in output
    assert "no files will be moved or deleted" in output
    assert get_counts(connect(config)) == before


def test_repair_db_dry_run_and_apply_safe_orphans(tmp_path: Path) -> None:
    library = tmp_path / "Library"
    config = _config(tmp_path / "library.db", library)
    _seed(config, library)
    stale_path = library / "Orphan.flac"
    stale_path.write_bytes(b"orphan")
    with connect(config) as conn:
        file_id = upsert_file(conn, stale_path, {"format": "flac", "status": "active"}, track_id=None)
        track_id = upsert_track(conn, {"title": "No Album"}, album_id=None)
        album_id = upsert_album(conn, {"album_key": "empty", "album": "Empty"})
        conn.execute("INSERT INTO operations(operation, target_type, target_id, mode, status, started_at, summary) VALUES ('import', 'path', 'x', 'apply', 'running', CURRENT_TIMESTAMP, 'stuck')")
        provider_id = record_provider_run(conn, "musicbrainz", "track", "99999", "running", finished_at=None)
        record_field_decision(conn, provider_id, "track", "99999", "style", action="review")
        conn.commit()

    _, dry = repair_path(config, kind="db", apply=False, verbose=True)
    assert "mark-file-stale" in dry
    with connect(config) as conn:
        assert conn.execute("SELECT status FROM files WHERE id = ?", (file_id,)).fetchone()["status"] == "active"

    _, applied = repair_path(config, kind="db", apply=True)
    assert "Status: OK" in applied
    with connect(config) as conn:
        assert conn.execute("SELECT status FROM files WHERE id = ?", (file_id,)).fetchone()["status"] == "stale"
        assert conn.execute("SELECT status FROM tracks WHERE id = ?", (track_id,)).fetchone()["status"] == "stale"
        assert conn.execute("SELECT status FROM albums WHERE id = ?", (album_id,)).fetchone()["status"] == "stale"
        assert conn.execute("SELECT status FROM operations WHERE operation = 'import'").fetchone()["status"] == "warn"
        assert conn.execute("SELECT status FROM provider_runs WHERE id = ?", (provider_id,)).fetchone()["status"] == "warn"


def test_repair_apply_is_idempotent(tmp_path: Path) -> None:
    library = tmp_path / "Library"
    config = _config(tmp_path / "library.db", library)
    _seed(config, library)

    repair_path(config, kind="missing-files", apply=True)
    _, second = repair_path(config, kind="missing-files", apply=True)

    assert "nothing to repair" in second
    assert "Status: OK" in second


def test_repair_cli_safety_blocks_automated_apply_outside_musiclab(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")
    config = _config(tmp_path / "library.db", tmp_path / "Library")
    _seed(config, tmp_path / "Library")
    parser = cli.build_parser()
    args = parser.parse_args(["maintain", "repair", "missing-files", "--apply"])

    code = cli.maintain_command(args, config=config)

    assert code == 1
    assert "Refusing automated --apply outside MusicLab" in capsys.readouterr().out
