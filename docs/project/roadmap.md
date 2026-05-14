# Public Roadmap

This roadmap describes the current Noqlen Forge Core focus and the later Noqlen ecosystem direction. It is intentionally conservative: planned projects are not promises, and implementation should follow safety, tests, and real-world review.

## Current Focus

Noqlen Forge Core is the current active project. It focuses on metadata, library organization, lyrics and covers, audio features, playlists, ratings, reports, repair and review workflows, and Navidrome-oriented workflows.

Real-library operations should start with dry-run output and explicit review. Apply/write behavior should remain deliberate, narrow, and visible before it changes tags, files, databases, or external service state.

## Near-Term Goals

- Improve public documentation for installation, configuration, workflows, and safety boundaries.
- Refine the source/GitHub install and release workflow before any future package registry publication.
- Continue safe dry-run validation for library workflows.
- Improve tests and fake/MusicLab coverage for provider, file, database, and service behavior.
- Stabilize provider workflows so optional services fail safely and clearly.
- Improve packaging quality before any future PyPI publication is planned.

## Later Ecosystem Direction

- First, stabilize and harden Noqlen Forge Core.
- Next, Noqlen Flux is planned for search, download, and import workflows.
- Then, Noqlen Anchor is planned for Navidrome and local server or service workflows.
- Later, Noqlen Aria may become an app, mobile, or interface layer.
- Do not start Noqlen Aria or mobile implementation work yet; future UI should control solid cores rather than host heavy logic first.
- Noqlen Core may emerge if shared concepts can be extracted cleanly from Forge, Flux, and Anchor.

These names describe direction, not current implementation status. Noqlen Forge Core remains the current active public project.

Developer-facing service and controller-readiness gaps are tracked in [App-readiness boundary audit](../development/app-readiness.md).

## Out Of Scope For Now

- Full Noqlen Aria app development.
- Any app/mobile implementation that bypasses Forge, Flux, and Anchor stabilization.
- PyPI publication unless it is explicitly planned and documented.
- Destructive real-library operations without dry-run output and review.
- Assuming external services are always available.
- Replacing user judgment for metadata decisions.

## Safety Principles

- Dry-run before apply.
- No destructive changes without explicit confirmation.
- Clear logs, reports, and planned-change output.
- Path validation for real file operations.
- Careful handling of symlinks and path traversal.
- Fake, fixture, or temporary data in automated tests.
- No secrets, complete lyric text, or raw fingerprints in logs, docs, examples, reports, or commits.
- Real-library validation should remain manual, controlled, and reviewable.
