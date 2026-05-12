# Real-world usage guide

This guide is for using Noqlen Forge Core on a real local music library. Examples use the public `noqlen-forge` CLI. The guide focuses on safe, practical workflows: inspect first, apply only after review, and work album by album until you trust the output.

For naming details and migration expectations, see [Naming and migration guide](naming-and-migration.md). For a step-by-step dry-run-first checklist to follow before applying changes on a real library, see [Manual real-library dry-run checklist](manual-real-library-checklist.md).

Noqlen Forge Core uses its own SQLite database, native providers/services, mutagen for tags, optional `ffmpeg`/`ffprobe` for bounded local audio analysis, and optional `fpcalc`/Chromaprint for native AcoustID identification. Earlier project history included external-tool workflow and analysis experiments; see [Technical lineage and integrations](../reference/integrations-and-lineage.md) for context.

## Before you start

Use a real shell variable for your library path so every command is explicit:

```bash
export LIBRARY="/path/to/Music Library"
export ALBUM="$LIBRARY/Artist/Album"
export INCOMING="/path/to/Incoming"
```

Start with one small album. Do not begin by applying changes to the whole library.

Make a backup when possible. At minimum, keep a backup of important tags, covers, playlists, Navidrome ratings, and the Noqlen Forge Core SQLite database before large changes.

Validate paths before running write-capable commands. Be especially careful with paths that contain spaces, symlinks, mounted phone storage, network mounts, or old library roots.

Avoid suspicious symlinks. If you are not sure where a symlink points, do not run organize/import/apply flows through it.

Do not use destructive shell commands or manual SQLite edits as part of normal Noqlen Forge Core usage. Prefer `noqlen-forge` dry-runs, reports, review, rewrite, and repair commands.

## Recommended safety rules

Dry-run first. Any command that can write tags, SQLite rows, files, or external API state should be run without `--apply` first.

Read the plan. Check status, warnings, review items, destination paths, provider confidence, and counts before applying.

Apply narrowly. Prefer one album or one incoming batch instead of the full library.

Audit before and after. Run `audit` before enrichment and again after applying changes.

Treat `REVIEW` as a stop sign. Resolve conflicts with `noqlen-forge review` instead of forcing automatic writes.

Treat low-confidence audio analysis as advisory. Missing or low-confidence Key is usually a `WARN`, not a critical failure.

Use Navidrome restore and playlist push carefully. They write to the Navidrome API only with `--apply`, but you should run diff/backup/dry-run first.

## First-time setup

Check where config and database files live:

```bash
noqlen-forge config path
noqlen-forge db path
```

Create a default config if needed:

```bash
noqlen-forge config init
noqlen-forge config show
```

Initialize and inspect the SQLite database:

```bash
noqlen-forge db init
noqlen-forge db status
```

`config init` creates the global config file. `db init` creates the SQLite database and applies migrations. These commands do not write tags or move music files.

## Scan your library

Think about three separate layers:

- Files on disk are the audio files and folders in your library.
- Tags in files are embedded metadata such as artist, album, title, MusicBrainz IDs, lyrics, covers, ReplayGain, and Key.
- The Noqlen Forge Core SQLite database is an index and workflow store used for query, reports, provider history, review state, operations, playlists, and Navidrome backups.

Run an initial dry-run scan:

```bash
noqlen-forge db scan "$LIBRARY"
```

If the output looks correct, update the SQLite database:

```bash
noqlen-forge db scan "$LIBRARY" --apply
noqlen-forge db status
```

`db scan` reads supported audio files and their tags. Without `--apply`, it only reports what would be inserted or updated. With `--apply`, it updates the Noqlen Forge Core SQLite database. It does not fix tags, call providers, move files, or rewrite metadata in the audio files.

## Audit an album

Audit one album before enrichment:

```bash
noqlen-forge audit "$ALBUM"
noqlen-forge audit "$ALBUM" --advanced
```

Status meanings:

- `OK`: essential and optional metadata are complete and no bad fields were found.
- `WARN`: essential metadata is usable, but optional metadata is missing or optional steps skipped.
- `REVIEW`: a conflict, ambiguity, missing identity, or unsafe decision needs human review.
- `FAIL`: the command could not complete the requested workflow.

Missing Key, Style, Mood, Last.fm tags, lyrics, or covers can be warnings depending on the workflow. They are not always critical failures. Missing or inconsistent MusicBrainz identity is more important and may require review before safe enrichment.

## Enrich metadata safely

Run the complete native enrichment flow as a dry-run first:

```bash
noqlen-forge enrich "$ALBUM" --full
```

Apply only after reviewing the plan:

```bash
noqlen-forge enrich "$ALBUM" --full --apply
noqlen-forge audit "$ALBUM"
```

`enrich --full` uses native Noqlen Forge Core providers and services. It is not a wrapper around historical external workflow tools such as beets, OneTagger, TuneUp, or Essentia. Native AcoustID Identify uses `fpcalc`/Chromaprint when configured. Native key detection is optional and uses backends such as `portable_basic`, `auto`, or `disabled`.

Do not force overwrites unless you know exactly which field is being replaced. Existing valid tags are protected by default in the safety model, especially identity fields and conflict-prone content.

## Covers and lyrics

Run covers as a dry-run first:

```bash
noqlen-forge cover "$ALBUM"
noqlen-forge cover "$ALBUM" --apply
```

Run lyrics separately when you want to inspect provider behavior:

```bash
noqlen-forge lyrics "$ALBUM"
noqlen-forge lyrics "$ALBUM" --apply
noqlen-forge lyrics "$ALBUM" --providers embedded,sidecar,lrclib
noqlen-forge lyrics "$ALBUM" --prefer-synced --apply
```

Covers and lyrics are conflict-prone. Existing valid lyrics are preserved unless you explicitly force replacement. Conflicting lyrics should become `REVIEW`; the CLI should not print full lyrics in normal output. Prefer local embedded/sidecar lyrics when they are correct.

## ReplayGain and audio analysis

ReplayGain is optional and can be slow on large albums. Run it on a small album first:

```bash
noqlen-forge replaygain "$ALBUM"
noqlen-forge replaygain "$ALBUM" --apply
```

You can include ReplayGain in full enrichment explicitly:

```bash
noqlen-forge enrich "$ALBUM" --full --replaygain
noqlen-forge enrich "$ALBUM" --full --replaygain --apply
```

Other local audio features are available through `analyze`:

```bash
noqlen-forge analyze "$ALBUM" --features
noqlen-forge analyze "$ALBUM" --bpm
noqlen-forge analyze "$ALBUM" --lastfm-tags
```

Missing `ffmpeg`, `ffprobe`, `aubio`, or provider credentials may produce warnings or skips for optional analysis rather than failing the whole workflow.

## Native key detection

Key detection is native, optional, and conservative. The supported user-facing backend names are `portable_basic`, `auto`, and `disabled`.

```bash
noqlen-forge analyze "$TRACK" --key
noqlen-forge analyze "$TRACK" --key --backend portable_basic
noqlen-forge analyze "$TRACK" --key --backend auto
noqlen-forge analyze "$TRACK" --key --backend disabled
```

`portable_basic` decodes bounded audio locally, builds a simple chroma profile, and returns confidence. Low-confidence Key estimates should not be written automatically. Missing Key is normally a `WARN`, not a reason to block the whole album.

## Importing new music

Use `import` for incoming/download folders before music enters your organized library:

```bash
noqlen-forge import "$INCOMING" --library "$LIBRARY"
noqlen-forge import "$INCOMING" --library "$LIBRARY" --apply
```

The safest default import mode is copy, because it preserves the incoming source. Use move only when you have reviewed the dry-run and understand the destination plan:

```bash
noqlen-forge import "$INCOMING" --library "$LIBRARY" --move --apply
```

Import may audit, enrich, run cover/lyrics based on config, optionally run ReplayGain, organize files, and update SQLite. Do not run import against a huge incoming folder the first time. Test one album or one small batch.

## Organizing files

Use `organize` to plan library paths, copies, moves, and renames:

```bash
noqlen-forge organize "$ALBUM" --library "$LIBRARY"
noqlen-forge organize "$ALBUM" --library "$LIBRARY" --apply
```

Use move only after reviewing every destination path:

```bash
noqlen-forge organize "$ALBUM" --library "$LIBRARY" --move --apply
```

Do not manually move or delete files while an import/organize workflow is in progress. If destination conflicts appear, let Noqlen Forge Core return `REVIEW` or skip/rename according to the configured policy.

## Playlists

Smart playlists are saved query definitions in SQLite. They are recalculated when exported and do not depend on Navidrome.

Create and export a static playlist file from a saved smart playlist:

```bash
noqlen-forge playlist smart create "Prog Metal Favorites" --query 'style:"Progressive Metal" rating:>=4' --apply
noqlen-forge playlist smart export "Prog Metal Favorites" --format m3u8 --output prog-metal.m3u8
noqlen-forge playlist smart list
noqlen-forge playlist smart show "Prog Metal Favorites"
```

Common export formats are M3U, M3U8, JSON, and CSV. Use path mode options when needed for a specific player, such as absolute, relative, or library-root-relative paths.

Playlist export writes only the requested output file. It should not write tags, alter ratings, move files, call Navidrome, or call providers.

## Navidrome ratings and playlists backup

Configure Navidrome with environment variables for secrets when possible. Do not commit passwords, tokens, or salts.

```bash
export NOQLEN_FORGE_NAVIDROME_PASSWORD="your-password"
noqlen-forge navidrome ping
```

Back up ratings and favorites:

```bash
noqlen-forge navidrome ratings backup
noqlen-forge navidrome ratings backup --apply
noqlen-forge navidrome ratings diff --server
noqlen-forge navidrome ratings export --format json --output navidrome-ratings.json
```

Back up playlists:

```bash
noqlen-forge navidrome playlists backup
noqlen-forge navidrome playlists backup --apply
noqlen-forge navidrome playlists export --format json --output navidrome-playlists.json
```

`ratings backup`, `ratings diff`, playlist backup, and exports are read-oriented with respect to Navidrome. Backup commands require `--apply` only to save backup rows into the local Noqlen Forge Core SQLite database.

Restore ratings only after diffing and reading the plan:

```bash
noqlen-forge navidrome ratings restore
noqlen-forge navidrome ratings restore --apply
```

Push playlists only after a dry-run:

```bash
noqlen-forge navidrome playlists list
noqlen-forge navidrome playlists push 'rating:>=4' --name "Favorites"
noqlen-forge navidrome playlists push 'rating:>=4' --name "Favorites" --apply
noqlen-forge navidrome playlists push-smart "Prog Metal Favorites" --apply
```

Restore and push write to the Navidrome API. They do not write tags or move files, but they can change server ratings, stars, or playlists, so treat them as real external writes.

## Review, rewrite and repair

Use review for conflicts and ambiguous provider decisions:

```bash
noqlen-forge review "$ALBUM"
noqlen-forge review "$ALBUM" --verbose
noqlen-forge review show 1
noqlen-forge review resolve 1 --action accept
noqlen-forge review resolve 1 --action accept --apply
```

Use rewrite for controlled, configured metadata standardization:

```bash
noqlen-forge maintain rewrite "$ALBUM"
noqlen-forge maintain rewrite "$ALBUM" --apply
noqlen-forge maintain rewrite "$ALBUM" --field style --apply
```

Use repair for conservative SQLite maintenance:

```bash
noqlen-forge maintain repair missing-files
noqlen-forge maintain repair missing-files --apply
noqlen-forge maintain repair untracked "$INCOMING"
noqlen-forge maintain repair db
noqlen-forge maintain repair db --apply
```

`maintain repair` is database/report focused. In this stage it should not delete music files, move/copy files, or write tags. `maintain repair duplicates` is report/review only and should not choose a winner automatically.

## Reports and exports

Use query and reports to decide what to fix next:

```bash
noqlen-forge db query 'missing:lyrics'
noqlen-forge db query 'genre:K-pop has:cover missing:key'
noqlen-forge query 'artist:"Ne Obliviscaris" missing:lyrics'

noqlen-forge report missing lyrics
noqlen-forge report missing cover
noqlen-forge report missing replaygain
noqlen-forge report missing-files
noqlen-forge report duplicates
```

Export library data for backup or inspection:

```bash
noqlen-forge export 'artist:"NewJeans"' --format csv --output newjeans.csv
noqlen-forge export 'missing:lyrics' --format json --output missing-lyrics.json
noqlen-forge export --duplicates --format json --output duplicates.json
noqlen-forge export --reviews --format json --output reviews.json
noqlen-forge export --library --format json --output library-backup.json
```

Reports and exports are read-only except for creating the requested output file. They should not alter tags, SQLite rows, files, providers, network state, or expose full lyrics, full fingerprints, secrets, or raw provider payloads.

## Suggested workflows

First test with a small album:

```bash
noqlen-forge db scan "$ALBUM"
noqlen-forge db scan "$ALBUM" --apply
noqlen-forge audit "$ALBUM"
noqlen-forge enrich "$ALBUM" --full
noqlen-forge enrich "$ALBUM" --full --apply
noqlen-forge audit "$ALBUM"
```

Import a new album:

```bash
noqlen-forge import "$INCOMING" --library "$LIBRARY"
noqlen-forge import "$INCOMING" --library "$LIBRARY" --apply
noqlen-forge audit "$ALBUM"
noqlen-forge enrich "$ALBUM" --full
noqlen-forge organize "$ALBUM" --library "$LIBRARY"
```

Back up Navidrome ratings:

```bash
noqlen-forge navidrome ping
noqlen-forge navidrome ratings backup
noqlen-forge navidrome ratings backup --apply
noqlen-forge navidrome ratings export --format json --output navidrome-ratings.json
```

Fix a library gradually:

```bash
noqlen-forge db query 'missing:lyrics'
noqlen-forge report missing --fields lyrics,cover,key
noqlen-forge audit "$ALBUM"
noqlen-forge enrich "$ALBUM" --full
noqlen-forge review "$ALBUM"
noqlen-forge maintain rewrite "$ALBUM"
noqlen-forge maintain repair db
```

## What not to do

Do not run `--apply` across the whole real library without a dry-run and review.

Do not use real library paths in automated tests or validation. Use MusicLab for validation.

Do not trust low-confidence Key estimates blindly. Treat them as hints.

Do not restore Navidrome ratings without a backup and diff.

Do not push or replace Navidrome playlists without a dry-run.

Do not overwrite lyrics or covers when the output shows a conflict unless you have manually reviewed the candidates.

Do not manually delete, move, or rename files while import/organize is running.

Do not edit the SQLite database manually unless you have a backup and understand the schema.

Do not paste secrets into config examples, reports, issue logs, or committed files.

## Troubleshooting

`ffmpeg` or `ffprobe` not found: local audio analysis, ReplayGain, fixture generation, or decoding-backed features may skip or warn. Install the missing binary or disable the optional feature.

`fpcalc` not found: native AcoustID fingerprinting/identify cannot run. Install Chromaprint/fpcalc or skip AcoustID-dependent matching.

Key missing: this is usually a warning. Enable key detection explicitly and use `portable_basic` if you want local native key analysis.

Lyrics missing: check provider configuration, internet availability for online providers, local embedded/sidecar files, and whether the album has instrumental or placeholder lyrics.

Cover conflict: inspect the dry-run output and use review/force only when you know the selected image is correct.

Navidrome auth error: verify base URL, username, password/token/salt, auth mode, TLS settings, and environment variables. Do not print secrets into logs.

Unmatched ratings or playlists: run `noqlen-forge db scan "$LIBRARY" --apply`, check MusicBrainz IDs and paths, then rerun the Navidrome diff/backup.

Wrong database path: run `noqlen-forge db path`, check `[database].path`, `XDG_DATA_HOME`, and the active user account.

Output is `WARN` but not `FAIL`: optional metadata may be missing or skipped. Read the warnings before deciding whether action is needed.

Files are missing or moved: run `noqlen-forge report missing-files`, `noqlen-forge report untracked "$LIBRARY"`, and then use `maintain repair` dry-run before applying any database maintenance.

Large output or confusing plans: narrow the target to one album, use JSON output where supported, and save reports under a temporary or backup directory rather than inside the real library.
