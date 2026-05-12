from pathlib import Path

from noqlen_forge.audio import Track
from noqlen_forge.config import default_config
from noqlen_forge.db import apply_migrations, connect, upsert_album, upsert_file, upsert_track
from noqlen_forge.rewrite import RewriteRuleSet, load_rewrite_rules, rewrite_path, rewrite_value


def _config(path: Path) -> dict:
    config = default_config()
    config["database"]["path"] = str(path)
    config["rewrite"]["style"] = {"Prog Metal": "Progressive Metal", "death metal": "Death Metal"}
    config["rewrite"]["label"] = {"Season of Mist": "Season Of Mist"}
    config["rewrite"]["mb_album_id"] = {"old-mbid": "new-mbid"}
    return config


def _track(path: Path, *, style: str = "Prog Metal; death metal; Prog Metal", label: str = "Season of Mist", mbid: str = "old-mbid") -> Track:
    return Track(path=path, format="flac", album="Urn", albumartist="Ne Obliviscaris", artist="Ne Obliviscaris", title="Urn", tags={"style": [style], "label": [label], "musicbrainz album id": [mbid]})


def _seed_db(config: dict, path: Path, *, style: str = "Prog Metal", label: str = "Season of Mist", mbid: str = "old-mbid") -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album": "Urn", "albumartist": "Ne Obliviscaris", "mb_album_id": mbid, "label": label})
        track_id = upsert_track(conn, {"title": "Urn", "artist": "Ne Obliviscaris"}, album_id=album_id)
        upsert_file(conn, path, {"format": "flac"}, track_id=track_id)
        conn.execute("INSERT INTO track_tags(track_id, key, value, type, source, confidence, updated_at) VALUES (?, 'style', ?, 'tag', 'test', 'local', 'now')", (track_id, style))
        conn.commit()


def test_load_rewrite_rules_from_config(tmp_path: Path) -> None:
    rules = load_rewrite_rules(_config(tmp_path / "library.db"), ["style", "label"])

    assert rules.rules["style"]["Prog Metal"] == "Progressive Metal"
    assert rules.rules["label"]["Season of Mist"] == "Season Of Mist"


def test_rewrite_case_insensitive_and_multi_value_dedupe() -> None:
    rules = RewriteRuleSet({"style": {"Prog Metal": "Progressive Metal", "death metal": "Death Metal"}}, False, "; ", True, True)

    value = rewrite_value("Prog Metal; death metal; Prog Metal", rules.rules["style"], rules, multi_value=True)

    assert value == "Progressive Metal; Death Metal"


def test_rewrite_case_sensitive() -> None:
    rules = RewriteRuleSet({"style": {"Prog Metal": "Progressive Metal"}}, True, "; ", True, True)

    assert rewrite_value("prog metal", rules.rules["style"], rules, multi_value=True) == "prog metal"


def test_rewrite_empty_replacement_does_not_clear_value(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path / "library.db")
    config["rewrite"]["style"] = {"Prog Metal": ""}
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"audio")
    monkeypatch.setattr("noqlen_forge.rewrite.read_track", lambda path: _track(path, style="Prog Metal"))

    code, output = rewrite_path(audio, config, apply=True, fields=["style"], tags_only=True)

    assert code == 0
    assert "Updated tags: 0" in output


def test_rewrite_dry_run_does_not_write_tags_or_db(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"audio")
    _seed_db(config, audio, style="Prog Metal; death metal; Prog Metal")
    monkeypatch.setattr("noqlen_forge.rewrite.read_track", lambda path: _track(path))
    monkeypatch.setattr("noqlen_forge.rewrite.apply_musicbrainz_writes", lambda plans, apply: (_ for _ in ()).throw(AssertionError("should not write")))

    code, output = rewrite_path(audio, config, apply=False, fields=["style", "label"])

    assert code == 0
    assert "Mode: DRY-RUN" in output
    with connect(config) as conn:
        assert conn.execute("SELECT label FROM albums").fetchone()["label"] == "Season of Mist"


def test_rewrite_apply_writes_tags(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"audio")
    monkeypatch.setattr("noqlen_forge.rewrite.read_track", lambda path: _track(path))
    captured = []
    monkeypatch.setattr("noqlen_forge.rewrite.apply_musicbrainz_writes", lambda plans, apply: captured.extend(plans) or [])

    code, output = rewrite_path(audio, config, apply=True, fields=["style"], tags_only=True)

    assert code == 0
    assert "Updated tags: 1" in output
    assert captured[0].changes == {"Style": "Progressive Metal; Death Metal"}


def test_rewrite_apply_updates_db_and_records_operation(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"audio")
    _seed_db(config, audio, style="Prog Metal; death metal; Prog Metal")
    monkeypatch.setattr("noqlen_forge.rewrite.read_track", lambda path: _track(path))

    code, output = rewrite_path(audio, config, apply=True, fields=["label"], db_only=True)

    assert code == 0
    assert "Updated DB: 1" in output
    with connect(config) as conn:
        assert conn.execute("SELECT label FROM albums").fetchone()["label"] == "Season Of Mist"
        assert conn.execute("SELECT operation FROM operations ORDER BY id DESC LIMIT 1").fetchone()["operation"] == "rewrite"


def test_rewrite_field_filter_limits_changes(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"audio")
    _seed_db(config, audio, style="Prog Metal; death metal; Prog Metal")
    monkeypatch.setattr("noqlen_forge.rewrite.read_track", lambda path: _track(path))

    rewrite_path(audio, config, apply=True, fields=["style"], db_only=True)

    with connect(config) as conn:
        assert conn.execute("SELECT label FROM albums").fetchone()["label"] == "Season of Mist"
        values = {row["value"] for row in conn.execute("SELECT value FROM track_tags WHERE key = 'style'")}
    assert values == {"Progressive Metal", "Death Metal"}


def test_rewrite_tags_only_does_not_update_db(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"audio")
    _seed_db(config, audio)
    monkeypatch.setattr("noqlen_forge.rewrite.read_track", lambda path: _track(path))
    monkeypatch.setattr("noqlen_forge.rewrite.apply_musicbrainz_writes", lambda plans, apply: [])

    rewrite_path(audio, config, apply=True, fields=["label"], tags_only=True)

    with connect(config) as conn:
        assert conn.execute("SELECT label FROM albums").fetchone()["label"] == "Season of Mist"


def test_rewrite_db_only_does_not_write_tags(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"audio")
    _seed_db(config, audio)
    monkeypatch.setattr("noqlen_forge.rewrite.read_track", lambda path: _track(path))
    monkeypatch.setattr("noqlen_forge.rewrite.apply_musicbrainz_writes", lambda plans, apply: (_ for _ in ()).throw(AssertionError("should not write")))

    code, output = rewrite_path(audio, config, apply=True, fields=["label"], db_only=True)

    assert code == 0
    assert "Updated tags: 0" in output


def test_rewrite_protected_field_blocks_without_force(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"audio")
    _seed_db(config, audio)
    monkeypatch.setattr("noqlen_forge.rewrite.read_track", lambda path: _track(path))

    code, output = rewrite_path(audio, config, apply=True, fields=["mb_album_id"])

    assert code == 1
    assert "Status: REVIEW" in output
    with connect(config) as conn:
        assert conn.execute("SELECT mb_album_id FROM albums").fetchone()["mb_album_id"] == "old-mbid"


def test_rewrite_safety_blocks_automated_apply_outside_musiclab(tmp_path: Path, monkeypatch, capsys) -> None:
    from noqlen_forge.cli import main

    config_root = tmp_path / "xdg"
    config_dir = config_root / "noqlen-forge"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.toml"
    db_path = tmp_path / "library.db"
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"audio")
    config_path.write_text(f"[database]\npath = \"{db_path}\"\n[rewrite.style]\n\"Prog Metal\" = \"Progressive Metal\"\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")

    code = main(["maintain", "rewrite", str(outside), "--apply"])

    assert code == 1
    assert "Refusing automated --apply outside MusicLab" in capsys.readouterr().out
