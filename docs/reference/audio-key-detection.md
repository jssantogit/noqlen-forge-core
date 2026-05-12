# Audio Key Detection Config Reference

Key detection is optional. The main Noqlen Forge Core enrichment flow is exposed through the public `noqlen-forge` CLI. It does not require KEY/INITIALKEY metadata, and missing Key is warning-level optional metadata rather than a critical error.

```toml
[audio.key_detection]
enabled = false
backend = "auto"
backends = ["portable_basic"]
min_confidence = "medium"
write_low_confidence = false

[audio.key_detection.portable_basic]
sample_rate = 11025
max_seconds = 90
segment_seconds = 10
segments = 6
timeout_seconds = 30
```

Fields:

- `enabled`: enables key detection from config-driven flows. Keep `false` when key should run only from explicit CLI flags.
- `backend`: selects `auto`, `disabled`, or `portable_basic`.
- `backends`: ordered backend list used by `auto`.
- `min_confidence`: minimum confidence accepted for normal key writes.
- `write_low_confidence`: keeps low-confidence estimates from being written automatically when `false`.
- `sample_rate`: decode rate used by `portable_basic`.
- `max_seconds`: maximum audio duration decoded by `portable_basic`.
- `segment_seconds`: segment length sampled by `portable_basic`.
- `segments`: maximum number of segments analyzed by `portable_basic`.
- `timeout_seconds`: decode timeout for `portable_basic`.

Backends:

- `portable_basic`: native lightweight backend using bounded local decoding. It may require `ffmpeg` and uses conservative confidence because it is intentionally simple.
- `disabled`: disables key detection.
- `auto`: tries the configured backend list in order and skips cleanly when no backend is available.

Essentia is not a dependency or supported key detection backend. Older config that selects `backend = "essentia"` is rejected with a clear message telling the user to use `portable_basic` or `auto`.

Examples:

```bash
noqlen-forge analyze "$TRACK" --key
noqlen-forge analyze "$TRACK" --key --backend portable_basic
noqlen-forge analyze "$TRACK" --key --backend auto
noqlen-forge analyze "$TRACK" --key --backend disabled
noqlen-forge enrich "$ALBUM" --full
```

`enrich --full` should not fail only because key detection is unavailable. To write Key during enrich, the config or CLI flow must enable key detection and the estimate must meet the configured confidence threshold. Low-confidence estimates should be reviewed manually.
