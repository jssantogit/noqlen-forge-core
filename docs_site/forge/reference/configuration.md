# Configuration

Forge configuration is local and user-controlled. Installing from source or GitHub does not require secrets.

## Local Files

Use the CLI to inspect the active paths:

```bash
noqlen-forge config path
noqlen-forge db path
```

Create and inspect the default config when you are ready:

```bash
noqlen-forge config init
noqlen-forge config show
```

The detailed configuration guide in the repository remains the current source for individual settings: `docs/usage/configuration-guide.md`.

## Secrets And Providers

Some optional providers or integrations may use API credentials. Do not paste tokens, passwords, API keys, provider payloads, full lyrics, or private library dumps into public issues or commits.

## Paths Before Apply

Review library roots, incoming paths, output paths, and generated destination paths before apply operations. Prefer small test folders and dry-runs while learning the tool.
