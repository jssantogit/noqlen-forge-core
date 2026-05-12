# Noqlen Forge Core

Noqlen Forge Core is the metadata and library-management core of the Noqlen ecosystem. The public CLI command is `noqlen-forge`; the Python package/import path is `noqlen_forge`. Default config, data, cache, and MusicLab paths use `noqlen-forge` naming. SQLite schemas, migrations, and persisted identifiers are reserved for a separate migration-safe audit.

Dry-run is the default for write-capable library workflows. Commands that write tags, update library data, copy or move files, or write to external API state require explicit review and `--apply` where the workflow supports applying changes.

## Native Flow

Noqlen Forge Core uses native services and providers for library scans, metadata enrichment, MusicBrainz identity, lyrics, covers, ReplayGain, audio features, smart playlists, Navidrome backup/restore/push safety flows, review, rewrite, sync, repair, reports, and MusicLab validation.

Earlier versions grew from a smaller metadata workflow/script and experimented with or connected to external tools such as OneTagger and TuneUp. Essentia was important during early audio-analysis and key-detection exploration, and ideas common in established music-library managers, including beets, helped inform database and library-management design. The current implementation provides native Noqlen Forge Core workflows for enrichment, import/organize, SQLite database work, playlists, Navidrome flows, and MusicLab validation rather than acting as a wrapper around those historical tools.

Optional technical backends such as `ffmpeg`/`ffprobe` and `fpcalc`/Chromaprint are used only behind native workflows that need bounded local audio decoding or fingerprinting. Essentia is not a dependency or supported backend. Low-confidence key estimates are not written automatically.

## Technical Lineage And Integrations

Navidrome remains a first-class integration target for ratings, playlists, backup, diff, restore, status, export, push workflows, and local server/library usage. Other provider and API names appear in the documentation to describe configured compatibility, identity, catalog, lyrics, cover, audio-analysis, or fingerprinting integration points.

Mentions of beets, OneTagger, TuneUp, Essentia, MusicBrainz, Discogs, iTunes/Apple, Deezer, AcoustID/Chromaprint, LRCLIB, Navidrome, or other referenced providers/services are historical, technical, or integration references only. Noqlen Forge Core is not officially affiliated with those projects or services unless explicitly stated, and integrations depend on user configuration and provider availability.

Recommended first pass:

```bash
noqlen-forge db init
noqlen-forge db scan "$LIBRARY"
noqlen-forge audit "$ALBUM"
noqlen-forge enrich "$ALBUM" --full
```

Apply only after reviewing the plan/output:

```bash
noqlen-forge db scan "$LIBRARY" --apply
noqlen-forge enrich "$ALBUM" --full --apply
noqlen-forge organize "$ALBUM" --library "$LIBRARY"
```

## Documentation

Start with the documentation index: [docs/README.md](docs/README.md).

User guides:

- [Getting started and real-world usage](docs/usage/real-world-guide.md)
- [Configuration guide](docs/usage/configuration-guide.md)
- [Native flow overview](docs/usage/native-flow.md)
- [Naming and migration guide](docs/usage/naming-and-migration.md)
- [Manual real-library dry-run checklist](docs/usage/manual-real-library-checklist.md)

Developer and reference docs:

- [Developer docs](docs/development/)
- [Testing and MusicLab](docs/development/testing-and-musiclab.md)
- [CLI reference](docs/reference/cli-reference.md)
- [Audio key detection reference](docs/reference/audio-key-detection.md)
- [Technical lineage and integrations](docs/reference/integrations-and-lineage.md)

## Install For Local Use

```bash
python3 -m pip install -e .
```

Create a default config with:

```bash
noqlen-forge config init
```

Keep real credentials in environment variables, not in config files or docs.

## Development Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

If the console script is not installed, run commands through the current package module:

```bash
python3 -m noqlen_forge.cli audit /path/to/album
```

Developer workflow, service architecture, Core API guidance, structured results, and validation policy live in [docs/development](docs/development/).
