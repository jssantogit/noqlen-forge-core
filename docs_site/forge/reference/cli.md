# CLI Reference

The public command is:

```bash
noqlen-forge
```

Run help for the exact current command tree:

```bash
noqlen-forge --help
```

## Safe First Checks

```bash
noqlen-forge --help
noqlen-forge dev check --smoke
```

These checks do not require a real music library.

## Command Areas

- `config` for local configuration path, initialization, and inspection.
- `db` for local database path, initialization, status, scans, queries, and explanations.
- `audit`, `enrich`, `import`, and `organize` for core metadata and library workflows.
- `cover` and `lyrics` for artwork and lyrics workflows.
- `report` and `export` for library reports and safe output files.
- `playlist` for smart playlist definitions and exports.
- `navidrome` for ratings and playlist backup, diff, restore, and push workflows.
- `maintain` and `review` for sync, rewrite, repair, and conflict review.
- `jobs` for background job status, resume, cancel, and pruning.
- `dev` for smoke checks and MusicLab validation.

Write-capable commands should be dry-run first and should require `--apply` before changing tags, files, database rows, or external server state. Use command-specific help before running unfamiliar operations.
