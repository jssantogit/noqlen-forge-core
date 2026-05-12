from pathlib import Path
from contextlib import redirect_stdout
import io
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_package_has_no_legacy_external_tool_runtime_paths() -> None:
    forbidden = ("beet", "beets", "onetagger", "onetagger-cli", "tuneup")
    allowed = {ROOT / "noqlen_forge" / "lab.py"}
    offenders: list[str] = []

    for path in sorted((ROOT / "noqlen_forge").glob("*.py")):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden:
            if term in text:
                offenders.append(f"{path.relative_to(ROOT)}: {term}")

    assert offenders == []


def test_removed_legacy_external_modules_stay_removed() -> None:
    removed = ("onetagger.py", "tuneup.py")

    assert [name for name in removed if (ROOT / "noqlen_forge" / name).exists()] == []


def test_public_help_and_default_config_omit_legacy_external_tools() -> None:
    from noqlen_forge.cli import build_parser
    from noqlen_forge.config import default_config, render_config

    forbidden = ("beets", "onetagger", "onetagger-cli", "tuneup", "--with-onetagger", "--with-legacy-tuneup", "--skip-onetagger", "--skip-legacy-tuneup")
    parser = build_parser()
    stdout = io.StringIO()
    for args in (["--help"], ["enrich", "--help"], ["metadata", "--help"], ["dev", "check", "--help"], ["lab", "--help"]):
        with redirect_stdout(stdout):
            try:
                parser.parse_args(list(args))
            except SystemExit:
                pass
    help_text = parser.format_help() + stdout.getvalue()
    config_text = render_config(default_config(), comments=True)
    example_text = (ROOT / "config.example.toml").read_text(encoding="utf-8")

    combined = f"{help_text}\n{config_text}\n{example_text}".lower()
    assert all(term not in combined for term in forbidden)


def test_runtime_dependencies_do_not_declare_removed_external_tools() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    forbidden_dependencies = ('"beets"', '"onetagger"', '"onetagger-cli"', '"tuneup"')

    assert all(term not in pyproject for term in forbidden_dependencies)


def test_public_cli_console_script_is_noqlen_forge_only() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts == {"noqlen-forge": "noqlen_forge.cli:main"}


def test_essentia_key_backend_stays_removed_from_public_runtime() -> None:
    from noqlen_forge.cli import build_parser
    from noqlen_forge.config import default_config, render_config

    runtime_forbidden = ("essentia.standard", "EssentiaKeyDetectionBackend")
    offenders: list[str] = []
    for path in sorted((ROOT / "noqlen_forge").glob("*.py")):
        if path.name == "lab.py":
            continue
        text = path.read_text(encoding="utf-8")
        for term in runtime_forbidden:
            if term in text:
                offenders.append(f"{path.relative_to(ROOT)}: {term}")

    parser = build_parser()
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        try:
            parser.parse_args(["analyze", "--help"])
        except SystemExit:
            pass
    public_text = "\n".join(
        [
            stdout.getvalue(),
            render_config(default_config(), comments=True),
            (ROOT / "config.example.toml").read_text(encoding="utf-8"),
        ]
    ).lower()

    assert offenders == []
    assert "essentia" not in public_text
    assert default_config()["audio"]["key_detection"]["backends"] == ["portable_basic"]
