# CLI Command Surface Inventory

## Purpose

This inventory records the current public `noqlen-forge` command surface as observed from installed CLI help. It is a baseline for documentation coverage, public site expansion, and later help usability work. It is not a tutorial and does not propose command behavior changes.

## How This Inventory Is Maintained

- Refresh this page from `noqlen-forge --help` and relevant nested `--help` output before changing public CLI help or command structure.
- Treat compatibility aliases as public discovery surface unless a later runtime change deprecates them with tests and release notes.
- Keep safety categories conservative. If write behavior is unclear, mark it as `needs verification` instead of guessing.
- Keep this inventory in repository docs first. Public site navigation and expanded guides can follow in later documentation commits.

## Command Safety Categories

| Category | Meaning |
| --- | --- |
| Read-only | Reads configuration, SQLite state, files, or external services without writing project state, tags, files, or server data. |
| Reads library + writes reports/files | Reads local state and writes only explicit report/export/output files. |
| Writes Noqlen DB/state | Writes SQLite rows, saved definitions, job history, local backups, or other Noqlen state. |
| Writes tags/files only with explicit apply | May rewrite tags, write sidecars, copy/move files, remove folder assets, or organize files only when explicit apply/write flags are used. |
| External service/server write | Writes to Navidrome or another configured external service only with explicit apply/write flags. |
| Developer/test-only | Maintainer validation or isolated MusicLab fixture workflows, not normal user library workflows. |
| Compatibility alias | Top-level alias retained for compatibility/discovery; behavior is owned by another command family. |

## Top-Level Command Inventory

| Command | Public role | Safety/write behavior | Notable flags | Docs coverage |
| --- | --- | --- | --- | --- |
| `config` | Manage configuration. | Read-only for `path` and `show`; writes config file through `init`, with `--force` replacing existing config. | `--force` on `config init`. | partially documented |
| `db` | Database path, init, status, scan, query, and explain. | Read-only for `path`, `status`, `query`, and `explain`; writes Noqlen DB/state for `init` and `scan --apply`. | `scan --apply`, `scan --verbose`, `query --albums/--tracks/--files`, `query --missing`, `query --format`, `query --limit`. | partially documented |
| `audit` | Inspect metadata completeness. | Read-only by default; `--job` records job state. | `--format text/json`, `--job`, `--verbose`, `--advanced`. | documented |
| `enrich` | Safe native enrichment pipeline. | Writes tags/files only with explicit apply; may also write Noqlen state for review/job/provider results as part of workflow. | `--apply`, `--dry-run`, `--full`, skip/force flags, provider/source selection, confidence flags, analysis flags, progress/plain output flags. | partially documented |
| `import` | Full safe import workflow for incoming files. | Writes tags/files and Noqlen DB/state only with `--apply`; may copy or move files. | `--apply`, `--library`, `--copy`, `--move`, `--replaygain`, skip flags, `--allow-review`, `--force`, `--verbose`. | documented |
| `organize` | Plan and organize files into a library layout. | Writes tags/files only with explicit apply; may copy or move files and record operations. | `--apply`, `--copy`, `--move`, `--library`, templates, `--conflict-policy`, `--verbose`. | documented |
| `query` | Query the local library database. | Read-only. | `--albums`, `--tracks`, `--files`, `--limit`, `--format`, `--verbose`. | partially documented |
| `report` | Missing metadata, duplicates, untracked files, and missing files. | Read-only. | Child-specific format, scope, library, strategy, and verbosity flags. | documented |
| `export` | Export reports and library data. | Reads library + writes reports/files when `--output` is used; otherwise read-only output to stdout. | `--all`, `--missing`, `--duplicates`, `--reviews`, `--library`, `--format`, `--output`, `--force`, scope flags, include/exclude fields. | documented |
| `playlist` | Smart playlist definitions and exports. | Writes Noqlen DB/state for smart definition create/delete/rename with `--apply`; export/refresh write explicit playlist files. | `smart` child command, query, sort, limit, path mode, output, force, apply flags. | partially documented; site gap |
| `navidrome` | Navidrome/Subsonic integration. | Read-only for ping/list/status/diff/export; writes local backup state with backup `--apply`; writes server data for restore/push with `--apply`. | Ratings and playlists child commands, `--apply`, `--server`, `--backup-only`, output/format, identity/confidence flags. | partially documented; site gap |
| `maintain` | Advanced sync, rewrite, and repair. | Writes Noqlen DB/state or tags/files only with `--apply`; exact target depends on child command and flags. | `sync`, `repair`, `rewrite`, `--apply`, direction flags, field flags, conflict policy, `--db-only`, `--tags-only`. | partially documented |
| `review` | List and resolve manual REVIEW decisions. | Read-only for list/show; writes Noqlen DB/state for resolve actions only with `--apply`. | `--format`, `--action`, `--value`, `--field`, `--apply`, `--force`, `--verbose`. | documented; help sparse |
| `jobs` | Inspect and control persistent workflow jobs. | Reads or writes Noqlen job state; `prune` is dry-run unless `--apply`, while cancel/resume mutate job state. | `list`, `status`, `show`, `cancel`, `resume`, `prune`, `--format`, `--limit`, `--status`, `--apply`. | partially documented; help sparse |
| `dev` | Maintainer validation and isolated MusicLab tools. | Developer/test-only. | `check`, `affected`, `lab`, mode flags, area flags, timing flags. | documented for contributors |
| `metadata` | Fetch provider metadata. | Dry-run unless `--apply`; write targets need safety clarification from command help. | `--apply`, `--dry-run`, `--force`, `--provider`, `--allow-more-providers`, `--min-confidence`, provider-specific IDs, `--candidate`, `--verbose`. | underdocumented; help sparse; needs safety clarification |
| `batch` | Process direct child album/single targets. | Dry-run unless `--apply`; likely writes through delegated workflows. Needs verification. | `--apply`, `--recursive`, `--yes`, `--continue-on-review`. | underdocumented; help sparse; needs safety clarification |
| `cleanup` | Remove empty/bad metadata. | Writes tags/files only with explicit apply. | `--apply`, `--dry-run`, `--verbose`. | underdocumented; help sparse |
| `cover` | Detect, fetch, save, and embed album cover. | Writes tags/files only with explicit apply; can save or remove folder cover files. | `--apply`, `--force`, embed/folder flags, `--remove-folder-cover`, cover source, confidence, `--verbose`. | partially documented; help sparse |
| `lyrics` | Detect, fetch, save, and embed lyrics. | Writes tags/files only with explicit apply; may write sidecar/text files. | `--apply`, `--force`, embed/save flags, provider flags, synced/unsynced preference flags, confidence, format, `--verbose`. | partially documented |
| `analyze` | Analyze optional local audio features. | Dry-run unless `--apply`; writes tags/state only with explicit apply. | `--apply`, `--bpm`, `--key`, `--backend`, feature flags, Last.fm/mood flags, force/skip flags, progress/plain flags. | underdocumented; help sparse |
| `replaygain` | Analyze loudness/ReplayGain. | Dry-run unless `--apply`; writes tags/files only with explicit apply. | `--apply`, `--force`, `--album`, `--tracks`, `--verbose`. | underdocumented; help sparse |
| `set-style` | Set STYLE manually. | Writes tags/files only with explicit apply. | `--apply`, `--dry-run`, `--force`. | underdocumented; help sparse |
| `candidates` | List MusicBrainz release candidates. | Read-only. | `path` only. | underdocumented; help sparse |
| `apply-mbid` | Apply MusicBrainz IDs. | Writes tags/files only with explicit apply. | `--release-id`, `--apply`, `--dry-run`, `--force`. | underdocumented; help sparse |

## Nested Command Inventory

### Configuration

| Command | Safety/write behavior | Notable flags | Docs coverage |
| --- | --- | --- | --- |
| `config path` | Read-only. | None. | documented |
| `config init` | Writes a config file; `--force` allows replacement. | `--force`. | partially documented |
| `config show` | Read-only, secrets masked. | None. | documented |

### Database

| Command | Safety/write behavior | Notable flags | Docs coverage |
| --- | --- | --- | --- |
| `db path` | Read-only. | None. | documented |
| `db init` | Writes Noqlen DB/state by creating/applying migrations. | None. | documented |
| `db status` | Read-only. | None. | documented |
| `db scan` | Dry-run by default; writes Noqlen DB/state only with `--apply`. | `--apply`, `--verbose`. | documented |
| `db query` | Read-only. | `--albums`, `--tracks`, `--files`, `--missing`, `--limit`, `--format`, `--verbose`. | documented |
| `db explain` | Read-only. | optional `field`, `--verbose`. | documented |

### Reports And Export

| Command | Safety/write behavior | Notable flags | Docs coverage |
| --- | --- | --- | --- |
| `report missing` | Read-only. | `field`, `--field`, `--fields`, `--albums`, `--tracks`, `--library`, `--format`, `--verbose`. | documented |
| `report duplicates` | Read-only. | optional `path`, `--tracks`, `--albums`, `--by`, `--strategy`, `--format`, `--verbose`. | documented |
| `report untracked` | Read-only. | optional `path`, `--library`, `--format`, `--verbose`. | documented |
| `report missing-files` | Read-only. | `--format`, `--verbose`. | documented |
| `export` | Reads library + writes reports/files when `--output` is used. | report selectors, output format, `--force`, scope and include/exclude flags. | documented |

### Smart Playlists

| Command | Safety/write behavior | Notable flags | Docs coverage |
| --- | --- | --- | --- |
| `playlist smart create` | Writes Noqlen DB/state only with `--apply`; otherwise plans definition. | `--query`, default format, sort, reverse, limit, path mode, library root, text/json output, `--apply`, `--force`. | partially documented; site gap |
| `playlist smart list` | Read-only. | `--format`, `--force`, `--verbose`. | partially documented |
| `playlist smart show` | Read-only. | `--format`, `--force`, `--verbose`. | partially documented |
| `playlist smart export` | Reads library + writes reports/files when `--output` is used. | path mode, library root, playlist format, `--output`, `--force`. | partially documented; site gap |
| `playlist smart refresh` | Recalculates and writes an explicit playlist output file when requested. | path mode, library root, playlist format, `--output`, `--force`. | underdocumented; site gap |
| `playlist smart delete` | Writes Noqlen DB/state only with `--apply`. | `--format`, `--apply`, `--force`, `--verbose`. | underdocumented |
| `playlist smart rename` | Writes Noqlen DB/state only with `--apply`. | `--format`, `--apply`, `--force`, `--verbose`. | underdocumented |

### Navidrome

| Command | Safety/write behavior | Notable flags | Docs coverage |
| --- | --- | --- | --- |
| `navidrome ping` | Read-only external API check. | None. | documented |
| `navidrome ratings backup` | Reads external service; writes local backup state only with `--apply`; can write explicit output file. | `--apply`, `--output`, `--format`, `--include-all`. | partially documented; site gap |
| `navidrome ratings status` | Read-only local backup status. | None. | partially documented |
| `navidrome ratings diff` | Read-only; may call API with `--server`; can write explicit output file. | `--server`, `--backup-only`, `--format`, `--output`, `--verbose`. | partially documented; site gap |
| `navidrome ratings export` | Reads local backup and writes explicit output file. | `--format`, required `--output`. | partially documented |
| `navidrome ratings restore` | External service/server write only with `--apply`; can write explicit report output. | `--apply`, ratings/starred/all selectors, match/confidence flags, `--preserve-server`, output flags. | partially documented; site gap; needs safety clarification |
| `navidrome playlists list` | Read-only external API call; can write explicit output file. | `--format`, `--output`, `--verbose`. | partially documented; site gap |
| `navidrome playlists backup` | Reads external service; writes local backup state only with `--apply`; can write explicit output file. | `--apply`, `--playlist-id`, `--name`, `--format`, `--output`, `--verbose`. | partially documented; site gap |
| `navidrome playlists status` | Read-only local backup status. | None. | partially documented |
| `navidrome playlists export` | Reads local backup and writes explicit output file. | `--format`, required `--output`. | partially documented |
| `navidrome playlists push` | External service/server write only with `--apply`; can write explicit plan/output file. | query, `--name` or `--playlist-id`, `--apply`, replace/append/preserve modes, confidence, sort/limit/path flags. | underdocumented; site gap; needs safety clarification |
| `navidrome playlists diff` | Read-only external comparison; can write explicit output file. | query, `--name` or `--playlist-id`, replace/append/preserve modes, confidence, sort/limit/path flags. | underdocumented; site gap |
| `navidrome playlists push-smart` | External service/server write only with `--apply`; can write explicit plan/output file. | name, `--apply`, replace/append/preserve modes, confidence, output flags, `--force`. | underdocumented; site gap; needs safety clarification |

### Maintenance And Review

| Command | Safety/write behavior | Notable flags | Docs coverage |
| --- | --- | --- | --- |
| `maintain sync` | Writes Noqlen DB/state or tags/files only with `--apply`; direction flags choose target. | `--tags-to-db`, `--db-to-tags`, `--refresh`, `--apply`, `--force`, field flags, conflict policy. | partially documented |
| `maintain repair` | Dry-run by default; writes Noqlen DB/state only with `--apply`; child action comes through positional args. | `repair_args`, `--apply`, `--verbose`. | partially documented; help sparse |
| `maintain rewrite` | Writes Noqlen DB/state or tags/files only with `--apply`; target controlled by `--db-only`/`--tags-only`. | `--apply`, field flags, `--db-only`, `--tags-only`, `--force`, `--verbose`. | partially documented |
| `review` | Read-only list/show and writes Noqlen DB/state for resolve with `--apply`; command shape is positional. | `review_args`, `--format`, `--action`, `--value`, `--field`, `--apply`, `--force`. | documented; help sparse |

### Jobs

| Command | Safety/write behavior | Notable flags | Docs coverage |
| --- | --- | --- | --- |
| `jobs list` | Read-only. | `--format`, `--limit`, `--status`, `--verbose`. | partially documented |
| `jobs status` | Read-only. | `job_id`, `--format`, `--verbose`. | partially documented |
| `jobs show` | Read-only. | `job_id`, `--format`, `--verbose`. | partially documented |
| `jobs cancel` | Writes Noqlen job state. | `job_id`, `--format`. | underdocumented; needs safety clarification |
| `jobs resume` | Writes Noqlen job state and may continue resumable workflow behavior. | `job_id`, `--format`. | underdocumented; needs safety clarification |
| `jobs prune` | Writes Noqlen job state only with `--apply`. | `--apply`, `--format`, `--limit`, `--verbose`. | partially documented; help sparse |

### Developer And MusicLab

| Command | Safety/write behavior | Notable flags | Docs coverage |
| --- | --- | --- | --- |
| `dev check` | Developer/test-only validation. | `--smoke`, `--quick`, `--full`, test-mode flags, `--changed`, `--area`, `--timing`. | documented for contributors |
| `dev affected` | Developer/test-only check suggestion. | optional paths. | underdocumented |
| `dev lab create` | Developer/test-only; creates isolated MusicLab fixture library. | `--path`. | documented for contributors |
| `dev lab list` | Developer/test-only; read-only scenario listing. | None. | documented for contributors |
| `dev lab run` | Developer/test-only; runs isolated MusicLab validation. | `--path`, `--live-providers`, `--timing`, `--quick`, `--full`, scenario/area/tag filters. | documented for contributors |
| `dev lab reset` | Developer/test-only; deletes MusicLab fixture after marker verification. | `--path`. | documented for contributors |
| `dev lab doctor` | Developer/test-only diagnostics. | `--path`. | documented for contributors |

## Compatibility Aliases

These aliases increase discovery noise in top-level help but remain part of the current public surface. This inventory records them without proposing removal.

| Alias | Canonical command | Safety/write behavior | Docs coverage |
| --- | --- | --- | --- |
| `sync` | `maintain sync` | Compatibility alias; same dry-run/apply behavior as `maintain sync`. | underdocumented as alias |
| `duplicates` | `report duplicates` | Compatibility alias; read-only. | underdocumented as alias |
| `missing` | `report missing` | Compatibility alias; read-only. | underdocumented as alias |
| `untracked` | `report untracked` | Compatibility alias; read-only. | underdocumented as alias |
| `missing-files` | `report missing-files` | Compatibility alias; read-only. | underdocumented as alias |

## Developer/Contributor Commands

The `dev` tree is intentionally visible as contributor tooling but should remain clearly separated from normal user workflows. `dev check --smoke` is the lightweight smoke command, while `dev lab` commands operate on isolated MusicLab fixtures and must not target a real user library.

The `dev lab` tree includes `create`, `list`, `run`, `reset`, and `doctor`. The `run` command supports quick/full modes plus scenario, area, tag, timing, optional provider, and path controls.

## Sparse-Help Follow-Ups

- Top-level help remains long because it includes workflow commands, focused tools, aliases, and contributor commands in one command list.
- Compatibility aliases increase discovery noise and should be accounted for in future help simplification without breaking compatibility.
- `metadata`, `batch`, `cleanup`, `analyze`, `set-style`, `candidates`, `apply-mbid`, `fields`, and `jobs` need better docs/help review.
- `review`, `maintain repair`, and several advanced workflows use positional passthrough shapes that are harder to discover from help alone.
- Dry-run flag behavior needs clearer documentation across commands that expose both `--apply` and `--dry-run`.
- Commands with optional explicit output files should consistently state whether they write only the requested output file or also update local state.

## Public Site Promotion Candidates

- Promote this inventory into public reference coverage after it has been reviewed against source-level behavior and release priorities.
- Expand public docs for Navidrome ratings restore and playlist push/diff/push-smart workflows.
- Expand public docs for smart playlist create/export/refresh/delete/rename behavior and storage/output boundaries.
- Add focused public reference pages for metadata provider workflows, lyrics, cover art, audio analysis, ReplayGain, and MusicBrainz candidate/MBID flows.
- Add a compact public safety matrix covering read-only, local output files, Noqlen DB/state writes, tag/file writes, and external service writes.

## Deferred CLI Usability Follow-Ups

- Simplify future CLI help without removing current compatibility aliases in this documentation-only commit.
- Separate common user workflows from advanced/focused tools in top-level help while preserving command compatibility.
- Clarify `--apply`, `--dry-run`, `--force`, output, and review behavior consistently in command help.
- Consider deeper nested help examples for Navidrome, playlists, jobs, metadata, batch, cleanup, analysis, and MBID workflows in a later runtime/tested change.
