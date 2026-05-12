# Structured Results

Services return `WorkflowResult` so CLI, tests, future local APIs, jobs, and Noqlen clients can consume the same safe workflow state.

## WorkflowResult

Every migrated workflow should populate the shared fields when they apply: `workflow`, `command`, `target`, `target_type`, `mode`, `status`, `started_at`, `finished_at`, `elapsed_seconds`, `steps`, `summary`, `warnings`, `errors`, `planned_changes`, `applied_changes`, `artifacts`, `counts`, `metadata`, `safe_details`, and `job`.

`job` is format-only for now. Set `resumable` and `cancelable` to `false` until a real queue exists, and expose simple progress fields only.

## Steps And Changes

Use `StepResult` for progress-visible stages. Keep `summary` compact and put only safe, serializable values in `details`, `warnings`, and `errors`.

Use `PlannedChange`, `AppliedChange`, and `ChangePlan` for write plans when the common model fits. Large values are sanitized during serialization, but services should still avoid attaching full lyrics, full fingerprints, raw provider payloads, or secrets.

## Serialization

Use `workflow_result_to_dict()`, `workflow_result_to_json()`, `workflow_result_from_dict()`, `sanitize_result_for_json()`, and `sanitize_value_for_output()` from `noqlen_forge.services.types`.

The serializer converts `datetime` to ISO strings, `Path` to strings, enums to strings, exceptions to safe messages, and bytes to length markers. Sensitive keys and values are redacted.

## CLI Output

Human CLI output should keep using service-specific renderers. For `--format json` on migrated workflow commands, stdout must contain only structured JSON and the exit code should come from `exit_code_from_status()` unless a legacy explicit code is intentionally preserved.

Services must not print. They return `WorkflowResult`; CLI adapters render text or JSON.

## Artifacts

Register generated outputs with `Artifact`: `type`, `path`, `format`, `description`, `created`, `size_bytes`, and `safe_to_show`. Examples include playlist files, CSV/JSON exports, reports, and local backup files.

## Safety

Structured results do not bypass `SafetyContext`. Dry-run/apply behavior remains enforced at service level, and automated validation must still block writes outside MusicLab.
