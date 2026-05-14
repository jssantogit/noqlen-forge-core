import json
from pathlib import Path

import pytest

from noqlen_forge import cli
from noqlen_forge.api import NoqlenForgeCore
from noqlen_forge.audio import Track
from noqlen_forge.audit import AuditResult, render_audit
from noqlen_forge.navidrome import NavidromeConfig, RatingItem
from noqlen_forge.safety import SafetyError
from noqlen_forge.services.cli_helpers import exit_code_from_status, parse_fields, render_structured_service_result
from noqlen_forge.services.core_service import CoverOptions, run_cover_service
from noqlen_forge.services.enrich_service import EnrichOptions, run_enrich_service
from noqlen_forge.services.library_service import OrganizeOptions, run_organize_service
from noqlen_forge.services.library_maintenance_service import BatchOptions, CleanupOptions, run_batch_service, run_cleanup_service
from noqlen_forge.services.maintenance_service import SyncOptions, run_sync_service
from noqlen_forge.services.audit_service import AuditOptions, audit_result_from_workflow, run_audit_service
from noqlen_forge.services.lyrics_service import LyricsOptions, run_lyrics_service
from noqlen_forge.services.metadata_service import ApplyMBIDOptions, CandidatesOptions, MetadataOptions, ReviewOptions, run_apply_mbid_service, run_candidates_service, run_metadata_service, run_review_service
from noqlen_forge.services.navidrome_service import NavidromePlaylistsOptions, NavidromeRatingsOptions, run_navidrome_playlists_service, run_navidrome_ratings_service
from noqlen_forge.services.playlist_service import PlaylistExportOptions, render_playlist_export_result, run_playlist_export_service
from noqlen_forge.services.report_service import ExportOptions, QueryOptions, build_duplicates_options, build_export_options, build_missing_options, render_report_result, run_export_service, run_query_service
from noqlen_forge.services.result_helpers import finish_object_result, finish_text_result, first_line, status_from_text_output
from noqlen_forge.services.types import sanitize_value_for_output, workflow_result_from_dict, workflow_result_to_dict, workflow_result_to_json
from noqlen_forge.smart_playlists import smart_create, smart_export
from noqlen_forge.workflow import AppliedChange, Artifact, PlannedChange, Status, StepResult, WorkflowResult
from test_export import _config, _seed
from test_metadata_providers import tracks
from test_review import _seed as _seed_review


class FakeNavidromeClient:
    def __init__(self, items: list[RatingItem] | None = None, playlists: list[dict] | None = None) -> None:
        self.config = NavidromeConfig(base_url="http://127.0.0.1:4533", username="tester")
        self.items = items or []
        self.playlists = playlists or []
        self.write_calls: list[tuple] = []

    def ping(self):
        return {"subsonic-response": {"status": "ok"}}

    def iter_rating_items(self):
        return list(self.items)

    def get_playlists(self):
        rows = [{"id": item["id"], "name": item["name"], "songCount": len(item.get("song_ids", []))} for item in self.playlists]
        return {"subsonic-response": {"status": "ok", "playlists": {"playlist": rows}}}

    def get_playlist(self, playlist_id):
        playlist = next((item for item in self.playlists if item["id"] == playlist_id), {"id": playlist_id, "name": playlist_id, "song_ids": []})
        entries = [{"id": song_id, "title": "Song", "artist": "Artist"} for song_id in playlist.get("song_ids", [])]
        return {"subsonic-response": {"status": "ok", "playlist": {"id": playlist_id, "name": playlist["name"], "entry": entries}}}


def _fake_lyrics_stats(path: Path, *, status: str = "OK"):
    return type(
        "Stats",
        (),
        {
            "status": status,
            "total": 1,
            "per_file": {},
            "provider_attempts": {},
            "embedded_existing": 0,
            "embedded_written": 0,
            "sidecar_written": 0,
            "missing": 0,
            "skipped": 0,
            "conflicts": [],
            "warnings": [],
            "selection_warnings": [],
            "errors": [],
        },
    )()


def test_service_options_are_argparse_free() -> None:
    options = AuditOptions(path=Path("Album"), config={"database": {"path": "library.db"}})

    assert options.path == Path("Album")
    assert not hasattr(options, "command")


def test_audit_service_returns_workflow_result(monkeypatch: pytest.MonkeyPatch) -> None:
    audit = AuditResult(tracks=[Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")], bad_fields=[])
    monkeypatch.setattr("noqlen_forge.services.audit_service.audit_path", lambda path: audit)

    result = run_audit_service(AuditOptions(path=Path("song.mp3")))

    assert isinstance(result, WorkflowResult)
    assert result.command == "audit"
    assert result.summary["files"] == 1
    assert audit_result_from_workflow(result) is audit


def test_audit_cli_uses_service(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    audit = AuditResult(tracks=[Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")], bad_fields=[])
    called = {"service": False}

    def fake_service(options: AuditOptions) -> WorkflowResult:
        called["service"] = True
        return WorkflowResult(Status.REVIEW, [StepResult(1, 1, "Read tags", Status.REVIEW, "1 files")], command="audit", target=options.path, details={"_audit_result": audit})

    monkeypatch.setattr(cli, "run_audit_service", fake_service)

    assert cli.main(["audit", "song.mp3"]) == 0

    assert called["service"] is True
    assert "Files: 1" in capsys.readouterr().out


def test_audit_cli_and_service_render_equivalent(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    audit = AuditResult(tracks=[Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")], bad_fields=[])
    monkeypatch.setattr("noqlen_forge.services.audit_service.audit_path", lambda path: audit)

    service = run_audit_service(AuditOptions(path=Path("song.mp3")))
    cli.main(["audit", "song.mp3"])

    assert capsys.readouterr().out.strip() == render_audit(audit_result_from_workflow(service)).strip()


def test_audit_cli_json_outputs_structured_workflow(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    audit = AuditResult(tracks=[Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")], bad_fields=[])
    monkeypatch.setattr("noqlen_forge.services.audit_service.audit_path", lambda path: audit)

    assert cli.main(["audit", "song.mp3", "--format", "json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "audit"
    assert payload["summary"]["files"] == 1
    assert payload["steps"][0]["name"] == "Read tags"


def test_enrich_service_dry_run_returns_structured_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    track = Track(path=tmp_path / "song.mp3", format="mp3", album="Album", artist="Artist", title="Song")
    audit = AuditResult(tracks=[track], bad_fields=[])

    monkeypatch.setattr("noqlen_forge.services.enrich_service.target_kind", lambda path: "single")
    monkeypatch.setattr("noqlen_forge.services.enrich_service.read_tracks", lambda path: [track])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.search_releases", lambda tracks: [])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.plan_cleanup", lambda tracks, release_date="": [])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.apply_cleanup", lambda plans, apply: None)
    monkeypatch.setattr("noqlen_forge.services.enrich_service.summarize_cleanup", lambda *args, **kwargs: "cleanup")
    monkeypatch.setattr("noqlen_forge.services.enrich_service.audit_path", lambda path: audit)

    result = run_enrich_service(EnrichOptions(path=tmp_path / "song.mp3", config={"enrich": {}}))

    assert result.command == "enrich"
    assert result.mode == "dry-run"
    assert result.summary["stages"]["enabled"] == ["cleanup"]
    assert [step.name for step in result.steps] == ["MusicBrainz", "Cleanup"]
    assert result.details["targets"][0]["final_audit"]


def test_enrich_service_apply_requires_explicit_confirmation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    track = Track(path=tmp_path / "song.mp3", format="mp3", album="Album", artist="Artist", title="Song")
    scored = type("Scored", (), {"score": 90, "release": {"id": "release-1"}})()

    monkeypatch.setattr("noqlen_forge.services.enrich_service.target_kind", lambda path: "single")
    monkeypatch.setattr("noqlen_forge.services.enrich_service.read_tracks", lambda path: [track])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.search_releases", lambda tracks: [{}])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.hydrate_releases", lambda releases: releases)
    monkeypatch.setattr("noqlen_forge.services.enrich_service.rank_releases", lambda tracks, releases: [scored])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.plan_cleanup", lambda tracks, release_date="": [])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.apply_cleanup", lambda plans, apply: None)
    monkeypatch.setattr("noqlen_forge.services.enrich_service.summarize_cleanup", lambda *args, **kwargs: "cleanup")
    monkeypatch.setattr("noqlen_forge.services.enrich_service.audit_path", lambda path: AuditResult(tracks=[track], bad_fields=[]))

    result = run_enrich_service(EnrichOptions(path=tmp_path / "song.mp3", config={}, apply=True))

    assert result.steps[0].status == Status.REVIEW
    assert "requires explicit confirmation" in result.steps[0].summary


def test_enrich_cli_uses_service_renderer(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    expected = WorkflowResult(
        Status.OK,
        [StepResult(1, 1, "Cleanup", Status.OK, "removed 0 empty/bad fields")],
        command="enrich",
        details={"targets": [{"target_name": "", "album": "Album", "artist": "Artist", "files": 1, "mode": "DRY-RUN", "stages": [{"index": 1, "total": 1, "name": "Cleanup", "status": "OK", "summary": "removed 0 empty/bad fields", "detail": "", "optional": False}], "warnings": [], "final_audit": "Status: OK"}]},
        safe_details={"targets": []},
    )
    called = {"service": False}

    def fake_service(options: EnrichOptions) -> WorkflowResult:
        called["service"] = True
        return expected

    monkeypatch.setattr(cli, "run_enrich_service", fake_service)

    assert cli.main(["enrich", "song.mp3", "--plain"]) == 0

    output = capsys.readouterr().out
    assert called["service"] is True
    assert "Album: Artist - Album" in output
    assert "Cleanup" in output


def test_enrich_service_serialized_result_redacts_sensitive_payloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    track = Track(path=tmp_path / "song.mp3", format="mp3", album="Album", artist="Artist", title="Song")

    monkeypatch.setattr("noqlen_forge.services.enrich_service.target_kind", lambda path: "single")
    monkeypatch.setattr("noqlen_forge.services.enrich_service.read_tracks", lambda path: [track])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.search_releases", lambda tracks: [])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.process_lyrics", lambda *args, **kwargs: type("Stats", (), {"total": 1, "embedded_existing": 0, "errors": [], "per_file": {track.path: "full lyrics should not leak"}, "synced_found": 1, "lyrics_after": 0, "tracks": [track]})())
    monkeypatch.setattr("noqlen_forge.services.enrich_service.plan_cleanup", lambda tracks, release_date="": [])
    monkeypatch.setattr("noqlen_forge.services.enrich_service.apply_cleanup", lambda plans, apply: None)
    monkeypatch.setattr("noqlen_forge.services.enrich_service.summarize_cleanup", lambda *args, **kwargs: "cleanup")
    monkeypatch.setattr("noqlen_forge.services.enrich_service.audit_path", lambda path: AuditResult(tracks=[track], bad_fields=[]))

    result = run_enrich_service(EnrichOptions(path=tmp_path / "song.mp3", config={"lyrics": {"enabled": True}}, lyrics=True))
    text = workflow_result_to_json(result)

    assert "full lyrics should not leak" not in text
    assert "fingerprint" not in text.casefold()


def test_playlist_export_service_returns_artifact(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    smart_create(config, "Favorites", "NewJeans", apply=True)
    output = tmp_path / "favorites.m3u8"

    result = run_playlist_export_service(PlaylistExportOptions(config, "Favorites", output=output))
    code, rendered = render_playlist_export_result(result, name="Favorites")

    assert code == 0
    assert result.status == Status.OK
    assert result.counts["tracks"] >= 1
    assert result.artifacts[0].path == output.resolve(strict=False)
    assert "Status: OK" in rendered
    assert output.read_text(encoding="utf-8").startswith("#EXTM3U")


def test_playlist_cli_and_service_export_equivalent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    smart_create(config, "Favorites", "NewJeans", apply=True)
    parser = cli.build_parser()
    args = parser.parse_args(["playlist", "smart", "export", "Favorites", "--format", "json"])

    service = run_playlist_export_service(PlaylistExportOptions(config, "Favorites", export_format="json"))
    assert cli.playlist_command(args, config=config) == 0

    cli_payload = json.loads(capsys.readouterr().out)
    service_payload = workflow_result_to_dict(service)
    assert cli_payload["command"] == "playlist smart export"
    assert cli_payload["counts"]["tracks"] == service_payload["counts"]["tracks"]
    assert cli_payload["details"]["definition"]["format"] == "json"


def test_playlist_cli_uses_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path / "library.db")
    called = {"service": False}

    def fake_service(options: PlaylistExportOptions) -> WorkflowResult:
        called["service"] = True
        return WorkflowResult(Status.OK, [StepResult(1, 1, "Write artifact", Status.OK, "stdout")], command="playlist smart export", details={"output_text": "#EXTM3U\n"})

    monkeypatch.setattr(cli, "run_playlist_export_service", fake_service)
    args = cli.build_parser().parse_args(["playlist", "smart", "export", "Favorites"])

    assert cli.playlist_command(args, config=config) == 0
    assert called["service"] is True
    assert capsys.readouterr().out == "#EXTM3U\n\n"


def test_navidrome_ratings_service_uses_fake_client_without_writes(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    client = FakeNavidromeClient([RatingItem(navidrome_id="n1", title="Song", artist="Artist", rating=5, starred=True)])

    result = run_navidrome_ratings_service(NavidromeRatingsOptions(config=config, command="backup", client=client))

    assert result.status == Status.WARN
    assert result.summary["total_items"] == 1
    assert result.safe_details["result"] == {}
    assert client.write_calls == []


def test_navidrome_playlists_service_returns_json_summary(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    client = FakeNavidromeClient(playlists=[{"id": "p1", "name": "Favorites", "song_ids": ["n1"]}])

    result = run_navidrome_playlists_service(NavidromePlaylistsOptions(config=config, command="list", client=client))

    assert result.status == Status.OK
    assert result.summary["count"] == 1
    assert result.safe_details["result"]["playlists"][0]["name"] == "Favorites"
    assert client.write_calls == []


def test_core_api_calls_navidrome_service_silently(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path / "library.db")
    client = FakeNavidromeClient(playlists=[{"id": "p1", "name": "Favorites"}])

    result = NoqlenForgeCore(config=config).navidrome_playlists_list(client=client)

    assert result.status == Status.OK
    assert result.summary["count"] == 1
    assert capsys.readouterr().out == ""


def test_workflow_result_json_redacts_sensitive_details() -> None:
    result = WorkflowResult(Status.OK, [StepResult(1, 1, "Read", Status.OK, "done")], command="test", details={"api_key": "secret-value", "lyrics": "full lyrics should not leak", "fingerprint": "abc" * 300, "safe": "value"})

    data = workflow_result_to_dict(result)
    text = workflow_result_to_json(result)

    assert data["details"]["api_key"] == "[redacted]"
    assert data["details"]["lyrics"] == "[redacted]"
    assert "full lyrics should not leak" not in text
    assert "secret-value" not in text
    assert data["details"]["safe"] == "value"


def test_workflow_result_json_has_job_and_round_trips() -> None:
    result = WorkflowResult(Status.OK, [StepResult(1, 1, "Read", Status.OK, "done")], command="audit", target=Path("Album"), target_type="album", summary={"files": 1})

    data = workflow_result_to_dict(result)
    restored = workflow_result_from_dict(data)

    assert data["workflow"] == "audit"
    assert data["target_type"] == "album"
    assert data["job"]["resumable"] is False
    assert restored.command == "audit"
    assert restored.summary["files"] == 1


def test_change_and_artifact_serialization_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    path.write_text("{}", encoding="utf-8")
    result = WorkflowResult(
        Status.OK,
        [StepResult(1, 1, "Plan", Status.OK)],
        command="test",
        planned_changes=[PlannedChange(path, "track", "lyrics", old_value="old", new_value="line\n" * 200, reason="candidate")],
        applied_changes=[AppliedChange("track", target_path=path, field="fingerprint", old_value="a" * 1000, new_value=b"abc")],
        artifacts=[Artifact("json", path=path, format="json", description="report")],
    )

    text = workflow_result_to_json(result)
    data = json.loads(text)

    assert "line\nline\nline" not in text
    assert data["planned_changes"][0]["new_value"] == "[redacted]"
    assert data["applied_changes"][0]["new_value"] == "[redacted]"
    assert data["artifacts"][0]["size_bytes"] == 2


def test_sanitize_value_redacts_secret_markers() -> None:
    assert sanitize_value_for_output("Authorization: Bearer secret") == "[redacted]"


def test_lyrics_service_dry_run_delegates_without_apply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = {"apply": None}

    def fake_process_lyrics(_tracks, **kwargs):
        calls["apply"] = kwargs["apply"]
        return _fake_lyrics_stats(tmp_path / "song.mp3")

    monkeypatch.setattr("noqlen_forge.services.lyrics_service.read_tracks", lambda path: [Track(path=path, format="mp3")])
    monkeypatch.setattr("noqlen_forge.services.lyrics_service.process_lyrics", fake_process_lyrics)
    monkeypatch.setattr("noqlen_forge.services.lyrics_service.render_lyrics_result", lambda *args, **kwargs: "Lyrics\nMode: DRY-RUN\nStatus: OK")

    result = run_lyrics_service(LyricsOptions(path=tmp_path / "song.mp3", apply=False))

    assert result.status == Status.DRY
    assert calls["apply"] is False


def test_lyrics_cli_json_is_structured_and_sanitized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("noqlen_forge.services.lyrics_service.read_tracks", lambda path: [Track(path=path, format="mp3")])
    monkeypatch.setattr("noqlen_forge.services.lyrics_service.process_lyrics", lambda *args, **kwargs: _fake_lyrics_stats(tmp_path / "song.mp3"))
    monkeypatch.setattr("noqlen_forge.services.lyrics_service.render_lyrics_result", lambda *args, **kwargs: "Lyrics\nThese are complete song words that should stay in text output only\nStatus: OK")

    assert cli.main(["lyrics", str(tmp_path / "song.mp3"), "--format", "json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "lyrics"
    assert payload["mode"] == "dry-run"
    assert "complete song words" not in json.dumps(payload)


def test_lyrics_service_apply_respects_safety(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")

    with pytest.raises(SafetyError):
        run_lyrics_service(LyricsOptions(path=tmp_path / "song.mp3", apply=True))


def test_legacy_smart_export_matches_service(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    smart_create(config, "Favorites", "NewJeans", apply=True)

    legacy_code, legacy_output = smart_export(config, "Favorites", export_format="json")
    service = run_playlist_export_service(PlaylistExportOptions(config, "Favorites", export_format="json"))

    assert legacy_code == 0
    assert json.loads(legacy_output)["count"] == json.loads(service.details["output_text"])["count"]


def test_cli_helpers_exit_code_and_fields() -> None:
    assert exit_code_from_status(Status.OK) == 0
    assert exit_code_from_status(Status.WARN) == 0
    assert exit_code_from_status(Status.REVIEW) == 2
    assert exit_code_from_status(Status.FAIL) == 1
    assert parse_fields(["artist"], "album, title") == ["artist", "album", "title"]


def test_service_result_helpers_normalize_legacy_text_results() -> None:
    workflow = WorkflowResult(Status.OK, [StepResult(1, 1, "Legacy", Status.OK)])

    result = finish_text_result(workflow, code=0, output="Legacy\nStatus: REVIEW", mode="dry-run")

    assert first_line("Legacy\nStatus: OK") == "Legacy"
    assert status_from_text_output(0, "Legacy\nStatus: WARN") == Status.WARN
    assert result.status == Status.REVIEW
    assert result.details["output_text"] == "Legacy\nStatus: REVIEW"


def test_service_result_helpers_normalize_object_results() -> None:
    class LegacyResult:
        code = 0
        status = "DRY"
        output = "Dry output"

    workflow = WorkflowResult(Status.OK, [StepResult(1, 1, "Legacy", Status.OK)])

    result = finish_object_result(workflow, LegacyResult(), mode="dry-run")

    assert result.status == Status.DRY
    assert result.summary == {"status": "DRY", "exit_code": 0}


def test_cover_service_apply_respects_safety(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")

    with pytest.raises(SafetyError):
        run_cover_service(CoverOptions(path=tmp_path / "song.mp3", config={}, apply=True))


def test_cover_cli_uses_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    called = {"service": False}

    def fake_service(options: CoverOptions) -> WorkflowResult:
        called["service"] = True
        return WorkflowResult(Status.OK, [StepResult(1, 1, "Cover", Status.OK, "ok")], command="cover", details={"exit_code": 0, "output_text": "Cover ok"})

    monkeypatch.setattr(cli, "run_cover_service", fake_service)

    assert cli.main(["cover", str(tmp_path)]) == 0
    assert called["service"] is True
    assert capsys.readouterr().out == "Cover ok\n"


def test_organize_cli_uses_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    called = {"service": False}

    def fake_service(options: OrganizeOptions) -> WorkflowResult:
        called["service"] = True
        return WorkflowResult(Status.DRY, [StepResult(1, 1, "Organize", Status.DRY, "dry")], command="organize", details={"exit_code": 0, "output_text": "Organize dry"})

    monkeypatch.setattr(cli, "run_organize_service", fake_service)

    assert cli.main(["organize", str(tmp_path), "--library", str(tmp_path / "Library")]) == 0
    assert called["service"] is True
    assert capsys.readouterr().out == "Organize dry\n"


def test_cleanup_service_returns_structured_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    track = Track(path=tmp_path / "song.mp3", format="mp3", tags={"bpm": ["300"]})
    monkeypatch.setattr("noqlen_forge.services.library_maintenance_service.read_tracks", lambda path: [track])
    monkeypatch.setattr("noqlen_forge.services.library_maintenance_service.apply_cleanup", lambda plans, apply: None)

    result = run_cleanup_service(CleanupOptions(tmp_path / "Album"))

    assert result.status == Status.DRY
    assert result.counts["removals"] == 1
    assert result.planned_changes[0].action == "remove"
    assert "output_text" not in result.safe_details


def test_batch_service_is_non_interactive_and_structured(tmp_path: Path) -> None:
    for index in range(21):
        album = tmp_path / f"Album {index}"
        album.mkdir()
        (album / "01.mp3").touch()

    result = run_batch_service(BatchOptions(tmp_path, apply=True, recursive=True, process=lambda target, apply: 0))

    assert result.status == Status.FAIL
    assert result.summary["cancelled"] is True
    assert result.counts["targets"] == 21


def test_sync_cli_uses_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    called = {"service": False}

    def fake_service(options: SyncOptions) -> WorkflowResult:
        called["service"] = True
        assert options.fields == ["artist", "album"]
        return WorkflowResult(Status.DRY, [StepResult(1, 1, "Sync", Status.DRY, "dry")], command="sync", details={"exit_code": 0, "output_text": "Sync dry"})

    monkeypatch.setattr(cli, "run_sync_service", fake_service)
    args = cli.build_parser().parse_args(["sync", str(tmp_path), "--tags-to-db", "--field", "artist", "--fields", "album"])

    assert cli.sync_command(args, config={}) == 0
    assert called["service"] is True
    assert capsys.readouterr().out == "Sync dry\n"


def test_query_service_and_cli_stdout_json_match(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    args = cli.build_parser().parse_args(["query", "NewJeans", "--format", "json"])

    service = run_query_service(QueryOptions(config, "NewJeans", output_format="json"))
    assert cli.query_command(args, config=config) == 0

    cli_payload = json.loads(capsys.readouterr().out)
    service_payload = json.loads(service.details["output_text"])
    assert cli_payload["count"] == service_payload["count"]


def test_export_service_and_cli_stdout_json_match(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path / "library.db")
    _seed(config, tmp_path / "Library")
    args = cli.build_parser().parse_args(["export", "NewJeans", "--format", "json"])

    service = run_export_service(ExportOptions(config, "NewJeans", export_format="json"))
    assert cli.export_command(args, config=config) == 0

    cli_payload = json.loads(capsys.readouterr().out)
    service_payload = json.loads(service.details["output_text"])
    assert cli_payload["count"] == service_payload["count"]


def test_report_option_builders_are_argparse_free(tmp_path: Path) -> None:
    config = {"duplicates": {"default_scope": "albums", "default_strategy": "strict"}}

    duplicates = build_duplicates_options(config, target=tmp_path, output_format="json")
    missing = build_missing_options(config, field="lyrics", field_option="mood", fields_csv="style, key", tracks=True)
    export = build_export_options(config, "artist:test", files=True, export_format="csv")

    assert duplicates.scope == "albums"
    assert duplicates.strategy == "strict"
    assert missing.fields == ["lyrics", "mood", "style", "key"]
    assert missing.scope == "tracks"
    assert export.scope == "files"
    assert export.export_format == "csv"


def test_report_renderer_preserves_machine_readable_output() -> None:
    result = WorkflowResult(Status.OK, [], details={"exit_code": 0, "output_text": '{"count": 0}'})

    code, output = render_report_result(result, title="Missing Metadata", scope="library", output_format="json")

    assert code == 0
    assert output == '{"count": 0}'


def test_structured_service_renderer_outputs_clean_json() -> None:
    result = WorkflowResult(Status.WARN, [StepResult(1, 1, "Read", Status.WARN, "ok")], command="audit", warnings=["check metadata"])

    code, output = render_structured_service_result(result)
    payload = json.loads(output)

    assert code == 0
    assert payload["status"] == "WARN"
    assert payload["steps"][0]["name"] == "Read"


def test_services_do_not_print_stdout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("noqlen_forge.services.core_service.cover_path", lambda *args, **kwargs: (0, "Cover ok"))

    run_cover_service(CoverOptions(path=tmp_path, config={}))

    assert capsys.readouterr().out == ""


def test_metadata_service_returns_structured_provider_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from noqlen_forge.metadata_providers import MetadataCandidate, ProviderAttempt, ProviderSelection

    candidate = MetadataCandidate(provider="discogs", source_id="release-1", confidence="high", score=91, genre="Rock", style="Progressive Metal")
    monkeypatch.setattr("noqlen_forge.services.metadata_service.read_tracks", lambda path: tracks(tags={}))
    monkeypatch.setattr("noqlen_forge.services.metadata_service.resolve_metadata_providers", lambda *args, **kwargs: ProviderSelection(["discogs"], [], {"discogs": "catalog"}))
    monkeypatch.setattr("noqlen_forge.services.metadata_service.fetch_metadata_with_providers", lambda *args, **kwargs: [ProviderAttempt("discogs", "OK", "matched", [candidate])])
    monkeypatch.setattr("noqlen_forge.services.metadata_service.apply_musicbrainz_writes", lambda plans, apply: [])

    result = run_metadata_service(MetadataOptions(tmp_path / "Album", config={}, providers=["discogs"]))

    assert result.status == Status.OK
    assert result.details["providers"][0]["candidates"][0]["source_id"] == "release-1"
    assert result.details["decisions"]
    assert "output_text" not in result.safe_details


def test_candidates_service_returns_structured_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Ranked:
        score = 97
        release = {"id": "rel-1", "title": "Album", "date": "2024", "country": "US"}
        reasons = ["artist match"]

    monkeypatch.setattr("noqlen_forge.services.metadata_service.read_tracks", lambda path: tracks())
    monkeypatch.setattr("noqlen_forge.services.metadata_service.search_releases", lambda items: [{"id": "rel-1"}])
    monkeypatch.setattr("noqlen_forge.services.metadata_service.hydrate_releases", lambda releases: releases)
    monkeypatch.setattr("noqlen_forge.services.metadata_service.rank_releases", lambda items, releases: [Ranked()])

    result = run_candidates_service(CandidatesOptions(tmp_path / "Album"))

    assert result.status == Status.OK
    assert result.details["candidates"][0]["release_id"] == "rel-1"


def test_apply_mbid_service_requires_explicit_medium_apply_confirmation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Ranked:
        score = 90
        release = {"id": "rel-1", "title": "Album"}
        reasons = ["artist match"]

    monkeypatch.setattr("noqlen_forge.services.metadata_service.read_tracks", lambda path: tracks(tags={}))
    monkeypatch.setattr("noqlen_forge.services.metadata_service.mb_album_ids", lambda items: set())
    monkeypatch.setattr("noqlen_forge.services.metadata_service.search_releases", lambda items: [{"id": "rel-1"}])
    monkeypatch.setattr("noqlen_forge.services.metadata_service.hydrate_releases", lambda releases: releases)
    monkeypatch.setattr("noqlen_forge.services.metadata_service.rank_releases", lambda items, releases: [Ranked()])
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("service called input")))

    result = run_apply_mbid_service(ApplyMBIDOptions(tmp_path / "Album", apply=True))

    assert result.status == Status.REVIEW
    assert result.summary["requires_confirmation"] is True


def test_apply_mbid_cli_keeps_confirmation_at_terminal_layer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    calls = {"confirmed": False}

    def fake_service(options: ApplyMBIDOptions) -> WorkflowResult:
        if not options.confirm_medium_confidence:
            return WorkflowResult(Status.REVIEW, [StepResult(1, 1, "Plan", Status.REVIEW)], command="apply-mbid", summary={"requires_confirmation": True}, details={"exit_code": 1, "output_text": "Selected score=90 release=rel-1 title=Album"})
        calls["confirmed"] = True
        return WorkflowResult(Status.APPLY, [StepResult(1, 1, "Plan", Status.APPLY)], command="apply-mbid", details={"exit_code": 0, "output_text": "APPLY: 1 files"})

    monkeypatch.setattr(cli, "run_apply_mbid_service", fake_service)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    assert cli.apply_mbid(tmp_path / "Album", release_id=None, apply=True) == 0
    assert calls["confirmed"] is True
    assert "APPLY: 1 files" in capsys.readouterr().out


def test_review_service_wraps_legacy_review_result(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    _seed_review(config, tmp_path / "song.flac")

    result = run_review_service(ReviewOptions(config, []))

    assert result.status == Status.REVIEW
    assert result.details["exit_code"] == 1
    assert "Pending decisions: 1" in result.details["output_text"]
