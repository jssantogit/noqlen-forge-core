from pathlib import Path
from contextlib import redirect_stdout
import io
import tomllib

from noqlen_forge import cli
from noqlen_forge.config import config_path, default_config, load_config, merge_config, save_default_config
from noqlen_forge.lastfm import get_lastfm_api_key


ROOT = Path(__file__).resolve().parents[1]


def _flatten_config_keys(values: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in values.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            keys.update(_flatten_config_keys(value, name))
        else:
            keys.add(name)
    return keys


def _help_text(args: list[str]) -> str:
    parser = cli.build_parser()
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        try:
            parser.parse_args(args)
        except SystemExit:
            pass
    return stdout.getvalue()


def test_config_path_uses_xdg_config_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert config_path() == tmp_path / "noqlen-forge" / "config.toml"


def test_config_path_uses_home_config_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert config_path() == tmp_path / ".config" / "noqlen-forge" / "config.toml"


def test_default_config_returns_expected_sections() -> None:
    config = default_config()

    for section in ("library", "database", "enrich", "metadata", "audio", "cover", "lyrics", "output", "apis"):
        assert section in config
    assert config["audio"]["key_detection"]["enabled"] is False
    assert config["audio"]["key_detection"]["backend"] == "auto"
    assert config["audio"]["key_detection"]["backends"] == ["portable_basic"]
    assert config["lyrics"]["online"]["user_agent"] == "noqlen-forge"
    assert config["lyrics"]["provider_settings"]["custom_http"]["api_key_env"] == "NOQLEN_FORGE_LYRICS_API_KEY"
    assert config["navidrome"]["client_name"] == "noqlen-forge"


def test_example_config_parses_and_matches_default_keys() -> None:
    example = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))

    assert _flatten_config_keys(example) == _flatten_config_keys(default_config())


def test_example_config_keeps_native_safe_defaults() -> None:
    example = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))

    assert example["library"]["root"] == ""
    assert example["library"]["incoming"] == ""
    assert example["database"]["path"] == ""
    assert example["database"]["auto_scan"] is False
    assert example["audio"]["key_detection"]["enabled"] is False
    assert example["audio"]["key_detection"]["backend"] == "auto"
    assert example["audio"]["key_detection"]["backends"] == ["portable_basic"]
    assert example["audio"]["key_detection"]["write_low_confidence"] is False
    assert example["lyrics"]["overwrite_existing"] is False
    assert example["cover"]["save_folder_cover"] is False
    assert example["navidrome"]["enabled"] is False
    assert example["navidrome"]["password"] == ""
    assert example["navidrome"]["token"] == ""
    assert example["navidrome"]["salt"] == ""
    assert example["navidrome"]["client_name"] == "noqlen-forge"
    assert example["lyrics"]["online"]["user_agent"] == "noqlen-forge"
    assert example["lyrics"]["provider_settings"]["custom_http"]["api_key_env"] == "NOQLEN_FORGE_LYRICS_API_KEY"
    assert example["import"]["mode"] == "copy"
    assert example["organize"]["mode"] == "copy"
    assert example["repair"]["allow_delete_records"] is False


def test_example_config_does_not_enable_removed_tools() -> None:
    example = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
    keys = _flatten_config_keys(example)

    for removed in ("beets", "onetagger", "tuneup", "essentia"):
        assert not any(removed in key.lower() for key in keys)
    assert "portable_basic" in example["audio"]["key_detection"]["backends"]
    assert set(example["metadata_providers"]["sources"]) == {"musicbrainz", "discogs"}


def test_key_detection_docs_and_example_config_stay_optional() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    example = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    dev_docs = (ROOT / "docs" / "development" / "audio-key-detection.md").read_text(encoding="utf-8")

    assert "portable_basic" in example
    assert 'backends = ["portable_basic"]' in example
    assert 'backend = "auto"' in example
    assert 'write_low_confidence = false' in example
    assert "Essentia is not a dependency or supported backend" in readme
    assert "Low-confidence key estimates are not written automatically" in readme
    assert "Key detection is optional" in (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    assert "should be treated as an estimate" in dev_docs
    forbidden_install = "must install " + "essentia"
    forbidden_mandatory = "essentia is " + "re" + "quired"
    assert forbidden_install not in readme.lower()
    assert forbidden_mandatory not in readme.lower()


def test_analyze_help_presents_key_backends_as_optional() -> None:
    output = _help_text(["analyze", "--help"])

    assert "Optional key detection backend" in output
    assert "auto" in output
    assert "portable_basic" in output
    assert "disabled" in output
    assert "required" not in output.lower()
    assert "essentia" not in output.lower()


def test_audit_and_missing_help_present_key_as_optional() -> None:
    for command in (["audit", "--help"], ["missing", "--help"]):
        output = _help_text(command)

        assert "Missing Key is WARN-level optional metadata" in output
        assert "auto, portable_basic, or disabled" in output
        assert "critical failure" in output
        assert "essentia" not in output.lower()


def test_load_config_without_file_returns_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert load_config() == default_config()


def test_load_config_merges_user_values_with_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "noqlen-forge" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[library]\nroot = "/music"\n\n[enrich]\nfull_includes_lastfm = false\n', encoding="utf-8")

    config = load_config()

    assert config["library"]["root"] == "/music"
    assert config["library"]["template"] == default_config()["library"]["template"]
    assert config["enrich"]["full_includes_lastfm"] is False
    assert config["enrich"]["full_includes_bpm"] is True


def test_merge_config_keeps_defaults_for_missing_keys() -> None:
    merged = merge_config({"a": {"b": 1, "c": 2}}, {"a": {"b": 3}})

    assert merged == {"a": {"b": 3, "c": 2}}


def test_config_init_creates_file(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    code = cli.config_command("init")

    assert code == 0
    assert (tmp_path / "noqlen-forge" / "config.toml").exists()
    assert "Created config:" in capsys.readouterr().out


def test_config_init_does_not_overwrite_without_force(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = save_default_config()
    path.write_text("[library]\nroot = \"keep\"\n", encoding="utf-8")

    code = cli.config_command("init", force=False)

    assert code == 1
    assert path.read_text(encoding="utf-8") == "[library]\nroot = \"keep\"\n"


def test_config_show_masks_keys(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "noqlen-forge" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[apis]\nlastfm_api_key = "abcdefghijkl1234"\n', encoding="utf-8")

    code = cli.config_command("show", config=load_config())
    output = capsys.readouterr().out

    assert code == 0
    assert "abcd...1234" in output
    assert "abcdefghijkl1234" not in output


def test_lastfm_api_key_uses_env_before_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "noqlen-forge" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[apis]\nlastfm_api_key = "from-config"\n', encoding="utf-8")
    monkeypatch.setenv("LASTFM_API_KEY", "from-env")

    assert get_lastfm_api_key() == "from-env"


def test_lastfm_api_key_falls_back_to_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    path = tmp_path / "noqlen-forge" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[apis]\nlastfm_api_key = "from-config"\n', encoding="utf-8")

    assert get_lastfm_api_key() == "from-config"


def test_cli_config_precedence_for_enrich_full() -> None:
    config = default_config()
    config["enrich"]["full_includes_lastfm"] = False
    config["enrich"]["full_includes_mood"] = False
    config["enrich"]["full_includes_bpm"] = False

    resolved = cli.resolve_enrich_options(
        config,
        full=True,
        analyze_bpm=True,
        analyze_key=False,
        analyze_features=False,
        with_lastfm=True,
        with_mood=False,
        skip_bpm=False,
        skip_key=False,
        skip_features=False,
        skip_lastfm=False,
        skip_mood=False,
        explicit_flags={"--analyze-bpm", "--with-lastfm"},
    )

    assert resolved["run_bpm"] is True
    assert resolved["run_lastfm"] is True
    assert resolved["run_mood"] is False


def test_enrich_full_does_not_include_replaygain_by_default() -> None:
    resolved = cli.resolve_enrich_options(
        default_config(),
        full=True,
        analyze_bpm=False,
        analyze_key=False,
        analyze_features=False,
        with_lastfm=False,
        with_mood=False,
    )

    assert resolved["run_replaygain"] is False


def test_enrich_full_ignores_legacy_external_config_keys() -> None:
    config = default_config()
    config["enrich"].update(
        {
            "include_onetagger": True,
            "include_legacy_tuneup": True,
            "full_includes_onetagger": True,
            "full_includes_legacy_tuneup": True,
        }
    )

    resolved = cli.resolve_enrich_options(
        config,
        full=True,
        analyze_bpm=False,
        analyze_key=False,
        analyze_features=False,
        with_lastfm=False,
        with_mood=False,
    )

    assert "run_onetagger" not in resolved
    assert "run_legacy_tuneup" not in resolved


def test_enrich_full_replaygain_config_and_cli_precedence() -> None:
    config = default_config()
    config["enrich"]["full_includes_replaygain"] = True

    enabled = cli.resolve_enrich_options(
        config,
        full=True,
        analyze_bpm=False,
        analyze_key=False,
        analyze_features=False,
        with_lastfm=False,
        with_mood=False,
    )
    skipped = cli.resolve_enrich_options(
        config,
        full=True,
        analyze_bpm=False,
        analyze_key=False,
        analyze_features=False,
        with_lastfm=False,
        with_mood=False,
        skip_replaygain=True,
        explicit_flags={"--skip-replaygain"},
    )
    forced = cli.resolve_enrich_options(
        default_config(),
        full=True,
        analyze_bpm=False,
        analyze_key=False,
        analyze_features=False,
        with_lastfm=False,
        with_mood=False,
        replaygain=True,
        explicit_flags={"--replaygain"},
    )

    assert enabled["run_replaygain"] is True
    assert skipped["run_replaygain"] is False
    assert forced["run_replaygain"] is True


def test_parser_accepts_config_commands() -> None:
    assert cli.build_parser().parse_args(["config", "path"]).config_command == "path"
    assert cli.build_parser().parse_args(["config", "init", "--force"]).force is True
    assert cli.build_parser().parse_args(["analyze", "album", "--key", "--backend", "portable_basic"]).backend == "portable_basic"
    assert cli.build_parser().parse_args(["analyze", "album", "--key", "--backend", "auto"]).backend == "auto"


def test_enrich_help_shows_native_pipeline_only(capsys) -> None:
    parser = cli.build_parser()
    try:
        parser.parse_args(["enrich", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out

    assert "safe native enrichment pipeline" in output
    assert "--with-onetagger" not in output
    assert "--with-legacy-tuneup" not in output
    assert "onetagger" not in output.lower()
    assert "tuneup" not in output.lower()
    assert "beets" not in output.lower()
    assert cli.build_parser().parse_args(["config", "show"]).config_command == "show"


def test_parser_accepts_enrich_replaygain_flags() -> None:
    args = cli.build_parser().parse_args(["enrich", "Album", "--full", "--replaygain", "--skip-replaygain"])

    assert args.replaygain is True
    assert args.skip_replaygain is True


def test_parser_accepts_db_commands() -> None:
    assert cli.build_parser().parse_args(["db", "path"]).db_command == "path"
    assert cli.build_parser().parse_args(["db", "init"]).db_command == "init"
    args = cli.build_parser().parse_args(["db", "scan", "Music", "--apply", "--verbose"])
    assert args.db_command == "scan"
    assert args.apply is True
    assert args.verbose is True
