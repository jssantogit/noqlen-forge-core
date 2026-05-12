from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from noqlen_forge import cli
from noqlen_forge.audit import AuditResult
from noqlen_forge.config import default_config
from noqlen_forge.db import apply_migrations, connect
from noqlen_forge.jobs import JobContext, JobOptions, JobStore, JobStatus, resume_job
from noqlen_forge.safety import SafetyError
from noqlen_forge.workflow import SafetyContext, Status, StepResult, WorkflowResult


pytestmark = [pytest.mark.contract, pytest.mark.service, pytest.mark.db]


def _config(path: Path) -> dict:
    config = default_config()
    config["database"]["path"] = str(path)
    return config


def test_job_store_creates_job_and_sanitizes_options(tmp_path: Path) -> None:
    store = JobStore(_config(tmp_path / "library.db"))

    job_id = store.create_job(JobOptions("lyrics", target="Album", options={"api_token": "secret", "lyrics": "full lyric text"}))
    job = store.get_job(job_id)

    assert job is not None
    assert job["status"] == JobStatus.PENDING.value
    assert job["options"]["api_token"] == "[redacted]"
    assert job["options"]["lyrics"] == "[redacted]"


def test_job_context_records_progress_steps_and_events(tmp_path: Path) -> None:
    store = JobStore(_config(tmp_path / "library.db"))
    job_id = store.create_job(JobOptions("audit", target="Album"))
    context = JobContext(store, job_id)

    context.update_progress(1, 2, "Read tags")
    context.start_step("Read tags")
    context.finish_step(StepResult(1, 2, "Read tags", Status.OK, "1 file"))
    context.add_event("custom", "message", {"fingerprint": "abc"})

    job = store.get_job(job_id)
    steps = store.get_steps(job_id)
    events = store.get_events(job_id)

    assert job["progress_current"] == 1
    assert steps[0]["name"] == "Read tags"
    assert any(event["event_type"] == "custom" for event in events)
    assert [event for event in events if event["event_type"] == "custom"][0]["data"]["fingerprint"] == "[redacted]"


def test_workflow_result_is_saved_as_job_result_json(tmp_path: Path) -> None:
    store = JobStore(_config(tmp_path / "library.db"))
    job_id = store.create_job(JobOptions("audit", target="Album"))
    result = WorkflowResult(Status.WARN, [StepResult(1, 1, "Read tags", Status.WARN, "missing optional")], command="audit", summary={"message": "1 warning"}, details={"lyrics": "full text"})

    store.mark_running(job_id)
    store.save_workflow_result(job_id, result)
    job = store.get_job(job_id)

    assert job["status"] == JobStatus.WARNING.value
    assert job["result"]["details"]["lyrics"] == "[redacted]"
    assert result.job["job_id"] == job_id


def test_jobs_cancel_marks_job_canceled(tmp_path: Path) -> None:
    store = JobStore(_config(tmp_path / "library.db"))
    job_id = store.create_job(JobOptions("audit"))

    assert store.cancel(job_id) is True

    assert store.get_job(job_id)["status"] == JobStatus.CANCELED.value
    assert any(event["event_type"] == "canceled" for event in store.get_events(job_id))


def test_jobs_resume_rejects_non_resumable(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    job_id = JobStore(config).create_job(JobOptions("audit", resumable=False))

    code, message = resume_job(config, job_id)

    assert code == 1
    assert "not resumable" in message


def test_jobs_resume_supported_fake_skips_completed_step(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    store = JobStore(config)
    job_id = store.create_job(JobOptions("job-test-resume", resumable=True))
    store.upsert_step(job_id, StepResult(1, 2, "Prepare", Status.OK, "done"))

    code, message = resume_job(config, job_id)

    assert code == 0
    assert "resumed" in message
    assert [step["name"] for step in store.get_steps(job_id)] == ["Prepare", "Resume"]
    assert store.get_job(job_id)["status"] == JobStatus.COMPLETED.value


def test_jobs_prune_dry_run_and_apply(tmp_path: Path) -> None:
    config = _config(tmp_path / "library.db")
    store = JobStore(config)
    job_id = store.create_job(JobOptions("audit"))
    store.save_workflow_result(job_id, WorkflowResult(Status.OK, [], command="audit", summary={"message": "done"}))
    old = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    with connect(config) as conn:
        apply_migrations(conn)
        conn.execute("UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?", (old, old, job_id))
        conn.commit()

    dry = store.prune(apply=False, days=30)
    assert dry["count"] == 1
    assert store.get_job(job_id) is not None

    applied = store.prune(apply=True, days=30)
    assert applied["count"] == 1
    assert store.get_job(job_id) is None


def test_jobs_cli_list_status_cancel_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path / "library.db")
    monkeypatch.setattr(cli, "load_cli_config", lambda: config)
    store = JobStore(config)
    job_id = store.create_job(JobOptions("audit", target="Album"))
    store.save_workflow_result(job_id, WorkflowResult(Status.OK, [StepResult(1, 1, "Read tags", Status.OK, "1 file")], command="audit", summary={"message": "1 file checked"}))

    assert cli.main(["jobs", "list"]) == 0
    output = capsys.readouterr().out
    assert job_id in output
    assert "Noqlen Forge jobs" in output
    assert "Status: OK" in output

    assert cli.main(["jobs", "status", job_id, "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["job"]["id"] == job_id
    assert payload["steps"][0]["name"] == "Read tags"

    assert cli.main(["jobs", "status", job_id]) == 0
    output = capsys.readouterr().out
    assert "Noqlen Forge job" in output
    assert "Job status:" in output
    assert "Status: COMPLETED" in output

    assert cli.main(["jobs", "cancel", job_id]) == 0
    assert "canceled" in capsys.readouterr().out


def test_audit_job_pilot_persists_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path / "library.db")
    monkeypatch.setattr(cli, "load_cli_config", lambda: config)
    monkeypatch.setattr("noqlen_forge.services.audit_service.audit_path", lambda path: AuditResult(tracks=[], bad_fields=[]))

    assert cli.main(["audit", str(tmp_path), "--job", "--format", "json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    job_id = payload["job"]["job_id"]

    stored = JobStore(config).get_result(job_id)
    assert stored is not None
    assert stored.job["kind"] == "audit"
    assert stored.steps[0]["name"] == "Read tags"


def test_job_context_safety_does_not_bypass_automated_apply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NOQLEN_FORGE_AUTOMATED_VALIDATION", "1")
    store = JobStore(_config(tmp_path / "library.db"))
    job_id = store.create_job(JobOptions("apply-test", target=str(tmp_path), mode="apply"))
    context = JobContext(store, job_id, safety_context=SafetyContext(target_path=tmp_path))

    with pytest.raises(SafetyError):
        context.safety_context.check_apply_allowed(True, context="job apply-test")
