import json
from pathlib import Path

import pytest

from noqlen_forge.audio import Track
from noqlen_forge.config import default_config
from noqlen_forge.db import (
    SCHEMA_VERSION,
    apply_migrations,
    connect,
    current_schema_version,
    database_path,
    db_explain,
    db_query,
    db_status,
    get_counts,
    init_db,
    normalize_path,
    parse_query,
    record_candidate,
    record_field_decision,
    record_provider_run,
    render_status,
    scan_library,
    upsert_audio_features,
    upsert_album,
    upsert_file,
    upsert_track,
)

pytestmark = pytest.mark.db


def _config(path: Path | None = None) -> dict:
    config = default_config()
    if path is not None:
        config["database"]["path"] = str(path)
    return config


def _track(path: Path, album: str = "Album", title: str = "Song") -> Track:
    return Track(
        path=path,
        format=path.suffix.lower().lstrip("."),
        album=album,
        albumartist="Artist",
        artist="Artist",
        title=title,
        tracknumber=1,
        duration=123.0,
        tags={
            "musicbrainz album id": ["mb-album"],
            "musicbrainz release group id": ["mb-rg"],
            "musicbrainz track id": ["mb-track"],
            "acoustid_id": ["acoustid"],
            "bpm": ["120"],
        },
    )


def test_database_path_uses_xdg_data_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert database_path(default_config()) == tmp_path / "noqlen-forge" / "library.db"


def test_database_path_falls_back_to_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert database_path(default_config()) == tmp_path / ".local" / "share" / "noqlen-forge" / "library.db"


def test_db_init_creates_tables(tmp_path) -> None:
    config = _config(tmp_path / "library.db")

    init_db(config)

    with connect(config) as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert {"albums", "tracks", "files", "provider_runs", "field_decisions", "schema_migrations", "jobs", "job_steps", "job_events"}.issubset(tables)


def test_migrations_are_idempotent(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    with connect(config) as conn:
        apply_migrations(conn)
        apply_migrations(conn)
        rows = conn.execute("SELECT COUNT(*) AS count FROM schema_migrations WHERE version = 1").fetchone()["count"]

    assert rows == 1


def test_db_status_returns_counts(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album_key": "a", "album": "A"})
        track_id = upsert_track(conn, {"title": "T"}, album_id=album_id)
        upsert_file(conn, tmp_path / "a.mp3", {"format": "mp3"}, track_id=track_id)
        conn.commit()

    status = db_status(config)

    assert status["version"] == SCHEMA_VERSION
    assert status["counts"]["albums"] == 1
    assert status["counts"]["tracks"] == 1
    assert status["counts"]["files"] == 1

    output = render_status(status)
    assert "Library database" in output
    assert "Mode: READ-ONLY" in output
    assert "Status: OK" in output


def test_upsert_album_does_not_duplicate_album_key(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    with connect(config) as conn:
        apply_migrations(conn)
        first = upsert_album(conn, {"album_key": "same", "album": "Old"})
        second = upsert_album(conn, {"album_key": "same", "album": "New"})
        count = get_counts(conn)["albums"]
        album = conn.execute("SELECT album FROM albums WHERE id = ?", (first,)).fetchone()["album"]

    assert first == second
    assert count == 1
    assert album == "New"


def test_upsert_file_does_not_duplicate_path(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    path = tmp_path / "a.mp3"
    with connect(config) as conn:
        apply_migrations(conn)
        first = upsert_file(conn, path, {"format": "mp3"})
        second = upsert_file(conn, path, {"format": "mp3", "size": 10})
        count = get_counts(conn)["files"]

    assert first == second
    assert count == 1


def test_upsert_audio_features_stores_replaygain(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album_key": "album", "album": "Album"})
        track_id = upsert_track(conn, {"title": "Song"}, album_id=album_id)
        upsert_audio_features(conn, track_id, {"replaygain_track_gain": -2.0, "replaygain_track_peak": 0.9, "replaygain_album_gain": -3.0, "replaygain_album_peak": 0.95, "loudness": -16.0})
        row = conn.execute("SELECT replaygain_track_gain, replaygain_album_gain, loudness FROM audio_features WHERE track_id = ?", (track_id,)).fetchone()

    assert row["replaygain_track_gain"] == -2.0
    assert row["replaygain_album_gain"] == -3.0
    assert row["loudness"] == -16.0


def test_foreign_keys_are_enabled(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album_key": "album", "album": "Album"})
        track_id = upsert_track(conn, {"title": "Song"}, album_id=album_id)
        conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        album_id_after = conn.execute("SELECT album_id FROM tracks WHERE id = ?", (track_id,)).fetchone()["album_id"]

    assert album_id_after is None


def test_indexes_exist(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    with connect(config) as conn:
        apply_migrations(conn)
        indexes = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}

    assert "idx_files_path" in indexes
    assert "idx_tracks_album_id" in indexes
    assert "idx_provider_candidates_provider_external_id" in indexes
    assert "idx_field_decisions_field_provider" in indexes
    assert "idx_files_status_path" in indexes
    assert "idx_provider_runs_target" in indexes
    assert "idx_field_decisions_target_field" in indexes
    assert "idx_operations_operation_started" in indexes


def test_db_scan_dry_run_does_not_write(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    monkeypatch.setattr("noqlen_forge.db.read_track", lambda path: _track(path))

    code, output = scan_library(config, tmp_path, apply=False)

    assert code == 0
    assert "Mode: DRY-RUN" in output
    assert "would add 1 albums, 1 tracks, 1 files" in output
    assert not (tmp_path / "library.db").exists()


def test_db_scan_apply_writes_albums_tracks_files(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    monkeypatch.setattr("noqlen_forge.db.read_track", lambda path: _track(path))

    code, output = scan_library(config, tmp_path, apply=True)

    assert code == 0
    assert "Mode: APPLY" in output
    with connect(config) as conn:
        counts = get_counts(conn)
    assert counts["albums"] == 1
    assert counts["tracks"] == 1
    assert counts["files"] == 1


def test_db_scan_repeat_is_idempotent_in_dry_run_and_apply(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    monkeypatch.setattr("noqlen_forge.db.read_track", lambda path: _track(path))

    first_code, _ = scan_library(config, tmp_path, apply=True)
    dry_code, dry_output = scan_library(config, tmp_path, apply=False)
    second_code, second_output = scan_library(config, tmp_path, apply=True)

    assert first_code == 0
    assert dry_code == 0
    assert second_code == 0
    assert "would add 0 albums, 0 tracks, 0 files" in dry_output
    assert "added 0 albums, 0 tracks, 0 files" in second_output
    with connect(config) as conn:
        counts = get_counts(conn)
    assert counts["albums"] == 1
    assert counts["tracks"] == 1
    assert counts["files"] == 1


def test_db_scan_repeat_skips_unchanged_files_without_updating_db_mtime(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    monkeypatch.setattr("noqlen_forge.db.read_track", lambda path: _track(path))

    first_code, _ = scan_library(config, tmp_path, apply=True)
    with connect(config) as conn:
        first = conn.execute("SELECT db_mtime, updated_at FROM files WHERE path = ?", (normalize_path(audio),)).fetchone()
    second_code, second_output = scan_library(config, tmp_path, apply=True)
    with connect(config) as conn:
        second = conn.execute("SELECT db_mtime, updated_at FROM files WHERE path = ?", (normalize_path(audio),)).fetchone()

    assert first_code == 0
    assert second_code == 0
    assert "skipped 1" in second_output
    assert second["db_mtime"] == first["db_mtime"]
    assert second["updated_at"] == first["updated_at"]


def test_db_scan_marks_removed_files_missing_without_duplicates(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    first = tmp_path / "one.mp3"
    second = tmp_path / "two.mp3"
    first.write_bytes(b"not real audio")
    second.write_bytes(b"not real audio")
    tracks = {first: _track(first), second: _track(second, title="Two")}
    monkeypatch.setattr("noqlen_forge.db.read_track", lambda path: tracks[path])

    scan_library(config, tmp_path, apply=True)
    second.unlink()
    dry_code, dry_output = scan_library(config, tmp_path, apply=False)
    apply_code, apply_output = scan_library(config, tmp_path, apply=True)

    assert dry_code == 0
    assert apply_code == 0
    assert "would mark 1 missing" in dry_output
    assert "marked 1 missing" in apply_output
    with connect(config) as conn:
        counts = get_counts(conn)
    assert counts["files"] == 2
    assert counts["missing_files"] == 1


def test_provider_history_records_are_written(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    with connect(config) as conn:
        apply_migrations(conn)
        run_id = record_provider_run(conn, "musicbrainz", "album", "1", "ok", query="artist album")
        candidate_id = record_candidate(conn, run_id, "musicbrainz", "release-id", score=95.0, confidence="high", selected=True, payload_summary={"title": "Album"})
        decision_id = record_field_decision(conn, run_id, "album", "1", "album", current_value="A", candidate_value="B", selected_value="B", provider="musicbrainz", confidence="high", action="replace")

    assert run_id > 0
    assert candidate_id > 0
    assert decision_id > 0


def test_paths_are_normalized(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    monkeypatch.chdir(tmp_path)
    path = Path("Music") / "Song With Spaces.mp3"

    assert normalize_path(path) == str((tmp_path / path).resolve(strict=False))


def test_current_schema_version_is_zero_without_migrations_table(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    with connect(config) as conn:
        assert current_schema_version(conn) == 0


def _seed_query_db(config: dict, tmp_path: Path) -> tuple[Path, Path]:
    urn_dir = tmp_path / "Ne Obliviscaris" / "Urn"
    runaway_dir = tmp_path / "RESCENE" / "Runaway - Single"
    newjeans_dir = tmp_path / "NewJeans" / "Get Up"
    urn_dir.mkdir(parents=True)
    runaway_dir.mkdir(parents=True)
    newjeans_dir.mkdir(parents=True)
    urn = urn_dir / "01 Libera, Pt. I.flac"
    urn2 = urn_dir / "02 Libera, Pt. II.flac"
    runaway = runaway_dir / "01 RUNAWAY.flac"
    super_shy = newjeans_dir / "01 Super Shy.flac"
    urn.write_bytes(b"audio")
    urn2.write_bytes(b"audio")
    runaway.write_bytes(b"audio")
    super_shy.write_bytes(b"audio")
    with connect(config) as conn:
        apply_migrations(conn)
        urn_album = upsert_album(conn, {"album_key": "urn", "album": "Urn", "albumartist": "Ne Obliviscaris", "date": "2017", "originaldate": "2017-10-27", "mb_album_id": "mb-album-urn", "mb_release_group_id": "mb-rg-urn", "label": "Season of Mist", "country": "Brazil", "release_type": "Album"})
        urn_track = upsert_track(conn, {"title": "Libera, Pt. I", "artist": "Ne Obliviscaris", "albumartist": "Ne Obliviscaris", "track": 1, "disc": 1, "mb_track_id": "mb-track-urn-1", "bpm": 124, "key": "Am", "energy": 76, "danceability": 42}, album_id=urn_album)
        urn_track2 = upsert_track(conn, {"title": "Libera, Pt. II", "artist": "Ne Obliviscaris", "albumartist": "Ne Obliviscaris", "track": 2, "disc": 1, "mb_track_id": "mb-track-urn-2", "bpm": 90, "energy": 68, "danceability": 39}, album_id=urn_album)
        upsert_file(conn, urn, {"format": "flac", "duration": 481.0, "has_cover": 1, "has_lyrics": 0, "status": "active"}, track_id=urn_track)
        upsert_file(conn, urn2, {"format": "flac", "duration": 355.0, "has_cover": 1, "has_lyrics": 0, "status": "active"}, track_id=urn_track2)
        upsert_audio_features(conn, urn_track, {"bpm": 124, "key": "Am", "energy": 76, "danceability": 42, "replaygain_track_gain": -7.1, "replaygain_track_peak": 0.94})
        conn.execute("INSERT INTO album_tags(album_id, key, value, type, source, confidence, updated_at) VALUES (?, 'style', 'Progressive Metal', 'style', 'discogs', 'high', 'now')", (urn_album,))
        run_id = record_provider_run(conn, "discogs", "album", urn_album, "warn", query="token=secret")
        record_candidate(conn, run_id, "discogs", "123", score=91.0, confidence="high", selected=True, payload_summary={"title": "Urn"})
        record_field_decision(conn, run_id, "album", urn_album, "style", current_value="Black Metal; Death Metal; Progressive Metal", candidate_value="Death Metal; Progressive Death Metal; Progressive Metal", selected_value="", provider="discogs", confidence="medium", action="review", reason="conflict with existing value")
        record_field_decision(conn, run_id, "album", urn_album, "lyrics", candidate_value="these are full lyrics that should never be printed", provider="discogs", action="skip", reason="not written")
        record_field_decision(conn, run_id, "album", urn_album, "api_key", candidate_value="super-secret", provider="discogs", action="skip", reason="contains api_key=super-secret")

        runaway_album = upsert_album(conn, {"album_key": "runaway", "album": "Runaway - Single", "albumartist": "RESCENE"})
        runaway_track = upsert_track(conn, {"title": "RUNAWAY", "artist": "RESCENE", "albumartist": "RESCENE", "track": 1}, album_id=runaway_album)
        upsert_file(conn, runaway, {"format": "flac", "has_cover": 0, "has_lyrics": 0, "status": "active"}, track_id=runaway_track)
        newjeans_album = upsert_album(conn, {"album_key": "get-up", "album": "Get Up", "albumartist": "NewJeans", "date": "2023"})
        newjeans_track = upsert_track(conn, {"title": "Super Shy", "artist": "NewJeans", "albumartist": "NewJeans", "track": 1, "bpm": 150, "energy": 80, "danceability": 89}, album_id=newjeans_album)
        upsert_file(conn, super_shy, {"format": "flac", "duration": 154.0, "has_cover": 1, "has_lyrics": 1, "status": "active"}, track_id=newjeans_track)
        upsert_audio_features(conn, newjeans_track, {"bpm": 150, "energy": 80, "danceability": 89, "replaygain_track_gain": -6.0, "replaygain_track_peak": 0.91})
        conn.execute("INSERT INTO album_tags(album_id, key, value, type, source, confidence, updated_at) VALUES (?, 'genre', 'K-pop', 'genre', 'lastfm', 'high', 'now')", (newjeans_album,))
        conn.commit()
    return urn_dir, runaway


def test_parse_query_supports_free_fields_quotes_negation_and_numeric_ops() -> None:
    plan = parse_query('NewJeans artist:"Ne Obliviscaris" -missing:cover bpm:>120')

    assert [(term.field, term.value, term.negated) for term in plan.terms] == [
        (None, "NewJeans", False),
        ("artist", "Ne Obliviscaris", False),
        ("missing", "cover", True),
        ("bpm", ">120", False),
    ]


def test_parse_query_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="Unknown field: unknown"):
        parse_query("unknown:value")


def test_db_query_unknown_field_returns_registry_hint(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    code, output = db_query(config, "unknown:value")

    assert code == 1
    assert "Unknown field: unknown" in output
    assert "noqlen-forge fields" in output


def test_db_query_searches_artist(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    code, output = db_query(config, "artist:Ne Obliviscaris")

    assert code == 0
    assert "Libera, Pt. I" in output
    assert "RESCENE" not in output


def test_db_query_searches_album(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, output = db_query(config, "album:Urn")

    assert "Urn" in output
    assert "RUNAWAY" not in output


def test_db_query_free_search(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, output = db_query(config, "RUNAWAY")

    assert "RUNAWAY" in output
    assert "Libera" not in output


def test_db_query_missing_lyrics(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, output = db_query(config, "missing:lyrics")

    assert "Results: 3 tracks" in output
    assert "Libera, Pt. I" in output
    assert "RUNAWAY" in output
    assert "Super Shy" not in output


def test_db_query_has_cover(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, output = db_query(config, "has:cover")

    assert "Libera, Pt. I" in output
    assert "RUNAWAY" not in output


def test_db_query_aliases_rg_art_and_mbids(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, missing_rg = db_query(config, "missing:rg")
    _, has_art = db_query(config, "has:art")
    _, missing_mbids = db_query(config, "missing:mbids")

    assert "RUNAWAY" in missing_rg
    assert "Super Shy" in has_art
    assert "RUNAWAY" in missing_mbids


def test_db_query_provider_discogs(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, output = db_query(config, "provider:discogs")

    assert "Libera, Pt. I" in output
    assert "RUNAWAY" not in output


def test_db_query_review_true(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, output = db_query(config, "review:true")

    assert "Libera, Pt. I" in output
    assert "RUNAWAY" not in output


def test_db_query_albums_and_tracks_modes(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, albums = db_query(config, "Ne Obliviscaris", target="albums")
    _, tracks = db_query(config, "Ne Obliviscaris", target="tracks")

    assert "Album Artist" in albums
    assert "Urn" in albums
    assert "Title" in tracks
    assert "Libera, Pt. II" in tracks


def test_db_query_combined_negated_and_numeric_filters(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, combined = db_query(config, 'style:"Progressive Metal" year:2017 bpm:>100 -missing:cover')
    _, genre = db_query(config, "genre:K-pop has:cover danceability:<90")

    assert "Libera, Pt. I" in combined
    assert "Libera, Pt. II" not in combined
    assert "Super Shy" in genre


def test_db_query_files_scope_and_limit(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    _, output = db_query(config, "has:cover", target="files", limit=1)

    assert "Results: 1 file" in output
    assert "Path" in output


def test_db_query_json_output_is_stable_and_safe(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    code, output = db_query(config, "artist:NewJeans missing:key", output_format="json")
    payload = json.loads(output)

    assert code == 0
    assert payload["status"] == "OK"
    assert payload["scope"] == "tracks"
    assert payload["count"] == 1
    assert payload["results"][0]["missing"] == ["key"]
    assert "lyrics" not in output.lower()
    assert "fingerprint" not in output.lower()
    assert "secret" not in output.lower()


def test_db_query_sql_injection_is_parameterized(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)

    code, _ = db_query(config, "artist:\"Ne'; DROP TABLE tracks; --\"")

    with connect(config) as conn:
        count = conn.execute("SELECT COUNT(*) AS count FROM tracks").fetchone()["count"]

    assert code == 0
    assert count == 4


def test_db_explain_shows_provider_runs(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    urn_dir, _ = _seed_query_db(config, tmp_path)

    code, output = db_explain(config, urn_dir)

    assert code == 0
    assert "Last enrich:" in output
    assert "discogs" in output
    assert "candidate discogs:123 selected" in output


def test_db_explain_field_filters_decisions(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    urn_dir, _ = _seed_query_db(config, tmp_path)

    _, output = db_explain(config, urn_dir, field="style")

    assert "Style:" in output
    assert "Lyrics:" not in output


def test_db_query_does_not_write_to_database(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_query_db(config, tmp_path)
    with connect(config) as conn:
        before = get_counts(conn)

    db_query(config, "artist:Ne Obliviscaris")

    with connect(config) as conn:
        after = get_counts(conn)
    assert after == before


def test_db_explain_hides_lyrics_and_secrets(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    urn_dir, _ = _seed_query_db(config, tmp_path)

    _, output = db_explain(config, urn_dir)

    assert "these are full lyrics" not in output
    assert "super-secret" not in output
    assert "[lyrics hidden]" in output
    assert "[secret hidden]" in output
