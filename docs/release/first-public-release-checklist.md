# First Public Release Checklist

Use this checklist before changing repository visibility to public and before planning any package registry publication. Keep each item reviewable from the clean `jssantogit/noqlen-forge-core` repository.

## Repository Readiness

- [ ] Confirm the repository history is the clean GitHub-backed Noqlen Forge Core history intended for public review.
- [ ] Confirm CI is passing on the latest default-branch commit.
- [ ] Confirm `README.md` is complete enough for first public review.
- [ ] Confirm source installation is documented and current.
- [ ] Confirm docs do not claim package registry availability before publication.
- [ ] Review license state and confirm it matches the intended public release posture.
- [ ] Review whether issues and discussions should be enabled, disabled, or configured before publication.

## Naming Readiness

- [ ] Confirm public docs use Noqlen naming consistently.
- [ ] Confirm CLI examples use `noqlen-forge`.
- [ ] Confirm Python import examples use `noqlen_forge`.
- [ ] Confirm distribution package references use `noqlen-forge` only where appropriate.
- [ ] Confirm no legacy project, command, package, or path names remain in public files.

## Safety Readiness

- [ ] Confirm committed files contain no secrets.
- [ ] Confirm committed files contain no real local paths.
- [ ] Confirm committed files contain no real music-library paths.
- [ ] Confirm docs, tests, and fixtures contain no complete lyric text.
- [ ] Confirm docs, tests, and fixtures contain no raw audio fingerprints.
- [ ] Confirm dry-run-first behavior is documented where write-capable workflows are described.
- [ ] Confirm destructive actions require explicit confirmation or an explicit apply flag.
- [ ] Confirm tests and CI use fake, fixture, or temporary data only.

## Packaging Readiness

- [ ] Build a wheel locally from a clean checkout.
- [ ] Build an sdist locally from a clean checkout.
- [ ] Install the wheel in a temporary virtual environment and run CLI smoke validation.
- [ ] Confirm internal agent artifacts are not packaged.
- [ ] Confirm generated build artifacts are not tracked.
- [ ] Confirm real library data is not packaged.

## GitHub Publication Readiness

- [ ] Keep the repository private until the final review is complete.
- [ ] Perform the final review on GitHub before changing visibility.
- [ ] Confirm no force-push is required for publication.
- [ ] Do not create a release tag until the public surface is reviewed.
- [ ] Do not publish to PyPI until publication is explicitly planned.

## Post-Publication Follow-Up

- [ ] Add or adjust GitHub topics if desired.
- [ ] Decide when to create the first version tag.
- [ ] Decide whether PyPI publication is needed.
- [ ] Update install docs after PyPI publication if it happens.
