# App-Readiness Boundary Audit

This audit records how ready Noqlen Forge Core is for future controllers that are not terminal-first. It is a developer planning document, not a commitment to start app, server, daemon, or mobile work now.

Current strategic order remains:

1. Finish and harden Noqlen Forge Core.
2. Move to Noqlen Flux.
3. Then Noqlen Anchor.
4. Only later Noqlen Aria.

## Intended Boundary

The `noqlen-forge` CLI should be a terminal adapter:

- Parse command-line arguments.
- Create options, configuration, and safety context.
- Call services or `NoqlenForgeCore` methods.
- Render terminal output, progress, and help text.
- Return process exit codes.

Core and service code should be reusable workflow logic:

- Avoid `argparse`, terminal rendering, direct `print()`, and `input()` confirmation flows.
- Return `WorkflowResult` with `StepResult` entries.
- Expose planned changes, applied changes, warnings, errors, counts, artifacts, safe details, job metadata, and timing.
- Preserve dry-run/apply safety through `SafetyContext`.
- Keep sensitive values out of public details and JSON output.

Future controllers should call services or `NoqlenForgeCore` directly:

- Noqlen Flux should reuse service patterns for safety, reports, jobs, database access, and MusicLab validation.
- Noqlen Anchor should reuse structured service outputs and local control boundaries instead of scraping terminal text.
- Noqlen Aria should eventually control mature cores; it should not host heavy workflow logic.
- Shelling out to `noqlen-forge` should be a fallback only when no service/Core API path exists.

## What Is Already Good

- `noqlen_forge.api.NoqlenForgeCore` provides a stable internal API class and a `capabilities()` manifest.
- `WorkflowResult`, `StepResult`, `ChangePlan`, `PlannedChange`, `AppliedChange`, `ApplyResult`, and `Artifact` define a reusable result vocabulary.
- `noqlen_forge/services` contains argparse-free options dataclasses and service entry points for most user-facing workflows.
- `SafetyContext` carries dry-run/apply protection across service callers and blocks automated apply outside MusicLab.
- `workflow_result_to_dict()` and `workflow_result_to_json()` provide JSON serialization with redaction for sensitive keys and long values.
- Jobs store sanitized workflow results, steps, events, and progress in SQLite without requiring a background daemon.
- MusicLab, fakes, and fixtures give future service migrations a safe validation path.
- Enrich now has a non-interactive service boundary and Core API method; medium-confidence apply requires explicit confirmation options outside the CLI.
- Config, database, metadata, review, maintenance, library, provider/audio, Navidrome, report/export, playlist export, and job operations have service boundaries, although some still wrap legacy text or object results.
- Navidrome ratings and playlist services accept injectable clients and have fake-client tests for safe service/Core API execution.

## Workflow Readiness Matrix

| Workflow area | Service-backed | Core API exposed | Structured result | Terminal coupling | App-readiness | Recommended next step |
| --- | --- | --- | --- | --- | --- | --- |
| `config` | Yes | Yes | Yes | Low | Ready | Keep CLI rendering separate and preserve masked config output. |
| `db` | Yes | Yes | Partial | Low | Partial | Continue replacing legacy rendered text in scan/query/explain details with richer structured rows, plans, and diagnostics. |
| `audit` | Yes | Yes | Yes | Low | Ready | Keep CLI rendering separate and expand result details only when needed by clients. |
| `enrich` | Yes | Yes | Partial | Medium | Partial | Continue replacing stage text summaries with structured planned/applied changes, stage artifacts, warnings, and progress events. |
| `import` | Yes | Yes | Partial | Medium | Partial | Replace remaining object/text wrapping with structured stage plans, enrichment sub-results, artifacts, and apply details. |
| `organize` | Yes | Yes | Partial | Low | Partial | Add applied file-operation details and artifacts to the existing planned-change/item summaries. |
| `query`, `report`, `export` | Yes | Partial | Partial | Medium | Partial | Add Core API methods for query and report variants; reduce reliance on `output_text` except where stdout format is the contract. |
| `review` | Yes | Yes | Partial | Medium | Partial | Replace wrapped review text with structured decision lists, selected actions, plans, and applied changes. |
| `maintain sync/repair/rewrite` | Yes | Yes | Partial | Medium | Partial | Convert wrapped text results into structured plans, conflicts, warnings, and applied changes. |
| `metadata`, `candidates`, `apply-mbid` | Yes | Yes | Partial | Medium | Partial | Expand structured provider decisions and write plans while keeping raw provider payloads out of results. |
| `cover` | Yes | Yes | Partial | Medium | Partial | Move cover decisions, selected source, confidence, and output files into structured fields. |
| `lyrics` | Yes | Yes | Partial | Medium | Partial | Keep full lyrics out of results; expose safe provider decisions, artifacts, and redacted summaries. |
| `replaygain` and audio analysis | Partial | Partial | Partial | Medium | Partial | ReplayGain has service/Core API coverage; add services for BPM, key, mood, and feature analysis and reduce direct CLI calls to analysis modules. |
| `playlist` | Partial | Partial | Partial | Medium | Partial | Playlist export is service/Core API backed; extend service coverage to smart create/list/show/refresh/delete/rename. |
| Navidrome ratings/playlists | Yes | Yes | Partial | Low | Partial | Keep fake-client coverage, then replace JSON/text parsing wrappers with direct structured payloads and apply safety summaries. |
| `jobs` | Yes | Yes | Yes | Medium | Partial | Keep storage structured, but align CLI custom rendering and job workflow parity with service/Core API result rendering. |
| `dev` and MusicLab | Partial | No | Partial | High | Not ready for app control | Keep as developer tooling; reuse MusicLab patterns for validation, not as end-user app workflows. |

## Blocker Status

### Resolved Blockers

- `enrich` now has an app-ready service/Core API boundary instead of being CLI-only.
- Medium-confidence enrich apply no longer silently proceeds and no longer depends on service-side interaction; service/Core API callers must pass an explicit confirmation option.
- Navidrome ratings and playlists now have service/Core API methods and injectable-client tests, so controllers do not need to scrape CLI output to run those workflows.
- Metadata/review, maintenance/library, provider/audio, and job workflows have broad service/Core API coverage after the recent refactors.
- Config path/init/show and DB path/init/status/scan/query/explain now have service/Core API paths with structured summaries for future controllers.

### Remaining Blockers Before Flux Planning

- Structured result coverage is broad but mixed. Several services still adapt legacy `(code, output)` functions or result objects and keep important details in `output_text`.
- Query/report variants and playlist operations beyond export still lack full Core API parity.
- Audio analysis beyond ReplayGain still runs mostly through direct analysis modules instead of dedicated service/Core API methods.

### Non-Blocking Follow-Ups

- Enrich still needs richer structured stage results, planned/applied changes, artifacts, and reusable progress/event details, but it is no longer blocked by the medium-confidence confirmation issue.
- Navidrome services are safe to call with fake clients and structured summaries, but they still parse JSON/text output from lower-level functions rather than building results directly.
- Jobs are structured at the storage/service/Core API layer, but CLI job rendering remains custom and should be normalized.
- Import, organize, review, maintenance, cover, lyrics, and metadata workflows should continue replacing text wrappers with structured decisions, warnings, conflicts, artifacts, and applied changes.
- CLI rendering still contains direct `print()` and progress handling. This is acceptable for the terminal adapter as long as service/Core API callers remain silent and structured.
- Safety remains strongest where services build `OperationContext`/`SafetyContext`; any remaining CLI-only apply path needs equivalent guardrails until migrated.

## Practical Migration Order

Do not rewrite everything at once. Prefer small, tested service adapters that preserve the CLI contract.

1. Document service/Core API gaps and keep this audit current as migrations land.
2. Add small service/Core API adapters for config and DB status/scan/explain.
3. Add Core API parity for query/report variants and non-export playlist operations.
4. Improve structured output coverage for existing services by filling `planned_changes`, `applied_changes`, `artifacts`, `counts`, `warnings`, and `safe_details` instead of relying on `output_text`.
5. Replace legacy text wrappers incrementally while preserving existing CLI stdout contracts.
6. Keep interactive confirmation in CLI adapters only; service/Core API boundaries must require explicit options or decisions.
7. Normalize CLI job rendering around service/Core API results.
8. Add dedicated service/Core API coverage for BPM, key, mood, and feature analysis.

Runtime behavior should remain unchanged until each migration has direct service tests, CLI/service parity tests, and MusicLab/fake validation where applicable.

## Not Now Boundaries

- Do not start Noqlen Aria or mobile implementation yet.
- Do not add Android UI work.
- Do not create server, daemon, background executor, or HTTP behavior in Forge Core.
- Do not extract a shared Noqlen Core package yet.
- Do not rewrite or break the CLI while services are being normalized.
- Do not remove compatibility aliases without deprecation and release notes.
- Do not run automated tests against a real music library.
- Do not move heavy workflow logic into future UI/controller layers.

## How This Helps Flux, Anchor, And Aria Later

Noqlen Flux can reuse Forge Core patterns for dry-run-first planning, reports, jobs, database records, provider fakes, and MusicLab-style validation.

Noqlen Anchor can reuse structured `WorkflowResult` outputs, safe job state, redacted JSON, and service boundaries as local control patterns.

Noqlen Aria should eventually orchestrate mature cores. If Forge Core keeps terminal rendering in the CLI and reusable logic in services, Aria can display plans, warnings, progress, and artifacts without owning metadata workflow internals.
