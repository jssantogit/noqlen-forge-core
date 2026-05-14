# Services

`noqlen_forge/services` exposes reusable workflow entry points for CLI, tests, future local APIs, jobs, and Noqlen clients. The CLI should remain a terminal adapter, while services own execution and return structured results.

## Pattern

1. Define an argparse-free options dataclass, for example `AuditOptions`.
2. Load config in the caller and pass it explicitly through options or `OperationContext`.
3. Run the workflow with `WorkflowRunner` and `StepResult` when steps are useful.
4. Return `WorkflowResult` with `workflow`, `command`, `target`, `target_type`, `mode`, `status`, timestamps, `steps`, `warnings`, `errors`, `summary`, `counts`, `artifacts`, `planned_changes`, `applied_changes`, `metadata`, `safe_details`, and job-ready progress fields.
5. Render terminal text only in the CLI adapter or service-specific renderer.
6. When adapting a legacy function that returns `(code, output)` or an object with `code/status/output`, use `noqlen_forge.services.result_helpers` instead of reimplementing status and summary mapping.

## Thin CLI Adapter

```python
def command(args, config=None) -> int:
    active_config = load_cli_config(config)
    result = run_example_service(ExampleOptions(path=args.path, config=active_config, apply=args.apply))
    code, output = render_service_result(result)
    print(output)
    return code
```

Keep parsing and flag-to-options mapping in the CLI. Move IO decisions, safety checks, planning, execution, counts, warnings, and artifacts into the service.

## Exit Codes

`exit_code_from_status()` maps `OK`, `SKIP`, `DRY`, `APPLY`, and `WARN` to `0`, `REVIEW` to `2`, and `FAIL` to `1`. If a migrated legacy function already returns an explicit code, preserve that code in `WorkflowResult.details["exit_code"]` and use `render_service_result()`.

## Machine-Readable Output

When stdout is JSON, CSV, M3U, or M3U8, the CLI must print only that artifact. Human summaries for file outputs can remain on stdout when that is the existing CLI contract; avoid adding new chatter around machine-readable stdout.

Use `safe_text()` from `noqlen_forge.output` for human output that may contain provider/debug/user-controlled data. Do not pass full lyrics, fingerprints, tokens, salts, API keys, passwords, or raw provider payloads through public service details.

## Safety

Services that can write must enforce `SafetyContext` before applying changes. Automated validation must continue to block `--apply` outside MusicLab, regardless of whether the caller is CLI, test harness, or a future API.

## Serialization

Use `workflow_result_to_dict()` or `workflow_result_to_json()` for structured output. These helpers redact sensitive keys such as lyrics, fingerprints, tokens, API keys, passwords, and salts. Do not add raw provider payloads or full lyrics to public `details`; put public machine-readable values in `safe_details`.

See [Structured results](structured-results.md) for the canonical schema and job-ready conventions.

## Testing

For each migrated command, add direct service tests and CLI/service parity tests. Compare status, summary, counts, artifacts, and major steps instead of large terminal strings unless the string is the external contract.

Patch CLI tests at the service boundary, not the legacy lower-level function, once a command has migrated. This verifies the handler remains a thin adapter.

Prefer semantic helpers such as `assert_status`, `assert_step`, `assert_no_secrets`, `assert_no_db_change`, `assert_json_clean`, and `assert_machine_output_clean` over large snapshots. New write-capable services need direct tests proving `SafetyContext` blocks automated `--apply` outside MusicLab.

## DB And Fields

Use field registry helpers for field names, aliases, protected fields, missing/has behavior, query support, and sync behavior. Do not add parallel field lists unless a SQL-specific allowlist is required and documented.

Use existing DB helpers for lookups, upserts, operation recording, and migrations before adding raw SQL to a service or CLI adapter. If repeated query shapes appear, add a small helper or targeted index with tests instead of copying SQL.

## Providers

New providers should follow the closest existing provider contract, return structured candidates/results with confidence and match reasons, and keep network calls mockable. Tests and MusicLab must use fake/mock providers or clients rather than real external services.

See [App-readiness boundary audit](app-readiness.md) for current migration priorities and controller-readiness gaps.
