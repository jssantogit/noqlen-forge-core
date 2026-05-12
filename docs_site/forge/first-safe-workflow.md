# First Safe Workflow

Begin with commands that do not require a real music library.

```bash
noqlen-forge --help
noqlen-forge dev check --smoke
```

The smoke check is the safest first validation after installation.

## Inspect Configuration

If you are ready to create local configuration, use the built-in config commands:

```bash
noqlen-forge config path
noqlen-forge config init
noqlen-forge config show
```

Review paths and provider settings before using them with real files. Keep provider credentials out of public issues, logs, examples, and commits.

## Inspect The Local Database

Forge uses a local SQLite database for library state. Start by checking the path and initializing it:

```bash
noqlen-forge db path
noqlen-forge db init
noqlen-forge db status
```

When you are ready to scan a small test library or a carefully chosen album folder, dry-run first:

```bash
noqlen-forge db scan "$LIBRARY"
```

`db scan` without `--apply` reports what would change. With `--apply`, it writes SQLite rows only; it does not rewrite tags or move files.

## Before A Real Library

- Start with one album or a small temporary fixture, not the whole collection.
- Run dry-run output first and read destination paths, warnings, counts, and confidence.
- Avoid destructive, rewrite, restore, push, or apply workflows until you understand the plan.
- Use `noqlen-forge --help` and command-specific help to confirm the current command tree before running unfamiliar commands.
