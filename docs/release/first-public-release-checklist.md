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

## Packaging Readiness

- [ ] Build a wheel locally from a clean checkout.
- [ ] Build an sdist locally from a clean checkout.
- [ ] Install the wheel in a temporary virtual environment and run CLI smoke validation.
- [ ] Confirm internal agent artifacts are not packaged.
- [ ] Confirm generated build artifacts are not tracked.
- [ ] Confirm generated `site/` files are not tracked.
- [ ] Confirm real library data is not packaged.

## Public Repository Readiness

- [ ] Verify the public repository surface before tagging the first release.
- [ ] Confirm GitHub Pages deploys through Actions and does not require committed generated site files.
- [ ] Confirm no force-push is required for release preparation.
- [ ] Do not create the first release tag until the public surface is reviewed.
- [ ] Do not publish to PyPI until publication is explicitly planned.

## First Tagged Release Follow-Up

- [ ] Add or adjust GitHub topics if desired.
- [ ] Decide when to create the first version tag.
- [ ] Decide whether PyPI publication is needed.
- [ ] Update install docs after PyPI publication if it happens.
