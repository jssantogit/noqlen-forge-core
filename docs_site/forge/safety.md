# Safety Model

Forge is designed around explicit, reviewable operations. Treat real libraries as valuable private data.

## Dry-Run Before Apply

Write-capable workflows should be dry-run first. Read the plan, warnings, confidence, destination paths, and counts before using `--apply`.

Some commands expose `--dry-run` as an explicit compatibility or readability flag, while many write-capable workflows are already dry-run by default. Do not treat the presence or absence of `--dry-run` as the only safety signal. Use command-specific `--help` and the rendered plan to understand exact dry-run, apply, force, output, and review behavior for the command path you are using.

Explicit apply/write modes are not first-run examples. Start with help, status, smoke checks, and fake or fixture validation. Do not test command behavior first on a personal music library.

Safe dry-run/apply discovery examples:

```bash
noqlen-forge audit --help
noqlen-forge organize --help
noqlen-forge maintain repair --help
noqlen-forge dev check --smoke
```

Future CLI cleanup may make dry-run/apply wording more consistent across command families. Until then, command-specific help remains the source of exact flags.

## Normal Help And Advanced Help

Normal help is shorter by design. It shows common workflow options, keeps safety-critical flags visible, and avoids listing every technical provider, backend, tuning, and debug option.

Advanced help is available with `--advanced --help` for users who already understand the workflow and need technical control:

```bash
noqlen-forge --help
noqlen-forge enrich --help
noqlen-forge enrich --advanced --help
noqlen-forge metadata --advanced --help
noqlen-forge organize --advanced --help
```

Advanced flags were not removed, and command behavior did not change. Existing scripts should continue working unless they depend on exact help text output. Start with normal help, inspect command-specific help before running workflows, and use advanced help only for provider, backend, tuning, stage, or debug controls.

Do not treat advanced options as first-run workflow defaults. Dry-run and review still come before apply/write workflows.

## Command Safety Modes

Use this table to understand what a command family may affect. A family can include multiple safety modes because subcommands and flags differ. Use `noqlen-forge COMMAND --help` and nested help before running unfamiliar workflows.

| Safety mode | Command families |
| --- | --- |
| Read-only | `config path/show`, `db path/status/query/explain`, `query`, `report`, `fields`, `candidates`, read-only `playlist`, `navidrome`, `jobs`, and `review` views |
| Writes reports/files | `export`, playlist export/refresh, Navidrome export/diff output files, and other commands with explicit `--output` files |
| Writes Noqlen DB/state | `config init`, `db init`, `db scan --apply`, playlist definition changes, Navidrome local backups, review resolution, jobs cancel/resume/prune, and selected maintenance workflows |
| Writes tags/files only with explicit apply/review | `enrich`, `import`, `organize`, `maintain`, `cover`, `lyrics`, `replaygain`, `metadata`, `batch`, `cleanup`, `analyze`, `set-style`, and `apply-mbid` when apply/write intent is used |
| External service/server write | Navidrome restore and playlist push workflows when apply/write intent is used |
| Developer/test-only | `dev` and `dev lab` MusicLab validation commands |
| Compatibility alias | `sync`, `duplicates`, `missing`, `untracked`, and `missing-files` |

Safe discovery commands:

```bash
noqlen-forge db status
noqlen-forge report missing --help
noqlen-forge navidrome ratings diff --help
noqlen-forge dev check --smoke
```

Use MusicLab, fakes, or disposable fixtures for validation. Automated tests for external APIs and services should use mocks or fakes, not a real server.

## Explicit Confirmation For Destructive Changes

Do not make destructive changes casually. Rewrite, repair, restore, push, move, copy, tag-writing, and database-writing operations should require clear intent and review.

## Path Validation

Review all source and destination paths before applying changes. Avoid broad paths, stale mounts, untrusted symlinks, and paths that could escape the intended library root.

Forge refuses dangerous filesystem roots and broad storage roots for write-capable safety checks. This protects against accidental operations on the filesystem root, home directory, broad mount locations, removable-media roots, and similar locations that are too wide to be a safe workflow target.

For local development or test environments, `NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS` can add extra protected roots without committing personal paths. It is optional and should be set only in the local shell or automation environment. Use the platform path-list separator: `:` on POSIX shells and `;` on Windows shells.

```bash
export NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS="/tmp/noqlen-real-library:/example/protected/music"
```

Do not commit real personal library paths to docs, tests, logs, reports, examples, or config snippets. Keep examples generic and review generated reports before sharing them.

## Symlink And Traversal Care

Workflows must be careful around symlinks and path traversal. Automated checks should prefer temporary fixtures and must not follow unsafe paths into private areas.

## Reports And Logs

Reports and logs should explain what happened without leaking private data. Do not publish secrets, full lyrics, raw fingerprints, private library dumps, or real local paths.

## Quarantine Concept

When a workflow needs to isolate questionable files or records, prefer an explicit quarantine-style destination or review state rather than deleting or overwriting data immediately.

## Fake And Temporary Test Data

Automated tests and development validation should use fake or temporary libraries. MusicLab fixtures are intended for safe validation and should not be replaced with real personal collections.

## Needs Follow-Up

Some commands need deeper public examples beyond help output: `metadata`, `batch`, `cleanup`, `analyze`, `set-style`, `candidates`, `apply-mbid`, `fields`, `jobs`, Navidrome playlist `diff`/`push-smart`, and dry-run flag behavior across advanced workflows.
