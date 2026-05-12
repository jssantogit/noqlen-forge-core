# Configuration guide

This guide explains how to configure Noqlen Forge Core for safe real-library use. Examples use the public `noqlen-forge` CLI. For naming details and migration expectations, see [Naming and migration guide](naming-and-migration.md).

Noqlen Forge Core configuration is for native services, providers, safety settings, and optional bounded technical backends such as `ffmpeg`, `ffprobe`, and `fpcalc`/Chromaprint. Historical external workflow tools such as beets, OneTagger, TuneUp, and Essentia are discussed only as lineage or technical context, not as workflow providers to configure.

## Config file location

Show the active config path:

```bash
noqlen-forge config path
```

Show the active database path:

```bash
noqlen-forge db path
```

Create and inspect the global config:

```bash
noqlen-forge config init
noqlen-forge config show
```

Noqlen Forge Core loads global configuration from `$XDG_CONFIG_HOME/noqlen-forge/config.toml` when `XDG_CONFIG_HOME` is set. Otherwise it uses `~/.config/noqlen-forge/config.toml`.

Precedence is intentionally simple for normal use:

- CLI flags override config for the command being run.
- Supported environment variables override config secrets and API keys.
- Config values override built-in defaults.
- Built-in defaults fill any missing config keys.

Use `config.example.toml` as a readable reference for the current built-in defaults, but keep secrets out of committed files.

## Safe defaults

The default posture should be conservative:

- Dry-run is the default for write-capable workflows.
- `--apply` is required before writing tags, changing SQLite rows, copying/moving files, or writing to Navidrome.
- Key detection is disabled for config-driven full enrichment by default.
- Low-confidence Key estimates are not written automatically.
- ReplayGain is available but skipped from `enrich --full` by default because it can be slow.
- Navidrome backup/diff/export are read-oriented, while restore/push require careful dry-run review before `--apply`.
- Existing lyrics, covers, and protected identity fields should not be overwritten without explicit intent.

Start with one album, review output, then apply only the command you trust. Before validating against a real library, use the dry-run-first checklist in [Manual real-library dry-run checklist](manual-real-library-checklist.md).

## Database

The database section controls the local SQLite library index:

```toml
[database]
path = ""
auto_scan = false
track_provider_history = true
track_tag_sync = true
```

When `path` is empty, the database uses `$XDG_DATA_HOME/noqlen-forge/library.db`; without `XDG_DATA_HOME`, it uses `~/.local/share/noqlen-forge/library.db`.

The database stores library state, file paths, query data, provider history, review state, operations, jobs, smart playlist definitions, and Navidrome backups. It does not replace tags in the audio files.

Typical setup:

```bash
noqlen-forge db init
noqlen-forge db scan "$LIBRARY"
noqlen-forge db scan "$LIBRARY" --apply
noqlen-forge db status
```

`db scan` reads files and tags. Without `--apply`, it only reports what would change. With `--apply`, it updates SQLite rows. It does not rewrite tags or move files.

Use repair commands with dry-run first:

```bash
noqlen-forge maintain repair missing-files
noqlen-forge maintain repair db
```

Apply repair only after reading the plan:

```bash
noqlen-forge maintain repair db --apply
```

## Library paths

The library section is for stable local paths and organization templates:

```toml
[library]
root = ""
incoming = ""
template = "{genre}/{albumartist}/{album}/{track:02d} {title}"
```

Use `root` for the main library and `incoming` for downloads/import staging. You can also pass paths explicitly with CLI flags, which is safer while testing:

```bash
noqlen-forge import "$INCOMING" --library "$LIBRARY"
noqlen-forge organize "$ALBUM" --library "$LIBRARY"
```

For organize/import workflows, prefer copy mode until you have reviewed destination paths. Avoid symlinks you do not fully trust, old mount points, and broad paths such as a whole phone music directory during first use.

Playlist exports support path modes on smart playlist export/refresh:

```bash
noqlen-forge playlist smart export "Favorites" --format m3u8 --output favorites.m3u8 --path-mode library --library-root "$LIBRARY"
```

Use absolute paths when the player sees the same filesystem, relative paths for portable playlist folders, and library-root-relative paths when a player maps the library root differently.

## Metadata providers

Native metadata providers are configured under `metadata_providers`:

```toml
[metadata_providers]
enabled = true
sources = ["musicbrainz", "discogs"]
max_active = 2
min_confidence = "medium"
allow_more_providers = false

[metadata_providers.musicbrainz]
enabled = true
role = "identity"

[metadata_providers.discogs]
enabled = true
role = "catalog"
token = ""
```

MusicBrainz is the identity authority for release and recording IDs. Discogs can enrich catalog fields such as style, label, catalog number, barcode, country, and format. iTunes and Deezer are optional fallback providers when enabled. AcoustID Identify is a native identifier provider that uses `fpcalc`/Chromaprint fingerprints.

Provider candidates can be confident, ambiguous, or conflicting. Low-confidence candidates and conflicts should become `REVIEW` instead of being written automatically.

Do not configure beets, OneTagger, TuneUp, or Essentia as workflow providers. References to them in the documentation are historical or technical context, not provider IDs for the current Noqlen Forge Core flow.

## Covers

Cover behavior is configured with:

```toml
[cover]
enabled = true
embed = true
save_folder_cover = false
filename = "cover"
sources = ["local", "musicbrainz", "itunes", "deezer"]
min_confidence = "medium"
prefer_front = true
max_size_mb = 10
```

`embed` writes selected cover art into file tags when the cover command is applied. `save_folder_cover` writes a sidecar folder cover file when enabled. `sources` controls provider priority. `min_confidence`, `prefer_front`, and `max_size_mb` keep selection conservative.

Run cover changes as dry-run first:

```bash
noqlen-forge cover "$ALBUM"
noqlen-forge cover "$ALBUM" --apply
```

Use `--force` or overwrite-oriented options only after checking the current cover and candidate. Cover conflicts should be reviewed instead of blindly replacing known-good art.

## Lyrics

Lyrics configuration controls local and online provider selection:

```toml
[lyrics]
providers = ["embedded", "sidecar", "lrclib"]
prefer_synced = true
allow_unsynced = true
prefer_local = true
prefer_existing = true
embed_lyrics = true
write_sidecar_lrc = false
overwrite_existing = false
review_on_conflict = true
review_on_existing_mismatch = true
```

Supported provider names include:

- `embedded`: existing lyrics in audio tags.
- `sidecar`: local sidecar lyrics files near the audio files.
- `lrclib`: online LRCLIB provider.
- `custom_http`: optional JSON HTTP endpoint configured under `[lyrics.provider_settings.custom_http]`.

Some older/example config may use `sources`; current user-facing provider configuration should prefer `providers`.

Useful related settings:

```toml
[lyrics.online]
enabled = true
timeout_seconds = 20
max_results = 5
rate_limit_seconds = 1.0
user_agent = "noqlen-forge"

[lyrics.provider_settings.custom_http]
enabled = false
base_url = ""
api_key_env = "NOQLEN_FORGE_LYRICS_API_KEY"
supports_synced = true
supports_unsynced = true
```

`prefer_synced` favors LRC/synced lyrics when confidence is good. `allow_unsynced` permits plain lyrics. `overwrite_existing = false` protects existing lyrics. `write_sidecar_lrc = false` avoids creating `.lrc` sidecars unless requested. Local sidecar lookup uses the provider's supported sidecar extensions near the audio file; there is no global `lyrics_dir` default in the current config. Conflicting high-confidence lyrics should become `REVIEW`, and normal output should not print full lyrics.

## Audio analysis

Audio analysis covers BPM, Energy, Danceability, Mood support signals, ReplayGain, and Key detection.

```toml
[audio]
replaygain_enabled = true
replaygain_backend = "ffmpeg"
target_lufs = -18.0
write_track_gain = true
write_track_peak = true
write_album_gain = true
write_album_peak = true
write_loudness = true
skip_existing = true
```

`ffmpeg`/`ffprobe` are technical local audio backends, not external metadata workflow managers. Missing optional tools should usually produce `WARN` or `SKIP` for the optional stage rather than breaking the main metadata workflow.

Examples:

```bash
noqlen-forge analyze "$ALBUM" --bpm
noqlen-forge analyze "$ALBUM" --features
noqlen-forge analyze "$ALBUM" --mood
```

Large audio analysis can be slow. Run it on one album before enabling it in broader workflows. Long-running workflows may record job history, but command execution is still synchronous in this stage.

## Key detection

Native Key detection is optional and conservative:

```toml
[audio.key_detection]
enabled = false
backend = "auto"
backends = ["portable_basic"]
min_confidence = "medium"
write_low_confidence = false
fail_on_error = false

[audio.key_detection.portable_basic]
sample_rate = 11025
max_seconds = 90
segment_seconds = 10
segments = 6
timeout_seconds = 30
```

`enabled = false` means config-driven full enrichment does not run Key detection by default. You can still request it explicitly:

```bash
noqlen-forge analyze "$TRACK" --key --backend portable_basic
```

`portable_basic` is a native lightweight estimator. It may require `ffmpeg` to decode audio. It does not use Essentia. Because it is intentionally simple, treat results as estimates and keep `write_low_confidence = false` unless you have a specific review workflow.

`backend = "auto"` tries configured backends in order. `backend = "disabled"` skips analysis. Missing Key is normally `WARN`, not `FAIL`.

## ReplayGain

ReplayGain is configured under `[audio]` and controlled in `enrich` with:

```toml
[enrich]
full_includes_replaygain = false
```

Run ReplayGain directly when needed:

```bash
noqlen-forge replaygain "$ALBUM"
noqlen-forge replaygain "$ALBUM" --apply
```

Or request it in full enrichment:

```bash
noqlen-forge enrich "$ALBUM" --full --replaygain
noqlen-forge enrich "$ALBUM" --full --replaygain --apply
```

Dry-run analyzes and reports the plan. `--apply` is required before writing ReplayGain/loudness tags. Keep `skip_existing = true` unless you intentionally want to recompute existing values. Avoid running ReplayGain across a large library until timing and output are understood.

## AcoustID / Chromaprint

Native AcoustID identification uses `fpcalc`/Chromaprint for fingerprint generation:

```toml
[metadata_providers.acoustid]
enabled = true
role = "identifier"
write_fingerprint = true
write_acoustid = true
use_for_identity = true
min_score = 0.80
max_candidates = 5

[tools]
fpcalc = "fpcalc"
```

Set the API key through an environment variable when possible:

```bash
export ACOUSTID_API_KEY="your-key"
```

`ACOUSTID_KEY` is also supported and has the same purpose. If no key is available, fingerprint generation can still run and lookup should be reported as `WARN`/skip rather than breaking the whole enrichment flow.

AcoustID does not use TuneUp. Existing AcoustID IDs, fingerprints, and MusicBrainz identity fields should be preserved unless you explicitly force identity/acoustid writes and the candidate is safe.

## Navidrome

Navidrome configuration lives under:

```toml
[navidrome]
enabled = false
base_url = ""
username = ""
password = ""
token = ""
salt = ""
client_name = "noqlen-forge"
api_version = "1.16.1"
auth = "password"
timeout_seconds = 20
verify_ssl = true
```

Prefer environment variables for secrets. For interactive shells, read secrets without printing them:

```bash
read -rsp "Navidrome password: " NOQLEN_FORGE_NAVIDROME_PASSWORD
export NOQLEN_FORGE_NAVIDROME_PASSWORD
```

Use the minimum secret needed for your configured auth mode. Never commit passwords, tokens, or salts. `auth = "password"` uses username/password; token/salt authentication uses the token and salt fields instead.

Safe read-oriented checks:

```bash
noqlen-forge navidrome ping
noqlen-forge navidrome ratings backup
noqlen-forge navidrome ratings diff --server
noqlen-forge navidrome playlists backup
```

Backup commands require `--apply` only to save backup rows into the local Noqlen Forge Core SQLite database. They should not write to Navidrome.

Write-capable Navidrome commands need dry-run review first:

```bash
noqlen-forge navidrome ratings restore
noqlen-forge navidrome ratings restore --apply
noqlen-forge navidrome playlists push 'rating:>=4' --name "Favorites"
noqlen-forge navidrome playlists push 'rating:>=4' --name "Favorites" --apply
```

Restore and push write to the Navidrome API. Run backup/diff first.

## Playlists

Smart playlists are saved query definitions in SQLite. They are recalculated at export time:

```bash
noqlen-forge playlist smart create "Favorites" --query 'rating:>=4' --apply
noqlen-forge playlist smart export "Favorites" --format m3u8 --output favorites.m3u8
noqlen-forge playlist smart refresh "Favorites" --output favorites.m3u8 --force
```

Supported export formats include `m3u`, `m3u8`, `json`, and `csv`; commands default to `m3u8` where the CLI does not receive `--format`. Use `--path-mode absolute`, `--path-mode relative`, or `--path-mode library` to control paths in playlist files. Use `--library-root` with library mode when needed. There is no global playlist config section today; playlist behavior is controlled by saved query definitions and CLI flags.

Smart playlist create/delete/rename change saved definitions only with `--apply`. Export/refresh write only the requested output file and require `--force` to overwrite an existing file.

## Jobs

Job defaults are:

```toml
[jobs]
enabled = true
history_days = 30
prune_completed = true
prune_failed = false
default_resumable = false
```

Jobs are local history/progress records for workflows. They prepare Noqlen Forge Core for future app/API integration, but the CLI still executes jobs synchronously in this stage.

Cancellation and resume are cooperative and stop only at safe checkpoints. Resume is available only for workflow kinds that explicitly support it. Prune is dry-run by default:

```bash
noqlen-forge jobs list
noqlen-forge jobs status JOB_ID
noqlen-forge jobs cancel JOB_ID
noqlen-forge jobs resume JOB_ID
noqlen-forge jobs prune
noqlen-forge jobs prune --apply
```

Job options and results should not store secrets, full lyrics, full fingerprints, or raw provider payloads.

## Safety

Keep these safety rules in config and operations:

- Keep dry-run as the default posture.
- Require explicit `--apply` for writes.
- Do not use real library paths in tests or automated validation.
- Keep validation writes inside MusicLab paths only.
- Avoid symlinks and ambiguous path containment for apply workflows.
- Keep output files in known report/export directories, not scattered inside the library root unless intentional.
- Do not print or commit secrets, full lyrics, full fingerprints, or raw provider payloads.
- Prefer environment variables for API keys and passwords.

MusicLab is the safe validation environment:

```bash
noqlen-forge dev lab reset
noqlen-forge dev lab run --quick
```

Do not point MusicLab or automated validation at your real library.

## Reports and exports

Reports and exports are controlled mostly through CLI options and the `[reports]` section:

```toml
[reports]
missing_enabled = true
untracked_enabled = true
default_missing_fields = ["cover", "lyrics", "synced_lyrics", "key", "replaygain", "bpm", "mood", "style", "label", "originaldate", "mb_album_id", "mb_track_id"]
hide_optional_by_default = false
```

Examples:

```bash
noqlen-forge report missing lyrics
noqlen-forge report missing --fields lyrics,cover,key
noqlen-forge report duplicates
noqlen-forge export 'missing:lyrics' --format json --output missing-lyrics.json
noqlen-forge export --library --format json --output library-backup.json
```

Reports and exports should be read-only except for creating the requested output file. Existing output files are protected unless the command supports and receives `--force`.

## Environment variables

Use environment variables for secrets and local runtime paths.

| Variable | Purpose |
| --- | --- |
| `XDG_CONFIG_HOME` | Overrides the base directory for `noqlen-forge/config.toml`. |
| `XDG_DATA_HOME` | Overrides the base directory for the default SQLite database path. |
| `LASTFM_API_KEY` | Overrides `[apis].lastfm_api_key` for Last.fm tags. |
| `DISCOGS_TOKEN` | Overrides Discogs token config. |
| `ACOUSTID_KEY` | AcoustID API key for native AcoustID lookup. |
| `ACOUSTID_API_KEY` | Alternate AcoustID API key variable. |
| `NOQLEN_FORGE_NAVIDROME_PASSWORD` | Overrides `[navidrome].password`. |
| `NOQLEN_FORGE_NAVIDROME_TOKEN` | Overrides `[navidrome].token`. |
| `NOQLEN_FORGE_NAVIDROME_SALT` | Overrides `[navidrome].salt`. |
| `NOQLEN_FORGE_LYRICS_API_KEY` | Default secret env var for `custom_http` lyrics provider. |
| `NOQLEN_FORGE_LAB` | Overrides the MusicLab working path. Use only for isolated lab paths. |
| `NOQLEN_FORGE_AUTOMATED_VALIDATION` | Enables automated validation safety behavior; apply outside MusicLab should be blocked. |

Do not store real secrets in `config.example.toml`, docs, reports, logs, or commits.

## Example profiles

Conservative profile:

```toml
[enrich]
full_includes_cover = false
full_includes_lyrics = false
full_includes_key = false
full_includes_replaygain = false

[lyrics]
providers = ["embedded", "sidecar"]
prefer_existing = true
overwrite_existing = false
review_on_conflict = true

[audio.key_detection]
enabled = false
write_low_confidence = false

[navidrome]
enabled = false
```

Controlled personal-use profile:

```toml
[database]
auto_scan = false
track_provider_history = true
track_tag_sync = true

[lyrics]
providers = ["embedded", "sidecar", "lrclib"]
prefer_synced = true
allow_unsynced = true
overwrite_existing = false

[audio.key_detection]
enabled = false
backend = "auto"
backends = ["portable_basic"]
min_confidence = "medium"

[audio]
replaygain_enabled = true
skip_existing = true

[navidrome]
enabled = true
auth = "password"
```

MusicLab/dev profile:

```toml
[library]
root = "/tmp/noqlen-forge-lab/Library"
incoming = "/tmp/noqlen-forge-lab/Incoming"

[database]
path = "/tmp/noqlen-forge-lab/library.db"

[lyrics.online]
enabled = false

[navidrome]
enabled = false
```

For MusicLab, prefer the built-in lab commands instead of manually configuring real library paths:

```bash
NOQLEN_FORGE_LAB=/tmp/noqlen-forge-lab noqlen-forge dev lab run --quick
```

## Troubleshooting

Config not found: run `noqlen-forge config path`, create it with `noqlen-forge config init`, and verify `XDG_CONFIG_HOME`.

Database in unexpected path: run `noqlen-forge db path`, check `[database].path`, `XDG_DATA_HOME`, and the active user account.

`ffmpeg` or `ffprobe` missing: install the tool or skip local audio features that need decoding.

`fpcalc` missing: install Chromaprint/fpcalc or disable AcoustID lookup/fingerprinting.

AcoustID API key missing: set `ACOUSTID_KEY` or `ACOUSTID_API_KEY`; without it, lookup can be skipped while the rest of enrichment continues.

Navidrome auth error: check `base_url`, `username`, auth mode, TLS verification, and `NOQLEN_FORGE_NAVIDROME_PASSWORD`/`TOKEN`/`SALT`.

Provider timeout: lower provider count, check network availability, or increase provider-specific timeout/rate limits where supported.

Output path blocked or refused: check whether the file already exists and whether the command requires `--force` before overwrite.

Key detection is `SKIP`: confirm `[audio.key_detection]`, requested backend, and availability of `ffmpeg` for `portable_basic` decoding.

Lyrics missing: verify provider list, local sidecar files, online settings, and whether the track is instrumental or has placeholder lyrics.

Navidrome restore/push blocked: run the dry-run form first, confirm identity matches, and use `--apply` only after reading the plan.
