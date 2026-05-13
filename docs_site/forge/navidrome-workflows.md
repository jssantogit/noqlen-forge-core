# Navidrome Workflows

Noqlen Forge Core includes Navidrome/Subsonic workflows for ratings, favorites, and playlists. These workflows are server-adjacent: they can read Navidrome state, save local backup/export files, and in specific apply modes write back to Navidrome.

External-service write commands should never be the first test. Start with help, status, list, backup, and diff commands. Restore and push workflows require explicit review before any write mode.

## Mental Model

| Workflow type | What it can affect |
| --- | --- |
| Read-only server checks | Read Navidrome API state without changing server data. |
| Local backup/status | Read server state and inspect or update local Noqlen backup state. |
| Export/output | Write an explicit local output file such as JSON or CSV. |
| Diff/plan | Compare local database, local backup, and optionally server state before any write. |
| Restore/push | Write ratings, favorites, or playlist contents to Navidrome only with explicit apply intent. |

Navidrome workflows do not write music tags or move music files. They can still affect user-visible server state, so backup and diff must come before restore or push.

## Safe Order Of Operations

1. Configure Navidrome credentials privately. Do not put server URLs, tokens, passwords, usernames, or private paths in docs, logs, examples, issues, or commits.
2. Run help and read the command tree.
3. Run read-only status or list commands where appropriate.
4. Create or inspect a local backup before comparing or restoring data.
5. Inspect diff output and warning counts before considering a server write.
6. Only then consider restore or push workflows with explicit review and command-specific help.

Safe discovery commands:

```bash
noqlen-forge navidrome --help
noqlen-forge navidrome ratings backup --help
noqlen-forge navidrome ratings diff --help
noqlen-forge navidrome playlists list --help
noqlen-forge navidrome playlists diff --help
```

## Ratings Workflows

Ratings workflows cover Navidrome user ratings and favorites. They are designed around backup, status, diff, export, then optional restore after review.

| Command | Purpose | Safety/write category | When to use | Inspect first | Safe help example |
| --- | --- | --- | --- | --- | --- |
| `ratings backup` | Fetch ratings/favorites from Navidrome. | Reads server state; writes local backup state only with apply intent; can write an explicit output file. | Use before diff or restore so Forge has a local reference point. | Confirm configured server identity privately, output format/path if used, and whether you are only planning or applying backup state. | `noqlen-forge navidrome ratings backup --help` |
| `ratings status` | Show last local backup status. | Read-only local status. | Use after backup or before diff/restore to confirm what local backup exists. | Backup timestamp/counts and whether local backup state is current enough for the operation. | `noqlen-forge navidrome ratings status --help` |
| `ratings diff` | Compare saved backup with local library state and optionally current server state. | Read-only comparison; may read server state with server-read flags; can write an explicit output file. | Use before restore to understand mismatches, missing matches, and confidence. | Match counts, unmatched records, confidence, selected output format, and whether the command will call the API. | `noqlen-forge navidrome ratings diff --help` |
| `ratings export` | Export saved local ratings backup. | Reads local backup and writes an explicit output file. | Use when you need an offline copy or review artifact. | Output path, format, and whether the export contains private server/library data. | `noqlen-forge navidrome ratings export --help` |
| `ratings restore` | Restore ratings/favorites to Navidrome. | External service/server write only with explicit apply intent; can write a local plan/output file. | Use only after backup, status, and diff review. | Identity matches, confidence thresholds, preserve-server behavior, selected ratings/favorites scope, warnings, and output plan. | `noqlen-forge navidrome ratings restore --help` |

Do not start with `ratings restore`. Treat restore as a reviewed server-write workflow, not an installation test.

## Playlist Workflows

Playlist workflows cover listing server playlists, backing them up, exporting saved backups, comparing Noqlen queries to server playlists, and pushing playlist contents after review.

| Command | Purpose | Safety/write category | When to use | Inspect first | Safe help example |
| --- | --- | --- | --- | --- | --- |
| `playlists list` | List Navidrome playlists using read-only API calls. | Read-only server call; can write an explicit output file. | Use as an early server connectivity and discovery check. | Output format/path if used and whether command output may reveal private playlist names. | `noqlen-forge navidrome playlists list --help` |
| `playlists backup` | Fetch Navidrome playlist state into a local backup. | Reads server state; writes local backup state only with apply intent; can write an explicit output file. | Use before export, diff, or push workflows. | Playlist selector, output format/path, and whether you are only planning or applying backup state. | `noqlen-forge navidrome playlists backup --help` |
| `playlists status` | Show last local playlist backup status. | Read-only local status. | Use after backup or before export/diff/push. | Backup availability, counts, and freshness. | `noqlen-forge navidrome playlists status --help` |
| `playlists export` | Export saved playlist backup. | Reads local backup and writes an explicit output file. | Use when you need an offline copy or review artifact. | Output path, format, and whether the exported data contains private playlist or library details. | `noqlen-forge navidrome playlists export --help` |
| `playlists diff` | Compare a Noqlen Forge query with an existing Navidrome playlist without writing. | Read-only comparison; can write an explicit output file. | Use before any playlist push to inspect additions, removals, matching, and mode behavior. | Query, target playlist selector, replace/append/preserve mode, match confidence, output format/path, sorting, limits, and path mode. | `noqlen-forge navidrome playlists diff --help` |
| `playlists push` | Plan or push a Noqlen Forge query as a Navidrome playlist. | External service/server write only with explicit apply intent; can write a local plan/output file. | Use only after list, backup/status, and diff review. | Target playlist selector, replace/append/preserve mode, confidence, output plan, limits, sorting, path mode, and warning counts. | `noqlen-forge navidrome playlists push --help` |
| `playlists push-smart` | Plan or push a saved smart playlist to Navidrome. | External service/server write only with explicit apply intent; can write a local plan/output file. | Use only after the smart playlist has been reviewed locally and server state has been backed up or inspected. | Saved playlist name, target mode, confidence, output plan, warnings, and whether local smart playlist results match expectations. | `noqlen-forge navidrome playlists push-smart --help` |

Do not start with `playlists push` or `playlists push-smart`. Use list, backup/status, and diff first.

## Do Not Start Here

- Do not start with ratings restore.
- Do not start with playlist push or push-smart.
- Do not test against a production server without a backup and diff review.
- Do not put tokens, passwords, server URLs, usernames, or private paths in docs, logs, examples, issues, or commits.
- Do not commit backups or exports that contain private library or server data.
- Do not assume a dry-run or plan is safe to apply without reading identity matches, confidence, warnings, counts, and target playlist/rating behavior.

## Reports And Review

Use reports, status, and diff output to inspect mismatches, missing files, unsafe identity matches, and confidence before writing server state. If a workflow can produce an output file, review where it will be written and whether it contains private server or library data before sharing it.

## Automated Tests

Automated tests and validation must use fake or mocked Navidrome clients. Do not use a real Navidrome server, real credentials, real user data, or production playlist/rating state in automated tests.

## Future Anchor Context

Noqlen Anchor Core is planned as future Navidrome and local server management work. It is not required for the current Forge workflows documented here.
