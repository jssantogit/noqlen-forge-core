# Public Repository Surface Audit

Audit date: 2026-05-13

Status: Pass, with acceptable findings and follow-up tasks recorded below.

## Scope Reviewed

- `README.md`
- `LICENSE`
- `pyproject.toml`
- `docs/**`
- `docs_site/**`
- `.github/workflows/**`
- `.github/ISSUE_TEMPLATE/**`
- `config.example.toml`
- `scripts/**`
- `tests/**`
- `noqlen_forge/**`
- `mkdocs.yml`
- `CONTRIBUTING.md`
- `SUPPORT.md`

`AGENTS.md` is not present in the public checkout.

## Commands And Searches Run

```bash
rg -n '/mnt/sdcard|Biblioteca de Musicas|Biblioteca de Músicas|/storage/emulated|/sdcard|/Users/|C:\\Users|/home/[^ /]+|password|passwd|secret|token|api[_-]?key|fingerprint|lyrics|private repository|keep.*private|gh-pages|mkdocs gh-deploy' . || true
find . -path ./.git -prune -o -path ./site -print
git ls-files site docs_site/site public/site 2>/dev/null || true
git ls-files '*__pycache__*' '*.pyc'
git grep -n -E 'Sonivra|sonivra|sonivra-meta|MusicMeta|musicmeta|\.musicmeta|private repository|keep.*private|mkdocs gh-deploy|gh-pages' -- . || true
git grep -n -E '/mnt/sdcard|Biblioteca de Musicas|Biblioteca de Músicas|/storage/emulated|/sdcard|/Users/|C:\\Users|/home/[^ /]+' -- README.md docs docs_site .github pyproject.toml config.example.toml noqlen_forge tests scripts || true
git grep -n -E 'AKIA[0-9A-Z]{16}|-----BEGIN (RSA |OPENSSH |DSA |EC |PGP )?PRIVATE KEY-----|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]+|xox[baprs]-[A-Za-z0-9-]+' -- . || true
git grep -n -E 'full lyrics|these are full lyrics|fingerprint[[:space:]]*=|fingerprint:|acoustid_fingerprint|lyrichash|super-secret|token=secret' -- README.md docs docs_site .github pyproject.toml config.example.toml noqlen_forge tests scripts || true
test -f CONTRIBUTING.md && test -f SUPPORT.md && test -f docs/README.md && test -f docs/usage/real-world-guide.md && test -f docs/usage/configuration-guide.md && test -f docs/project/roadmap.md && test -f docs/release/first-public-release-checklist.md && test -f mkdocs.yml && test -d docs_site && test -d .github/workflows
```

The first `rg` search was manually reviewed. It produced many expected matches for `lyrics`, `fingerprint`, `token`, `secret`, and `password` because these terms are part of the product safety model, config placeholders, and redaction tests.

## Category Results

| Category | Status | Result |
| --- | --- | --- |
| License and README consistency | Pass | `LICENSE` is present at the repository root, uses MIT text, and `README.md` links to it. README describes the early public release, source install path, live docs site, safety model, and PyPI-not-yet-available posture. |
| Package metadata consistency | Pass | `pyproject.toml` package name, CLI entrypoint, description, Python version, classifiers, and MIT license text align with the README and current public package/import names. |
| Public docs readiness | Pass | Repository docs and `docs_site/**` are English, public-facing, source-install oriented, and dry-run-first. No private-only release posture was found. |
| Private path exposure | Pass | No personal paths, Windows user paths, Android storage example paths, or home-directory user paths were found. Runtime safety constants mention broad protected roots such as `/mnt`, `/media`, `/storage`, and `/sdcard`; these are generic safety controls, not private examples. |
| Secrets exposure | Pass | No real secrets, tokens, passwords, API keys, private keys, GitHub tokens, or Slack-style tokens were found. Matches are placeholder config keys, empty example values, environment variable names, safety warnings, or redaction fixtures. |
| Lyrics/fingerprint exposure | Pass | No full lyrics or raw audio fingerprints were found. Matches are feature names, safety wording, config flags, redaction tests, or short placeholder strings that intentionally assert sensitive data is hidden. |
| Generated artifacts | Pass | `site/` exists locally but is ignored and untracked. `git ls-files site docs_site/site public/site` returned no tracked generated site files. `git ls-files '*__pycache__*' '*.pyc'` returned no tracked Python cache artifacts. |
| GitHub Actions/Pages policy | Pass | GitHub Pages is built and deployed by Actions from the generated `site/` artifact through `python -m mkdocs build --strict`, `actions/upload-pages-artifact`, and `actions/deploy-pages`. No `gh-pages` branch deployment or `mkdocs gh-deploy` workflow was found. |
| Safety/dry-run documentation | Pass | README, repository docs, docs site, support guidance, contributing guidance, and issue templates consistently warn against secrets, private paths, full lyrics, raw fingerprints, and unsafe apply operations. |
| Real-library workflow safety | Pass | Public docs emphasize dry-run first, MusicLab/fake/temporary validation, and protected library roots. No real-library workflow was executed during this audit. |
| Old branding/stale wording | Pass | No old project names or obsolete branding terms searched by the audit were found. No stale wording claiming the repository is private was found. |

## Known Acceptable Findings

- `site/` is present in the local working tree as an ignored generated directory. It is not tracked and was not added.
- `lyrics`, `fingerprint`, `secret`, `token`, `password`, and `api_key` appear throughout docs, config examples, source, and tests as intentional feature names, safety warnings, placeholder keys, or redaction fixtures.
- `config.example.toml` contains empty credential fields and environment variable names such as `NOQLEN_FORGE_LYRICS_API_KEY`; these are documentation placeholders, not real secrets.
- Tests contain short fake strings such as `token=secret`, `super-secret`, `full lyrics should not leak`, and `abcdef` to verify redaction behavior. These are not real credentials, full lyrics, or raw fingerprints.
- Runtime safety code and helper tests reference broad roots such as `/mnt`, `/media`, `/storage`, and `/sdcard` as protected path classes. These are generic controls, not real personal paths.
- `.github/ISSUE_TEMPLATE/**` contains public safety warnings telling users not to paste secrets, full lyrics, raw fingerprints, private paths, or private dumps.

## Remaining Blockers

None found in the audited public surface.

## Follow-Up Tasks

- Remove or regenerate the ignored local `site/` directory before packaging or release operations if a clean working tree artifact check is desired.
- Re-run this audit before the first public tag and after any future workflow, packaging, docs-site, or release-process changes.
- Keep the first public release checklist open until CI status, repository settings, GitHub topics, branch protections, and release tagging decisions are confirmed in GitHub.

## Behavior Confirmation

This audit records repository-surface findings only. It does not change runtime behavior, site UI, CSS, assets, MkDocs overrides, workflows, package entrypoints, dependencies, generated site files, tests, or real music files.
