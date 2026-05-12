# Installation

Noqlen Forge Core is available from source and GitHub. PyPI installation is not available yet.

## Source Install

```bash
git clone https://github.com/jssantogit/noqlen-forge-core.git
cd noqlen-forge-core
python -m pip install -e .
noqlen-forge --help
```

Use this path when you want an editable checkout for local development, documentation work, or issue reproduction.

## GitHub Install

```bash
python -m pip install "git+https://github.com/jssantogit/noqlen-forge-core.git"
noqlen-forge --help
```

This installs from the public GitHub repository without creating an editable working tree.

## PyPI Status

PyPI publishing is future work. Do not use `python -m pip install noqlen-forge` unless a future release explicitly announces package availability there.
