# Public Hardening And First Tagged Release Checklist

Use this checklist during public hardening and before the first tagged release. Keep each item reviewable from the public `jssantogit/noqlen-forge-core` repository.

## Repository Readiness

- [ ] Confirm the repository history is the clean GitHub-backed Noqlen Forge Core history intended for release review.
- [ ] Confirm CI is passing on the latest default-branch commit.
- [ ] Confirm `README.md` describes the current CLI, source install path, support status, and early public release posture accurately.
- [ ] Confirm source installation is documented and current.
- [ ] Confirm docs do not claim package registry availability before it is explicitly planned.
- [ ] Confirm `LICENSE` exists at the repository root and `README.md` links to it correctly.
- [ ] Review issues, discussions, labels, branch protections, and repository topics for the current public repository surface.

## Naming Readiness

- [ ] Confirm public docs use Noqlen naming consistently.
- [ ] Confirm CLI examples use `noqlen-forge`.
- [ ] Confirm Python import examples use `noqlen_forge`.
- [ ] Confirm distribution package references use `noqlen-forge` only where appropriate.
- [ ] Confirm no legacy project, command, package, or path names remain in public files.

## Safety Readiness

- [ ] Confirm committed files contain no secrets.
- [ ] Confirm committed files contain no personal paths or private library paths.
- [ ] Confirm docs, tests, and fixtures contain no complete lyric text.
- [ ] Confirm docs, tests, and fixtures contain no raw audio fingerprints.
- [ ] Confirm docs, logs, examples, reports, and JSON snippets do not expose secrets, full lyrics, fingerprints, raw provider payloads, or personal library paths.
- [ ] Confirm dry-run-first behavior is documented where write-capable workflows are described.
- [ ] Confirm destructive actions require explicit confirmation or an explicit apply flag.
- [ ] Confirm tests and CI use fake, fixture, or temporary data only.
- [ ] Confirm automated validation does not require or inspect a real music library.
- [ ] Confirm MusicLab, fake, fixture, or temporary validation remains documented for automated checks.
- [ ] Confirm protected library root controls are documented for real-library safety.

## Packaging Readiness

- [ ] Build a wheel locally from a clean checkout.
- [ ] Build an sdist locally from a clean checkout.
- [ ] Install the wheel in a temporary virtual environment and run CLI smoke validation.
- [ ] Confirm ignored local-only files are not packaged.
- [ ] Confirm generated build artifacts are not tracked.
- [ ] Confirm generated `site/` files are not tracked.
- [ ] Confirm real library data is not packaged.

## Public Repository Readiness

- [ ] Verify the public repository surface before tagging the first release.
- [ ] Confirm GitHub Pages deploys through Actions and does not require committed generated site files.
- [ ] Confirm generated `site/` files are ignored and remain uncommitted.
- [ ] Confirm release validation does not use `mkdocs gh-deploy`.
- [ ] Confirm release validation does not depend on a manual `gh-pages` branch.
- [ ] Confirm CI and Pages are checked after pushing release-preparation changes.
- [ ] Confirm no force-push is required for release preparation.
- [ ] Do not create the first release tag until the public surface is reviewed.
- [ ] Do not publish to PyPI until publication is explicitly planned.

## Public Hardening Status

Current pre-release hardening status:

- `LICENSE` is present at the repository root and uses MIT text.
- README and license references are consistent with the current public source-install posture.
- Public docs are aligned with current Noqlen naming: Noqlen Forge Core, `noqlen-forge`, and `noqlen_forge`.
- Legacy naming references searched during the public surface audit have been removed from tracked public files.
- A hardcoded personal library path was removed from runtime-facing public files.
- Protected library root controls are documented.
- MusicLab, fake, fixture, or temporary validation is the documented automated validation policy.
- Automated tests should not use a real music library.
- GitHub Pages deployment should go through GitHub Actions only.
- Generated `site/` files should not be committed.
- `mkdocs gh-deploy` should not be used for this repository.
- Release validation should not depend on a manual `gh-pages` deployment flow.
- CI and Pages should be checked after push before relying on release-readiness status.

Remaining before first tagged release:

- Confirm CI status on the latest pushed default-branch commit.
- Confirm GitHub Pages deployment status after the next push.
- Confirm repository settings, branch protections, topics, and release tagging decisions in GitHub.

## First Tagged Release Follow-Up

- [ ] Add or adjust GitHub topics if desired.
- [ ] Decide when to create the first version tag.
- [ ] Decide whether PyPI publication is needed.
- [ ] Update install docs after PyPI publication if it happens.

## Next Phase

- Expand public site documentation for installation, configuration, workflows, safety boundaries, and release status.
- Inventory and document all `noqlen-forge` CLI subcommands, options, and flags.
- Simplify top-level CLI help and discovery for safer first-use navigation.
- Audit Core/API boundaries for future Noqlen Flux, Noqlen Anchor, and Noqlen Aria reuse.
- Do not start Noqlen Aria or mobile implementation yet.
