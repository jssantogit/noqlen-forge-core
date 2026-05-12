# Noqlen Forge Core

[![CI](https://github.com/jssantogit/noqlen-forge-core/actions/workflows/ci.yml/badge.svg)](https://github.com/jssantogit/noqlen-forge-core/actions/workflows/ci.yml)

Noqlen Forge Core is a command-line metadata and library-management core for local music collections.

Documentation: https://jssantogit.github.io/noqlen-forge-core/

## What Is Noqlen Forge Core?

Noqlen Forge Core helps scan, audit, enrich, organize, review, and report on a music library with dry-run-first safety. It is the current public core of the Noqlen ecosystem, exposed through the `noqlen-forge` CLI and the `noqlen_forge` Python package/import path.

The README is a quick landing page for new visitors. The full documentation site includes installation, first safe workflow, safety model, feature overview, Navidrome-oriented workflows, and roadmap.

## What It Helps With

- Library scans and a local database for understanding a collection before making changes.
- Metadata audit and enrichment workflows for safer review of albums, tracks, and provider data.
- Covers and lyrics workflows that keep review and privacy concerns visible.
- ReplayGain and audio feature workflows for local analysis where configured.
- Review, repair, rewrite, and report workflows for controlled library maintenance.
- Playlists and ratings workflows for library organization and listening data.
- Navidrome-oriented backup, diff, restore, export, and push workflows.
- Safe validation through fake or temporary data and MusicLab instead of a real music library.

## Current Status

- Noqlen Forge Core is an early public release.
- The project is CLI-first today.
- Source and GitHub installation are the supported install paths for now.
- PyPI installation is not available yet.
- Real-library use should begin with dry-run output and careful review.
- Essentia is not a dependency or supported backend.
- Low-confidence key estimates are not written automatically.
- Noqlen Flux, Anchor, Core, and Aria are future ecosystem work, not current dependencies.

## Installation From Source

PyPI installation is not available yet. Install from source or GitHub for now.

```bash
git clone https://github.com/jssantogit/noqlen-forge-core.git
cd noqlen-forge-core
python -m pip install -e .
noqlen-forge --help
```

## First Safe Check

Start with commands that do not require a real music library:

```bash
noqlen-forge --help
noqlen-forge dev check --smoke
```

This is the safest first validation after installation. Save real-library workflows for the usage docs, begin with dry-run output, and apply changes only after reviewing what will happen.

## Documentation

Start with the live documentation site for installation, safe first workflow, feature overview, reference, and roadmap:

- [Live documentation site](https://jssantogit.github.io/noqlen-forge-core/)

Repository-maintained notes remain available here:

- [Documentation index](docs/README.md)
- [Usage guides](docs/usage/)
- [Real-world usage guide](docs/usage/real-world-guide.md)
- [Configuration guide](docs/usage/configuration-guide.md)
- [Public roadmap](docs/project/roadmap.md)
- [First public release checklist](docs/release/first-public-release-checklist.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)

Local MkDocs site builds are available for documentation work:

```bash
python -m pip install -e ".[docs]"
python -m mkdocs build --strict
```

## Safety Model

- Use dry-run before apply for real-library operations.
- Do not make destructive changes without explicit confirmation.
- Review reports and plans before writing tags, files, database changes, or external API state.
- Validate paths and avoid unsafe symlink or path traversal behavior.
- Do not include secrets, full lyrics, raw fingerprints, private library dumps, or real local paths in issues, logs, examples, or commits.

## Roadmap Snapshot

- Noqlen Forge Core is the current focus for local music-library metadata and management workflows.
- Noqlen Flux Core is planned for downloads and import workflows.
- Noqlen Anchor Core is planned for Navidrome and local server management.
- Noqlen Aria is a future player and app experience.
- Noqlen Core may emerge as shared base components mature.

## Contributing And Support

Contributions should stay safe, reviewable, and privacy-conscious. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request, and use [SUPPORT.md](SUPPORT.md) for issue-reporting guidance that avoids leaking private library details.

## License

See [LICENSE](LICENSE) for license information.
