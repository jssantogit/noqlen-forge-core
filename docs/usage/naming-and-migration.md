# Naming and migration guide

Noqlen Forge Core is the public product and core name for this project. The public CLI command is `noqlen-forge`, and the Python package/import path is `noqlen_forge`.

Default application paths now use `noqlen-forge` naming:

- Config: `$XDG_CONFIG_HOME/noqlen-forge/config.toml`, or `~/.config/noqlen-forge/config.toml` when `XDG_CONFIG_HOME` is not set.
- Data: `$XDG_DATA_HOME/noqlen-forge/library.db`, or `~/.local/share/noqlen-forge/library.db` when `XDG_DATA_HOME` is not set.
- Cache: `~/.cache/noqlen-forge/...` for provider caches.
- MusicLab: `~/MusicLab/noqlen-forge-lab`, marked with `.noqlen-forge-lab`.

No automatic data migration is performed. Existing pre-release local directories are not renamed, deleted, or modified by Noqlen Forge Core. Developers preparing for the public release should create fresh config/data/cache paths using the final defaults.

## Product names

- `Noqlen` is the broader ecosystem name.
- `Noqlen Forge Core` is this metadata and library-management core.
- `noqlen-forge` is the public CLI command.
- `noqlen_forge` is the Python package/import path.

## What changed

- Default config, data, cache, MusicLab, user-agent, and client-name values use Noqlen Forge naming.
- Public docs and examples use `Noqlen`, `Noqlen Forge Core`, `noqlen-forge`, and `noqlen_forge`.
- Runtime secret and validation environment variables use `NOQLEN_FORGE_*` names where they are specific to Noqlen Forge Core.

## What did not change

- SQLite database schemas, migration names, column names, provider IDs, operation IDs, and persisted identifiers are not migrated in this step.
- Existing local config/data/cache directories are not read as compatibility aliases or renamed automatically.
- No music files, tags, real libraries, or external API state are modified by this naming change.

## Recommended usage

Use `noqlen-forge` for interactive commands and examples:

```bash
noqlen-forge db init
noqlen-forge db scan "$LIBRARY"
noqlen-forge audit "$ALBUM"
noqlen-forge enrich "$ALBUM" --full
```

For direct module invocation, use `noqlen_forge`:

```bash
python -m noqlen_forge.cli audit "$ALBUM"
python -m noqlen_forge.cli db status
```

## Future migration policy

Any future migration of database schemas, migration names, operation identifiers, provider identifiers, or persisted IDs must be explicit, tested, backed up, and migration-safe.

No destructive rename should happen automatically. Any future persisted-data migration must provide dry-run output and migration reports before applying changes, and it must preserve backups or rollback guidance appropriate to the affected data.
