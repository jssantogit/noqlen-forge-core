# Core API

`noqlen_forge.api.NoqlenForgeCore` is the current stable internal API class name for Noqlen Forge Core clients. It is not an Android app, HTTP server, daemon, or external API. It is a Python layer over reusable services so callers do not need to know the CLI or command-module internals.

## Usage

```python
from noqlen_forge.api import NoqlenForgeCore

core = NoqlenForgeCore(config_path="config.toml")
result = core.audit("/music/Album")

print(result.status)
for step in result.steps:
    print(step.name, step.status)
```

Every public workflow method returns `WorkflowResult`. Use `workflow_result_to_dict()` or `workflow_result_to_json()` from `noqlen_forge.services.types` for machine-readable output.

Metadata/review-oriented workflows currently exposed through stable service adapters include `metadata()`, `candidates()`, `apply_mbid()`, and `review()`. `apply_mbid(..., apply=True)` requires explicit options for medium-confidence matches in non-terminal callers; interactive confirmation remains a CLI-only compatibility path.

## Services And CLI

Services own reusable workflow behavior and safety decisions. CLI handlers parse arguments, call services, render human output, and return process exit codes. The Core API sits beside the CLI and adapts stable services into silent structured results for clients such as a future local API or Android bridge.

The Core API must not print, call `sys.exit`, depend on `argparse`, or return loose text.

## Safety

Dry-run remains the default for workflows that can write. Apply behavior requires `apply=True` and still goes through `SafetyContext`. Automated validation blocks `apply=True` outside a MusicLab tree containing `.noqlen-forge-lab`.

Results are sanitized for JSON output and must not expose secrets, full lyrics, full fingerprints, or raw provider payloads.

## Jobs

The API supports foreground jobs through the existing SQLite job model:

```python
created = core.create_job("audit", "/music/Album", {"verbose": True})
job_id = created.summary["job_id"]
result = core.run_job(job_id)
status = core.jobs_status(job_id)
```

There is no background thread, daemon, or parallel executor. A future mobile bridge can create, run, inspect, and cancel jobs using this model.

## Capabilities

`core.capabilities()` returns a manifest with workflow names, apply support, job support, implementation status, dangerous operations, and schema version. Future Noqlen Aria clients should read this manifest instead of hardcoding available workflows.

Workflows without a silent service adapter return a structured `FAIL` result with `NotImplementedWorkflowError` until their service contract is stabilized.

See [App-readiness boundary audit](app-readiness.md) for the current service, Core API, and terminal-coupling readiness matrix.
