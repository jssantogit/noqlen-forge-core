# Noqlen Forge Core Documentation

This documentation is split by audience so users can start safely without reading contributor internals, while developers can still find architecture and validation guidance.

Live documentation site: https://jssantogit.github.io/noqlen-forge-core/

The published site is built from `docs_site/`. This `docs/` directory keeps repository-maintained usage, reference, development, and release-planning notes without duplicating the full published site.

## User Docs

- [Public roadmap](project/roadmap.md): current Forge focus, later Noqlen ecosystem direction, and safety principles.
- [Installation from source](../README.md#installation-from-source): source install, PyPI availability note, and safe smoke validation.
- [Real-world usage guide](usage/real-world-guide.md): safe dry-run-first workflows for a local music library.
- [Configuration guide](usage/configuration-guide.md): config locations, safe defaults, providers, Navidrome, playlists, jobs, and troubleshooting.
- [Native flow overview](usage/native-flow.md): native Noqlen Forge Core capabilities, safety model, and historical workflow context.
- [Technical lineage and integrations](reference/integrations-and-lineage.md): external-tool history, current integration targets, and non-affiliation notes.
- [Manual real-library dry-run checklist](usage/manual-real-library-checklist.md): manual checklist before applying changes to real files or external API state.
- [Naming and migration guide](usage/naming-and-migration.md): how Noqlen naming, default paths, and persisted identifiers are handled before release.

## Developer Docs

- [Contributing](../CONTRIBUTING.md): contribution scope, safe validation, and pull request expectations.
- [Support](../SUPPORT.md): how to report issues without exposing private data.
- [Services](development/services.md): service boundaries, thin CLI adapters, safety, serialization, and testing expectations.
- [Core API](development/core-api.md): reusable Core API contracts for stable workflows.
- [Structured results](development/structured-results.md): workflow result schema and machine-readable output guidance.
- [Audio key detection](development/audio-key-detection.md): backend registry and native key detection implementation notes.
- [Testing and MusicLab](development/testing-and-musiclab.md): validation pyramid, docs-only checks, and isolated MusicLab rules.
- [First public release checklist](release/first-public-release-checklist.md): review checklist before public visibility or package publication planning.

## Reference Docs

- [CLI reference](reference/cli-reference.md): command groups and safety notes.
- [Audio key detection config reference](reference/audio-key-detection.md): key detection configuration fields and backend meanings.
- [Technical lineage and integrations](reference/integrations-and-lineage.md): public context for historical tool references and provider/API names.

## Naming And Migration Notes

Use `noqlen-forge` for examples and manual use. The Python package/import path is `noqlen_forge`; default config, data, cache, and MusicLab paths use `noqlen-forge` naming. DB schemas, migrations, provider IDs, operation IDs, and persisted identifiers are handled separately. See [Naming and migration guide](usage/naming-and-migration.md) for details.

## Safety Model

Noqlen Forge Core is dry-run-first. Write-capable flows require `--apply`, path validation, explicit reports/plans, and narrow scope. Automated validation must use MusicLab or fakes, never the real music library. Docs, logs, JSON, reports, and committed files must not contain secrets, full lyrics, raw fingerprints, or raw provider payloads.
