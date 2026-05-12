# Contributing

Noqlen Forge Core welcomes practical bug reports, documentation improvements, test improvements, and focused feature proposals. Contributions should be small, reviewable, and clearly scoped.

## Safe Contribution Scope

- Use public CLI examples with `noqlen-forge`.
- Use Python examples with `noqlen_forge`.
- Keep changes focused on one issue or improvement at a time.
- Prefer fake, fixture, or temporary data for tests and examples.
- Do not use destructive real-library workflows for testing.
- Start any real-library validation with dry-run output only, then review plans before applying changes.
- Do not include secrets, real local paths, complete lyric text, raw fingerprints, private library dumps, or copyrighted metadata dumps in issues, tests, fixtures, logs, or examples.
- Do not assume PyPI installation is available unless the README says the package has been published.

## Local Validation

For local development, install from the checkout and run safe validation commands:

```bash
python -m pip install -e .
noqlen-forge --help
noqlen-forge dev check --smoke
pytest -q
python -m build
```

Tests should use fake, fixture, or temporary data only. Do not point automated tests at a real music library, real provider credentials, or private server data.

## Pull Request Expectations

- Keep pull requests small and easy to review.
- Explain the user-facing behavior or documentation change.
- Include tests for behavior changes when practical.
- Confirm safe smoke checks before asking for review.
- Call out any skipped validation and why it was skipped.
