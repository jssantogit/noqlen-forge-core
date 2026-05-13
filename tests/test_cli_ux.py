from pathlib import Path

import pytest

from noqlen_forge import cli
from noqlen_forge.workflow import Status, WorkflowResult


def _service_output(text: str, code: int = 0) -> WorkflowResult:
    return WorkflowResult(Status.FAIL if code else Status.OK, [], details={"exit_code": code, "output_text": text})


def test_top_level_help_includes_cli_groups() -> None:
    help_text = cli.build_parser().format_help()

    assert "Common workflow:" in help_text
    assert "Reports and inspection:" in help_text
    assert "Focused tools:" in help_text
    assert "Integrations:" in help_text
    assert "Contributor tools:" in help_text
    assert "Compatibility aliases such as sync, missing, duplicates, untracked and missing-files remain available." in help_text
    assert "Run `noqlen-forge COMMAND --help` for exact flags and safety notes." in help_text
    for command in ("config", "db", "import", "audit", "metadata", "enrich", "organize", "report"):
        assert command in help_text
    positional_help = help_text.split("positional arguments:", 1)[1].split("options:", 1)[0]
    assert "COMMAND     Command to run." in positional_help
    assert "\n    " not in positional_help


def test_top_level_help_exits_successfully_and_shows_primary_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "usage: noqlen-forge [-h] COMMAND ..." in output
    for command in ("config", "db", "audit", "report", "navidrome", "dev"):
        assert command in output
    assert "Soni" + "vra" not in output
    assert "Music" + "Meta" not in output


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


def _command_help(command: str, capsys) -> str:
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args([command, "--help"])

    assert exc.value.code == 0
    return capsys.readouterr().out


def _advanced_command_help(command: str, capsys, *, advanced_first: bool = True) -> str:
    argv = [command, "--advanced", "--help"] if advanced_first else [command, "--help", "--advanced"]
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(argv)

    assert exc.value.code == 0
    return capsys.readouterr().out


@pytest.mark.parametrize(
    ("command", "hidden_flag", "section"),
    [
        ("enrich", "--provider", "Provider options:"),
        ("cover", "--cover-source", "Cover options:"),
        ("lyrics", "--providers", "Lyrics options:"),
        ("metadata", "--allow-more-providers", "Metadata matching options:"),
        ("organize", "--template", "Maintenance options:"),
        ("import", "--skip-enrich", "Stage selection:"),
        ("analyze", "--bpm-range", "Audio analysis options:"),
    ],
)
def test_advanced_help_hides_technical_flags_in_normal_help(command: str, hidden_flag: str, section: str, capsys) -> None:
    normal = _command_help(command, capsys)

    assert hidden_flag not in normal
    assert section not in normal
    normalized = " ".join(normal.split())
    assert f"Run `noqlen-forge {command} --advanced --help` for provider, backend and tuning options." in normalized


@pytest.mark.parametrize(
    ("command", "usage"),
    [
        ("enrich", "usage: noqlen-forge enrich [OPTIONS] path"),
        ("metadata", "usage: noqlen-forge metadata [OPTIONS] path"),
        ("cover", "usage: noqlen-forge cover [OPTIONS] path"),
        ("lyrics", "usage: noqlen-forge lyrics [OPTIONS] path"),
        ("analyze", "usage: noqlen-forge analyze [OPTIONS] path"),
        ("jobs", "usage: noqlen-forge jobs [OPTIONS] COMMAND ..."),
    ],
)
def test_help_uses_concise_usage_lines(command: str, usage: str, capsys) -> None:
    normal = _command_help(command, capsys)

    assert usage in normal
    assert "[--provider" not in normal.splitlines()[0]
    assert "[--bpm-range" not in normal.splitlines()[0]


@pytest.mark.parametrize(
    ("command", "shown_flag", "section"),
    [
        ("enrich", "--provider", "Provider options:"),
        ("cover", "--cover-source", "Provider options:"),
        ("lyrics", "--providers", "Provider options:"),
        ("metadata", "--allow-more-providers", "Provider options:"),
        ("organize", "--template", "Maintenance options:"),
        ("import", "--skip-enrich", "Stage selection:"),
        ("analyze", "--bpm-range", "Audio analysis options:"),
    ],
)
def test_advanced_help_shows_technical_flags_in_sections(command: str, shown_flag: str, section: str, capsys) -> None:
    advanced = _advanced_command_help(command, capsys)

    assert shown_flag in advanced
    assert section in advanced
    assert "Output/debug options:" in advanced


def test_advanced_help_includes_useful_flag_descriptions(capsys) -> None:
    advanced = _advanced_command_help("enrich", capsys)
    normalized = " ".join(advanced.split())

    assert "Restrict metadata lookup to a provider" in normalized
    assert "Minimum provider confidence accepted for metadata decisions" in normalized
    assert "Prefer a specific cover-art source" in normalized
    assert "Show raw Last.fm provider output for debugging" in normalized


def test_advanced_help_accepts_help_before_advanced(capsys) -> None:
    advanced = _advanced_command_help("enrich", capsys, advanced_first=False)

    assert "--provider" in advanced
    assert "Provider options:" in advanced


def test_safety_and_common_flags_remain_visible_in_normal_help(capsys) -> None:
    normal = _command_help("enrich", capsys)

    for flag in ("--apply", "--dry-run", "--full", "--plain", "--verbose", "--no-progress"):
        assert flag in normal


def test_normal_help_examples_do_not_start_with_apply(capsys) -> None:
    for command in ("enrich", "cover", "lyrics", "metadata", "organize", "import", "analyze"):
        normal = _command_help(command, capsys)
        examples = normal.split("Examples:", 1)[1] if "Examples:" in normal else ""

        assert "--apply" not in examples


def test_advanced_flags_remain_parseable_without_advanced_help() -> None:
    parser = cli.build_parser()

    enrich = parser.parse_args(["enrich", ".", "--provider", "musicbrainz", "--bpm-range", "80", "160"])
    metadata = parser.parse_args(["metadata", ".", "--allow-more-providers"])
    cover = parser.parse_args(["cover", ".", "--cover-source", "itunes"])
    lyrics = parser.parse_args(["lyrics", ".", "--providers", "local", "--save-txt"])

    assert enrich.provider == ["musicbrainz"]
    assert enrich.bpm_range == [80.0, 160.0]
    assert metadata.allow_more_providers is True
    assert cover.cover_source == ["itunes"]
    assert lyrics.providers == "local"
    assert lyrics.save_txt is True


def test_commands_without_advanced_sections_do_not_gain_advanced_hint(capsys) -> None:
    for command in ("replaygain", "batch", "cleanup", "set-style", "candidates", "apply-mbid", "fields", "jobs"):
        output = _advanced_command_help(command, capsys)

        assert "Output/debug options:" not in output
        assert "--advanced" not in output


def test_sparse_command_help_describes_safety_and_scope(capsys) -> None:
    expected = {
        "metadata": ["Fetch metadata from configured providers", "Dry-run is the default", "external metadata services"],
        "batch": ["Run enrichment over child album/single targets", "advanced convenience command", "may write tags"],
        "cleanup": ["Plan cleanup of empty or malformed metadata", "writes tag changes", "does not move, copy or delete"],
        "analyze": ["Analyze optional local audio features", "may write supported tags", "Last.fm"],
        "set-style": ["Plan a manual STYLE tag value", "writes the STYLE tag", "--force"],
        "candidates": ["Read-only", "calls MusicBrainz", "does not write tags"],
        "apply-mbid": ["Plan MusicBrainz ID tag updates", "Dry-run is the default", "writes MusicBrainz identifier tags"],
        "fields": ["Read-only", "reference command", "does not scan files"],
    }

    for command, snippets in expected.items():
        output = _command_help(command, capsys)
        for snippet in snippets:
            assert snippet in output


def test_top_level_help_keeps_compatibility_aliases_visible() -> None:
    help_text = cli.build_parser().format_help()

    assert "Compatibility aliases such as sync, missing, duplicates, untracked and missing-files remain available." in help_text
    assert "sync -> maintain sync" not in help_text
    assert "duplicates -> report duplicates" not in help_text


def test_compatibility_aliases_remain_callable() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["sync", ".", "--tags-to-db"]).command == "sync"
    assert parser.parse_args(["duplicates"]).command == "duplicates"
    assert parser.parse_args(["missing", "lyrics"]).command == "missing"
    assert parser.parse_args(["untracked", "."]).command == "untracked"
    assert parser.parse_args(["missing-files"]).command == "missing-files"


def test_compatibility_alias_help_remains_callable(capsys) -> None:
    for alias in ("sync", "duplicates", "missing", "untracked", "missing-files"):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args([alias, "--help"])

        assert exc.value.code == 0
        assert f"usage: noqlen-forge {alias}" in capsys.readouterr().out


def test_musicbrainz_verbose_fallback_message_is_english(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "search_releases", lambda tracks: [])
    monkeypatch.setattr(cli, "hydrate_releases", lambda releases: releases)
    monkeypatch.setattr(cli, "rank_releases", lambda tracks, releases: [])

    assert cli._apply_best_musicbrainz(Path("album"), [], apply=False, force=False, verbose=True) == []

    output = capsys.readouterr().out
    assert "No matching release candidates were found." in output


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
