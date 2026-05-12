import json
import sqlite3
from pathlib import Path

from noqlen_forge import cli
from noqlen_forge.config import default_config
from noqlen_forge.db import apply_migrations, connect, get_counts, normalize_path, upsert_album, upsert_audio_features, upsert_file, upsert_track
from noqlen_forge.reports import missing_files_report, missing_report, untracked_report


def _config(path: Path) -> dict:
    config = default_config()
    config["database"]["path"] = str(path)
    return config


def _seed(config: dict, root: Path) -> dict[str, Path]:
    urn = root / "Ne Obliviscaris" / "Urn" / "01 Libera.flac"
    rich = root / "RESCENE" / "Runaway" / "01 RUNAWAY.flac"
    orphan = root / "Old" / "01 Gone.flac"
    urn.parent.mkdir(parents=True)
    rich.parent.mkdir(parents=True)
    orphan.parent.mkdir(parents=True)
    urn.write_bytes(b"audio")
    rich.write_bytes(b"audio")
    orphan.write_bytes(b"audio")
    with connect(config) as conn:
        apply_migrations(conn)
        urn_album = upsert_album(conn, {"album_key": "urn", "album": "Urn", "albumartist": "Ne Obliviscaris"})
        urn_track = upsert_track(conn, {"title": "Libera, Pt. I", "artist": "Ne Obliviscaris", "albumartist": "Ne Obliviscaris", "track": 1}, album_id=urn_album)
        upsert_file(conn, urn, {"format": "flac", "has_cover": 0, "has_lyrics": 0, "has_synced_lyrics": 0, "status": "active"}, track_id=urn_track)
        rich_album = upsert_album(conn, {"album_key": "runaway", "album": "Runaway", "albumartist": "RESCENE", "mb_album_id": "mb-album", "mb_release_group_id": "mb-rg", "label": "Label"})
        rich_track = upsert_track(conn, {"title": "RUNAWAY", "artist": "RESCENE", "albumartist": "RESCENE", "track": 1, "mb_track_id": "mb-track", "mb_release_track_id": "mb-rel-track", "key": "Am", "bpm": 120, "mood": "Energetic"}, album_id=rich_album)
        file_id = upsert_file(conn, rich, {"format": "flac", "has_cover": 1, "has_lyrics": 1, "has_synced_lyrics": 1, "status": "active"}, track_id=rich_track)
        upsert_audio_features(conn, rich_track, {"replaygain_track_gain": -2.0, "replaygain_track_peak": 0.9, "replaygain_album_gain": -3.0, "replaygain_album_peak": 0.95, "loudness": -15.0, "energy": 80, "danceability": 70})
        conn.execute("INSERT INTO artwork(album_id, file_id, embedded, updated_at) VALUES (?, ?, 1, 'now')", (rich_album, file_id))
        conn.execute("INSERT INTO lyrics(track_id, synced, embedded, sidecar_path, text_hash, updated_at) VALUES (?, 1, 1, ?, 'hash', 'now')", (rich_track, str(rich.with_suffix(".lrc"))))
        conn.execute("INSERT INTO album_tags(album_id, key, value, updated_at) VALUES (?, 'style', 'K-pop', 'now')", (rich_album,))
        old_album = upsert_album(conn, {"album_key": "old", "album": "Old", "albumartist": "Old Artist"})
        old_track = upsert_track(conn, {"title": "Gone", "artist": "Old Artist", "track": 1}, album_id=old_album)
        upsert_file(conn, orphan, {"format": "flac", "status": "active"}, track_id=old_track)
        conn.commit()
    orphan.unlink()
    return {"urn": urn, "rich": rich, "orphan": orphan}


def _db_counts(config: dict) -> dict[str, int]:
    with connect(config) as conn:
        return get_counts(conn)


def test_missing_detects_lyrics_cover_replaygain_and_mbids(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    code, output = missing_report(config, fields=["lyrics", "cover", "replaygain", "mbids"])

    assert code == 0
    assert "Missing report" in output
    assert "Ne Obliviscaris - Urn" in output
    assert "Lyrics: 1/1 missing" in output
    assert "Cover: 1/1 missing" in output
    assert "ReplayGain: 1/1 missing" in output
    assert "MB Album Id: 1/1 missing" in output
    assert "Status: WARN" in output


def test_missing_tracks_lists_paths(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    paths = _seed(config, tmp_path / "Library")

    _, output = missing_report(config, fields=["lyrics"], scope="tracks")

    assert "Missing Lyrics: 2 tracks" in output
    assert str(paths["urn"].resolve(strict=False)) in output
    assert str(paths["orphan"].resolve(strict=False)) in output


def test_missing_aliases_rg_art_lrc_work(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    _, output = missing_report(config, fields=["rg", "art", "lrc"])

    assert "ReplayGain" in output
    assert "Cover" in output
    assert "Synced Lyrics" in output
    assert "Sidecar LRC" in output


def test_missing_alias_mbids_expands_registry_group(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    _, output = missing_report(config, fields=["mbids"])

    assert "MB Album Id" in output
    assert "MB Track Id" in output
    assert "MB Release Group Id" in output


def test_missing_json_is_valid_and_safe(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    _, output = missing_report(config, fields=["lyrics"], output_format="json")
    payload = json.loads(output)

    assert payload["status"] == "WARN"
    assert payload["fields"] == ["lyrics"]
    assert payload["summary"]["albums"] == 2
    assert "text_hash" not in output
    assert "fingerprint" not in output.casefold()


def test_untracked_detects_file_outside_db_and_ok_after_scan_record(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    track = incoming / "01 New.flac"
    track.write_bytes(b"audio")

    _, output = untracked_report(config, incoming)
    assert "Untracked files: 1" in output
    assert normalize_path(track) in output

    with connect(config) as conn:
        album_id = upsert_album(conn, {"album_key": "new", "album": "New"})
        track_id = upsert_track(conn, {"title": "New"}, album_id=album_id)
        upsert_file(conn, track, {"format": "flac", "status": "active"}, track_id=track_id)
        conn.commit()

    _, after = untracked_report(config, incoming)
    assert "Untracked files: none" in after
    assert "Status: OK" in after


def test_untracked_json_is_valid(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    (incoming / "01 New.flac").write_bytes(b"audio")

    _, output = untracked_report(config, incoming, output_format="json")
    payload = json.loads(output)

    assert payload["status"] == "WARN"
    assert payload["summary"]["untracked"] == 1


def test_missing_files_detects_db_path_not_on_disk_and_ok_when_all_exist(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    paths = _seed(config, tmp_path / "Library")

    _, output = missing_files_report(config)
    assert "Missing files in database: 1" in output
    assert normalize_path(paths["orphan"]) in output

    paths["orphan"].parent.mkdir(parents=True, exist_ok=True)
    paths["orphan"].write_bytes(b"audio")
    _, after = missing_files_report(config)
    assert "Missing files in database: none" in after
    assert "Status: OK" in after


def test_missing_files_json_is_valid(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    _, output = missing_files_report(config, output_format="json")
    payload = json.loads(output)

    assert payload["status"] == "WARN"
    assert payload["summary"]["missing_files"] == 1


def test_reports_do_not_alter_db_or_files(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    paths = _seed(config, tmp_path / "Library")
    before_counts = _db_counts(config)
    before_mtime = paths["urn"].stat().st_mtime_ns
    before_changes = sqlite3.connect(config["database"]["path"]).total_changes

    missing_report(config, fields=["lyrics", "cover"])
    untracked_report(config, paths["urn"].parent)
    missing_files_report(config)

    assert _db_counts(config) == before_counts
    assert paths["urn"].stat().st_mtime_ns == before_mtime
    assert before_changes == 0


def test_grouped_report_commands_do_not_alter_db_or_files(tmp_path: Path, capsys) -> None:
    config = _config(tmp_path / "library.db")
    paths = _seed(config, tmp_path / "Library")
    before_counts = _db_counts(config)
    before_mtime = paths["urn"].stat().st_mtime_ns
    parser = cli.build_parser()

    for argv in (["report", "missing", "lyrics"], ["report", "duplicates"], ["report", "untracked", str(paths["urn"].parent)], ["report", "missing-files"]):
        args = parser.parse_args(argv)
        assert cli.report_command(args, config=config) == 0

    assert _db_counts(config) == before_counts
    assert paths["urn"].stat().st_mtime_ns == before_mtime
    assert "Report:" in capsys.readouterr().out


def test_help_does_not_alter_db(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    before_counts = _db_counts(config)

    parser = cli.build_parser()
    parser.format_help()
    for argv in (["report", "--help"], ["maintain", "--help"]):
        try:
            parser.parse_args(list(argv))
        except SystemExit as exc:
            assert exc.code == 0

    assert _db_counts(config) == before_counts


def test_missing_uses_db_without_reading_tags(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    monkeypatch.setattr("noqlen_forge.audio.read_track", lambda path: (_ for _ in ()).throw(AssertionError("read_track called")))

    code, output = missing_report(config, fields=["lyrics"])

    assert code == 0
    assert "Status: WARN" in output


def test_missing_rejects_unknown_fields_after_workflow_refactor(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    code, output = missing_report(config, fields=["unknown-field"])

    assert code == 1
    assert output == "No supported missing fields requested"
