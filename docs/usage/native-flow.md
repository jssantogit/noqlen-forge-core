# Native Noqlen Forge Core Flow

Examples use the public `noqlen-forge` CLI for Noqlen Forge Core. The Python package/import path is `noqlen_forge`. For naming details and migration expectations, see [Naming and migration guide](naming-and-migration.md).

Noqlen Forge Core provides native workflows for enrichment, import/organize, SQLite database work, playlists, Navidrome flows, and MusicLab validation. Earlier project history included experimentation with external workflow and analysis tools; see [Technical lineage and integrations](../reference/integrations-and-lineage.md) for the public context.

## Native Capabilities

- SQLite library database for scan, query, export, operations, provider history, jobs, and review state.
- Native metadata providers for identity, catalog fields, fallback metadata, and AcoustID Identify.
- Cover and lyrics flows with local/provider selection and review-safe writes.
- ReplayGain, BPM, Energy, Danceability, Last.fm tags, conservative Mood, and portable key detection.
- Import and organize workflows that plan copies/moves before applying.
- Smart playlists recalculated from the query engine at export time.
- Navidrome ratings and playlist backup/diff/export plus guarded restore/push flows.
- Review, rewrite, sync, repair, reports, and MusicLab validation.

## Technical Backends

Allowed technical backends are not workflow managers:

- `ffmpeg`/`ffprobe` may be used for bounded local audio decoding, fixture generation, ReplayGain, and analysis.
- `fpcalc`/Chromaprint may be used for native AcoustID fingerprinting and identification.

Historical workflow and analysis references:

- beets informed some database and library-management thinking common to music-library managers.
- OneTagger and TuneUp were part of earlier external-tool workflow experiments.
- Essentia informed early audio-analysis and key-detection exploration.

## Recommended Use

Start with the SQLite database and an album-level dry-run:

```bash
noqlen-forge db init
noqlen-forge db scan "$LIBRARY"
noqlen-forge db scan "$LIBRARY" --apply
noqlen-forge audit "$ALBUM"
noqlen-forge enrich "$ALBUM" --full
```

Apply only after reviewing the output:

```bash
noqlen-forge enrich "$ALBUM" --full --apply
noqlen-forge organize "$ALBUM" --library "$LIBRARY"
```

Export playlists and back up Navidrome human data separately:

```bash
noqlen-forge playlist smart create "Favorites" --query 'rating:>=4' --apply
noqlen-forge playlist smart export "Favorites" --format m3u8 --output favorites.m3u8
noqlen-forge navidrome ratings backup
noqlen-forge navidrome ratings backup --apply
noqlen-forge navidrome playlists backup
noqlen-forge navidrome playlists backup --apply
```

`navidrome ratings backup` and `navidrome playlists backup` read from Navidrome. Their `--apply` saves backup rows to the local Noqlen Forge Core SQLite database; it does not write to Navidrome. Navidrome restore and playlist push are separate write-capable commands and remain dry-run by default.

## What Writes

- `audit`, `db query`, reports, diffs, and dry-run command forms do not write tags, files, or external API state.
- `db scan --apply` writes only SQLite library rows.
- `enrich --apply`, `lyrics --apply`, `cover --apply`, `maintain rewrite --apply`, and `maintain sync --db-to-tags --apply` may write tags when the plan is safe.
- `organize --apply` and `import --apply` may copy or move files only after planning and safety checks.
- `playlist smart export` writes only the requested playlist output file.
- Navidrome restore/push writes to the Navidrome API only with explicit `--apply` and after identity matching and plan reporting.

## Safety Model

Dry-run is the default whenever a command could write tags, SQLite rows, files, or external API state. Use `--apply` only after reading the plan. The tool should not touch a real library without explicit confirmation.

MusicLab is the safe real-flow environment for validation. It creates a fake library marked with `.noqlen-forge-lab`, uses local/fake providers and fake Navidrome clients, and blocks dangerous validation paths. Use it before trusting a new workflow:

```bash
noqlen-forge dev lab reset
noqlen-forge dev lab run --quick
noqlen-forge dev lab run --full
```

Outputs and reports are intentionally review-oriented: they show status, warnings, planned changes, artifacts, and counts without printing secrets, full lyrics, full fingerprints, or raw provider payloads.

## Core And Mobile Readiness

Noqlen Forge Core keeps heavy logic in services and reusable core contracts. The CLI is an adapter over those services. `WorkflowResult`, structured output, and job records provide the shape needed for future Noqlen Aria integration, where the app can call the same core/services instead of reproducing command-line behavior.
