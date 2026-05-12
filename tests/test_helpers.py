from __future__ import annotations

from helpers import assert_command_status, assert_json_stdout_clean, assert_musiclab_safe_paths, assert_no_db_change, assert_no_file_change, assert_no_real_library_path, assert_no_secrets, assert_playlist_file_valid, assert_status, assert_step, temp_db_config


def test_assert_command_status_matches_semantic_status() -> None:
    assert_command_status("Audit\nStatus: WARN\nDetails", "WARN")


def test_assert_step_matches_compact_lab_step() -> None:
    assert_step("[1/2] Create fixtures        OK     26 targets", "Create fixtures", "OK")


def test_assert_no_real_library_path_allows_safe_output() -> None:
    assert_no_real_library_path("MusicLab: /tmp/noqlen-forge-lab")


def test_temp_db_config_points_to_requested_path(tmp_path) -> None:
    path = tmp_path / "library.db"

    assert temp_db_config(path)["database"]["path"] == str(path)


def test_semantic_helpers_cover_common_assertions(tmp_path) -> None:
    assert_status("Result\nStatus: OK\n", "OK")
    assert_no_db_change({"tracks": 1}, {"tracks": 1})
    assert_no_file_change({}, {})
    assert_no_secrets("Status: OK")
    assert_json_stdout_clean('{"status":"ok"}')
    playlist = tmp_path / "playlist.m3u8"
    playlist.write_text("#EXTM3U\ntrack.flac\n", encoding="utf-8")
    assert_playlist_file_valid(playlist)
    lab = tmp_path / "noqlen-forge-lab"
    lab.mkdir()
    (lab / ".noqlen-forge-lab").write_text("lab\n", encoding="utf-8")
    assert_musiclab_safe_paths(lab / "Library" / "Album")
