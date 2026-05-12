# Testing and MusicLab

This page collects developer validation guidance. User-facing MusicLab and real-library safety guidance lives in [Manual real-library dry-run checklist](../usage/manual-real-library-checklist.md) and [Native flow overview](../usage/native-flow.md).

## Development Checks

```bash
noqlen-forge dev check --smoke
noqlen-forge dev check --quick
noqlen-forge dev check --unit
noqlen-forge dev check --contract
noqlen-forge dev check --integration
noqlen-forge dev check --changed
noqlen-forge dev check --lab-quick
noqlen-forge dev check --lab
noqlen-forge dev check --full
```

Use focused checks during implementation. Use `--smoke` for docs/dev-only changes and micro-adjustments, `--quick` for fast functional feedback, and `--full` before functional commits. Quick check never replaces full check before automatic commits.

## Docs-Only Validation

Docs/dev-only changes should not run the full suite by default. Use lightweight validation:

```bash
git diff --check
noqlen-forge dev check --smoke
```

Docs/dev-only paths include `README.md`, `docs/**`, and Markdown-only changes that do not alter runtime behavior.

## Functional Validation

Functional changes require full validation before commit:

```bash
noqlen-forge dev check --full
```

Equivalent explicit validation is:

```bash
python -m py_compile noqlen_forge/*.py
pytest -q -m "not lab"
noqlen-forge dev lab reset
noqlen-forge dev lab run --full
noqlen-forge dev lab run --full --timing
```

## Validation Pyramid

- Smoke: compile checks and representative help commands.
- Unit: pure functions, field registry, parsers, scoring, and query logic.
- Contract: services, providers, DB helper contracts, structured output, and fake API interfaces.
- Integration: temporary SQLite/filesystem, fake providers, fake Navidrome, and composed workflows.
- Lab quick: reduced MusicLab essentials for safe real-flow iteration.
- Lab full: complete isolated MusicLab user flow.
- Release: full validation plus release/docs/dependency sanity.

## MusicLab Rules

MusicLab is the safe real-flow validation environment. It uses generated fixture audio, fake/local providers, fake Navidrome clients, and an isolated library tree marked with `.noqlen-forge-lab`.

```bash
noqlen-forge dev lab create
noqlen-forge dev lab list
noqlen-forge dev lab run
noqlen-forge dev lab run --quick
noqlen-forge dev lab run --area lyrics
noqlen-forge dev lab run --scenario navidrome
noqlen-forge dev lab run --full --timing
noqlen-forge dev lab doctor
noqlen-forge dev lab reset
```

The hidden top-level `lab ...` form remains callable as a compatibility alias for existing automation.

Safety rules:

- MusicLab never uses real user files.
- `lab reset` deletes only directories marked with `.noqlen-forge-lab`.
- Dangerous paths such as `/`, `$HOME`, broad music roots, and known real-library roots are blocked.
- Real providers do not run by default; live provider checks must be explicit and credentialed.
- Any validation command with `--apply` must target only paths inside MusicLab.
- Automated validation must never write tags, files, SQLite rows, or external API state in the real music library.

## Pytest Markers

Use `unit`, `contract`, `integration`, or `lab` as the basic category. Add area markers such as `db`, `provider`, `lyrics`, `navidrome`, `playlist`, `cli`, `service`, `filesystem`, `network_fake`, `slow`, or `release` when applicable.

MusicLab full-flow tests must be `lab` and `slow`. Fake API tests should be `integration` or `contract` plus `network_fake`, not `lab`, unless they validate MusicLab itself.
