# Noqlen Forge Core

Noqlen Forge Core is a command-line metadata and library-management core for local music collections. It is the current public focus of the Noqlen ecosystem.

## Who It Is For

Forge is for users who manage local music libraries and want controlled workflows for metadata, covers, lyrics, ReplayGain, playlists, ratings, reports, and Navidrome-oriented maintenance. It favors reviewable command-line operations over hidden automation.

## What It Can Do

- Scan a library into a local SQLite database.
- Audit and enrich metadata with conservative provider handling.
- Work with covers, lyrics, ReplayGain, and audio features where configured.
- Produce reports for missing data, duplicates, untracked files, and review items.
- Support smart playlists, ratings, and Navidrome-oriented backup/diff/restore flows.
- Validate behavior with fake or temporary MusicLab data before touching a real library.

## CLI-First Means

Forge is exposed through the `noqlen-forge` command. The command line is the stable user surface for now: it makes plans, flags, paths, and apply steps explicit and scriptable.

## Why Dry-Run, Review, And Reporting Matter

Music libraries are personal, large, and hard to reconstruct. Forge treats writes as deliberate actions: inspect the plan, review warnings and confidence, then use `--apply` only when the output is understood.

## Where To Go Next

- [Install Forge](installation.md)
- [Run a first safe workflow](first-safe-workflow.md)
- [Read the safety model](safety.md)
- [Use the CLI reference](reference/cli.md)
