import json
from pathlib import Path

from noqlen_forge.cli import build_parser
from noqlen_forge.config import default_config
from noqlen_forge.db import apply_migrations, connect, get_counts, upsert_album, upsert_file, upsert_track
from noqlen_forge.duplicates import duplicates_path


def _config(path: Path) -> dict:
    config = default_config()
    config["database"]["path"] = str(path)
    return config


def _add_track(config: dict, path: Path, title: str = "Song", artist: str = "Artist", album: str = "Album", albumartist: str = "Artist", duration: float = 123.0, mb_track_id: str = "", mb_release_track_id: str = "", acoustid_id: str = "", mb_album_id: str = "", mb_release_group_id: str = "", track: int = 1, originaldate: str = "2026") -> None:
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album_key": f"{path}:album", "album": album, "albumartist": albumartist, "mb_album_id": mb_album_id, "mb_release_group_id": mb_release_group_id, "originaldate": originaldate})
        track_id = upsert_track(conn, {"title": title, "artist": artist, "albumartist": albumartist, "track": track, "mb_track_id": mb_track_id, "mb_release_track_id": mb_release_track_id, "acoustid_id": acoustid_id}, album_id=album_id)
        upsert_file(conn, path, {"format": path.suffix.lstrip("."), "duration": duration, "status": "active"}, track_id=track_id)
        conn.commit()


def test_detect_duplicate_by_mb_track_id(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", mb_track_id="mb-track")
    _add_track(config, tmp_path / "copy.flac", mb_track_id="mb-track")

    code, output = duplicates_path(config, scope="tracks", by="mb_track_id")

    assert code == 0
    assert "same MB Track ID" in output
    assert "Confidence: high" in output
    assert "Status: WARN" in output


def test_detect_duplicate_by_acoustid_id(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", acoustid_id="acoustid")
    _add_track(config, tmp_path / "b.flac", acoustid_id="acoustid")

    _, output = duplicates_path(config, scope="tracks", by="acoustid")

    assert "same AcoustID" in output
    assert "Confidence: high" in output


def test_detect_duplicate_by_artist_title_duration(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", title="Runaway", duration=100.0)
    _add_track(config, tmp_path / "b.flac", title="Runaway", duration=101.5)

    _, output = duplicates_path(config, scope="tracks", by="artist,title,duration")

    assert "same artist/title/duration" in output
    assert "Confidence: medium" in output


def test_duration_delta_is_respected(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", title="Runaway", duration=100.0)
    _add_track(config, tmp_path / "b.flac", title="Runaway", duration=104.0)

    _, output = duplicates_path(config, scope="tracks", by="artist,title,duration")

    assert "Duplicate tracks: none" in output


def test_detect_duplicate_albums_by_mb_album_id(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a" / "01.flac", album="Album A", mb_album_id="mb-album")
    _add_track(config, tmp_path / "b" / "01.flac", album="Album B", mb_album_id="mb-album")

    _, output = duplicates_path(config, scope="albums", by="mb_album_id")

    assert "Duplicate albums: 1 groups" in output
    assert "same MB Album ID" in output


def test_detect_duplicate_albums_by_release_group_id(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a" / "01.flac", mb_release_group_id="rg", mb_album_id="release-a")
    _add_track(config, tmp_path / "b" / "01.flac", mb_release_group_id="rg", mb_album_id="release-b")

    _, output = duplicates_path(config, scope="albums", by="mb_release_group_id")
    assert "same release group/album" in output


def test_strict_strategy_avoids_fuzzy_without_ids(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", title="Same", duration=100.0)
    _add_track(config, tmp_path / "b.flac", title="Same", duration=100.5)

    _, output = duplicates_path(config, scope="tracks", strategy="strict")
    assert "Duplicate tracks: none" in output


def test_loose_strategy_detects_low_confidence_album_name(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a" / "01.flac", album="Same Album")
    _add_track(config, tmp_path / "b" / "01.flac", album="Same Album")

    _, output = duplicates_path(config, scope="albums", by="albumartist,album", strategy="loose")
    assert "Confidence: low" in output
    assert "Status: REVIEW" in output


def test_safe_strategy_does_not_match_different_titles(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", title="One", duration=100.0)
    _add_track(config, tmp_path / "b.flac", title="Two", duration=100.0)

    _, output = duplicates_path(config, scope="tracks")
    assert "Duplicate tracks: none" in output


def test_tracks_and_albums_scope_are_separate(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", mb_track_id="mb-track")
    _add_track(config, tmp_path / "b.flac", mb_track_id="mb-track")

    _, albums = duplicates_path(config, scope="albums", by="mb_album_id")
    _, tracks = duplicates_path(config, scope="tracks", by="mb_track_id")
    assert "Duplicate albums: none" in albums
    assert "Duplicate tracks: 1 groups" in tracks


def test_by_selects_criterion(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", mb_track_id="mb-track", title="One")
    _add_track(config, tmp_path / "b.flac", mb_track_id="mb-track", title="Two")

    _, output = duplicates_path(config, scope="tracks", by="artist,title,duration")
    assert "Duplicate tracks: none" in output


def test_json_output_is_valid_and_stable(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", mb_track_id="mb-track")
    _add_track(config, tmp_path / "b.flac", mb_track_id="mb-track")

    _, output = duplicates_path(config, scope="tracks", by="mb_track_id", output_format="json")
    payload = json.loads(output)
    assert payload["scope"] == "tracks"
    assert payload["strategy"] == "safe"
    assert payload["status"] == "WARN"
    assert payload["groups"][0]["confidence"] == "high"
    assert "files" in payload["groups"][0]


def test_command_does_not_alter_db_or_files(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    audio = tmp_path / "a.flac"
    audio.write_bytes(b"fixture")
    before_bytes = audio.read_bytes()
    _add_track(config, audio, mb_track_id="mb-track")
    _add_track(config, tmp_path / "b.flac", mb_track_id="mb-track")
    with connect(config) as conn:
        before = get_counts(conn)

    code, _ = duplicates_path(config, scope="tracks")

    with connect(config) as conn:
        after = get_counts(conn)
    assert code == 0
    assert before == after
    assert audio.read_bytes() == before_bytes


def test_path_limits_duplicate_search(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    _add_track(config, inside / "a.flac", mb_track_id="mb-track")
    _add_track(config, outside / "b.flac", mb_track_id="mb-track")

    _, output = duplicates_path(config, target=inside, scope="tracks", by="mb_track_id")
    assert "Duplicate tracks: none" in output


def test_cli_has_no_duplicates_apply_flag() -> None:
    parser = build_parser()
    try:
        parser.parse_args(["duplicates", "--apply"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("duplicates must not accept --apply")


def test_output_does_not_expose_sensitive_payloads(tmp_path) -> None:
    config = _config(tmp_path / "library.db")
    _add_track(config, tmp_path / "a.flac", acoustid_id="acoustid-secret")
    _add_track(config, tmp_path / "b.flac", acoustid_id="acoustid-secret")

    _, output = duplicates_path(config, scope="tracks", by="acoustid")
    assert "fingerprint" not in output.casefold()
    assert "lyrics" not in output.casefold()
    assert "acoustid-secret" not in output
