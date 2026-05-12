import csv
import json
import sqlite3
from io import StringIO
from pathlib import Path

from noqlen_forge import cli
from noqlen_forge.db import connect, get_counts
from noqlen_forge.smart_playlists import smart_create, smart_delete, smart_export, smart_list, smart_refresh, smart_rename, smart_show
from test_export import _config, _seed


def _smart_count(config: dict) -> int:
    with connect(config) as conn:
        return int(conn.execute("SELECT COUNT(*) AS count FROM smart_playlists").fetchone()["count"])


def _seed_ratings(config: dict) -> None:
    with connect(config) as conn:
        account_id = conn.execute("INSERT INTO player_accounts(player, name, base_url, username, created_at, updated_at) VALUES ('navidrome', 'fake', 'http://fake', 'tester', 'now', 'now')").lastrowid
        row = conn.execute("SELECT t.id AS track_id, f.id AS file_id, t.title FROM tracks t JOIN files f ON f.track_id = t.id WHERE t.title = 'Super Shy' LIMIT 1").fetchone()
        conn.execute(
            """
            INSERT INTO player_rating_backups(player_account_id, player, user, navidrome_id, library_track_id, library_file_id, identity_key, title, artist, album, rating, starred, backed_up_at, updated_at)
            VALUES (?, 'navidrome', 'tester', 'song-1', ?, ?, 'mb:track', ?, 'NewJeans', 'Get Up', 5, 1, 'now', 'now')
            """,
            (account_id, row["track_id"], row["file_id"], row["title"]),
        )
        conn.commit()


def test_smart_create_dry_run_does_not_save(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    code, output = smart_create(config, "Favorites", 'artist:"NewJeans"')

    assert code == 0
    assert "DRY-RUN" in output
    assert _smart_count(config) == 0


def test_smart_create_apply_saves_and_blocks_duplicate(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    code, _ = smart_create(config, "Favorites", 'artist:"NewJeans"', apply=True, sort="artist", limit=10)
    duplicate_code, duplicate = smart_create(config, "Favorites", 'artist:"NewJeans"', apply=True)

    assert code == 0
    assert _smart_count(config) == 1
    assert duplicate_code == 1
    assert "already exists" in duplicate


def test_smart_create_invalid_query_blocks(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")

    code, output = smart_create(config, "Broken", "unknownfield:value", apply=True)

    assert code == 1
    assert "Unknown field" in output
    assert _smart_count(config) == 0


def test_smart_list_and_show(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    smart_create(config, "Favorites", 'artist:"NewJeans"', apply=True)

    list_code, list_output = smart_list(config)
    show_code, show_output = smart_show(config, "Favorites")

    assert list_code == 0
    assert show_code == 0
    assert "Smart playlists: 1" in list_output
    assert 'artist:"NewJeans"' in show_output
    assert "Tracks now:" in show_output


def test_smart_export_m3u8_json_and_csv(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    paths = _seed(config, tmp_path / "Library")
    smart_create(config, "Favorites", 'artist:"NewJeans"', apply=True)

    m3u = tmp_path / "favorites.m3u8"
    code, summary = smart_export(config, "Favorites", output=m3u)
    json_code, json_output = smart_export(config, "Favorites", export_format="json")
    csv_code, csv_output = smart_export(config, "Favorites", export_format="csv")

    assert code == 0
    assert "Status: OK" in summary
    assert m3u.read_text(encoding="utf-8").startswith("#EXTM3U")
    assert str(paths["rich"].resolve(strict=False)) in m3u.read_text(encoding="utf-8")
    assert json_code == 0
    assert json.loads(json_output)["type"] == "smart_playlist"
    assert csv_code == 0
    assert next(csv.DictReader(StringIO(csv_output)))["artist"] == "NewJeans"


def test_smart_refresh_recalculates(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    smart_create(config, "Missing Lyrics", "missing:lyrics", apply=True)
    output = tmp_path / "missing.m3u8"

    smart_refresh(config, "Missing Lyrics", output=output)
    before = output.read_text(encoding="utf-8")
    with connect(config) as conn:
        conn.execute("UPDATE files SET has_lyrics = 1")
        conn.commit()
    code, _ = smart_refresh(config, "Missing Lyrics", output=output, force=True)

    assert code == 0
    assert before != output.read_text(encoding="utf-8")


def test_smart_delete_and_rename_dry_run_and_apply(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    smart_create(config, "Favorites", "NewJeans", apply=True)

    smart_rename(config, "Favorites", "Renamed")
    assert smart_show(config, "Favorites")[0] == 0
    assert smart_show(config, "Renamed")[0] == 1
    assert smart_rename(config, "Favorites", "Renamed", apply=True)[0] == 0
    assert smart_show(config, "Renamed")[0] == 0
    smart_delete(config, "Renamed")
    assert _smart_count(config) == 1
    assert smart_delete(config, "Renamed", apply=True)[0] == 0
    assert _smart_count(config) == 0


def test_smart_output_force_and_library_path_mode(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    smart_create(config, "Favorites", "NewJeans", apply=True)
    output = tmp_path / "favorites.m3u8"
    smart_export(config, "Favorites", output=output)

    blocked_code, blocked = smart_export(config, "Favorites", output=output)
    forced_code, _ = smart_export(config, "Favorites", output=output, force=True)
    library_code, library_output = smart_export(config, "Favorites", path_mode="library")

    assert blocked_code == 1
    assert "Use --force" in blocked
    assert forced_code == 0
    assert library_code == 1
    assert "requires --library-root" in library_output


def test_smart_rating_starred_and_missing_has_queries(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    _seed_ratings(config)

    smart_create(config, "Rated", "rating:>=4 starred:true", apply=True)
    smart_create(config, "Covered", "has:cover", apply=True)
    smart_create(config, "Missing Lyrics", "missing:lyrics", apply=True)

    assert json.loads(smart_export(config, "Rated", export_format="json")[1])["count"] >= 1
    assert json.loads(smart_export(config, "Covered", export_format="json")[1])["count"] >= 1
    assert json.loads(smart_export(config, "Missing Lyrics", export_format="json")[1])["count"] >= 1


def test_smart_playlist_does_not_alter_music_files_or_tags(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    paths = _seed(config, tmp_path / "Library")
    with connect(config) as conn:
        before_counts = get_counts(conn)
    before_mtime = paths["rich"].stat().st_mtime_ns

    smart_create(config, "Favorites", "NewJeans", apply=True)
    smart_export(config, "Favorites", export_format="json")
    smart_refresh(config, "Favorites", output=tmp_path / "favorites.m3u8")

    assert paths["rich"].stat().st_mtime_ns == before_mtime
    with connect(config) as conn:
        assert get_counts(conn) == before_counts


def test_smart_cli_stdout_export_is_structured_workflow(tmp_path: Path, capsys) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    smart_create(config, "Favorites", "NewJeans", apply=True)
    parser = cli.build_parser()
    args = parser.parse_args(["playlist", "smart", "export", "Favorites", "--format", "json"])

    assert cli.playlist_command(args, config=config) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["command"] == "playlist smart export"
    assert payload["counts"]["tracks"] >= 1
    assert "Smart playlist export" not in out
