import csv
import json
import sqlite3
from io import StringIO
from pathlib import Path

from noqlen_forge import cli
from noqlen_forge.config import default_config
from noqlen_forge.db import apply_migrations, connect, get_counts, normalize_path, record_field_decision, record_provider_run, upsert_album, upsert_audio_features, upsert_file, upsert_track
from noqlen_forge.export import export_data


def _config(path: Path) -> dict:
    config = default_config()
    config["database"]["path"] = str(path)
    return config


def _seed(config: dict, root: Path) -> dict[str, Path]:
    rich = root / "NewJeans" / "Get Up" / "01 Super Shy.flac"
    missing = root / "Ne Obliviscaris" / "Urn" / "01 Libera.flac"
    duplicate = root / "NewJeans" / "Get Up Duplicate" / "01 Super Shy.flac"
    for path in (rich, missing, duplicate):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album_key": "get-up", "album": "Get Up", "albumartist": "NewJeans", "date": "2023", "originaldate": "2023-07-21", "mb_album_id": "mb-album", "mb_release_group_id": "mb-rg", "label": "ADOR", "catalog_number": "cat", "barcode": "bar", "country": "KR", "release_type": "EP"})
        track_id = upsert_track(conn, {"title": "Super Shy", "artist": "NewJeans", "albumartist": "NewJeans", "track": 1, "disc": 1, "mb_track_id": "mb-track", "acoustid_id": "acoustid", "bpm": 120, "key": "G", "mood": "Bright", "energy": 80, "danceability": 90}, album_id=album_id)
        file_id = upsert_file(conn, rich, {"format": "flac", "duration": 150.0, "has_cover": 1, "has_lyrics": 1, "has_synced_lyrics": 1, "status": "active"}, track_id=track_id)
        upsert_audio_features(conn, track_id, {"bpm": 120, "key": "G", "energy": 80, "danceability": 90, "replaygain_track_gain": -2.0, "replaygain_track_peak": 0.9, "replaygain_album_gain": -3.0, "replaygain_album_peak": 0.95, "loudness": -14.0})
        conn.execute("INSERT INTO track_tags(track_id, key, value, updated_at) VALUES (?, 'genre', 'K-pop', 'now')", (track_id,))
        conn.execute("INSERT INTO album_tags(album_id, key, value, updated_at) VALUES (?, 'style', 'Dance Pop', 'now')", (album_id,))
        conn.execute("INSERT INTO artwork(album_id, file_id, embedded, hash, updated_at) VALUES (?, ?, 1, 'coverhash', 'now')", (album_id, file_id))
        conn.execute("INSERT INTO lyrics(track_id, synced, embedded, text_hash, updated_at) VALUES (?, 1, 1, 'lyrichash', 'now')", (track_id,))
        duplicate_track = upsert_track(conn, {"title": "Super Shy", "artist": "NewJeans", "albumartist": "NewJeans", "track": 1, "mb_track_id": "mb-track"}, album_id=album_id)
        upsert_file(conn, duplicate, {"format": "flac", "duration": 150.0, "has_cover": 0, "has_lyrics": 0, "status": "active"}, track_id=duplicate_track)
        missing_album = upsert_album(conn, {"album_key": "urn", "album": "Urn", "albumartist": "Ne Obliviscaris"})
        missing_track = upsert_track(conn, {"title": "Libera", "artist": "Ne Obliviscaris", "track": 1}, album_id=missing_album)
        upsert_file(conn, missing, {"format": "flac", "has_cover": 0, "has_lyrics": 0, "has_synced_lyrics": 0, "status": "active"}, track_id=missing_track)
        run_id = record_provider_run(conn, "musicbrainz", "track", missing_track, "review", query="no secret")
        record_field_decision(conn, run_id, "track", missing_track, "style", current_value="", candidate_value="Progressive Metal", provider="musicbrainz", confidence="medium", action="review", reason="ambiguous")
        resolved_run = record_provider_run(conn, "discogs", "album", album_id, "ok", query="summary")
        decision_id = record_field_decision(conn, resolved_run, "album", album_id, "label", current_value="", candidate_value="ADOR", selected_value="ADOR", provider="discogs", confidence="high", action="accept", reason="safe")
        conn.execute("UPDATE field_decisions SET resolved = 1, resolved_action = 'accept' WHERE id = ?", (decision_id,))
        conn.commit()
    return {"rich": rich, "missing": missing, "duplicate": duplicate}


def _counts(config: dict) -> dict[str, int]:
    with connect(config) as conn:
        return get_counts(conn)


def test_export_query_csv_generates_valid_csv(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    code, output = export_data(config, 'artist:"NewJeans"', export_format="csv")
    rows = list(csv.DictReader(StringIO(output)))

    assert code == 0
    assert rows
    assert "path" in rows[0]
    assert rows[0]["artist"] == "NewJeans"
    assert "lyrics" not in rows[0]
    assert "fingerprint" not in output.casefold()


def test_export_query_json_generates_valid_json(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    code, output = export_data(config, "NewJeans", export_format="json", include_assets=True)
    payload = json.loads(output)

    assert code == 0
    assert payload["type"] == "query"
    assert payload["scope"] == "tracks"
    assert payload["count"] >= 1
    assert payload["results"][0]["assets"]["cover"] in {True, False}


def test_export_missing_uses_missing_report(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    _, output = export_data(config, missing="lyrics", export_format="csv")
    rows = list(csv.DictReader(StringIO(output)))

    assert rows
    assert any("lyrics" in row["missing_fields"] for row in rows)


def test_export_duplicates_json_uses_duplicate_report(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    _, output = export_data(config, duplicates=True, export_format="json")
    payload = json.loads(output)

    assert payload["type"] == "duplicates"
    assert payload["groups"]


def test_export_reviews_json_includes_pending_and_resolved(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    _, output = export_data(config, reviews=True, export_format="json")
    payload = json.loads(output)

    assert payload["type"] == "reviews"
    assert payload["pending"]
    assert payload["resolved"]


def test_export_library_includes_albums_tracks_files(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    _, output = export_data(config, library=True, export_format="json")
    payload = json.loads(output)

    assert payload["type"] == "library"
    assert payload["summary"]["albums"] >= 2
    assert payload["tracks"]
    assert payload["files"]


def test_export_fields_and_aliases_limit_columns(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    _, output = export_data(config, "NewJeans", export_format="csv", fields="title,artist,album_artist,rg")
    row = next(csv.DictReader(StringIO(output)))

    assert list(row) == ["title", "artist", "albumartist", "replaygain"]


def test_export_output_file_and_force_behaviour(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    output = tmp_path / "exports" / "query.csv"

    code, summary = export_data(config, "NewJeans", export_format="csv", output=output)
    blocked_code, blocked = export_data(config, "NewJeans", export_format="csv", output=output)
    force_code, _ = export_data(config, "NewJeans", export_format="csv", output=output, force=True)

    assert code == 0
    assert "Output:" in summary
    assert output.exists()
    assert blocked_code == 1
    assert "Use --force" in blocked
    assert force_code == 0


def test_export_stdout_does_not_mix_human_text(tmp_path: Path, capsys) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    parser = cli.build_parser()
    args = parser.parse_args(["export", "NewJeans", "--format", "json"])

    assert cli.export_command(args, config=config) == 0
    out = capsys.readouterr().out

    assert json.loads(out)["type"] == "query"
    assert "Export:" not in out


def test_export_does_not_alter_db_or_tags(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    paths = _seed(config, tmp_path / "Library")
    before_counts = _counts(config)
    before_mtime = paths["rich"].stat().st_mtime_ns
    before_changes = sqlite3.connect(config["database"]["path"]).total_changes

    export_data(config, "NewJeans", export_format="json")
    export_data(config, missing="lyrics", export_format="csv")
    export_data(config, duplicates=True, export_format="json")
    export_data(config, reviews=True, export_format="json")
    export_data(config, library=True, export_format="json")

    assert _counts(config) == before_counts
    assert paths["rich"].stat().st_mtime_ns == before_mtime
    assert before_changes == 0


def test_export_automated_output_blocks_real_library(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")

    code, output = export_data(config, "NewJeans", export_format="json", output=Path("/mnt/noqlen-forge-export.json"))

    assert code == 1
    assert "dangerous path" in output
