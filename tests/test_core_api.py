import json
from pathlib import Path

import pytest

from noqlen_forge.api import ConfigError, NoqlenForgeCore
from noqlen_forge.importer import ImportResult
from noqlen_forge.services.types import workflow_result_to_dict, workflow_result_to_json
from noqlen_forge.workflow import Status, StepResult, WorkflowResult


def test_core_api_import_and_default_init() -> None:
    core = NoqlenForgeCore()

    assert core.capabilities()["name"] == "Noqlen Forge Core"
    assert core.capabilities()["workflows"]["audit"]["implemented"] is True


def test_core_api_init_with_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "library.db"
    config_path.write_text(f'[database]\npath = "{db_path}"\n', encoding="utf-8")

    core = NoqlenForgeCore(config_path=config_path)

    assert core.config["database"]["path"] == str(db_path)


def test_core_api_rejects_config_and_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        NoqlenForgeCore(config={}, config_path=tmp_path / "config.toml")


def test_core_api_capabilities_manifest() -> None:
    manifest = NoqlenForgeCore(config={}).capabilities()

    assert manifest["schema"] == "core-api/v1"
    assert manifest["supports_json"] is True
    assert "import_music" in manifest["dangerous_operations"]
    assert manifest["workflows"]["lyrics"]["apply"] is True


def test_core_api_audit_returns_workflow_result(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = WorkflowResult(Status.OK, [StepResult(1, 1, "Read tags", Status.OK)], command="audit")
    monkeypatch.setattr("noqlen_forge.api.run_audit_service", lambda options: expected)

    result = NoqlenForgeCore(config={}).audit("Album")

    assert result is expected
    assert isinstance(result, WorkflowResult)


def test_core_api_metadata_uses_service_without_printing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    expected = WorkflowResult(Status.OK, [StepResult(1, 1, "Metadata", Status.OK)], command="metadata", details={"providers": []})
    called = {"path": None}

    def fake_service(options):
        print("metadata noise")
        called["path"] = options.path
        return expected

    monkeypatch.setattr("noqlen_forge.api.run_metadata_service", fake_service)

    result = NoqlenForgeCore(config={}).metadata("Album")

    assert result is expected
    assert called["path"] == Path("Album")
    assert capsys.readouterr().out == ""


def test_core_api_review_is_structured_service(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = WorkflowResult(Status.OK, [StepResult(1, 1, "Review", Status.OK)], command="review")

    monkeypatch.setattr("noqlen_forge.api.run_review_service", lambda options: expected)

    assert NoqlenForgeCore(config={}).review(review_args=["list"]) is expected


def test_core_api_enrich_uses_service_without_printing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    expected = WorkflowResult(Status.OK, [StepResult(1, 1, "Cleanup", Status.OK)], command="enrich", details={"targets": []})
    called = {"path": None}

    def fake_service(options):
        print("enrich noise")
        called["path"] = options.path
        return expected

    monkeypatch.setattr("noqlen_forge.api.run_enrich_service", fake_service)

    result = NoqlenForgeCore(config={}).enrich("Album", full=True)

    assert result is expected
    assert called["path"] == Path("Album")
    assert capsys.readouterr().out == ""


def test_core_api_does_not_print_stdout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def noisy_service(options):
        print("should not leak")
        return WorkflowResult(Status.OK, [StepResult(1, 1, "Read tags", Status.OK)], command="audit")

    monkeypatch.setattr("noqlen_forge.api.run_audit_service", noisy_service)

    result = NoqlenForgeCore(config={}).audit("Album")

    captured = capsys.readouterr()

    assert result.status == Status.OK
    assert captured.out == ""


def test_core_api_dry_run_does_not_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    marker = tmp_path / "written"

    def fake_import(*args, **kwargs):
        if kwargs.get("apply"):
            marker.write_text("write", encoding="utf-8")
        return ImportResult(0, "Import\nStatus: OK", status="OK")

    monkeypatch.setattr("noqlen_forge.services.library_service.import_path", fake_import)

    result = NoqlenForgeCore(config={}).import_music(tmp_path / "Incoming", library=tmp_path / "Library", apply=False)

    assert result.status == Status.OK
    assert not marker.exists()


def test_core_api_cleanup_returns_structured_result_without_printing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    track = __import__("noqlen_forge.audio", fromlist=["Track"]).Track(path=tmp_path / "song.mp3", format="mp3", tags={"bpm": ["300"]})
    monkeypatch.setattr("noqlen_forge.services.library_maintenance_service.read_tracks", lambda path: [track])
    monkeypatch.setattr("noqlen_forge.services.library_maintenance_service.apply_cleanup", lambda plans, apply: None)

    result = NoqlenForgeCore(config={}).cleanup(tmp_path / "Album")

    assert result.status == Status.DRY
    assert result.planned_changes
    assert capsys.readouterr().out == ""


def test_core_api_apply_respects_automated_safety(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("noqlen_forge.services.lyrics_service.lyrics_path", lambda *args, **kwargs: (0, "Lyrics\nStatus: OK"))

    result = NoqlenForgeCore(config={}, automated_validation=True).lyrics(tmp_path / "Album", apply=True)

    assert result.status == Status.FAIL
    assert "outside MusicLab" in result.errors[0]


def test_core_api_apply_allowed_inside_musiclab(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".noqlen-forge-lab").write_text("lab", encoding="utf-8")
    monkeypatch.setattr("noqlen_forge.services.lyrics_service.lyrics_path", lambda *args, **kwargs: (0, "Lyrics\nStatus: OK"))

    result = NoqlenForgeCore(config={}, automated_validation=True).lyrics(tmp_path / "Album", apply=True)

    assert result.status == Status.APPLY


def test_core_api_workflow_result_serializes_and_sanitizes(monkeypatch: pytest.MonkeyPatch) -> None:
    unsafe = WorkflowResult(
        Status.OK,
        [StepResult(1, 1, "Read tags", Status.OK, details=["token=secret-value"])],
        command="audit",
        details={"lyrics": "full lyrics should not appear", "fingerprint": "abc123", "safe": "ok"},
    )
    monkeypatch.setattr("noqlen_forge.api.run_audit_service", lambda options: unsafe)

    payload = workflow_result_to_dict(NoqlenForgeCore(config={}).audit("Album"))
    rendered = json.dumps(payload)

    assert payload["details"]["lyrics"] == "[redacted]"
    assert payload["details"]["fingerprint"] == "[redacted]"
    assert "secret-value" not in rendered
    assert "full lyrics should not appear" not in rendered


def test_core_api_json_serialization() -> None:
    result = WorkflowResult(Status.OK, [StepResult(1, 1, "Cleanup", Status.OK)], command="enrich")
    payload = json.loads(workflow_result_to_json(result))

    assert payload["status"] == "OK"
    assert payload["workflow"] == "enrich"


def test_core_api_jobs_create_status_cancel(tmp_path: Path) -> None:
    core = NoqlenForgeCore(config={"database": {"path": str(tmp_path / "jobs.db")}})

    created = core.create_job("audit", tmp_path / "Album", {"verbose": True})
    job_id = created.summary["job_id"]
    status = core.jobs_status(job_id)
    listed = core.jobs_list()
    canceled = core.cancel_job(job_id)

    assert created.status == Status.OK
    assert status.summary["status"] == "pending"
    assert listed.counts["jobs"] == 1
    assert canceled.summary["canceled"] is True


def test_core_api_config_and_db_methods_return_structured_without_printing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = {"database": {"path": str(tmp_path / "library.db")}, "apis": {"lastfm_api_key": "abcdefghijkl1234"}}
    core = NoqlenForgeCore(config=config)

    config_result = core.config_show()
    db_path = core.db_path()
    db_init = core.db_init()
    db_status = core.db_status()

    assert config_result.safe_details["config"]["apis"]["lastfm_api_key"] == "abcd...1234"
    assert db_path.summary["path"] == tmp_path / "library.db"
    assert db_init.summary["initialized"] is True
    assert db_status.summary["schema_version"] > 0
    assert capsys.readouterr().out == ""
