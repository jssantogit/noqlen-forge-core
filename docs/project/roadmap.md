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

- Noqlen Flux Core is planned for downloads and import workflows.
- Noqlen Anchor Core is planned for Navidrome and local server management.
- Noqlen Aria is a future player and app experience.
- Noqlen Aria should not be developed before Forge, Flux, and Anchor are mature enough to provide stable foundations.
- Noqlen Core may emerge if shared concepts can be extracted cleanly from Forge, Flux, and Anchor.

These names describe direction, not current implementation status. Noqlen Forge Core remains the current active public project.

## Out Of Scope For Now

- Full Noqlen Aria app development.
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
