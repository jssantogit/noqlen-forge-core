# CLI Reference

Examples use the public `noqlen-forge` command. Old pre-release public aliases are not part of the packaged command surface.

Dry-run is the default for write-capable workflows. Use `--apply` only after reviewing the plan, warnings, confidence, destination paths, and counts.

For a repository-owned inventory of the complete command surface, compatibility aliases, safety categories, and documentation follow-ups, see [CLI Command Surface Inventory](cli-inventory.md).

## Common Workflow

```bash
noqlen-forge audit "$ALBUM"
noqlen-forge enrich "$ALBUM" --full
noqlen-forge import "$INCOMING" --library "$LIBRARY"
noqlen-forge organize "$ALBUM" --library "$LIBRARY"
```

`audit` is read-only. `enrich`, `import`, and `organize` are dry-run by default and write only with `--apply`.

## Configuration

```bash
noqlen-forge config init
noqlen-forge config path
noqlen-forge config show
```

Global config paths use `noqlen-forge` naming. See [Configuration guide](../usage/configuration-guide.md).

## Database

```bash
noqlen-forge db path
noqlen-forge db init
noqlen-forge db status
noqlen-forge db scan "$LIBRARY"
noqlen-forge db scan "$LIBRARY" --apply
noqlen-forge db query 'artist:"NewJeans"'
noqlen-forge db explain "$ALBUM" style
```

`db query`, `db explain`, and `db status` are read-only. `db scan --apply` writes only SQLite library rows; it does not rewrite tags or move files.

## Reports And Export

```bash
noqlen-forge report missing lyrics
noqlen-forge report duplicates
noqlen-forge report untracked "$LIBRARY"
noqlen-forge report missing-files
noqlen-forge export 'missing:lyrics' --format json --output missing-lyrics.json
```

Reports are read-only. `export` is read-only except for creating the requested output file and should not expose full lyrics, raw fingerprints, secrets, or raw provider payloads.

## Smart Playlists

```bash
noqlen-forge playlist smart create "Favorites" --query 'rating:>=4'
noqlen-forge playlist smart create "Favorites" --query 'rating:>=4' --apply
noqlen-forge playlist smart export "Favorites" --format m3u8 --output favorites.m3u8
noqlen-forge playlist smart refresh "Favorites" --output favorites.m3u8 --force
noqlen-forge playlist smart list
noqlen-forge playlist smart show "Favorites"
```

Smart playlist definitions are saved in SQLite only with `--apply`. Export/refresh write only the requested playlist output file and do not call Navidrome APIs or alter tags, ratings, music files, or paths.

## Lyrics

```bash
noqlen-forge lyrics "$ALBUM"
noqlen-forge lyrics "$ALBUM" --apply
noqlen-forge lyrics "$ALBUM" --providers embedded,sidecar,lrclib
noqlen-forge lyrics "$ALBUM" --write-sidecar-lrc --apply
noqlen-forge lyrics providers
```

Existing valid lyrics are preserved unless explicit force behavior is requested. Conflicts should become review items instead of overwriting automatically, and output must not print full lyrics.

## Navidrome

```bash
noqlen-forge navidrome ping
noqlen-forge navidrome ratings backup
noqlen-forge navidrome ratings backup --apply
noqlen-forge navidrome ratings diff --server
noqlen-forge navidrome ratings restore
noqlen-forge navidrome ratings restore --apply
noqlen-forge navidrome playlists backup
noqlen-forge navidrome playlists backup --apply
noqlen-forge navidrome playlists push 'rating:>=4' --name "Favorites"
noqlen-forge navidrome playlists push 'rating:>=4' --name "Favorites" --apply
```

Backup and diff flows are read-oriented. Rating restore and playlist push write to Navidrome only with `--apply`, after identity matching and plan reporting. Validation must use fake/mock clients, not a real server.

## Maintenance

```bash
noqlen-forge maintain sync "$ALBUM" --tags-to-db
noqlen-forge maintain sync "$ALBUM" --db-to-tags
noqlen-forge maintain rewrite "$ALBUM"
noqlen-forge maintain rewrite "$ALBUM" --apply
noqlen-forge maintain repair missing-files
noqlen-forge maintain repair untracked "$INCOMING"
noqlen-forge maintain repair db
```

`maintain sync`, `maintain rewrite`, and `maintain repair` are dry-run by default. `maintain repair` is database/report focused in this stage and must not delete music files, move/copy files, or write tags.

## Review

```bash
noqlen-forge review "$ALBUM"
noqlen-forge review "$ALBUM" --verbose
noqlen-forge review show 1
noqlen-forge review resolve 1 --action accept
noqlen-forge review resolve 1 --action accept --apply
```

Use review flows for conflicts, ambiguous provider candidates, and unsafe overwrites. Protected identity fields require explicit force behavior before replacement.

## Jobs

```bash
noqlen-forge audit "$ALBUM" --job
noqlen-forge jobs list
noqlen-forge jobs status JOB_ID
noqlen-forge jobs status JOB_ID --format json
noqlen-forge jobs cancel JOB_ID
noqlen-forge jobs resume JOB_ID
noqlen-forge jobs prune
noqlen-forge jobs prune --apply
```

Job options and results are sanitized before storage and must not contain secrets, full lyrics, full fingerprints, or raw provider payloads.

## MusicLab And Development Commands

```bash
noqlen-forge dev check --smoke
noqlen-forge dev check --quick
noqlen-forge dev check --full
noqlen-forge dev lab reset
noqlen-forge dev lab run --quick
noqlen-forge dev lab run --full
```

Developer-only tools live under `dev` so the public help stays focused on user workflows. MusicLab uses an isolated fixture library marked with `.noqlen-forge-lab`. Automated validation must never run `--apply` against the real music library.
