# Safety Model

Forge is designed around explicit, reviewable operations. Treat real libraries as valuable private data.

## Dry-Run Before Apply

Write-capable workflows should be dry-run first. Read the plan, warnings, confidence, destination paths, and counts before using `--apply`.

## Explicit Confirmation For Destructive Changes

Do not make destructive changes casually. Rewrite, repair, restore, push, move, copy, tag-writing, and database-writing operations should require clear intent and review.

## Path Validation

Review all source and destination paths before applying changes. Avoid broad paths, stale mounts, untrusted symlinks, and paths that could escape the intended library root.

## Symlink And Traversal Care

Workflows must be careful around symlinks and path traversal. Automated checks should prefer temporary fixtures and must not follow unsafe paths into private areas.

## Reports And Logs

Reports and logs should explain what happened without leaking private data. Do not publish secrets, full lyrics, raw fingerprints, private library dumps, or real local paths.

## Quarantine Concept

When a workflow needs to isolate questionable files or records, prefer an explicit quarantine-style destination or review state rather than deleting or overwriting data immediately.

## Fake And Temporary Test Data

Automated tests and development validation should use fake or temporary libraries. MusicLab fixtures are intended for safe validation and should not be replaced with real personal collections.
