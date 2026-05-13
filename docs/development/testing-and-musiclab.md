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

## Project Naming

Development guidance should use current Noqlen naming:

- Noqlen is the overall ecosystem.
- Noqlen Forge Core is this repository and the current active project.
- `noqlen-forge` is the public CLI.
- `noqlen_forge` is the Python package and import path.
- Noqlen Flux is the future core for search, download, and import workflows.
- Noqlen Anchor is the future core for local server and service workflows.
- Noqlen Aria is later app, mobile, or interface work and must not be started yet.

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

- Smoke: compile checks and representative CLI help output.
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

- Start write-capable real-library workflows with dry-run output and review before apply.
- Do not make destructive changes without explicit confirmation.
- Keep logs, reports, and planned-change output clear and reviewable.
- Use backup and recovery steps where a workflow can modify files, tags, databases, or external service state.
- Validate paths before file operations, and handle symlinks and path traversal carefully.
- Quarantine suspicious files or records where applicable instead of deleting or overwriting immediately.
- MusicLab never uses real user files.
- `lab reset` deletes only directories marked with `.noqlen-forge-lab`.
- Dangerous paths such as `/`, `$HOME`, broad mount roots, removable-media roots, and configured protected library roots are blocked.
- Real providers do not run by default; live provider checks must be explicit and credentialed.
- External APIs should be mocked or faked in automated validation.
- Any validation command with `--apply` must target only paths inside MusicLab.
- Automated validation must never write tags, files, SQLite rows, or external API state in the real music library.
- Real personal paths must not be committed to docs, tests, logs, reports, fixtures, or examples.
- Never expose secrets, full lyrics, fingerprints, private data, personal paths, or raw provider payloads.

## Site Deployment Policy

Site deployment policy is documented in [Site deployment and cache validation](../site/deployment.md). In short: deploy through GitHub Actions only, build with `python -m mkdocs build --strict`, do not use `mkdocs gh-deploy`, do not rely on a manual `gh-pages` branch, and do not commit generated `site/` files.

## Protected Library Roots

`NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS` is an optional local/test/development guardrail for marking additional library roots as protected. It is useful when a developer wants automated validation to refuse a copied local library or another fake root without embedding private paths in runtime code or tests.

Set it only in the local shell or local automation environment. Do not commit personal values to repository files. The value is a platform path-list: use `:` between entries on POSIX shells and `;` between entries on Windows shells.

```bash
export NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS="/tmp/noqlen-real-library:/example/protected/music"
```

Tests should prefer `tmp_path`, fake fixtures, or MusicLab paths. If a test needs protected-root behavior, inject a fake path or set `NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS` to a fake path for that test only.

## Pytest Markers

Use `unit`, `contract`, `integration`, or `lab` as the basic category. Add area markers such as `db`, `provider`, `lyrics`, `navidrome`, `playlist`, `cli`, `service`, `filesystem`, `network_fake`, `slow`, or `release` when applicable.

MusicLab full-flow tests must be `lab` and `slow`. Fake API tests should be `integration` or `contract` plus `network_fake`, not `lab`, unless they validate MusicLab itself.
