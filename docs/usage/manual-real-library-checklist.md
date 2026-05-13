# Manual real-library dry-run checklist

## 1. Purpose

Use this checklist to validate Noqlen Forge Core on a real music library safely. The first pass is dry-run only: inspect paths, counts, confidence, reports, and Navidrome differences before any command writes tags, SQLite rows, files, or external API state.

Use the public `noqlen-forge` command for manual validation. For naming details and migration expectations, see [Naming and migration guide](naming-and-migration.md).

Do not start with the whole library. Start with one copied album or a small copied test folder.

## 2. Before touching your real library

- Back up the real music folder, or start from a copy of a small part of it.
- Back up the Noqlen Forge Core SQLite database before scans, imports, repairs, restores, or large report refreshes.
- Confirm the real library path and keep it in a shell variable such as `$LIBRARY`.
- Confirm the sample path and keep it in a shell variable such as `$SAMPLE_ALBUM`.
- Confirm incoming/staging paths before import with a variable such as `$INCOMING`.
- Avoid symlinks until path safety is verified.
- Do not manually edit the SQLite database unless you have a backup.
- Do not commit or export secrets, API keys, Navidrome passwords, salts, tokens, full lyrics, or raw provider payloads.
- Do not run commands against the real library from automation. These commands are for later manual use by the user.

Noqlen Forge Core refuses dangerous filesystem roots and broad storage roots during path-safety checks. Automated `--apply` workflows are constrained to MusicLab or fake fixtures and should not target real libraries without explicit manual review.

If you want an extra local guardrail for a copied or real library root, set `NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS` in your shell or local automation environment. This variable is optional, is not a config file setting, and must not be used to commit personal paths. It accepts a platform path-list: `:` between entries on POSIX shells and `;` between entries on Windows shells.

```bash
export NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS="/tmp/noqlen-real-library:/example/protected/music"
```

Treat generated reports and logs as potentially private. Keep them outside the library when possible, review them for paths before sharing, and do not commit reports that expose real local paths.

```bash
export LIBRARY="/path/to/Music Library"
export SAMPLE_ALBUM="/path/to/Copied Sample/Artist/Album"
export SAMPLE_TRACK="$SAMPLE_ALBUM/01 Example.flac"
export INCOMING="/path/to/Copied Incoming"
```

## 3. Prepare a safe sample

- Copy one album, or a few representative tracks, to a separate sample folder.
- Include paths with spaces if your real library has them.
- Include only files you can restore from backup.
- Do not use the entire real library for the first scan, enrich, import, organize, or Navidrome write test.
- Keep the original album untouched while validating the copied sample.

## 4. Confirm config and database paths

Check active config and database paths before scanning or exporting reports:

```bash
noqlen-forge config path
noqlen-forge db path
noqlen-forge db status
```

Review the output for unexpected paths. Stop if the database path points to an old experiment, a temporary file you need to keep, or a location you do not recognize.

## 5. Baseline reports

Create read-only baseline reports before write-capable workflows:

```bash
noqlen-forge report missing lyrics
noqlen-forge report missing --fields lyrics,cover,key
noqlen-forge report duplicates
noqlen-forge report missing-files
noqlen-forge export 'missing:lyrics' --format json --output /tmp/noqlen-forge-missing-lyrics.json
noqlen-forge export --duplicates --format json --output /tmp/noqlen-forge-duplicates.json
noqlen-forge export --reviews --format json --output /tmp/noqlen-forge-reviews.json
noqlen-forge export --library --format json --output /tmp/noqlen-forge-library-backup.json
```

Confirm output paths before exports. Prefer `/tmp` or a dedicated reports folder outside the library for first-pass validation.

## 6. DB scan dry-run

Run the scan without `--apply` first:

```bash
noqlen-forge db scan "$SAMPLE_ALBUM"
```

After the sample looks correct, you can dry-run the full library scan, but still without `--apply`:

```bash
noqlen-forge db scan "$LIBRARY"
```

Review file counts, skipped files, unexpected paths, duplicate-looking entries, and symlink warnings. Confirm the database path again before any applied scan.

## 7. Audit dry-run

Audit the copied sample before enrichment:

```bash
noqlen-forge audit "$SAMPLE_ALBUM"
noqlen-forge audit "$SAMPLE_ALBUM" --advanced
```

Look for `WARN`, `REVIEW`, and `FAIL` statuses. Missing optional data can be acceptable, but missing or inconsistent MusicBrainz identity, bad fields, and conflicts should be reviewed before apply.

## 8. Enrich dry-run

Run full enrichment as a dry-run:

```bash
noqlen-forge enrich "$SAMPLE_ALBUM" --full
```

Review provider choices, low-confidence matches, field changes, existing tag protection, cover and lyrics decisions, and any `REVIEW` state. Do not use `--apply` until the dry-run output is reviewed.

## 9. Lyrics/covers/audio analysis dry-run

Run conflict-prone or slower workflows separately on the sample:

```bash
noqlen-forge lyrics "$SAMPLE_ALBUM"
noqlen-forge cover "$SAMPLE_ALBUM"
noqlen-forge replaygain "$SAMPLE_ALBUM"
noqlen-forge analyze "$SAMPLE_TRACK" --key --backend portable_basic
```

Check for lyrics conflicts, existing lyrics mismatches, cover replacement plans, missing provider data, skipped optional tools, low-confidence Key, and unexpectedly large file counts. Full lyrics and raw provider payloads should not be printed or saved in normal reports.

## 10. Import dry-run

Use a copied incoming folder first:

```bash
noqlen-forge import "$INCOMING" --library "$LIBRARY"
```

Review every planned source path, destination path, copy/move mode, skipped file, conflict, and `REVIEW` item. Do not run import apply on the entire real incoming folder without a backup.

## 11. Organize dry-run

Use a copied album or copied folder first:

```bash
noqlen-forge organize "$SAMPLE_ALBUM" --library "$LIBRARY"
```

Review destination paths, path templates, collisions, moves, copies, skipped files, and symlink warnings. Do not run organize apply on the entire real library without a backup.

## 12. Playlists dry-run/export test

For query exports, write to a temporary output path first:

```bash
noqlen-forge export 'rating:>=4' --format m3u8 --output /tmp/favorites.m3u8
```

For saved smart playlists, use the supported smart playlist export command:

```bash
noqlen-forge playlist smart export "Favorites" --format m3u8 --output /tmp/favorites.m3u8
```

If using library path mode, confirm the library root before export:

```bash
noqlen-forge playlist smart export "Favorites" --format m3u8 --output /tmp/favorites-library.m3u8 --path-mode library --library-root "$LIBRARY"
```

Review playlist output paths and ensure they point where the target player expects. Playlist export writes only the requested output file, but the path still matters.

## 13. Navidrome ratings/playlists backup

Back up and inspect Navidrome state before restore or push planning:

```bash
noqlen-forge navidrome ratings backup
noqlen-forge navidrome ratings diff --server
noqlen-forge navidrome ratings diff --server --format json --output /tmp/navidrome-ratings-diff.json
noqlen-forge navidrome playlists backup
noqlen-forge navidrome ratings export --format json --output /tmp/navidrome-ratings.json
noqlen-forge navidrome playlists export --format json --output /tmp/navidrome-playlists.json
```

Navidrome backup commands are read-only with respect to Navidrome. Saving backup rows to the local SQLite database requires `--apply`; do not do that until you have confirmed the database path and reviewed the backup plan.

## 14. Navidrome restore/push safety

Restore and push write to the Navidrome API only with `--apply`, but start with dry-run plans:

```bash
noqlen-forge navidrome ratings restore
noqlen-forge navidrome playlists push 'rating:>=4' --name "Favorites"
noqlen-forge navidrome playlists push-smart "Favorites"
```

Before any Navidrome API write:

- Back up ratings before restore.
- Back up playlists before push or replace.
- Run diff before restore.
- Run dry-run before any API write.
- Review unmatched songs, low-confidence matches, conflicts, existing playlist policy, and target playlist names.
- Do not use replace-oriented options until the backup and dry-run are reviewed.

## 15. Review outputs

Before applying anything, check for:

- Unexpected target paths.
- Unexpected file counts.
- `WARN`, `REVIEW`, or `FAIL` statuses.
- Low-confidence metadata matches.
- Missing provider data.
- Lyrics or cover conflicts.
- Low-confidence Key estimates.
- Navidrome unmatched ratings or playlists.
- Playlist output paths.
- Skipped files.
- Moved or copied paths.
- Symlink warnings.
- Secrets or private payloads in reports before sharing or committing them.

Keep reports and terminal logs long enough to compare before/after behavior, but do not commit generated reports if they contain private library data.

## 16. When it is safe to use --apply

Apply is not part of the first dry-run checklist. Use `--apply` only after dry-run output is reviewed, backups exist, and the command target is narrow.

### Safe apply progression

1. Apply only DB scan on a small sample if needed.
2. Apply metadata enrich on copied sample files.
3. Re-audit.
4. Test import on a copied incoming folder.
5. Test organize on a copied folder.
6. Back up Navidrome ratings and playlists before API writes.
7. Only then consider real-library apply.

Apply one workflow at a time. Never combine multiple risky apply operations in one first run.

Example apply-stage commands, only after the matching dry-runs and backups are reviewed:

```bash
noqlen-forge db scan "$SAMPLE_ALBUM" --apply
noqlen-forge enrich "$SAMPLE_ALBUM" --full --apply
noqlen-forge audit "$SAMPLE_ALBUM"
noqlen-forge import "$INCOMING" --library "$LIBRARY" --apply
noqlen-forge organize "$SAMPLE_ALBUM" --library "$LIBRARY" --apply
noqlen-forge navidrome ratings backup --apply
noqlen-forge navidrome playlists backup --apply
noqlen-forge navidrome ratings restore --apply
noqlen-forge navidrome playlists push 'rating:>=4' --name "Favorites" --apply
```

Do not run organize/import/apply on the entire real library without backup.

## 17. What to never do

- Never start by applying changes to the whole real library.
- Never use `--apply` before reviewing the dry-run output.
- Never run organize or import apply on the entire real library without backup.
- Never trust a path-mode library playlist export without confirming `--library-root`.
- Never export to an unknown path or overwrite reports you still need.
- Never follow symlinks through organize/import/apply until path safety is verified.
- Never manually edit the SQLite database unless you have a backup and know exactly what you are changing.
- Never commit or publish secrets, API keys, Navidrome tokens, salts, passwords, full lyrics, fingerprints, raw provider payloads, private library paths, or generated real-library reports.
- Never rely on undo unless the specific command explicitly supports it.

## 18. Recovery notes

- Restore copied sample files from the original backup or fresh copy.
- Restore the SQLite database backup if a scan, backup save, repair, or workflow write changed local state unexpectedly.
- Use saved reports to identify which files, tags, DB rows, or Navidrome items were planned or changed.
- Do not manually delete or move files to recover unless the report makes the state clear.
- For Navidrome, restore only after a diff and human review. Prefer preserving server values unless replacement is intentional.
- If a command produced `REVIEW`, resolve it through the review workflow rather than forcing automatic writes.

## 19. Final sign-off checklist

- The first run used one copied album or a small copied folder.
- The config path was checked.
- The database path was checked.
- The library root was checked before scan, import, organize, or path-mode playlist export.
- Export output paths were checked before writing files.
- Baseline reports were saved outside the library or in a deliberate reports folder.
- DB scan dry-run was reviewed.
- Audit dry-run was reviewed.
- Enrich dry-run was reviewed.
- Lyrics, covers, ReplayGain, and Key dry-runs were reviewed if used.
- Import and organize dry-runs were reviewed before any copied-folder apply.
- Navidrome ratings were backed up before restore.
- Navidrome playlists were backed up before push or replace.
- Navidrome diff was reviewed before restore.
- `WARN`, `REVIEW`, `FAIL`, low-confidence, skipped, and unmatched items were understood.
- No secrets or private real-library artifacts were committed or shared.
- Any `--apply` was run one workflow at a time and only after backup and review.
