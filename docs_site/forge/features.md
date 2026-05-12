# Features

This page groups the current Forge feature areas at a high level. Use `noqlen-forge --help` for the exact current command tree.

## Library Database And Scans

Forge can maintain a local SQLite view of library files, tags, paths, provider history, review state, jobs, smart playlist definitions, and Navidrome backups. Scan operations are dry-run first and only update database rows when explicitly applied.

## Metadata Audit And Enrichment

Audit and enrichment workflows help compare existing tags with provider candidates. Conflicts, ambiguous matches, and low-confidence results should become review work instead of silent overwrites.

## Covers And Lyrics

Cover and lyrics workflows can use embedded data, sidecar files, and configured providers. Existing valid data should be preserved unless explicit overwrite behavior is requested. Public output must not expose full lyrics.

## ReplayGain And Audio Features

ReplayGain and audio feature workflows are available where configured. Expensive or optional analysis should remain deliberate, bounded, and reviewable.

## Review, Repair, Rewrite, And Reports

Review and report workflows help surface missing data, duplicates, untracked files, unsafe changes, and library drift. Repair and rewrite workflows should start as dry-runs and require careful review before apply operations.

## Playlists And Ratings

Forge supports smart playlist definitions, playlist export/refresh, and ratings-oriented workflows. File outputs and server writes should be explicit and reviewed.

## Navidrome-Oriented Workflows

Navidrome workflows focus on ratings backup/diff/restore, playlist backup/status/export/push, and reporting. Restore and push operations require explicit apply behavior.

## MusicLab And Fake Validation

Development and automated checks should use fake or temporary data, including MusicLab fixtures, rather than real music libraries. This keeps validation repeatable and privacy-conscious.
