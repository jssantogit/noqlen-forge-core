from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any


APP_NAME = "Noqlen Forge Core"
APP_SLUG = "noqlen-forge"
APP_USER_AGENT = "noqlen-forge/0.1.0"
LYRICS_API_KEY_ENV = "NOQLEN_FORGE_LYRICS_API_KEY"


def default_config() -> dict[str, Any]:
    return {
        "library": {
            "root": "",
            "incoming": "",
            "template": "{genre}/{albumartist}/{album}/{track:02d} {title}",
        },
        "organize": {
            "enabled": True,
            "library_path": "",
            "mode": "copy",
            "template": "$genre/$albumartist/$album/$track $title",
            "singleton_template": "$genre/$artist/Singles/$title",
            "compilation_template": "Compilations/$album/$track $title",
            "conflict_policy": "review",
            "delete_empty_dirs": False,
            "ascii_paths": False,
            "max_filename_length": 180,
        },
        "import": {
            "enabled": True,
            "library_path": "",
            "mode": "copy",
            "run_enrich": True,
            "run_cover": True,
            "run_lyrics": True,
            "run_replaygain": False,
            "run_organize": True,
            "audit_before": True,
            "audit_after": True,
            "stop_on_review": True,
            "stop_on_fail": True,
            "auto_scan_db": True,
            "delete_source_empty_dirs": False,
        },
        "database": {
            "path": "",
            "auto_scan": False,
            "track_provider_history": True,
            "track_tag_sync": True,
        },
        "jobs": {
            "enabled": True,
            "history_days": 30,
            "prune_completed": True,
            "prune_failed": False,
            "default_resumable": False,
        },
        "sync": {
            "enabled": True,
            "default_direction": "tags-to-db",
            "conflict_policy": "review",
            "write_empty_fields": False,
            "protect_identity_fields": True,
            "auto_scan_before_sync": True,
            "auto_audit_after_sync": True,
        },
        "rewrite": {
            "enabled": True,
            "case_sensitive": False,
            "apply_to_db": True,
            "apply_to_tags": True,
            "dry_run_by_default": True,
            "artist": {"New Jeans": "NewJeans"},
            "albumartist": {"New Jeans": "NewJeans"},
            "genre": {"kpop": "K-pop", "k-pop": "K-pop", "pop korean": "K-pop"},
            "style": {"Prog Metal": "Progressive Metal", "technical death": "Technical Death Metal"},
            "label": {"Season of Mist": "Season Of Mist"},
            "multi_value": {"separator": "; ", "trim_values": True, "dedupe_values": True},
        },
        "repair": {
            "enabled": True,
            "dry_run_by_default": True,
            "missing_files_action": "mark-missing",
            "untracked_action": "scan",
            "duplicates_action": "review",
            "db_orphans_action": "mark-stale",
            "allow_delete_records": False,
        },
        "duplicates": {
            "enabled": True,
            "default_scope": "tracks",
            "default_strategy": "safe",
            "min_duration_delta": 2.0,
            "same_album_only": False,
            "include_path_duplicates": True,
            "include_mbids": True,
            "include_acoustid": True,
            "include_title_artist_duration": True,
        },
        "navidrome": {
            "enabled": False,
            "base_url": "",
            "username": "",
            "password": "",
            "token": "",
            "salt": "",
            "client_name": APP_SLUG,
            "api_version": "1.16.1",
            "auth": "password",
            "timeout_seconds": 20,
            "verify_ssl": True,
        },
        "reports": {
            "missing_enabled": True,
            "untracked_enabled": True,
            "default_missing_fields": [
                "cover",
                "lyrics",
                "synced_lyrics",
                "key",
                "replaygain",
                "bpm",
                "mood",
                "style",
                "label",
                "originaldate",
                "mb_album_id",
                "mb_track_id",
            ],
            "hide_optional_by_default": False,
        },
        "enrich": {
            "full_includes_cover": False,
            "full_includes_lyrics": False,
            "full_includes_key": False,
            "full_includes_lastfm": True,
            "full_includes_mood": True,
            "full_includes_bpm": True,
            "full_includes_features": True,
            "full_includes_acoustid_identification": True,
            "full_includes_acoustid": True,
            "full_includes_cleanup": True,
            "full_includes_metadata_providers": True,
            "full_includes_replaygain": False,
        },
        "audio": {
            "replaygain_enabled": True,
            "replaygain_backend": "ffmpeg",
            "key_detection": {
                "enabled": False,
                "backend": "auto",
                "backends": ["portable_basic"],
                "write_low_confidence": False,
                "min_confidence": "medium",
                "fail_on_error": False,
                "portable_basic": {
                    "sample_rate": 11025,
                    "max_seconds": 90,
                    "segment_seconds": 10,
                    "segments": 6,
                    "timeout_seconds": 30,
                },
            },
            "target_lufs": -18.0,
            "write_track_gain": True,
            "write_track_peak": True,
            "write_album_gain": True,
            "write_album_peak": True,
            "write_loudness": True,
            "skip_existing": True,
        },
        "metadata": {
            "prefer_original_date": True,
            "clean_bad_fields": True,
            "write_mbids": True,
        },
        "metadata_providers": {
            "enabled": True,
            "sources": ["musicbrainz", "discogs"],
            "max_active": 2,
            "min_confidence": "medium",
            "allow_more_providers": False,
            "musicbrainz": {
                "enabled": True,
                "role": "identity",
            },
            "discogs": {
                "enabled": True,
                "role": "catalog",
                "token": "",
                "use_for_genre": True,
                "use_for_style": True,
                "use_for_label": True,
                "use_for_catalog_number": True,
                "use_for_barcode": True,
                "use_for_country": True,
                "use_for_format": True,
                "use_for_release_type": True,
            },
            "acoustid": {
                "enabled": True,
                "role": "identifier",
                "write_fingerprint": True,
                "write_acoustid": True,
                "use_for_identity": True,
                "min_score": 0.80,
                "max_candidates": 5,
            },
            "deezer": {
                "enabled": False,
                "role": "fallback",
                "use_for_genre": True,
                "use_for_label": False,
                "use_for_date": True,
                "use_for_tracklist": True,
                "use_for_duration": True,
                "use_for_cover": True,
            },
            "itunes": {
                "enabled": False,
                "role": "fallback",
                "storefront": "us",
                "use_for_genre": True,
                "use_for_date": True,
                "use_for_tracklist": True,
                "use_for_duration": True,
                "use_for_cover": True,
                "use_for_explicit": True,
            },
        },
        "audit": {
            "show_catalog_fields": False,
            "show_advanced_fields": False,
        },
        "cover": {
            "enabled": True,
            "embed": True,
            "save_folder_cover": False,
            "filename": "cover",
            "sources": ["local", "musicbrainz", "itunes", "deezer"],
            "min_confidence": "medium",
            "prefer_front": True,
            "max_size_mb": 10,
        },
        "lyrics": {
            "enabled": True,
            "providers": ["embedded", "sidecar", "lrclib"],
            "sources": ["lrclib"],
            "prefer_synced": True,
            "allow_unsynced": True,
            "prefer_local": True,
            "prefer_existing": True,
            "embed_lyrics": True,
            "embed": True,
            "write_sidecar_lrc": False,
            "save_lrc": True,
            "save_txt": False,
            "min_confidence": "medium",
            "cache_enabled": True,
            "fallback_on_not_found": True,
            "fallback_on_low_confidence": True,
            "fallback_on_instrumental": False,
            "review_on_conflict": True,
            "review_on_existing_mismatch": True,
            "allow_instrumental": False,
            "allow_empty": False,
            "overwrite_existing": False,
            "overwrite": False,
            "selection": {
                "synced_bonus": 10,
                "local_bonus": 8,
                "existing_bonus": 12,
                "duration_tolerance_seconds": 3.0,
                "conflict_similarity_threshold": 0.75,
            },
            "online": {
                "enabled": True,
                "timeout_seconds": 20,
                "max_results": 5,
                "rate_limit_seconds": 1.0,
                "user_agent": APP_SLUG,
            },
            "provider_settings": {
                "custom_http": {
                    "enabled": False,
                    "base_url": "",
                    "api_key_env": LYRICS_API_KEY_ENV,
                    "supports_synced": True,
                    "supports_unsynced": True,
                },
            },
        },
        "output": {
            "progress": True,
            "color": True,
            "verbose": False,
            "debug": False,
        },
        "apis": {
            "lastfm_api_key": "",
            "acoustid_api_key": "",
            "discogs_token": "",
            "deezer_api_key": "",
            "itunes_storefront": "us",
            "spotify_client_id": "",
            "spotify_client_secret": "",
            "genius_access_token": "",
            "musixmatch_api_key": "",
            "audd_api_key": "",
        },
        "tools": {
            "fpcalc": "fpcalc",
        },
    }


def config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root) / APP_SLUG / "config.toml"
    return Path.home() / ".config" / APP_SLUG / "config.toml"


def load_config() -> dict[str, Any]:
    path = config_path()
    defaults = default_config()
    if not path.exists():
        return defaults
    with path.open("rb") as handle:
        user_config = tomllib.load(handle)
    return merge_config(defaults, user_config)


def save_default_config(path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_config(default_config(), comments=False), encoding="utf-8")
    return target


def merge_config(defaults: dict[str, Any], user_config: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(defaults)
    for key, value in user_config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def get_config_value(config: dict[str, Any], section: str, key: str, default: Any = None) -> Any:
    section_values = config.get(section)
    if not isinstance(section_values, dict):
        return default
    return section_values.get(key, default)


def get_api_credential(config: dict[str, Any], key: str, env_names: list[str] | None = None) -> str:
    names = env_names or [key.upper()]
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return str(get_config_value(config, "apis", key, "") or "").strip()


def render_config(config: dict[str, Any], comments: bool = False, mask_secrets: bool = False) -> str:
    lines: list[str] = []
    for section, values in _flatten_sections(config).items():
        if not isinstance(values, dict):
            continue
        if comments:
            lines.extend(_section_comment(section))
        lines.append(f"[{section}]")
        for key, value in values.items():
            output_value = _mask_secret(value) if mask_secrets and _secret_key(key) else value
            lines.append(f"{_toml_key(key)} = {_toml_value(output_value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _flatten_sections(config: dict[str, Any], prefix: str = "") -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for section, values in config.items():
        if not isinstance(values, dict):
            continue
        name = f"{prefix}.{section}" if prefix else section
        scalar_values = {key: value for key, value in values.items() if not isinstance(value, dict)}
        nested_values = {key: value for key, value in values.items() if isinstance(value, dict)}
        if scalar_values:
            sections[name] = scalar_values
        sections.update(_flatten_sections(nested_values, prefix=name))
    return sections


def masked_config(config: dict[str, Any]) -> dict[str, Any]:
    masked = copy.deepcopy(config)
    _mask_config_in_place(masked)
    return masked


def _mask_config_in_place(values: dict[str, Any]) -> None:
    for key, value in values.items():
        if isinstance(value, dict):
            _mask_config_in_place(value)
        elif _secret_key(key):
            values[key] = _mask_secret(value)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _toml_key(value: str) -> str:
    text = str(value)
    if text.replace("_", "").replace("-", "").isalnum() and " " not in text:
        return text
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _secret_key(key: str) -> bool:
    lowered = key.lower()
    return "key" in lowered or "token" in lowered or "secret" in lowered or "password" in lowered or lowered == "salt"


def _mask_secret(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "<set>"
    return f"{text[:4]}...{text[-4:]}"


def _section_comment(section: str) -> list[str]:
    comments = {
        "library": ["# Library paths and future organization template."],
        "enrich": ["# Defaults used by noqlen-forge enrich --full when no overriding CLI flag is passed."],
        "metadata": ["# Safe metadata behavior. Dry-run remains the default; --apply is still required."],
        "metadata_providers": ["# Ordered metadata providers. MusicBrainz remains identity authority; Discogs enriches catalog fields."],
        "metadata_providers.musicbrainz": ["# MusicBrainz is authoritative for MBIDs and release identity."],
        "metadata_providers.discogs": ["# Optional Discogs provider. DISCOGS_TOKEN overrides config tokens."],
        "metadata_providers.acoustid": ["# Optional Chromaprint/AcoustID identifier. ACOUSTID_KEY overrides config keys."],
        "navidrome": ["# Optional read-only Navidrome/Subsonic API backup. Prefer NOQLEN_FORGE_NAVIDROME_PASSWORD/TOKEN/SALT for secrets."],
        "audit": ["# Audit display controls for optional advanced metadata fields."],
        "cover": ["# Cover support is opt-in for future enrich integration. The cover command can still be run manually."],
        "lyrics": ["# Lyrics command defaults. Dry-run remains the default; --apply is still required."],
        "lyrics.selection": ["# Lyrics selection scoring. Explicit rules still protect existing lyrics and REVIEW conflicts."],
        "lyrics.online": ["# Shared online lyrics limits. Tests and MusicLab use fake/mock providers, not real internet."],
        "lyrics.provider_settings.custom_http": ["# Optional custom HTTP JSON lyrics endpoint. Secrets should come from the api_key_env environment variable."],
        "output": ["# Terminal output defaults."],
        "apis": ["# Optional API keys. Environment variables override these values."],
        "tools": ["# External tool paths used by local analysis providers."],
    }
    return comments.get(section, [])
