# Safety Model

Forge is designed around explicit, reviewable operations. Treat real libraries as valuable private data.

## Dry-Run Before Apply

Write-capable workflows should be dry-run first. Read the plan, warnings, confidence, destination paths, and counts before using `--apply`.

## Explicit Confirmation For Destructive Changes

Do not make destructive changes casually. Rewrite, repair, restore, push, move, copy, tag-writing, and database-writing operations should require clear intent and review.

## Path Validation

Review all source and destination paths before applying changes. Avoid broad paths, stale mounts, untrusted symlinks, and paths that could escape the intended library root.

Forge refuses dangerous filesystem roots and broad storage roots for write-capable safety checks. This protects against accidental operations on the filesystem root, home directory, broad mount locations, removable-media roots, and similar locations that are too wide to be a safe workflow target.

For local development or test environments, `NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS` can add extra protected roots without committing personal paths. It is optional and should be set only in the local shell or automation environment. Use the platform path-list separator: `:` on POSIX shells and `;` on Windows shells.

```bash
export NOQLEN_FORGE_PROTECTED_LIBRARY_ROOTS="/tmp/noqlen-real-library:/example/protected/music"
```

Do not commit real personal library paths to docs, tests, logs, reports, examples, or config snippets. Keep examples generic and review generated reports before sharing them.

## Symlink And Traversal Care

Workflows must be careful around symlinks and path traversal. Automated checks should prefer temporary fixtures and must not follow unsafe paths into private areas.

## Reports And Logs

Reports and logs should explain what happened without leaking private data. Do not publish secrets, full lyrics, raw fingerprints, private library dumps, or real local paths.

## Quarantine Concept

When a workflow needs to isolate questionable files or records, prefer an explicit quarantine-style destination or review state rather than deleting or overwriting data immediately.

## Fake And Temporary Test Data

Automated tests and development validation should use fake or temporary libraries. MusicLab fixtures are intended for safe validation and should not be replaced with real personal collections.
