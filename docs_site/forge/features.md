# Features

This page maps major Noqlen Forge Core feature areas to the `noqlen-forge` command families that support them. It is a guide to the right workflow area, not a full flag inventory. Use [CLI Reference](reference/cli.md) and command-specific help for exact flags and current nesting.

Start with [First Safe Workflow](first-safe-workflow.md) if this is your first run. For write behavior, privacy, dry-run discipline, and protected-path expectations, see [Safety Model](safety.md). For local config and database state, see [Configuration](reference/configuration.md). For external-service workflows, see [Navidrome Workflows](navidrome-workflows.md).

Advanced commands and sparse-help areas are intentionally documented in the CLI and repository reference pages instead of being duplicated here.

## Configuration And Database Setup

Use configuration and database setup commands to find local Noqlen state, initialize owned local state, and confirm the SQLite database is available before running broader workflows.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Configuration paths, masked config inspection, and config creation. | `config` | `noqlen-forge config path` | `config path` and `config show` are read-only. `config init` writes a local config file only after explicit user intent. | [Configuration](reference/configuration.md), [CLI Reference](reference/cli.md) |
| Database path, schema initialization, status, scans, and database queries. | `db` | `noqlen-forge db status` | `db path`, `db status`, `db query`, and `db explain` are read-only. `db init` writes database state. `db scan` is dry-run by default and writes rows only with apply intent. | [Configuration](reference/configuration.md), [Safety Model](safety.md) |

## Library Scanning And Status

Use library scanning and status workflows to build or inspect the local SQLite view of library files, tags, paths, and known missing state.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Checking whether Forge has database state and whether local state is ready. | `db status`, `db path` | `noqlen-forge db status` | Read-only status and path inspection. | [First Safe Workflow](first-safe-workflow.md), [CLI Reference](reference/cli.md) |
| Planning a scan before updating local database rows. | `db scan` | `noqlen-forge db scan --help` | Dry-run by default. Apply mode updates Noqlen database rows; it does not rewrite tags or move music files. | [Safety Model](safety.md), [Configuration](reference/configuration.md) |
| Explaining and querying stored database fields. | `db query`, `db explain`, `query` | `noqlen-forge query --help` | Read-only database inspection. | [CLI Reference](reference/cli.md) |

## Metadata Audit And Enrichment

Use audit and enrichment workflows to inspect metadata completeness, compare provider candidates, and plan tag improvements without silent overwrites.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Finding missing or incomplete metadata. | `audit`, `report missing` | `noqlen-forge audit --help` | `audit` is read-only by default. Optional job recording writes sanitized job state. | [CLI Reference](reference/cli.md), [Safety Model](safety.md) |
| Planning provider-backed enrichment across tags, covers, lyrics, and audio features. | `enrich`, `metadata`, `candidates`, `apply-mbid` | `noqlen-forge enrich --help` | Dry-run first. Apply modes can write tags, review state, provider state, or related Noqlen workflow state. | [Safety Model](safety.md), [CLI Reference](reference/cli.md) |
| Sending ambiguous matches or conflicts to manual review instead of overwriting. | `review` | `noqlen-forge review --help` | Listing and showing review items are read-only. Resolution writes local review state only with apply intent. | [Safety Model](safety.md), [CLI Reference](reference/cli.md) |

## Reports And Queries

Use report and query workflows to inspect local library state, missing metadata, duplicates, untracked files, missing files, and exportable summaries.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Read-only reports for missing metadata, duplicates, untracked files, and missing files. | `report`, `missing`, `duplicates`, `untracked`, `missing-files` | `noqlen-forge report missing --help` | Reports are read-only. Top-level aliases are compatibility aliases for grouped report commands. | [CLI Reference](reference/cli.md), [Safety Model](safety.md) |
| Database-backed searches and filtered views. | `query`, `db query` | `noqlen-forge query --help` | Read-only database inspection. | [CLI Reference](reference/cli.md) |
| Explicit JSON, CSV, or report outputs. | `export` | `noqlen-forge export --help` | Reads local state and writes only the requested output file when output is requested. Review generated files before sharing. | [Safety Model](safety.md), [Configuration](reference/configuration.md) |

## Missing, Untracked, And Duplicate Checks

Use these checks to identify library drift before repair, import, organization, or cleanup work.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Missing metadata fields. | `report missing`, `missing` | `noqlen-forge report missing --help` | Read-only. | [CLI Reference](reference/cli.md) |
| Duplicate albums or tracks. | `report duplicates`, `duplicates` | `noqlen-forge report duplicates --help` | Read-only. | [CLI Reference](reference/cli.md) |
| Files present on disk but not tracked in database state. | `report untracked`, `untracked` | `noqlen-forge report untracked --help` | Read-only. | [Safety Model](safety.md), [CLI Reference](reference/cli.md) |
| Database entries whose files are missing. | `report missing-files`, `missing-files` | `noqlen-forge report missing-files --help` | Read-only. Use repair workflows only after reviewing reports and dry-run plans. | [Safety Model](safety.md), [CLI Reference](reference/cli.md) |

## Import And Organization Workflows

Use import and organization workflows to plan how incoming files would be enriched, copied, moved, or placed into a library layout.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Safe import planning for incoming files. | `import` | `noqlen-forge import --help` | Dry-run by default. Apply mode may enrich tags, copy or move files, organize files, and record database operations. | [First Safe Workflow](first-safe-workflow.md), [Safety Model](safety.md) |
| Planning folder and filename organization. | `organize` | `noqlen-forge organize --help` | Dry-run by default. Apply mode may copy or move files and record operations. Review destination paths and conflicts first. | [Safety Model](safety.md), [CLI Reference](reference/cli.md) |
| Batch-style album or single processing. | `batch` | `noqlen-forge batch --help` | Dry-run unless applied. Treat as write-capable and review command help before use. | [CLI Reference](reference/cli.md) |

## Review And Maintenance Workflows

Use review and maintenance workflows to inspect conflicts, repair selected database/report inconsistencies, synchronize database and tags, and canonicalize configured metadata values.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Manual review queues and conflict decisions. | `review` | `noqlen-forge review --help` | Read-only for list/show. Resolution writes local review state only with apply intent. | [Safety Model](safety.md), [CLI Reference](reference/cli.md) |
| Syncing local database state and file tags. | `maintain sync`, `sync` | `noqlen-forge maintain sync --help` | Dry-run by default. Apply target depends on direction flags and can affect database state or tags. | [Safety Model](safety.md), [CLI Reference](reference/cli.md) |
| Repairing selected inconsistencies and rewriting configured metadata values. | `maintain repair`, `maintain rewrite`, `cleanup` | `noqlen-forge maintain --help` | Dry-run by default for write-capable maintenance. Review selected action, target, and output before apply intent. | [Safety Model](safety.md), [CLI Reference](reference/cli.md) |

## Covers, Lyrics, ReplayGain, And Focused Metadata Tools

Use focused tools when you need a narrower workflow than full enrichment.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Detecting, fetching, saving, embedding, or removing cover art. | `cover` | `noqlen-forge cover --help` | Writes tags or cover files only with explicit apply intent. Existing valid art should be preserved unless overwrite behavior is requested. | [Safety Model](safety.md), [Configuration](reference/configuration.md) |
| Detecting, fetching, saving, or embedding lyrics. | `lyrics` | `noqlen-forge lyrics --help` | Writes tags or sidecar/text files only with explicit apply intent. Public output must not expose full lyrics. | [Safety Model](safety.md), [Configuration](reference/configuration.md) |
| ReplayGain, loudness, audio feature analysis, manual STYLE, and MusicBrainz IDs. | `replaygain`, `analyze`, `set-style`, `candidates`, `apply-mbid`, `fields` | `noqlen-forge replaygain --help` | Analysis and metadata writes are dry-run or read-only until explicit apply intent. `fields` and `candidates` are read-only. | [CLI Reference](reference/cli.md), [Safety Model](safety.md) |

## Playlists And Smart Playlists

Use playlist workflows to save smart playlist definitions and generate playlist output files from local database queries.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Saved smart playlist definitions. | `playlist smart create`, `playlist smart list`, `playlist smart show`, `playlist smart delete`, `playlist smart rename` | `noqlen-forge playlist smart --help` | List/show are read-only. Create/delete/rename write local Noqlen state only with apply intent. | [CLI Reference](reference/cli.md), [Safety Model](safety.md) |
| Playlist exports and refreshes. | `playlist smart export`, `playlist smart refresh` | `noqlen-forge playlist smart export --help` | Reads local state and writes only requested playlist output files. Review output paths before sharing generated files. | [CLI Reference](reference/cli.md), [Configuration](reference/configuration.md) |

## Navidrome Ratings And Playlist Workflows

Use Navidrome workflows to inspect, back up, diff, export, restore, or push ratings and playlists through configured external-service access.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Ratings and favorites backup, status, diff, export, and restore. | `navidrome ratings` | `noqlen-forge navidrome ratings --help` | Backup/diff/export are read-oriented or local-output workflows. Restore writes to Navidrome only with explicit apply intent after backup and diff review. | [Navidrome Workflows](navidrome-workflows.md), [Safety Model](safety.md) |
| Server playlist list, backup, status, export, diff, push, and push-smart workflows. | `navidrome playlists` | `noqlen-forge navidrome playlists --help` | List/status/diff/export are read-oriented or local-output workflows. Push and push-smart write to Navidrome only with explicit apply intent after review. | [Navidrome Workflows](navidrome-workflows.md), [CLI Reference](reference/cli.md) |
| General Navidrome command discovery. | `navidrome` | `noqlen-forge navidrome --help` | Do not start with restore or push. Keep server details and credentials private. | [Navidrome Workflows](navidrome-workflows.md), [Configuration](reference/configuration.md) |

## Jobs And Developer Validation

Use jobs commands to inspect persistent workflow records, and developer validation commands to run isolated checks without real libraries.

| Use this for | Primary command families | Safe first example | Safety/write behavior | More detail |
| --- | --- | --- | --- | --- |
| Inspecting, canceling, resuming, and pruning workflow job records. | `jobs` | `noqlen-forge jobs --help` | List/status/show are read-only. Cancel/resume/prune can change job state; prune is dry-run unless applied. | [CLI Reference](reference/cli.md), [Safety Model](safety.md) |
| Smoke checks and isolated MusicLab validation. | `dev`, `dev check`, `dev lab` | `noqlen-forge dev check --smoke` | Developer/test-only. Use fakes, temporary data, or MusicLab fixtures, not personal libraries. | [First Safe Workflow](first-safe-workflow.md), [CLI Reference](reference/cli.md) |

## Safe Discovery Shortlist

These examples are help, status, or validation commands and do not require a real music library:

```bash
noqlen-forge --help
noqlen-forge config path
noqlen-forge db status
noqlen-forge audit --help
noqlen-forge report missing --help
noqlen-forge organize --help
noqlen-forge navidrome --help
noqlen-forge dev check --smoke
```
