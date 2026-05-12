from __future__ import annotations

import json
from pathlib import Path

import pytest

from noqlen_forge.db import apply_migrations, connect, record_candidate, record_field_decision, record_provider_run, upsert_album, upsert_file, upsert_track
from noqlen_forge.review import review_list, review_resolve, review_show
from helpers import assert_command_status, temp_db_config

pytestmark = pytest.mark.db


def _seed(config: dict, path: Path, *, field: str = "style", current: str = "Black Metal", candidate: str = "Progressive Metal", action: str = "review", target_type: str = "album") -> int:
    with connect(config) as conn:
        apply_migrations(conn)
        album_id = upsert_album(conn, {"album": "Urn", "albumartist": "Ne Obliviscaris", "mb_album_id": "mb-old"})
        track_id = upsert_track(conn, {"title": "Libera", "artist": "Ne Obliviscaris", "mb_track_id": "track-old"}, album_id=album_id)
        file_id = upsert_file(conn, path, {"format": "flac"}, track_id=track_id)
        target_id = album_id if target_type == "album" else track_id if target_type == "track" else file_id
        run_id = record_provider_run(conn, "discogs", target_type, target_id, "review", query="Urn")
        record_candidate(conn, run_id, "discogs", "11086464", score=0.91, confidence="medium", payload_summary={"format": "File, Album"})
        record_candidate(conn, run_id, "discogs", "11319329", score=0.91, confidence="medium", payload_summary={"country": "Australia", "format": "2xVinyl"})
        decision_id = record_field_decision(conn, run_id, target_type, target_id, field, current_value=current, candidate_value=candidate, selected_value="", provider="discogs", confidence="medium", action=action, reason="existing value conflicts with provider value")
        conn.commit()
        return decision_id


def test_review_lists_pending_decisions(tmp_path: Path) -> None:
    config = temp_db_config(tmp_path / "library.db")
    album = tmp_path / "Album"
    track = album / "song.flac"
    _seed(config, track)

    code, output = review_list(config, album)

    assert code == 1
    assert "Pending decisions: 1" in output
    assert "Status: REVIEW" in output
    assert "11086464" in output


def test_review_returns_ok_when_empty(tmp_path: Path) -> None:
    config = temp_db_config(tmp_path / "library.db")

    code, output = review_list(config)

    assert code == 0
    assert_command_status(output, "OK")


def test_review_show_id(tmp_path: Path) -> None:
    config = temp_db_config(tmp_path / "library.db")
    decision_id = _seed(config, tmp_path / "song.flac")

    code, output = review_show(config, decision_id)

    assert code == 0
    assert f"Review decision {decision_id}" in output
    assert "Actions: accept" in output


def test_review_json_output(tmp_path: Path) -> None:
    config = temp_db_config(tmp_path / "library.db")
    _seed(config, tmp_path / "song.flac")

    code, output = review_list(config, output_format="json")
    payload = json.loads(output)

    assert code == 1
    assert payload["status"] == "REVIEW"
    assert payload["pending"] == 1
    assert payload["decisions"][0]["actions"] == ["accept", "keep", "skip", "reject"]


def test_review_resolve_accept_dry_run_does_not_write(tmp_path: Path, monkeypatch) -> None:
    config = temp_db_config(tmp_path / "library.db")
    decision_id = _seed(config, tmp_path / "song.flac")
    monkeypatch.setattr("noqlen_forge.review._write_tag", lambda path, label, value: (_ for _ in ()).throw(AssertionError("dry-run wrote tag")))

    code, output = review_resolve(config, str(decision_id), action="accept")

    assert code == 0
    assert "Mode: DRY-RUN" in output
    with connect(config) as conn:
        row = conn.execute("SELECT resolved FROM field_decisions WHERE id = ?", (decision_id,)).fetchone()
    assert row["resolved"] == 0


def test_review_resolve_accept_apply_writes_simple_field(tmp_path: Path, monkeypatch) -> None:
    config = temp_db_config(tmp_path / "library.db")
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / ".noqlen-forge-lab").write_text("lab\n", encoding="utf-8")
    track = lab / "song.flac"
    decision_id = _seed(config, track)
    writes: list[tuple[Path, str, str]] = []
    monkeypatch.setattr("noqlen_forge.review._write_tag", lambda path, label, value: writes.append((path, label, value)))

    code, output = review_resolve(config, str(decision_id), action="accept", apply=True)

    assert code == 0
    assert "Status: OK" in output
    assert writes == [(track, "Style", "Progressive Metal")]
    with connect(config) as conn:
        decision = conn.execute("SELECT resolved, resolved_action, selected_value FROM field_decisions WHERE id = ?", (decision_id,)).fetchone()
        tag = conn.execute("SELECT value FROM album_tags WHERE key = 'style'").fetchone()
        op = conn.execute("SELECT operation FROM operations ORDER BY id DESC LIMIT 1").fetchone()
    assert dict(decision) == {"resolved": 1, "resolved_action": "accept", "selected_value": "Progressive Metal"}
    assert tag["value"] == "Progressive Metal"
    assert op["operation"] == "review_resolve"


def test_review_resolve_keep_does_not_write_tag(tmp_path: Path, monkeypatch) -> None:
    config = temp_db_config(tmp_path / "library.db")
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / ".noqlen-forge-lab").write_text("lab\n", encoding="utf-8")
    decision_id = _seed(config, lab / "song.flac")
    monkeypatch.setattr("noqlen_forge.review._write_tag", lambda path, label, value: (_ for _ in ()).throw(AssertionError("keep wrote tag")))

    code, _ = review_resolve(config, str(decision_id), action="keep", apply=True)

    assert code == 0
    with connect(config) as conn:
        row = conn.execute("SELECT resolved, resolved_action, selected_value FROM field_decisions WHERE id = ?", (decision_id,)).fetchone()
    assert dict(row) == {"resolved": 1, "resolved_action": "keep", "selected_value": "Black Metal"}


def test_review_resolve_reject_records_rejection(tmp_path: Path) -> None:
    config = temp_db_config(tmp_path / "library.db")
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / ".noqlen-forge-lab").write_text("lab\n", encoding="utf-8")
    decision_id = _seed(config, lab / "song.flac")

    code, _ = review_resolve(config, str(decision_id), action="reject", apply=True)

    assert code == 0
    with connect(config) as conn:
        row = conn.execute("SELECT resolved, resolved_action FROM field_decisions WHERE id = ?", (decision_id,)).fetchone()
    assert dict(row) == {"resolved": 1, "resolved_action": "reject"}


def test_review_resolve_skip_keeps_decision_pending(tmp_path: Path) -> None:
    config = temp_db_config(tmp_path / "library.db")
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / ".noqlen-forge-lab").write_text("lab\n", encoding="utf-8")
    decision_id = _seed(config, lab / "song.flac")

    code, _ = review_resolve(config, str(decision_id), action="skip", apply=True)
    list_code, output = review_list(config)

    assert code == 0
    assert list_code == 1
    assert f"show {decision_id}" in output
    with connect(config) as conn:
        row = conn.execute("SELECT resolved, resolved_action FROM field_decisions WHERE id = ?", (decision_id,)).fetchone()
    assert dict(row) == {"resolved": 0, "resolved_action": "skip"}


def test_review_resolve_manual_value(tmp_path: Path, monkeypatch) -> None:
    config = temp_db_config(tmp_path / "library.db")
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / ".noqlen-forge-lab").write_text("lab\n", encoding="utf-8")
    decision_id = _seed(config, lab / "song.flac")
    monkeypatch.setattr("noqlen_forge.review._write_tag", lambda path, label, value: None)

    code, _ = review_resolve(config, str(decision_id), value="Progressive Metal; Death Metal", apply=True)

    assert code == 0
    with connect(config) as conn:
        values = {row["value"] for row in conn.execute("SELECT value FROM album_tags WHERE key = 'style'")}
    assert values == {"Progressive Metal", "Death Metal"}


def test_review_protected_field_requires_force(tmp_path: Path) -> None:
    config = temp_db_config(tmp_path / "library.db")
    decision_id = _seed(config, tmp_path / "song.flac", field="mb_album_id", current="old", candidate="new")

    code, output = review_resolve(config, str(decision_id), action="accept", apply=True)

    assert code == 1
    assert "requires --force" in output
    assert_command_status(output, "REVIEW")


def test_review_unknown_field_returns_clear_error(tmp_path: Path) -> None:
    config = temp_db_config(tmp_path / "library.db")
    decision_id = _seed(config, tmp_path / "song.flac", field="not_a_field")

    code, output = review_resolve(config, str(decision_id), action="accept")

    assert code == 1
    assert "Unknown field" in output


def test_review_safety_blocks_automated_apply_outside_musiclab(tmp_path: Path, monkeypatch) -> None:
    config = temp_db_config(tmp_path / "library.db")
    decision_id = _seed(config, tmp_path / "song.flac")
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")

    code, output = review_resolve(config, str(decision_id), action="accept", apply=True)

    assert code == 1
    assert "Refusing automated --apply outside MusicLab" in output


def test_review_resolve_idempotent_after_apply(tmp_path: Path, monkeypatch) -> None:
    config = temp_db_config(tmp_path / "library.db")
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / ".noqlen-forge-lab").write_text("lab\n", encoding="utf-8")
    decision_id = _seed(config, lab / "song.flac")
    monkeypatch.setattr("noqlen_forge.review._write_tag", lambda path, label, value: None)

    assert review_resolve(config, str(decision_id), action="accept", apply=True)[0] == 0
    code, output = review_resolve(config, str(decision_id), action="accept", apply=True)

    assert code == 0
    assert "no pending decision" in output
    with connect(config) as conn:
        count = conn.execute("SELECT COUNT(*) AS count FROM operations WHERE operation = 'review_resolve'").fetchone()["count"]
    assert count == 1
