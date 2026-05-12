from pathlib import Path

from noqlen_forge.audio import Track
from noqlen_forge.config import default_config
from noqlen_forge.db import apply_migrations, connect, get_counts, upsert_album, upsert_file, upsert_track
from noqlen_forge.sync import sync_path


def _config(path: Path) -> dict:
    config = default_config()
    config["database"]["path"] = str(path)
    return config


def _track(path: Path, title: str = "Tag Title", album: str = "Tag Album", mbid: str = "mb-tag") -> Track:
    return Track(
        path=path,
        format="mp3",
        album=album,
        albumartist="Tag Artist",
        artist="Tag Artist",
        title=title,
        tracknumber=1,
        tags={"musicbrainz album id": [mbid], "genre": ["Tag Genre"], "bpm": ["120"]},
    )


def _seed_db(config: dict, path: Path, title: str = "DB Title", album: str = "DB Album", mbid: str = "mb-db") -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album_key": "album", "album": album, "albumartist": "DB Artist", "mb_album_id": mbid})
        track_id = upsert_track(conn, {"title": title, "artist": "DB Artist", "albumartist": "DB Artist", "track": 1}, album_id=album_id)
        upsert_file(conn, path, {"format": "mp3"}, track_id=track_id)
        conn.commit()


def test_sync_tags_to_db_dry_run_does_not_alter_db(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    _seed_db(config, audio)
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path))

    code, output = sync_path(audio, config, direction="tags-to-db", apply=False, conflict_policy="tags-wins", fields=["title"])

    assert code == 0
    assert "Mode: DRY-RUN" in output
    with connect(config) as conn:
        title = conn.execute("SELECT title FROM tracks").fetchone()["title"]
    assert title == "DB Title"


def test_sync_tags_to_db_apply_updates_db_not_tags(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"original bytes")
    _seed_db(config, audio)
    before = audio.read_bytes()
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path))

    code, output = sync_path(audio, config, direction="tags-to-db", apply=True, conflict_policy="tags-wins", fields=["title"])

    assert code == 0
    assert "DB updates: 1" in output
    assert audio.read_bytes() == before
    with connect(config) as conn:
        title = conn.execute("SELECT title FROM tracks").fetchone()["title"]
        op = conn.execute("SELECT operation FROM operations ORDER BY id DESC LIMIT 1").fetchone()["operation"]
    assert title == "Tag Title"
    assert op == "sync"


def test_sync_db_to_tags_dry_run_does_not_write_tags(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    _seed_db(config, audio)
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path, title="Different"))
    calls = []
    monkeypatch.setattr("noqlen_forge.sync.apply_musicbrainz_writes", lambda plans, apply: calls.append(plans) or [])

    code, output = sync_path(audio, config, direction="db-to-tags", apply=False, conflict_policy="db-wins", fields=["title"])

    assert code == 0
    assert "Tag writes: 1" in output
    assert calls == []


def test_sync_db_to_tags_apply_writes_db_value(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    _seed_db(config, audio)
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path, title="Different"))
    captured = []
    monkeypatch.setattr("noqlen_forge.sync.apply_musicbrainz_writes", lambda plans, apply: captured.extend(plans) or [])

    code, _ = sync_path(audio, config, direction="db-to-tags", apply=True, conflict_policy="db-wins", fields=["title"])

    assert code == 0
    assert captured[0].changes == {"Title": "DB Title"}


def test_sync_conflict_review_blocks_writes(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    _seed_db(config, audio)
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path, title="Different"))
    monkeypatch.setattr("noqlen_forge.sync.apply_musicbrainz_writes", lambda plans, apply: (_ for _ in ()).throw(AssertionError("should not write")))

    code, output = sync_path(audio, config, direction="db-to-tags", apply=True, conflict_policy="review", fields=["title"])

    assert code == 1
    assert "Status: REVIEW" in output


def test_sync_protected_identity_needs_force(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    _seed_db(config, audio, mbid="mb-db")
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path, mbid="mb-tag"))

    code, output = sync_path(audio, config, direction="tags-to-db", apply=True, conflict_policy="tags-wins", fields=["mb_album_id"])

    assert code == 1
    assert "protected identity" in output
    with connect(config) as conn:
        assert conn.execute("SELECT mb_album_id FROM albums").fetchone()["mb_album_id"] == "mb-db"


def test_sync_protected_identity_alias_needs_force(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    _seed_db(config, audio, mbid="mb-db")
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path, mbid="mb-tag"))

    code, output = sync_path(audio, config, direction="tags-to-db", apply=True, conflict_policy="tags-wins", fields=["mbid"])

    assert code == 1
    assert "protected identity" in output


def test_sync_force_allows_identity_update(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    _seed_db(config, audio, mbid="mb-db")
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path, mbid="mb-tag"))

    code, _ = sync_path(audio, config, direction="tags-to-db", apply=True, force=True, conflict_policy="tags-wins", fields=["mb_album_id"])

    assert code == 0
    with connect(config) as conn:
        assert conn.execute("SELECT mb_album_id FROM albums").fetchone()["mb_album_id"] == "mb-tag"


def test_sync_field_limits_changes(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    _seed_db(config, audio)
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path, title="New Title", album="New Album"))

    code, _ = sync_path(audio, config, direction="tags-to-db", apply=True, conflict_policy="tags-wins", fields=["title"])

    assert code == 0
    with connect(config) as conn:
        assert conn.execute("SELECT title FROM tracks").fetchone()["title"] == "New Title"
        assert conn.execute("SELECT album FROM albums").fetchone()["album"] == "DB Album"


def test_sync_dry_run_without_existing_db_does_not_create_db(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path))

    code, _ = sync_path(audio, config, direction="tags-to-db", apply=False, fields=["title"])

    assert code == 0
    assert not (tmp_path / "library.db").exists()


def test_sync_tags_to_db_apply_creates_db_rows(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path))

    code, _ = sync_path(audio, config, direction="tags-to-db", apply=True, fields=["title"])

    assert code == 0
    with connect(config) as conn:
        assert get_counts(conn)["tracks"] == 1


def test_sync_tags_to_db_updates_audio_features(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    _seed_db(config, audio)
    monkeypatch.setattr("noqlen_forge.sync.read_track", lambda path: _track(path))

    code, _ = sync_path(audio, config, direction="tags-to-db", apply=True, conflict_policy="tags-wins", fields=["bpm"])

    assert code == 0
    with connect(config) as conn:
        assert conn.execute("SELECT bpm FROM tracks").fetchone()["bpm"] == 120
        assert conn.execute("SELECT bpm FROM audio_features").fetchone()["bpm"] == 120
