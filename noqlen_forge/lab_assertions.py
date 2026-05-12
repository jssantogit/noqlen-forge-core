from __future__ import annotations

import csv
import io
import json
from pathlib import Path


def assert_exists(path: Path) -> None:
    if not path.exists():
        raise AssertionError(f"Expected path to exist: {path}")


def assert_not_exists(path: Path) -> None:
    if path.exists():
        raise AssertionError(f"Expected path not to exist: {path}")


def assert_status(output: str, status: str) -> None:
    if f"Status: {status}" not in output and f"MusicLab: {status}" not in output:
        raise AssertionError(f"Expected status {status} in output")


def assert_db_counts(counts: dict[str, int], **expected: int) -> None:
    for key, value in expected.items():
        if counts.get(key) != value:
            raise AssertionError(f"Expected DB count {key}={value}, got {counts.get(key)}")


def assert_no_real_paths(output: str) -> None:
    real_library = "/mnt/sdcard/Music/Biblioteca de Musicas"
    if real_library in output:
        raise AssertionError("Output references the real music library")


def assert_no_secrets(output: str) -> None:
    lowered = output.casefold()
    for secret in ("lastfm_api_key", "discogs_token", "acoustid_key", "token", "salt"):
        if secret in lowered:
            raise AssertionError(f"Output contains secret marker: {secret}")


def assert_output_clean(output: str) -> None:
    assert_no_real_paths(output)
    assert_no_secrets(output)


def assert_file_not_overwritten(before: tuple[int, str], after: tuple[int, str]) -> None:
    if before != after:
        raise AssertionError("File fingerprint changed unexpectedly")


def assert_json_valid(text: str) -> object:
    return json.loads(text)


def assert_csv_valid(text: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows and not text.strip().splitlines():
        raise AssertionError("CSV output is empty")
    return rows


def assert_playlist_valid(text: str) -> None:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise AssertionError("Playlist output is not a valid M3U")
