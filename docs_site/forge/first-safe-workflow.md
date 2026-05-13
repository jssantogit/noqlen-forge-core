# First Safe Workflow

Use this ladder after installation to confirm Noqlen Forge Core works before pointing it at valuable files. The safest first run is discovery and validation, not tag writing, file moves, or external-server updates.

## Step 1: Confirm The CLI Works

Start with commands that do not require a real music library:

```bash
noqlen-forge --help
noqlen-forge dev check --smoke
```

`--help` confirms the command is installed and shows the current command tree. `dev check --smoke` runs lightweight validation and representative help checks without touching a personal collection.

## Step 2: Inspect Configuration Paths

Check where Forge will read configuration from before creating or changing anything:

```bash
noqlen-forge config path
```

If configuration already exists and you are comfortable viewing masked settings, inspect it with:

```bash
noqlen-forge config show
```

Do not paste provider credentials, private paths, tokens, or generated config output into public issues, logs, examples, or commits. For more detail, see [Configuration](reference/configuration.md).

## Step 3: Inspect Or Initialize The Database Safely

Forge uses a local SQLite database for Noqlen state. Inspect the path and current status first:

```bash
noqlen-forge db path
noqlen-forge db status
```

If the database has not been created yet, initialize it only after reviewing the path:

```bash
noqlen-forge db init
```

`db init` creates or updates Noqlen database state. It does not rewrite music tags, move music files, or organize folders.

## Step 4: Use MusicLab, Fakes, Or Fixtures First

Use fake data, temporary fixtures, or MusicLab validation before experimenting with files you care about. The safest supported validation entry point is still:

```bash
noqlen-forge dev check --smoke
```

Contributor-oriented MusicLab commands are available under `dev lab`. They are intended for isolated fixture validation, not real libraries:

```bash
noqlen-forge dev lab --help
```

If you create your own fixture later, keep it small and disposable, for example under `/tmp/noqlen-lab` or `/example/music-fixture`.

## Step 5: Explore Workflow Help Before Running Workflows

Read command-specific help before using workflow commands. Start with discovery commands like:

```bash
noqlen-forge audit --help
noqlen-forge report missing --help
noqlen-forge organize --help
noqlen-forge navidrome --help
```

The [CLI Reference](reference/cli.md) summarizes command groups, nested workflows, compatibility aliases, and safety categories.

## Step 6: Dry-Run Before Apply

Write-capable workflows must be reviewed in dry-run mode before any apply/write mode. Read the plan, warnings, confidence, destination paths, counts, and review items before deciding whether to continue.

Some commands expose `--dry-run`; many write-capable commands are dry-run by default and require explicit `--apply` before changing tags, files, database rows, or external server state. Always check command-specific help before using any apply mode.

Do not use apply/write examples as a first-run test. First-run examples should stay limited to help, status, configuration inspection, smoke checks, and fake or fixture validation.

## Do Not Start Here

- Do not run apply/write workflows on a real library as the first test.
- Do not test with a personal music library.
- Do not point Forge at large, broad, or irreplaceable folders without reviewing the command, path, and dry-run output.
- Do not use external-server write workflows before backup, diff, status, and help checks.
- Do not treat `--apply` as safe just because a command supports it; apply mode requires explicit review and intent.

## Safe Mental Model

| Workflow type | What to assume |
| --- | --- |
| Read-only commands | Safest starting point. They inspect help, configuration, database status, reports, or service status. |
| Report/output commands | May create explicit output files, such as JSON, CSV, or playlist exports. Review output paths before running. |
| DB/state commands | Can alter Noqlen SQLite state, saved definitions, job history, backups, or review records. |
| Tag/file write commands | Can change metadata tags, sidecar files, covers, lyrics, or file locations only after explicit review/write intent. |
| External-service commands | Can affect remote or local services such as Navidrome when write modes are used. Start with ping, status, backup, diff, and help. |

For the broader safety model, see [Safety Model](safety.md). For Navidrome-specific workflows, see [Navidrome Workflows](navidrome-workflows.md).

## When You Are Ready For A Fixture Dry-Run

After the steps above, choose a disposable fixture rather than a real collection. Keep the path generic and small, review command help, then run a dry-run-only workflow appropriate to the command you are evaluating.

Example fixture path shape:

```text
/tmp/noqlen-lab
/example/music-fixture
```

Do not move from fixture dry-runs to apply/write workflows until you understand exactly what the command plans to read, write, copy, move, update, or send to an external service.
