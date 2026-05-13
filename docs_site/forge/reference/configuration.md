# Configuration

Noqlen Forge Core configuration is local and user-controlled. Configuration changes affect how Forge plans workflows, chooses providers, stores local state, and writes reports, but configuration by itself does not mutate a music library.

Write-capable workflows still require the same safety discipline: inspect configuration first, run help/status commands, review dry-run output, and use apply/write modes only after understanding the plan.

## Safe Discovery Commands

These commands are safe first checks and do not require a real music library:

```bash
noqlen-forge config path
noqlen-forge config show
noqlen-forge db path
noqlen-forge db status
noqlen-forge dev check --smoke
```

Use [First Safe Workflow](../first-safe-workflow.md) for the recommended first-run ladder and [CLI Reference](cli.md) for command groups and safety modes.

## Configuration Mental Model

| Area | Safe default expectation |
| --- | --- |
| Config file | Local settings are opt-in and should be inspected before workflow execution. |
| Database | Forge stores Noqlen state in SQLite; DB writes are separate from music tag writes. |
| Providers | External provider access should be configured carefully and tested with fakes/mocks where possible. |
| Lyrics and artwork | Existing valid data is protected by command force/overwrite rules; full lyrics and raw payloads should not be exposed. |
| Navidrome | Server connection details stay private; restore and push workflows require backup/diff review first. |
| Output and reports | Output paths should be explicit and reviewed before sharing generated files. |
| Protected roots | Forge refuses dangerous broad filesystem targets; local extra protected roots are optional guardrails. |

Automated validation must use MusicLab, fakes, or fixtures rather than real libraries. External providers and services should be mocked or faked in automated tests.

## Config File Path

Inspect the active configuration path before creating or editing configuration:

```bash
noqlen-forge config path
```

Create default local configuration only when you are ready to own that local state:

```bash
noqlen-forge config init
```

Inspect current settings with secrets masked:

```bash
noqlen-forge config show
```

The repository file `config.example.toml` is the current safe example of built-in sections and defaults. It intentionally keeps credential values empty and documents environment variable names for secrets.

## Database Path And Noqlen State

Forge uses a local SQLite database for library state, workflow history, saved definitions, backups, and review records. Database state is not the same as music tags.

```bash
noqlen-forge db path
noqlen-forge db status
```

`db init` creates or updates Noqlen database state. It does not rewrite tags, move files, or organize folders. `db scan` is dry-run by default and writes database rows only with explicit apply intent.

## Providers

Provider settings control metadata lookups and enrichment sources. Configure external providers carefully:

- Keep real API keys, tokens, and credentials out of config files that may be committed or shared.
- Prefer environment variables for secrets.
- Use fake or mocked providers in automated tests.
- Review provider confidence and candidate output before applying metadata changes.
- Do not log or commit raw provider payloads.

The example configuration includes provider sections for MusicBrainz, Discogs, fallback catalog providers, AcoustID, lyrics providers, and optional API keys. Empty secret values are placeholders, not real credentials.

## Lyrics

Lyrics configuration controls local-first lookup, online providers, sidecar behavior, embedding behavior, and conflict review behavior. Keep lyrics privacy visible:

- Do not include full lyrics in docs, logs, reports, examples, issues, or commits.
- Do not commit raw provider payloads.
- Prefer review behavior for conflicts or existing mismatches.
- Treat sidecar output paths as private library artifacts until reviewed.

Provider configuration should stay high-level in public examples. Do not publish real lyrics, provider responses, or account-specific details.

## Artwork And Covers

Cover configuration controls cover source order, embedding behavior, folder-cover behavior, confidence thresholds, and size limits. Write-capable cover workflows still require dry-run/review/apply discipline.

Review whether a workflow would embed artwork into tags, save a folder image, replace existing art, or remove a folder cover file before using write modes.

## Navidrome

Navidrome configuration is optional and disabled by default in the example configuration. Keep server connection details private:

- Do not publish server URLs, usernames, passwords, tokens, salts, or local network details.
- Prefer environment variables for secrets.
- Start with help, status, list, backup, and diff workflows.
- Do not start with restore, push, or push-smart.

See [Navidrome Workflows](../navidrome-workflows.md) for backup, status, diff, export, restore, push, and push-smart safety expectations.

## Output And Reports

Output and report configuration should support review without leaking private data. Reports and logs should not contain secrets, full lyrics, raw fingerprints, private library dumps, raw provider payloads, or personal paths.

Before sharing an export, backup, report, or debug log, inspect it for private library and server data.

## Protected Library Roots

Forge refuses dangerous filesystem roots and broad storage roots for write-capable safety checks. For local development or test environments, `NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS` can add extra protected roots without committing personal paths.

Use generic local guardrails only, for example:

```bash
export NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS="/tmp/noqlen-protected-fixture:/example/protected/music"
```

This variable is optional and should be set only in the local shell or automation environment. It is not a substitute for reviewing command help, dry-run output, destination paths, and apply behavior.

## Do Not Commit

- Do not commit personal config files.
- Do not commit secrets, tokens, passwords, API keys, salts, or server details.
- Do not commit real library paths or private library dumps.
- Do not commit generated `site/` output.
- Do not commit exports, backups, reports, or logs containing private library or server data.

## Where To Go Next

- [Safety Model](../safety.md) for dry-run, apply, path, and privacy rules.
- [First Safe Workflow](../first-safe-workflow.md) for a safe first-run sequence.
- [CLI Reference](cli.md) for command groups and safety categories.
- [Navidrome Workflows](../navidrome-workflows.md) for external-service workflow safety.
- `config.example.toml` in the source repository for the current example configuration sections.
