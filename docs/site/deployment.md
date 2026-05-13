# Site Deployment And Cache Validation

Noqlen Forge Core publishes the public documentation site with MkDocs and GitHub Pages. This page documents the deployment policy and practical checks for local preview, GitHub Actions deployment, and stale-site troubleshooting.

## Deployment Policy

- GitHub Pages deploys through GitHub Actions only.
- The Pages workflow builds the site with `python -m mkdocs build --strict`.
- Do not commit generated `site/` files.
- Do not use `mkdocs gh-deploy` for this repository.
- Do not rely on a manual `gh-pages` branch or hand-built Pages content.
- Repository settings for Pages should use GitHub Actions as the source.

## Local Validation

Build the site before relying on local changes:

```bash
python -m mkdocs build --strict
```

Preview with the MkDocs development server:

```bash
python -m mkdocs serve -a 0.0.0.0:8000
```

If port `8000` is already in use, use another port:

```bash
python -m mkdocs serve -a 0.0.0.0:8001
```

If the MkDocs server is not suitable, build and serve the generated static files locally:

```bash
python -m mkdocs build --strict
cd site
python -m http.server 8010 --bind 0.0.0.0
```

The static fallback is for local preview only. Do not commit the generated `site/` directory.

## Published-Site Validation

After a documentation or site change is pushed:

1. Open the GitHub Actions Pages workflow run for the pushed commit.
2. Confirm the Pages workflow completed successfully.
3. Open the published GitHub Pages URL: https://jssantogit.github.io/noqlen-forge-core/
4. If the site appears stale, hard refresh or clear the browser cache.
5. Test in a private or incognito tab if the normal browser session still appears stale.
6. On mobile, reload the page and close/reopen the drawer when validating navigation UI changes.

Stale published output immediately after a successful deploy can be caused by browser cache, GitHub Pages propagation delay, a service worker or old browser session, or viewing an old URL.

## Troubleshooting

Port already in use:

Use another `mkdocs serve` port, for example `python -m mkdocs serve -a 0.0.0.0:8001`.

Site looks old after a successful deploy:

Hard refresh, clear browser cache, test in a private/incognito tab, and wait briefly for GitHub Pages propagation. Confirm the URL is the current Pages URL, not an older preview or cached session.

Local build passes but the published site is stale:

Confirm the pushed commit triggered the GitHub Pages workflow, the workflow completed successfully, and Pages settings use GitHub Actions as the source. If the workflow is green, treat browser cache and Pages propagation delay as the first suspects.

Generated `site/` appears in `git status`:

Do not add it. The top-level `site/` directory is a local build artifact and should remain ignored and untracked. Remove or regenerate it locally if needed, but keep it out of commits.

Pages source is not GitHub Actions:

Update the repository Pages settings to use GitHub Actions. Do not switch to committed `site/` files, `mkdocs gh-deploy`, or a manual `gh-pages` deployment flow.
