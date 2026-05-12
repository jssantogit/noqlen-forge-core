import pytest

from noqlen_forge import cli
from noqlen_forge.workflow import Status, WorkflowResult


def _service_output(text: str, code: int = 0) -> WorkflowResult:
    return WorkflowResult(Status.FAIL if code else Status.OK, [], details={"exit_code": code, "output_text": text})


def test_top_level_help_includes_cli_groups() -> None:
    help_text = cli.build_parser().format_help()

    assert "Getting started:" in help_text
    assert "Core workflows:" in help_text
    assert "Reports:" in help_text
    assert "Playlists and ratings:" in help_text
    assert "Maintenance and review:" in help_text
    assert "Contributor tools:" in help_text
    assert "Focused tools:" in help_text
    positional_help = help_text.split("positional arguments:", 1)[1].split("options:", 1)[0]
    assert "    lab" not in positional_help


def test_dev_help_exposes_musiclab_namespace(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["dev", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "Maintainer and contributor tools" in output
    assert "lab" in output
    assert "noqlen-forge dev lab run --quick" in output


def test_dev_lab_and_hidden_lab_alias_are_callable() -> None:
    dev_lab_args = cli.build_parser().parse_args(["dev", "lab", "list"])
    lab_alias_args = cli.build_parser().parse_args(["lab", "list"])

    assert dev_lab_args.command == "dev"
    assert dev_lab_args.dev_command == "lab"
    assert dev_lab_args.lab_command == "list"
    assert lab_alias_args.command == "lab"
    assert lab_alias_args.lab_command == "list"


def test_internal_debug_flags_are_hidden_but_callable(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["enrich", "--help"])

    assert exc.value.code == 0
    assert "--debug" not in capsys.readouterr().out

    args = cli.build_parser().parse_args(["enrich", ".", "--debug"])
    assert args.debug is True


def test_report_help_works_and_mentions_read_only(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["report", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "Reports are read-only" in output
    assert "noqlen-forge report missing lyrics" in output


def test_maintain_help_works_and_mentions_dry_run_apply_and_musiclab(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["maintain", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "dry-run" in output
    assert "--apply" in output
    assert "MusicLab" in output


def test_report_missing_calls_missing_logic(monkeypatch, capsys) -> None:
    called = {}
    monkeypatch.setattr(cli, "load_config", lambda: {"reports": {}, "duplicates": {}})
    monkeypatch.setattr(cli, "run_missing_service", lambda options: called.update(options=options) or _service_output("Missing Lyrics: 1 tracks"))

    code = cli.main(["report", "missing", "lyrics"])

    assert code == 0
    assert called["options"].fields == ["lyrics"]
    output = capsys.readouterr().out
    assert "Report: Missing Lyrics" in output
    assert "Missing Lyrics: 1 tracks" in output


def test_report_duplicates_calls_duplicates_logic(monkeypatch, capsys) -> None:
    called = {}
    monkeypatch.setattr(cli, "load_config", lambda: {"duplicates": {"default_scope": "tracks", "default_strategy": "safe"}})
    monkeypatch.setattr(cli, "run_duplicates_service", lambda options: called.update(options=options) or _service_output("Duplicate tracks: none"))

    code = cli.main(["report", "duplicates"])

    assert code == 0
    assert called["options"].scope == "tracks"
    output = capsys.readouterr().out
    assert "Report: Duplicate Tracks/Albums" in output
    assert "Duplicate tracks: none" in output


def test_report_untracked_calls_untracked_logic(monkeypatch, capsys, tmp_path) -> None:
    called = {}
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "run_untracked_service", lambda options: called.update(options=options) or _service_output("Untracked files: none"))

    code = cli.main(["report", "untracked", str(tmp_path)])


    assert code == 0
    assert called["options"].path == tmp_path
    assert "Report: Untracked Files" in capsys.readouterr().out


def test_report_missing_files_calls_missing_files_logic(monkeypatch, capsys) -> None:
    called = {}
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "run_missing_files_service", lambda options: called.update(options=options) or _service_output("Missing files in database: none"))

    code = cli.main(["report", "missing-files"])

    assert code == 0
    assert called["options"].output_format == "text"
    assert "Report: Missing Files" in capsys.readouterr().out


def test_legacy_report_aliases_still_work_without_output_spam(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: {"duplicates": {"default_scope": "tracks", "default_strategy": "safe"}})
    monkeypatch.setattr(cli, "run_missing_service", lambda options: _service_output("missing output"))
    monkeypatch.setattr(cli, "run_duplicates_service", lambda options: _service_output("duplicates output"))
    monkeypatch.setattr(cli, "run_untracked_service", lambda options: _service_output("untracked output"))
    monkeypatch.setattr(cli, "run_missing_files_service", lambda options: _service_output("missing files output"))

    assert cli.main(["missing", "lyrics"]) == 0
    assert cli.main(["duplicates"]) == 0
    assert cli.main(["untracked", "."]) == 0
    assert cli.main(["missing-files"]) == 0

    output = capsys.readouterr().out
    assert "alias for" not in output.casefold()
    assert "missing output" in output
    assert "duplicates output" in output
    assert "untracked output" in output
    assert "missing files output" in output


def test_maintain_sync_calls_sync_logic(monkeypatch, capsys, tmp_path) -> None:
    called = {}
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "run_sync_service", lambda options: called.update(options=options) or _service_output("Mode: DRY-RUN"))

    code = cli.main(["maintain", "sync", str(tmp_path), "--tags-to-db"])

    assert code == 0
    assert called["options"].direction == "tags-to-db"
    output = capsys.readouterr().out
    assert "Maintenance: Sync tags to database" in output
    assert "Mode: DRY-RUN" in output


def test_legacy_sync_alias_still_works(monkeypatch, capsys, tmp_path) -> None:
    called = {}
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "run_sync_service", lambda options: called.update(options=options) or _service_output("sync output"))

    code = cli.main(["sync", str(tmp_path), "--db-to-tags"])

    assert code == 0
    assert called["options"].direction == "db-to-tags"
    assert "Maintenance:" not in capsys.readouterr().out


def test_fields_command_lists_registry(capsys) -> None:
    assert cli.main(["fields", "--category", "audio"]) == 0

    output = capsys.readouterr().out
    assert "Supported fields:" in output
    assert "Audio:" in output
    assert "- replaygain" in output


def test_report_duplicates_has_no_apply_flag() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["report", "duplicates", "--apply"])

    assert exc.value.code != 0


def test_maintain_repair_is_available(capsys) -> None:
    assert cli.main(["maintain", "repair"]) in {0, 1}
    output = capsys.readouterr().out
    assert "Repair:" in output or "Database not initialized" in output
