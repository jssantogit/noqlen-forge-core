# CLI Reference

The public command is:

```bash
noqlen-forge
```

Use command help for the exact current command tree and flags:

```bash
noqlen-forge --help
noqlen-forge audit --help
noqlen-forge report missing --help
noqlen-forge navidrome --help
```

This page summarizes the public command surface so you can find the right workflow before reading command-specific help.

## Safe First Checks

These commands do not require a real music library:

```bash
noqlen-forge --help
noqlen-forge config path
noqlen-forge db status
noqlen-forge dev check --smoke
```

Before using any write-capable workflow, read the command-specific help and dry-run output. Some commands expose `--dry-run`; write-capable workflows commonly require explicit `--apply` before changing tags, files, database rows, or external server state.

## Safety Categories

| Category | What it means |
| --- | --- |
| Read-only | Reads config, local database state, files, or external services without writing state, tags, files, or server data. |
| Writes reports/files | Writes only an explicit report, export, playlist, or output file. |
| Writes Noqlen DB/state | Writes SQLite rows, saved definitions, job history, backups, or review state. |
| Writes tags/files only with explicit apply | May rewrite tags, write sidecars, save cover/lyrics files, copy/move files, or organize files only after explicit apply/write intent. |
| External service/server write | Writes to Navidrome or another configured service only after explicit apply/write intent. |
| Developer/test-only | Maintainer checks and isolated MusicLab fixture validation, not normal user library workflows. |
| Compatibility alias | A top-level alias kept for compatibility and discovery; behavior belongs to another command family. |

## Command Groups

### Getting Started

| Command | Use it for | Safety notes |
| --- | --- | --- |
| `config` | Configuration path, initialization, and masked inspection. | `config path` and `config show` are read-only. `config init` writes a config file. |
| `db` | Database path, initialization, status, scans, queries, and explain output. | `db status`, `db query`, and `db explain` are read-only. `db scan` is dry-run by default and writes database rows only with `--apply`. |

Useful discovery commands:

```bash
noqlen-forge config --help
noqlen-forge db --help
```

### Core Workflows

| Command | Use it for | Safety notes |
| --- | --- | --- |
| `audit` | Inspect metadata completeness. | Read-only by default; optional job recording writes job state. |
| `enrich` | Enrich tags, cover, lyrics, audio features, and provider-backed metadata. | Dry-run by default; use command help before any apply mode. |
| `import` | Run the safe import workflow for incoming files. | Dry-run by default; with `--apply` it may enrich tags, copy/move files, and update local state. |
| `organize` | Plan and organize files into a library layout. | Dry-run by default; with `--apply` it may copy or move files and record operations. |

Start with help and dry-run output, not a broad library path:

```bash
noqlen-forge audit --help
noqlen-forge enrich --help
noqlen-forge import --help
noqlen-forge organize --help
```

### Reports

| Command | Use it for | Safety notes |
| --- | --- | --- |
| `query` | Query the local library database. | Read-only. |
| `report` | Missing metadata, duplicates, untracked files, and missing files. | Read-only. |
| `export` | Export reports and library data as JSON or CSV. | Reads local state and writes only the requested output file when `--output` is used. |
| `duplicates` | Compatibility alias for `report duplicates`. | Read-only alias. |
| `missing` | Compatibility alias for `report missing`. | Read-only alias. |
| `untracked` | Compatibility alias for `report untracked`. | Read-only alias. |
| `missing-files` | Compatibility alias for `report missing-files`. | Read-only alias. |

Report discovery examples:

```bash
noqlen-forge query --help
noqlen-forge report --help
noqlen-forge report missing --help
noqlen-forge export --help
```

### Playlists And Ratings

| Command | Use it for | Safety notes |
| --- | --- | --- |
| `playlist` | Smart playlist definitions and playlist exports. | Smart definitions write local state only with apply intent. Exports write requested playlist output files. |
| `navidrome` | Navidrome ratings and playlist backup, diff, restore, export, and push workflows. | Backup/diff/export are read-oriented or local-output workflows. Restore and push write to Navidrome only with explicit apply intent. |

Nested workflow families include:

| Family | Commands |
| --- | --- |
| `playlist smart` | `create`, `list`, `show`, `export`, `refresh`, `delete`, `rename` |
| `navidrome ratings` | `backup`, `status`, `diff`, `export`, `restore` |
| `navidrome playlists` | `list`, `backup`, `status`, `export`, `push`, `diff`, `push-smart` |

Use nested help before connecting to services or writing outputs:

```bash
noqlen-forge playlist smart --help
noqlen-forge navidrome ratings --help
noqlen-forge navidrome playlists --help
```

### Maintenance And Review

| Command | Use it for | Safety notes |
| --- | --- | --- |
| `maintain` | Advanced sync, repair, and rewrite workflows. | Dry-run by default; write target depends on child command and flags. |
| `review` | Inspect and resolve manual REVIEW decisions. | Listing and showing review items are read-only. Resolution writes review/local state only with explicit apply intent. |

Nested maintenance families include:

| Family | Commands |
| --- | --- |
| `maintain sync` | Synchronize SQLite records and file tags. |
| `maintain repair` | Repair selected database/report inconsistencies. |
| `maintain rewrite` | Canonicalize configured textual metadata values. |

### Focused Tools

| Command | Use it for | Safety notes |
| --- | --- | --- |
| `cover` | Detect, fetch, save, and embed album cover art. | Writes tags or cover files only with explicit apply intent. |
| `lyrics` | Detect, fetch, save, and embed lyrics. | Writes tags or sidecar/text files only with explicit apply intent. |
| `replaygain` | Analyze loudness and ReplayGain values. | Dry-run by default; writes only with explicit apply intent. |
| `metadata` | Fetch provider metadata. | Dry-run by default; check command help for provider and write behavior. |
| `analyze` | Analyze optional local audio features. | Dry-run by default; writes only with explicit apply intent. |
| `set-style` | Set STYLE manually. | Dry-run by default; writes only with explicit apply intent. |
| `candidates` | List MusicBrainz release candidates. | Read-only. |
| `apply-mbid` | Apply MusicBrainz IDs. | Dry-run by default; writes only with explicit apply intent. |

### Jobs And Batch-Style Workflows

| Command | Use it for | Safety notes |
| --- | --- | --- |
| `jobs` | Inspect, cancel, resume, and prune workflow job records. | List/status/show are read-only. Cancel/resume/prune can change job state; `prune` is dry-run unless applied. |
| `batch` | Process direct child album/single targets. | Dry-run by default; check help before any apply mode. |
| `cleanup` | Remove empty or bad metadata. | Dry-run by default; writes only with explicit apply intent. |
| `fields` | List supported metadata fields. | Read-only. |

### Contributor Tools

| Command | Use it for | Safety notes |
| --- | --- | --- |
| `dev` | Smoke checks, targeted validation, and isolated MusicLab workflows. | Developer/test-only. MusicLab uses fixture data and should not be pointed at a real user library. |

The safest validation command is:

```bash
noqlen-forge dev check --smoke
```

## Compatibility Aliases

The top-level aliases `sync`, `duplicates`, `missing`, `untracked`, and `missing-files` are kept for compatibility and discovery. Prefer the grouped forms in new examples: `maintain sync`, `report duplicates`, `report missing`, `report untracked`, and `report missing-files`.

Aliases are not deprecated by this page. Future help simplification should preserve compatibility unless a later release explicitly documents a change.

## Common Safe Examples

```bash
noqlen-forge --help
noqlen-forge config path
noqlen-forge db status
noqlen-forge audit --help
noqlen-forge report missing --help
noqlen-forge navidrome --help
noqlen-forge dev check --smoke
```

These examples are discovery or status commands. They do not require a real personal music library.

## More Detail

Use command-specific help for exact flags and current nesting:

```bash
noqlen-forge COMMAND --help
noqlen-forge COMMAND SUBCOMMAND --help
```

For a deeper repository-owned inventory, see `docs/reference/cli-inventory.md` in the source repository. The safety model is summarized in [Safety Model](../safety.md), and a safe starting path is described in [First Safe Workflow](../first-safe-workflow.md).

## Known Follow-Ups

- Some command help is still sparse, especially advanced focused tools and job workflows.
- Top-level help is still long because it includes common workflows, advanced tools, aliases, and contributor commands.
- Compatibility aliases are kept for compatibility and discovery.
- Deeper public pages for Navidrome, playlists, configuration, and safety will be expanded later.
