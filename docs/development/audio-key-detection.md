# Audio Key Detection

## Architecture

Key detection is implemented through the backend registry in `noqlen_forge/audio_key.py`. CLI and enrichment code ask the registry to analyze a path through `KeyDetectionQuery`; they should not import backend libraries directly.

Default backends:

- `disabled`: returns `SKIP` and performs no analysis.
- `portable_basic`: native lightweight backend with bounded decoding/analysis.
- `auto`: tries configured backends in order.

`portable_basic` is the native default for local key estimation. It uses bounded decoding, may need `ffmpeg`, returns a confidence label, and should be treated as an estimate rather than musical truth.

## Backend Contract

New backends should expose a stable `name`, optional `available()` check, and an `analyze(path, config)` method returning `KeyDetectionResult`. Results must include status, backend name, reason when skipped/warned/failed, and confidence when a key is detected.

Status behavior:

- `OK`: a usable key estimate was produced.
- `SKIP`: backend is disabled, unavailable, or intentionally not applicable.
- `WARN`: analysis ran but produced no safe key or hit a recoverable per-file issue.
- `FAIL`: invalid backend selection or an unrecoverable implementation/configuration error.

Main enrich/import/audit flows must treat missing key backends as `SKIP`/`WARN`, not as fatal failures. Missing Key remains optional metadata and should not become `REVIEW` by itself.

Low-confidence results must not write tags automatically. They can be reported for manual review or written only through explicit configuration/force behavior designed for that purpose.

## Adding A Backend

Add new backends through the registry rather than branching in CLI handlers. Keep dependencies optional, configurable, documented, and mockable. Do not reintroduce Essentia, NumPy, SciPy, or similar heavy libraries as mandatory runtime dependencies without explicit discussion.

Implementation checklist:

- Add the backend class in or behind `noqlen_forge/audio_key.py`.
- Register it with `KEY_DETECTION_BACKENDS`.
- Add config keys under `[audio.key_detection]` or `[audio.key_detection.<backend>]`.
- Keep imports lazy so `import noqlen_forge` works without optional packages.
- Do not copy code from removed third-party key detection libraries.
- Return low-confidence results without writing tags automatically.
- Add focused tests and MusicLab coverage when behavior changes.

## Testing

Prefer synthetic audio and small fixtures. Tests should not require internet, real user libraries, removed key detection libraries, or heavy native packages. Mock backend availability and decoding failures where possible.

Useful checks:

```bash
pytest -q -k "key or audio or config or help"
noqlen-forge dev check --quick
noqlen-forge dev lab run --scenario audio-key
```

Before functional commits, run the full project validation described in [Testing and MusicLab](testing-and-musiclab.md).

## Mobile Considerations

Future Noqlen Aria integrations should call the Core API/services and the same registry contract, not shell out to CLI commands. Mobile-capable backends must stay optional, bounded in CPU/time, and safe when the technical decoder is unavailable.
