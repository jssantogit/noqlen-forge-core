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
- `noqlen_forge/services` contains argparse-free options dataclasses and service entry points for several workflows.
- `SafetyContext` carries dry-run/apply protection across service callers and blocks automated apply outside MusicLab.
- `workflow_result_to_dict()` and `workflow_result_to_json()` provide JSON serialization with redaction for sensitive keys and long values.
- Jobs store sanitized workflow results, steps, events, and progress in SQLite without requiring a background daemon.
- MusicLab, fakes, and fixtures give future service migrations a safe validation path.
- Report/export and playlist export services already model explicit output artifacts where applicable.

## Workflow Readiness Matrix

| Workflow area | Service-backed | Core API exposed | Structured result | Terminal coupling | App-readiness | Recommended next step |
| --- | --- | --- | --- | --- | --- | --- |
| `config` | No | No | No | Medium | Partial | Add small read-only service methods for config path/show/init planning before exposing through Core API. |
| `db` | Partial | No | Partial | Medium | Partial | Wrap `db status`, `query`, `explain`, and `scan` in services with explicit DB artifacts/counts. |
| `audit` | Yes | Yes | Yes | Low | Ready | Keep CLI rendering separate and expand result details only when needed by clients. |
| `enrich` | No | Partial | Partial | High | Not ready | Document the service contract first; then migrate stages behind one adapter without changing CLI behavior. |
| `import` | Yes | Yes | Partial | Medium | Partial | Replace text-output wrapping with structured plan, stage, artifact, and apply details. |
| `organize` | Yes | Yes | Partial | Medium | Partial | Promote organize plans and applied file operations into `ChangePlan`/`Artifact` fields. |
| `query`, `report`, `export` | Yes | Partial | Partial | Medium | Partial | Add Core API methods for query/report variants and reduce reliance on `output_text`. |
| `review` | No | Partial | Partial | High | Not ready | Define a non-interactive service contract for list/show/resolve with explicit decisions and flags. |
| `maintain sync/repair/rewrite` | Yes | Yes | Partial | Medium | Partial | Convert wrapped text results into structured plans, conflicts, warnings, and applied changes. |
| `metadata`, `candidates`, `apply-mbid` | No | No | Partial | High | Not ready | Split provider lookup, candidate selection, and write planning into services before Core API exposure. |
| `cover` | Yes | Yes | Partial | Medium | Partial | Move cover decisions, selected source, confidence, and output files into structured fields. |
| `lyrics` | Yes | Yes | Partial | Medium | Partial | Keep full lyrics out of results; expose safe provider decisions, artifacts, and redacted summaries. |
| `replaygain` and audio analysis | Partial | Partial | Partial | Medium | Partial | Add services for BPM, key, mood, and feature analysis; reduce direct CLI calls to analysis modules. |
| `playlist` | Partial | Partial | Partial | Medium | Partial | Extend service coverage beyond export to create/list/show/refresh/delete/rename. |
| Navidrome ratings/playlists | No | Partial | Partial | High | Not ready | Add fake-client-backed services before Core API execution or controller reuse. |
| `jobs` | Partial | Partial | Yes | Medium | Partial | Wrap CLI job list/status/cancel/resume/prune rendering in service/Core API methods consistently. |
| `dev` and MusicLab | Partial | No | Partial | High | Not ready for app control | Keep as developer tooling; reuse MusicLab patterns for validation, not as end-user app workflows. |

## App-Readiness Blockers

- `cli.py` still owns extensive direct `print()` rendering, progress lines, and command branching. This is acceptable for the CLI but should not leak into future controllers.
- `apply-mbid` still contains an `input()` confirmation path for a medium-confidence match. Future non-terminal control needs explicit flags/options rather than interactive confirmation.
- Several service adapters call legacy functions that return `(code, output)` or result objects with text output, then store `output_text` in `WorkflowResult.details`.
- Some workflows are service-backed but not fully structured: they expose status and summary, but planned changes, applied changes, artifacts, provider decisions, or conflicts remain embedded in text.
- `NoqlenForgeCore` intentionally returns `NotImplementedWorkflowError` results for workflows without silent adapters, including `enrich`, `review`, and Navidrome rating workflows.
- Top-level CLI progress and stage rendering for enrich-style flows assumes terminal output. Future clients need event/progress data, not terminal lines.
- Provider-heavy workflows need clearer fake-client and fake-provider boundaries before they are safe to expose to future controllers.
- Some job operations are structured at the storage layer but still rendered directly by CLI handlers rather than consistently flowing through services.
- Safety is strongest where services build `OperationContext`/`SafetyContext`; CLI-only paths must keep equivalent guardrails until migrated.

## Practical Migration Order

Do not rewrite everything at once. Prefer small, tested service adapters that preserve the CLI contract.

1. Document service/Core API gaps and keep this audit current as migrations land.
2. Normalize smaller adapters first: config, DB status/query/explain, playlist non-write views, jobs list/status, and report variants.
3. Improve structured output coverage for existing services by filling `planned_changes`, `applied_changes`, `artifacts`, `counts`, `warnings`, and `safe_details` instead of relying on `output_text`.
4. Isolate terminal rendering in CLI-specific renderers and keep machine-readable stdout contracts unchanged.
5. Replace interactive confirmation with explicit options at the service boundary while preserving CLI compatibility.
6. Add fake-client-backed service contracts for Navidrome and provider-heavy flows before exposing them through Core API execution.
7. Document the enrich service contract before refactoring enrich; avoid starting with the largest enrich rewrite.
8. Only after the smaller contracts are stable, address enrich and future app-controller integration points.

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
