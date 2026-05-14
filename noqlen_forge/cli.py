from __future__ import annotations

import argparse
import json
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .analyze import analyze_bpm_path, analyze_features_path, analyze_key_path
from .audit import audit_path, render_audit, render_final_audit
from .audio import audio_files, get_tag, mb_album_ids, read_tracks, target_kind
from .batch import batch_targets
from .cleanup import apply_cleanup, plan_cleanup, summarize_cleanup
from .config import config_path, get_config_value, load_config, masked_config, render_config, save_default_config
from .cover import CoverResult, cover_path, process_cover
from .db import database_path, db_explain, db_query, db_status, init_db, render_status, scan_library
from .duplicates import duplicates_path
from .dev import dev_command
from .export import export_data
from .fields import FieldCategory, FieldScope, render_fields
from .lastfm import analyze_lastfm_tags
from .lyrics import LyricsStats, has_embedded_lyrics, lyrics_path, process_lyrics
from .lyrics_providers import render_provider_list
from .importer import import_path
from .jobs import JobOptions, JobStore, JobStatus, resume_job, run_workflow_as_job
from .lab import lab_command
from .metadata_providers import acoustid_plans_from_candidate, build_context, fetch_metadata_with_providers, merge_ambiguous_discogs_common_fields, merge_candidate, metadata_status, plans_from_decisions, render_metadata_output, resolve_metadata_providers
from .mood import analyze_mood_path
from .musicbrainz import get_release, hydrate_releases, search_releases
from .navidrome import navidrome_ping, playlists_backup as navidrome_playlists_backup, playlists_diff as navidrome_playlists_diff, playlists_export as navidrome_playlists_export, playlists_list as navidrome_playlists_list, playlists_push as navidrome_playlists_push, playlists_push_smart as navidrome_playlists_push_smart, playlists_status as navidrome_playlists_status, ratings_backup as navidrome_ratings_backup, ratings_diff as navidrome_ratings_diff, ratings_export as navidrome_ratings_export, ratings_restore as navidrome_ratings_restore, ratings_status as navidrome_ratings_status
from .organize import organize_path
from .reports import missing_files_report, missing_report, untracked_report
from .repair import repair_path
from .replaygain import replaygain_path
from .rewrite import rewrite_path
from .safety import SafetyError, automated_validation_enabled, require_lab_path_for_automated_apply
from .scoring import rank_releases, score_release
from .smart_playlists import smart_create, smart_delete, smart_export, smart_list, smart_refresh, smart_rename, smart_show
from .services.audit_service import AuditOptions, audit_result_from_workflow, run_audit_service
from .services.cli_helpers import load_cli_config, parse_fields, render_service_result, render_structured_service_result
from .services.core_service import CoverOptions, ReplayGainOptions, run_cover_service, run_replaygain_service
from .services import enrich_service as enrich_service_module
from .services.enrich_service import EnrichOptions, run_enrich_service
from .services.library_service import ImportOptions, OrganizeOptions, run_import_service, run_organize_service
from .services.library_maintenance_service import BatchOptions, CleanupOptions, run_batch_service, run_cleanup_service
from .services.job_service import JobsOptions, run_jobs_service
from .services.lyrics_service import LyricsOptions, render_lyrics_service_result, run_lyrics_service
from .services.maintenance_service import RepairOptions, RewriteOptions, SyncOptions, run_repair_service, run_rewrite_service, run_sync_service
from .services.metadata_service import ApplyMBIDOptions, CandidatesOptions, MetadataOptions, ReviewOptions, run_apply_mbid_service, run_candidates_service, run_metadata_service, run_review_service
from .services.playlist_service import PlaylistExportOptions, render_playlist_export_result, run_playlist_export_service
from .services.report_service import QueryOptions, build_duplicates_options, build_export_options, build_missing_files_options, build_missing_options, build_untracked_options, missing_report_title, render_report_result, report_scope_label, run_duplicates_service, run_export_service, run_missing_files_service, run_missing_service, run_query_service, run_untracked_service
from .services.types import workflow_result_to_json
from .style import set_style_path
from .sync import sync_path
from .workflow import Status
from .writers import apply_musicbrainz_writes, plan_musicbrainz_writes, plan_partial_musicbrainz_repair, summarize_partial_repair, summarize_plans


PUBLIC_COMMAND_METAVAR = "COMMAND"


class AdvancedHelpParser(argparse.ArgumentParser):
    def parse_known_args(self, args: list[str] | None = None, namespace: argparse.Namespace | None = None):  # type: ignore[override]
        self._show_advanced_help = bool(args is not None and "--advanced" in args)
        return super().parse_known_args(args, namespace)

    def format_help(self) -> str:
        advanced = bool(getattr(self, "_show_advanced_help", False))
        advanced_actions = [action for action in self._actions if getattr(action, "_noqlen_advanced", False)]
        if advanced or not advanced_actions:
            return super().format_help()

        original_epilog = self.epilog
        original_help = {action: action.help for action in advanced_actions}
        for action in advanced_actions:
            action.help = argparse.SUPPRESS
        hint = f"Run `noqlen-forge {self.prog.split(' ', 1)[1]} --advanced --help` for provider, backend and tuning options."
        self.epilog = f"{hint}\n\n{original_epilog}" if original_epilog else hint
        try:
            return super().format_help()
        finally:
            self.epilog = original_epilog
            for action, help_text in original_help.items():
                action.help = help_text


def _advanced_group(parser: argparse.ArgumentParser, title: str) -> argparse._ArgumentGroup:
    groups = getattr(parser, "_noqlen_advanced_groups", None)
    if groups is None:
        groups = {}
        parser._noqlen_advanced_groups = groups
    if title not in groups:
        groups[title] = parser.add_argument_group(title)
    return groups[title]


def _add_advanced_argument(parser: argparse.ArgumentParser, section: str, *args, **kwargs) -> argparse.Action:
    action = _advanced_group(parser, section).add_argument(*args, **kwargs)
    action._noqlen_advanced = True
    return action


def _add_advanced_help_switch(parser: argparse.ArgumentParser) -> None:
    parser._optionals.title = "Common options"
    _add_advanced_argument(parser, "Output/debug options", "--advanced", action="store_true", help="Show technical provider, backend, tuning and debug options in help output")


def _add_debug_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)


def _hide_top_level_command_choices(subparsers: argparse._SubParsersAction) -> None:
    subparsers._choices_actions = []


def _add_sync_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", type=Path)
    sync_direction = parser.add_mutually_exclusive_group()
    sync_direction.add_argument("--tags-to-db", action="store_true")
    sync_direction.add_argument("--db-to-tags", action="store_true")
    sync_direction.add_argument("--refresh", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--field", action="append")
    parser.add_argument("--fields")
    parser.add_argument("--conflict-policy", choices=("review", "db-wins", "tags-wins", "skip"))
    parser.add_argument("--verbose", action="store_true")
    _add_debug_argument(parser)


def _add_rewrite_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--field", action="append")
    parser.add_argument("--fields")
    parser.add_argument("--db-only", action="store_true")
    parser.add_argument("--tags-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    _add_debug_argument(parser)


def _add_repair_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("repair_args", nargs="*")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    _add_debug_argument(parser)


def _add_duplicates_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", type=Path)
    duplicates_scope = parser.add_mutually_exclusive_group()
    duplicates_scope.add_argument("--tracks", action="store_true")
    duplicates_scope.add_argument("--albums", action="store_true")
    parser.add_argument("--by")
    parser.add_argument("--strategy", choices=("safe", "loose", "strict"))
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--verbose", action="store_true")
    _add_debug_argument(parser)


def _add_missing_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = """Report missing library metadata without writing.

Missing Key is WARN-level optional metadata, not a critical failure. Native key detection backends are optional: auto, portable_basic, or disabled.
"""
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument("field", nargs="?")
    parser.add_argument("--field", dest="field_option")
    parser.add_argument("--fields")
    missing_scope = parser.add_mutually_exclusive_group()
    missing_scope.add_argument("--albums", action="store_true")
    missing_scope.add_argument("--tracks", action="store_true")
    parser.add_argument("--library", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--verbose", action="store_true")
    _add_debug_argument(parser)


def _add_untracked_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--verbose", action="store_true")


def _add_missing_files_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--verbose", action="store_true")


def _add_jobs_parser(subparsers: argparse._SubParsersAction) -> None:
    jobs = subparsers.add_parser(
        "jobs",
        help="Inspect resumable/cancelable workflow jobs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge jobs [OPTIONS] COMMAND ...",
        description="""Jobs are persistent records for long workflows.

Execution is still synchronous in this release. Cancellation is cooperative and resume is available only for explicitly resumable job kinds.
""",
        epilog="""Examples:
  noqlen-forge jobs list
  noqlen-forge jobs status JOB_ID
  noqlen-forge jobs status JOB_ID --format json
  noqlen-forge jobs cancel JOB_ID
  noqlen-forge jobs resume JOB_ID
  noqlen-forge jobs prune
  noqlen-forge jobs prune
""",
    )
    jobs_sub = jobs.add_subparsers(dest="jobs_command", required=True)
    list_parser = jobs_sub.add_parser("list", help="List recent jobs")
    list_parser.add_argument("--format", choices=("text", "json"), default="text")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--status", choices=tuple(item.value for item in JobStatus))
    list_parser.add_argument("--verbose", action="store_true")
    _add_debug_argument(list_parser)
    for name in ("status", "show"):
        parser = jobs_sub.add_parser(name, help="Show job status" if name == "status" else "Show job details")
        parser.add_argument("job_id")
        parser.add_argument("--format", choices=("text", "json"), default="text")
        parser.add_argument("--verbose", action="store_true")
        _add_debug_argument(parser)
    cancel = jobs_sub.add_parser("cancel", help="Cooperatively cancel a job")
    cancel.add_argument("job_id")
    cancel.add_argument("--format", choices=("text", "json"), default="text")
    resume = jobs_sub.add_parser("resume", help="Resume an explicitly resumable job")
    resume.add_argument("job_id")
    resume.add_argument("--format", choices=("text", "json"), default="text")
    prune = jobs_sub.add_parser("prune", help="Prune old job history; dry-run unless --apply")
    prune.add_argument("--apply", action="store_true")
    prune.add_argument("--format", choices=("text", "json"), default="text")
    prune.add_argument("--limit", type=int, default=20)
    prune.add_argument("--verbose", action="store_true")
    _add_debug_argument(prune)


def _add_navidrome_parser(subparsers: argparse._SubParsersAction) -> None:
    navidrome = subparsers.add_parser(
        "navidrome",
        help="Navidrome API ratings backup, diff, export and safe restore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Navidrome/Subsonic integration.

Backup/diff/export are read-oriented. Restore is dry-run by default and writes to Navidrome only with --apply after identity matching. It never writes tags or music files.
""",
        epilog="""Examples:
  noqlen-forge navidrome ping
  noqlen-forge navidrome ratings backup
  noqlen-forge navidrome ratings backup --apply
  noqlen-forge navidrome ratings status
  noqlen-forge navidrome ratings diff
  noqlen-forge navidrome ratings restore
  noqlen-forge navidrome ratings restore --apply
  noqlen-forge navidrome ratings export --format json --output navidrome-ratings.json
""",
    )
    nav_sub = navidrome.add_subparsers(dest="navidrome_command", required=True)
    nav_sub.add_parser("ping", help="Test read-only API connectivity")
    ratings = nav_sub.add_parser("ratings", help="Backup and inspect Navidrome ratings")
    ratings_sub = ratings.add_subparsers(dest="ratings_command", required=True)
    backup = ratings_sub.add_parser("backup", help="Fetch ratings/favorites; dry-run unless --apply")
    backup.add_argument("--apply", action="store_true")
    backup.add_argument("--output", type=Path)
    backup.add_argument("--format", choices=("text", "json", "csv"), default="text")
    backup.add_argument("--include-all", action="store_true", help="Reserved for a future full-library read-only scan")
    ratings_sub.add_parser("status", help="Show last local backup status")
    diff = ratings_sub.add_parser("diff", help="Compare saved backup with local library and optionally server state")
    diff.add_argument("--server", action="store_true", help="Also read current Navidrome API state")
    diff.add_argument("--backup-only", action="store_true", help="Do not call the API; compare local backup with the Noqlen Forge database only")
    diff.add_argument("--format", choices=("text", "json", "csv"), default="text")
    diff.add_argument("--output", type=Path)
    diff.add_argument("--verbose", action="store_true")
    _add_debug_argument(diff)
    export = ratings_sub.add_parser("export", help="Export saved local backup")
    export.add_argument("--format", choices=("json", "csv"), default="json")
    export.add_argument("--output", type=Path, required=True)
    restore = ratings_sub.add_parser("restore", help="Safely restore ratings/favorites to Navidrome; dry-run unless --apply")
    restore.add_argument("--apply", action="store_true")
    restore_scope = restore.add_mutually_exclusive_group()
    restore_scope.add_argument("--ratings", action="store_true", help="Restore only userRating values")
    restore_scope.add_argument("--starred", action="store_true", help="Restore only favorites")
    restore_scope.add_argument("--all", action="store_true", help="Restore ratings and favorites")
    restore.add_argument("--only-matched", action="store_true")
    restore.add_argument("--allow-medium-confidence", action="store_true")
    restore.add_argument("--force", action="store_true", help="Allow low-confidence REVIEW actions")
    restore.add_argument("--preserve-server", action="store_true", help="Do not overwrite existing server rating/favorite values")
    restore.add_argument("--format", choices=("text", "json", "csv"), default="text")
    restore.add_argument("--output", type=Path)
    restore.add_argument("--verbose", action="store_true")
    _add_debug_argument(restore)
    playlists = nav_sub.add_parser("playlists", help="List, backup, export, diff, and safely push playlists to Navidrome")
    playlists_sub = playlists.add_subparsers(dest="playlists_command", required=True)
    playlists_list = playlists_sub.add_parser("list", help="List Navidrome playlists using read-only API calls")
    playlists_list.add_argument("--format", choices=("text", "json", "csv"), default="text")
    playlists_list.add_argument("--output", type=Path)
    playlists_list.add_argument("--verbose", action="store_true")
    _add_debug_argument(playlists_list)
    playlists_backup = playlists_sub.add_parser("backup", help="Fetch Navidrome playlists; dry-run unless --apply")
    playlists_backup.add_argument("--apply", action="store_true")
    target = playlists_backup.add_mutually_exclusive_group()
    target.add_argument("--playlist-id")
    target.add_argument("--name")
    playlists_backup.add_argument("--format", choices=("text", "json", "csv"), default="text")
    playlists_backup.add_argument("--output", type=Path)
    playlists_backup.add_argument("--verbose", action="store_true")
    _add_debug_argument(playlists_backup)
    playlists_sub.add_parser("status", help="Show last local playlist backup status")
    playlists_export = playlists_sub.add_parser("export", help="Export saved playlist backup")
    playlists_export.add_argument("--format", choices=("json", "csv"), default="json")
    playlists_export.add_argument("--output", type=Path, required=True)

    def add_playlist_push_options(parser: argparse.ArgumentParser, *, include_apply: bool = True) -> None:
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--name")
        target.add_argument("--playlist-id")
        if include_apply:
            parser.add_argument("--apply", action="store_true")
        policy = parser.add_mutually_exclusive_group()
        policy.add_argument("--replace", action="store_true")
        policy.add_argument("--append", action="store_true")
        policy.add_argument("--preserve-existing", action="store_true")
        parser.add_argument("--allow-medium-confidence", action="store_true")
        parser.add_argument("--format", choices=("text", "json", "csv"), default="text")
        parser.add_argument("--output", type=Path)
        parser.add_argument("--limit", type=int)
        parser.add_argument("--sort")
        parser.add_argument("--reverse", action="store_true")
        parser.add_argument("--path-mode", choices=("absolute", "relative", "library"), default="absolute")
        parser.add_argument("--library-root", type=Path)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--verbose", action="store_true")
        _add_debug_argument(parser)

    push = playlists_sub.add_parser("push", help="Plan or push a Noqlen Forge query as a Navidrome playlist")
    push.add_argument("query")
    add_playlist_push_options(push)
    diff = playlists_sub.add_parser("diff", help="Compare a Noqlen Forge query with an existing Navidrome playlist without writing")
    diff.add_argument("query")
    add_playlist_push_options(diff, include_apply=False)
    push_smart = playlists_sub.add_parser("push-smart", help="Plan or push a saved smart playlist to Navidrome")
    push_smart.add_argument("name")
    push_smart.add_argument("--apply", action="store_true")
    smart_policy = push_smart.add_mutually_exclusive_group()
    smart_policy.add_argument("--replace", action="store_true")
    smart_policy.add_argument("--append", action="store_true")
    smart_policy.add_argument("--preserve-existing", action="store_true")
    push_smart.add_argument("--allow-medium-confidence", action="store_true")
    push_smart.add_argument("--format", choices=("text", "json", "csv"), default="text")
    push_smart.add_argument("--output", type=Path)
    push_smart.add_argument("--force", action="store_true")
    push_smart.add_argument("--verbose", action="store_true")
    _add_debug_argument(push_smart)


def _add_smart_playlist_options(parser: argparse.ArgumentParser, *, include_query_options: bool = False, include_apply: bool = False, include_export_format: bool = False, include_output: bool = False) -> None:
    if include_query_options:
        parser.add_argument("--sort")
        parser.add_argument("--reverse", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--path-mode", choices=("absolute", "relative", "library"), default="absolute")
        parser.add_argument("--library-root", type=Path)
    if include_export_format:
        parser.add_argument("--format", choices=("m3u", "m3u8", "json", "csv"))
    else:
        parser.add_argument("--format", choices=("text", "json"), default="text")
    if include_output:
        parser.add_argument("--output", type=Path)
    if include_apply:
        parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    _add_debug_argument(parser)


def _add_lab_parser(subparsers: argparse._SubParsersAction, *, help_text: str | None = None) -> argparse.ArgumentParser:
    lab = subparsers.add_parser(
        "lab",
        help=help_text,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""MusicLab maintainer validation tools.

MusicLab creates, resets and runs isolated validation fixtures for contributors. It uses a fixture library marked with .noqlen-forge-lab and must never operate on the real user library.
""",
        epilog="""Examples:
  noqlen-forge dev lab reset
  noqlen-forge dev lab list
  noqlen-forge dev lab run
  noqlen-forge dev lab run --quick
  noqlen-forge dev lab run --full
  noqlen-forge dev lab run --area lyrics
  noqlen-forge dev lab run --scenario lyrics
  noqlen-forge dev lab run --tag filesystem
  noqlen-forge dev lab run --timing
""",
    )
    if help_text == argparse.SUPPRESS:
        subparsers._choices_actions = [action for action in subparsers._choices_actions if action.dest != "lab"]
    lab_subparsers = lab.add_subparsers(dest="lab_command", required=True)
    lab_create = lab_subparsers.add_parser("create", help="Create a clean MusicLab fixture library")
    lab_create.add_argument("--path", type=Path)
    lab_subparsers.add_parser("list", help="List MusicLab scenarios and areas")
    lab_run = lab_subparsers.add_parser("run", help="Run deterministic validation flows in an isolated fixture library")
    lab_run.add_argument("--path", type=Path)
    lab_run.add_argument("--live-providers", action="store_true")
    lab_run.add_argument("--timing", action="store_true", help="Show compact per-step duration in MusicLab output")
    lab_run.add_argument("--quick", action="store_true", help="Run the reduced essential MusicLab flow")
    lab_run.add_argument("--full", action="store_true", help="Run the full MusicLab flow (default)")
    lab_run.add_argument("--scenario", help="Run one named MusicLab scenario")
    lab_run.add_argument("--area", choices=("core", "db", "jobs", "cli", "services", "lyrics", "navidrome", "playlists", "import", "organize", "sync", "reports", "review", "rewrite", "repair", "export", "safety"), help="Run MusicLab scenarios for one area")
    lab_run.add_argument("--tag", help="Run MusicLab scenarios with one tag")
    lab_run.add_argument("--simulate-failure", action="store_true", help=argparse.SUPPRESS)
    lab_reset = lab_subparsers.add_parser("reset", help="Delete a MusicLab directory after marker verification")
    lab_reset.add_argument("--path", type=Path)
    lab_doctor = lab_subparsers.add_parser("doctor", help="Check MusicLab safety and optional dependencies")
    lab_doctor.add_argument("--path", type=Path)
    return lab


def build_parser() -> argparse.ArgumentParser:
    invoked_name = Path(sys.argv[0]).name or "noqlen-forge"
    prog = invoked_name if invoked_name == "noqlen-forge" else "noqlen-forge"
    parser = AdvancedHelpParser(
        prog=prog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Noqlen Forge Core CLI prepares local music metadata safely.

Common workflow:
  config      Configure Noqlen Forge Core
  db          Initialize, scan and inspect Noqlen state
  import      Import files safely into Noqlen-managed workflows
  audit       Inspect metadata quality before changing files
  maintain    Sync, repair and rewrite with safety checks
  metadata    Search/query provider metadata
  candidates  Inspect MusicBrainz release candidates
  apply-mbid  Apply explicit MusicBrainz IDs with review
  enrich      Enrich tags, covers, lyrics and audio features
  review      Resolve review decisions
  organize    Copy/move files into a library layout

Reports and inspection:
  report      Missing fields, duplicates, untracked and missing files
  query       Query the local Noqlen database
  export      Export reports and library data as JSON/CSV

Focused tools:
  cover       Cover-art workflow
  lyrics      Lyrics workflow
  replaygain  Loudness/ReplayGain workflow
  fields      List supported metadata fields

Integrations:
  playlist    Smart playlist workflows
  navidrome   Ratings and playlist workflows

Contributor tools:
  dev         Validation and isolated MusicLab tools
""",
        epilog="""Notes:
  Compatibility aliases such as sync, missing, duplicates, untracked and missing-files remain available.
  Run `noqlen-forge COMMAND --help` for exact flags and safety notes.
  Start with read-only/help/status commands before apply/write workflows.
""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar=PUBLIC_COMMAND_METAVAR, help="Command to run.", parser_class=AdvancedHelpParser)

    _add_navidrome_parser(subparsers)
    _add_jobs_parser(subparsers)

    playlist = subparsers.add_parser(
        "playlist",
        help="Create and export playlists from library queries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Playlist tools.

Smart playlists are saved library queries. They are recalculated when exported, never write tags, never move/copy/delete music files, and never call Navidrome.
""",
        epilog="""Examples:
  noqlen-forge playlist smart create "Favorites" --query 'rating:>=4' --apply
  noqlen-forge playlist smart export "Favorites" --format m3u8 --output favorites.m3u8
  noqlen-forge playlist smart refresh "Favorites" --output favorites.m3u8 --force
  noqlen-forge playlist smart list
""",
    )
    playlist_subparsers = playlist.add_subparsers(dest="playlist_command", required=True)
    smart = playlist_subparsers.add_parser("smart", help="Manage smart playlists backed by saved queries")
    smart_subparsers = smart.add_subparsers(dest="smart_command", required=True)
    smart_create_parser = smart_subparsers.add_parser("create", help="Plan or save a smart playlist definition")
    smart_create_parser.add_argument("name")
    smart_create_parser.add_argument("--query", required=True)
    smart_create_parser.add_argument("--default-format", choices=("m3u", "m3u8", "json", "csv"), default="m3u8")
    _add_smart_playlist_options(smart_create_parser, include_query_options=True, include_apply=True)
    smart_list_parser = smart_subparsers.add_parser("list", help="List smart playlists")
    _add_smart_playlist_options(smart_list_parser)
    smart_show_parser = smart_subparsers.add_parser("show", help="Show a smart playlist")
    smart_show_parser.add_argument("name")
    _add_smart_playlist_options(smart_show_parser)
    smart_export_parser = smart_subparsers.add_parser("export", help="Export a smart playlist")
    smart_export_parser.add_argument("name")
    smart_export_parser.add_argument("--path-mode", choices=("absolute", "relative", "library"))
    smart_export_parser.add_argument("--library-root", type=Path)
    _add_smart_playlist_options(smart_export_parser, include_export_format=True, include_output=True)
    smart_refresh_parser = smart_subparsers.add_parser("refresh", help="Recalculate and export a smart playlist")
    smart_refresh_parser.add_argument("name")
    smart_refresh_parser.add_argument("--path-mode", choices=("absolute", "relative", "library"))
    smart_refresh_parser.add_argument("--library-root", type=Path)
    _add_smart_playlist_options(smart_refresh_parser, include_export_format=True, include_output=True)
    smart_delete_parser = smart_subparsers.add_parser("delete", help="Delete a smart playlist definition; dry-run unless --apply")
    smart_delete_parser.add_argument("name")
    _add_smart_playlist_options(smart_delete_parser, include_apply=True)
    smart_rename_parser = smart_subparsers.add_parser("rename", help="Rename a smart playlist definition; dry-run unless --apply")
    smart_rename_parser.add_argument("old_name")
    smart_rename_parser.add_argument("new_name")
    _add_smart_playlist_options(smart_rename_parser, include_apply=True)

    config = subparsers.add_parser("config", help="Manage global configuration")
    config_subparsers = config.add_subparsers(dest="config_command", required=True)
    config_subparsers.add_parser("path", help="Print the active config path")
    config_init = config_subparsers.add_parser("init", help="Create the default config file")
    config_init.add_argument("--force", action="store_true")
    config_subparsers.add_parser("show", help="Show merged configuration with secrets masked")

    db = subparsers.add_parser(
        "db",
        help="Database scan, query, explain and status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Database scan, query, explain and status.

Reads tags and the SQLite database. `db scan` is dry-run by default and only writes the database with --apply. Query/explain/status are read-only.
""",
        epilog="""Examples:
  noqlen-forge db status
  noqlen-forge db scan "$LIBRARY"
  noqlen-forge db scan "$LIBRARY" --apply
  noqlen-forge db query 'artist:"NewJeans"'
  noqlen-forge db explain "$ALBUM" style
""",
    )
    db_subparsers = db.add_subparsers(dest="db_command", required=True)
    db_subparsers.add_parser("path", help="Print the active library database path")
    db_subparsers.add_parser("init", help="Create the database and apply migrations")
    db_subparsers.add_parser("status", help="Show database status and counts")
    db_scan = db_subparsers.add_parser("scan", help="Scan audio files into the database; dry-run unless --apply")
    db_scan.add_argument("path", type=Path)
    db_scan.add_argument("--apply", action="store_true")
    db_scan.add_argument("--verbose", action="store_true")
    db_query_parser = db_subparsers.add_parser("query", help="Query the local library database")
    db_query_parser.add_argument("query")
    db_query_scope = db_query_parser.add_mutually_exclusive_group()
    db_query_scope.add_argument("--albums", action="store_true")
    db_query_scope.add_argument("--tracks", action="store_true")
    db_query_scope.add_argument("--files", action="store_true")
    db_query_parser.add_argument("--missing")
    db_query_parser.add_argument("--limit", type=int, default=50)
    db_query_parser.add_argument("--format", choices=("text", "json"), default="text")
    db_query_parser.add_argument("--verbose", action="store_true")
    _add_debug_argument(db_query_parser)
    db_explain_parser = db_subparsers.add_parser("explain", help="Explain provider decisions for a file or album path")
    db_explain_parser.add_argument("path", type=Path)
    db_explain_parser.add_argument("field", nargs="?")
    db_explain_parser.add_argument("--verbose", action="store_true")
    _add_debug_argument(db_explain_parser)

    report = subparsers.add_parser(
        "report",
        help="Missing fields, duplicates and untracked files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Report missing metadata, duplicate records and file/database drift.

Reports are read-only. They do not write tags, alter the SQLite database, move/copy/delete files, or print lyrics/fingerprints/secrets.
""",
        epilog="""Examples:
  noqlen-forge report missing lyrics
  noqlen-forge report duplicates
  noqlen-forge report untracked "$LIBRARY"
  noqlen-forge report missing-files
""",
    )
    report_subparsers = report.add_subparsers(dest="report_command", required=True)
    _add_missing_parser(report_subparsers.add_parser("missing", help="Report missing library metadata without writing; missing Key is WARN-level"))
    _add_duplicates_parser(report_subparsers.add_parser("duplicates", help="Detect duplicate tracks or albums without writing"))
    _add_untracked_parser(report_subparsers.add_parser("untracked", help="Report audio files on disk that are not in the database"))
    _add_missing_files_parser(report_subparsers.add_parser("missing-files", help="Report database file records missing on disk"))

    maintain = subparsers.add_parser(
        "maintain",
        help="Advanced sync/repair tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge maintain [OPTIONS] COMMAND ...",
        description="""Advanced maintenance tools.

`maintain sync` compares SQLite records and file tags. `maintain rewrite` canonicalizes configured textual metadata rules. `maintain repair` safely repairs SQLite inconsistencies. These workflows are dry-run by default; --apply is required to write database rows or tags. Automated validation refuses --apply outside MusicLab.
""",
        epilog="""Examples:
  noqlen-forge maintain sync "$ALBUM" --tags-to-db
  noqlen-forge maintain sync "$ALBUM" --db-to-tags
  noqlen-forge maintain rewrite "$ALBUM"
  noqlen-forge maintain repair missing-files
  noqlen-forge maintain repair db
""",
    )
    maintain_subparsers = maintain.add_subparsers(dest="maintain_command", required=True)
    _add_sync_arguments(maintain_subparsers.add_parser("sync", help="Synchronize SQLite database records and file tags; dry-run unless --apply"))
    _add_repair_arguments(maintain_subparsers.add_parser("repair", help="Safely repair SQLite inconsistencies; dry-run unless --apply"))
    _add_rewrite_arguments(maintain_subparsers.add_parser("rewrite", help="Canonicalize configured metadata values; dry-run unless --apply"))

    sync = subparsers.add_parser("sync", help=argparse.SUPPRESS)
    _add_sync_arguments(sync)

    _add_duplicates_parser(subparsers.add_parser("duplicates", help=argparse.SUPPRESS))

    _add_missing_parser(subparsers.add_parser("missing", help=argparse.SUPPRESS))

    _add_untracked_parser(subparsers.add_parser("untracked", help=argparse.SUPPRESS))

    _add_missing_files_parser(subparsers.add_parser("missing-files", help=argparse.SUPPRESS))

    query = subparsers.add_parser("query", help="Query the local library database")
    query.add_argument("query")
    query_scope = query.add_mutually_exclusive_group()
    query_scope.add_argument("--albums", action="store_true")
    query_scope.add_argument("--tracks", action="store_true")
    query_scope.add_argument("--files", action="store_true")
    query.add_argument("--limit", type=int, default=50)
    query.add_argument("--format", choices=("text", "json"), default="text")
    query.add_argument("--verbose", action="store_true")
    _add_debug_argument(query)

    export = subparsers.add_parser(
        "export",
        help="Export library reports and data as JSON/CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Export library reports and data.

Export is read-only except for the optional output file. It does not write tags, alter the SQLite database, move/copy/delete files, call providers, or print full lyrics/fingerprints/secrets.
""",
        epilog="""Examples:
  noqlen-forge export 'artist:"NewJeans"' --format csv --output newjeans.csv
  noqlen-forge export --missing lyrics --format csv --output missing-lyrics.csv
  noqlen-forge export --duplicates --format json --output duplicates.json
  noqlen-forge export --reviews --format json --output reviews.json
  noqlen-forge export --library --format json --output library-backup.json
""",
    )
    export.add_argument("query", nargs="?")
    export_target = export.add_mutually_exclusive_group()
    export_target.add_argument("--all", action="store_true")
    export_target.add_argument("--missing")
    export_target.add_argument("--duplicates", action="store_true")
    export_target.add_argument("--reviews", action="store_true")
    export_target.add_argument("--library", action="store_true")
    export.add_argument("--format", choices=("json", "csv"), default="json")
    export.add_argument("--output", type=Path)
    export.add_argument("--force", action="store_true")
    export_scope = export.add_mutually_exclusive_group()
    export_scope.add_argument("--albums", action="store_true")
    export_scope.add_argument("--tracks", action="store_true")
    export_scope.add_argument("--files", action="store_true")
    export.add_argument("--include-tags", action="store_true")
    export.add_argument("--include-audio", action="store_true")
    export.add_argument("--include-assets", action="store_true")
    export.add_argument("--include-provider-history", action="store_true")
    export.add_argument("--fields")
    export.add_argument("--exclude-fields")
    export.add_argument("--verbose", action="store_true")
    _add_debug_argument(export)

    fields = subparsers.add_parser(
        "fields",
        help="List supported metadata fields without writing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge fields [OPTIONS]",
        description="""List metadata fields understood by Noqlen Forge Core.

Read-only. This is a reference command for scripts, configuration, reports and advanced troubleshooting. It does not scan files, write tags, alter the SQLite database, or call providers.
""",
    )
    fields.add_argument("--category", choices=tuple(item.value for item in FieldCategory), help="Limit output to one field category")
    fields.add_argument("--scope", choices=tuple(item.value for item in FieldScope), help="Limit output to fields used by one scope")

    review = subparsers.add_parser(
        "review",
        help="List and resolve manual REVIEW decisions; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""List and resolve manual REVIEW decisions.

Examples:
  noqlen-forge review "$ALBUM"
  noqlen-forge review list "$ALBUM"
  noqlen-forge review show 1
  noqlen-forge review resolve 1 --action accept
  noqlen-forge review resolve 1 --action accept --apply
  noqlen-forge review resolve "$ALBUM" --field style --value "Progressive Metal; Death Metal" --apply
""",
    )
    review.add_argument("review_args", nargs="*")
    review.add_argument("--format", choices=("text", "json"), default="text")
    review.add_argument("--verbose", action="store_true")
    review.add_argument("--action", choices=("accept", "keep", "skip", "reject"))
    review.add_argument("--value")
    review.add_argument("--field")
    review.add_argument("--apply", action="store_true")
    review.add_argument("--force", action="store_true")

    _add_lab_parser(subparsers, help_text=argparse.SUPPRESS)

    dev = subparsers.add_parser(
        "dev",
        help="Maintainer and contributor tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Maintainer and contributor tools.

These commands are for Noqlen Forge Core maintainers and contributors. Quick checks are for implementation feedback. Full checks run non-lab pytest plus isolated MusicLab validation and are required before automatic commits.
""",
        epilog="""Examples:
  noqlen-forge dev check --quick
  noqlen-forge dev check --smoke
  noqlen-forge dev check --full
  noqlen-forge dev check --unit
  noqlen-forge dev check --contract
  noqlen-forge dev check --integration
  noqlen-forge dev check --area lyrics
  noqlen-forge dev check --changed
  noqlen-forge dev affected noqlen_forge/lyrics.py
  noqlen-forge dev check --lab
  noqlen-forge dev check --lab-quick
  noqlen-forge dev check --lab --timing
  noqlen-forge dev lab run --quick
  noqlen-forge dev lab list
""",
    )
    dev_subparsers = dev.add_subparsers(dest="dev_command", required=True)
    dev_check = dev_subparsers.add_parser("check", help="Run development validation; defaults to --quick")
    dev_check_mode = dev_check.add_mutually_exclusive_group()
    dev_check_mode.add_argument("--smoke", action="store_true", help="Run py_compile and representative --help commands")
    dev_check_mode.add_argument("--quick", action="store_true", help="Run py_compile and fast pytest selection")
    dev_check_mode.add_argument("--full", action="store_true", help="Run full commit validation without duplicating lab tests inside pytest")
    dev_check_mode.add_argument("--unit", action="store_true", help="Run unit tests only")
    dev_check_mode.add_argument("--contract", action="store_true", help="Run contract tests only")
    dev_check_mode.add_argument("--integration", action="store_true", help="Run non-slow, non-lab integration tests")
    dev_check_mode.add_argument("--lab-quick", action="store_true", help="Run reduced MusicLab validation")
    dev_check_mode.add_argument("--lab", action="store_true", help="Run MusicLab reset and validation")
    dev_check_mode.add_argument("--release", action="store_true", help="Run full validation plus release-only checks")
    dev_check_mode.add_argument("--changed", action="store_true", help="Run checks suggested by changed files")
    dev_check.add_argument("--area", choices=("lyrics", "navidrome", "playlists", "db", "service", "cli", "providers", "import", "organize", "sync"), help="Run tests for one area without MusicLab")
    dev_check.add_argument("--timing", action="store_true", help="Pass --timing to MusicLab when applicable")
    dev_affected = dev_subparsers.add_parser("affected", help="Suggest checks for changed or supplied files")
    dev_affected.add_argument("paths", nargs="*", type=Path)
    _add_lab_parser(dev_subparsers, help_text="Run isolated MusicLab validation for maintainers")

    audit = subparsers.add_parser(
        "audit",
        help="Inspect metadata completeness without writing; missing Key is warning-level optional metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Inspect metadata completeness without writing.

Missing Key is WARN-level optional metadata, not a critical failure. Native key detection backends are optional: auto, portable_basic, or disabled.
""",
    )
    audit.add_argument("path", type=Path)
    audit.add_argument("--format", choices=("text", "json"), default="text")
    audit.add_argument("--job", action="store_true", help="Record this synchronous audit as a persistent job")
    audit.add_argument("--verbose", action="store_true")
    audit.add_argument("--advanced", action="store_true")

    organize = subparsers.add_parser(
        "organize",
        help="Copy/move files into a library layout; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge organize [OPTIONS] path",
        description="""Safely plan and organize files into a library layout.

Dry-run is the default. With --apply it copies or moves files and may update operation records. Automated validation must target MusicLab for --apply.
Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries.
""",
        epilog="""Examples:
  noqlen-forge organize "$ALBUM" --library "$LIBRARY"
  noqlen-forge organize "$ALBUM" --move --library "$LIBRARY"
""",
    )
    organize.add_argument("path", type=Path, help="Album, track or folder to organize")
    organize.add_argument("--apply", action="store_true", help="Apply planned copy or move operations after review")
    organize_mode = organize.add_mutually_exclusive_group()
    organize_mode.add_argument("--copy", action="store_true", help="Plan copy operations into the target library")
    organize_mode.add_argument("--move", action="store_true", help="Plan move operations instead of copies")
    organize.add_argument("--library", type=Path, help="Target library root for planned organization")
    _add_advanced_argument(organize, "Maintenance options", "--template", help="Override the configured album path template")
    _add_advanced_argument(organize, "Maintenance options", "--singleton-template", help="Override the configured single-track path template")
    _add_advanced_argument(organize, "Maintenance options", "--conflict-policy", choices=("review", "skip", "rename"), help="Choose how path conflicts are handled")
    organize.add_argument("--verbose", action="store_true", help="Show detailed organization decisions")
    _add_advanced_help_switch(organize)
    _add_debug_argument(organize)

    import_parser = subparsers.add_parser(
        "import",
        help="Full safe import workflow; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge import [OPTIONS] path",
        description="""Run the safe import workflow for incoming files.

Dry-run is the default. With --apply it may enrich tags, copy/move files into the library, and record operations in SQLite. Automated validation must target MusicLab for --apply.
Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries.
""",
        epilog="""Examples:
  noqlen-forge import "$INCOMING" --library "$LIBRARY"
  noqlen-forge import "$INCOMING" --move --library "$LIBRARY"
""",
    )
    import_parser.add_argument("path", type=Path, help="Incoming folder or file to import")
    import_parser.add_argument("--apply", action="store_true", help="Apply planned enrichment and organization writes after review")
    import_parser.add_argument("--library", type=Path, help="Target library root for planned organization")
    import_mode = import_parser.add_mutually_exclusive_group()
    import_mode.add_argument("--copy", action="store_true", help="Plan copy operations into the target library")
    import_mode.add_argument("--move", action="store_true", help="Plan move operations instead of copies")
    import_parser.add_argument("--replaygain", action="store_true", help="Include ReplayGain analysis in the import workflow")
    _add_advanced_argument(import_parser, "Stage selection", "--skip-enrich", action="store_true", help="Skip enrichment during import")
    _add_advanced_argument(import_parser, "Stage selection", "--skip-cover", action="store_true", help="Skip cover-art work during import enrichment")
    _add_advanced_argument(import_parser, "Stage selection", "--skip-lyrics", action="store_true", help="Skip lyrics work during import enrichment")
    _add_advanced_argument(import_parser, "Stage selection", "--skip-organize", action="store_true", help="Skip organization after import enrichment")
    _add_advanced_argument(import_parser, "Maintenance options", "--allow-review", action="store_true", help="Allow REVIEW results to be recorded during import")
    _add_advanced_argument(import_parser, "Force/refresh options", "--force", action="store_true", help="Allow replacement where the workflow permits it")
    import_parser.add_argument("--verbose", action="store_true", help="Show detailed import decisions")
    _add_advanced_help_switch(import_parser)
    _add_debug_argument(import_parser)

    metadata = subparsers.add_parser(
        "metadata",
        help="Fetch provider metadata; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge metadata [OPTIONS] path",
        description="""Fetch metadata from configured providers and plan tag updates.

Dry-run is the default and is intended for review first. With --apply, accepted plans may write supported metadata tags to audio files. This command can call external metadata services according to provider configuration.
Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries.
""",
        epilog="""Examples:
  noqlen-forge metadata "$ALBUM"
  noqlen-forge metadata "$ALBUM" --dry-run
""",
    )
    metadata.add_argument("path", type=Path, help="Album, single, track or folder to inspect")
    metadata.add_argument("--apply", action="store_true", help="Write accepted metadata plans to tags")
    metadata.add_argument("--dry-run", action="store_true", help="Review planned metadata changes without writing; this is the default")
    _add_advanced_argument(metadata, "Force/refresh options", "--force", action="store_true", help="Allow replacement of existing supported fields where the workflow permits it")
    _add_advanced_argument(metadata, "Provider options", "--provider", action="append", choices=("musicbrainz", "acoustid", "discogs", "itunes", "deezer", "beatport"), help="Use a specific provider; may be repeated")
    _add_advanced_argument(metadata, "Provider options", "--allow-more-providers", action="store_true", help="Allow provider fan-out beyond the configured active-provider limit")
    _add_advanced_argument(metadata, "Metadata matching options", "--min-confidence", choices=("high", "medium", "low"), help="Minimum confidence required before a provider match can be accepted")
    _add_advanced_argument(metadata, "Provider options", "--discogs-release-id", help="Force a specific Discogs release ID for provider lookup")
    _add_advanced_argument(metadata, "Metadata matching options", "--candidate", type=int, help="Select a numbered provider candidate when supported")
    _add_advanced_argument(metadata, "Provider options", "--itunes-storefront", help="Override the iTunes storefront for this lookup")
    metadata.add_argument("--verbose", action="store_true", help="Show detailed provider attempts and decisions")
    _add_advanced_help_switch(metadata)
    _add_debug_argument(metadata)

    batch = subparsers.add_parser(
        "batch",
        help="Process child album/single targets; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge batch [OPTIONS] path",
        description="""Run enrichment over child album/single targets in batches.

Dry-run is the default and should be reviewed first. With --apply, each processed target follows the enrichment workflow and may write tags, cover, lyrics or audio feature fields according to the selected workflow options. This is an advanced convenience command for curated incoming folders.
""",
    )
    batch.add_argument("path", type=Path, help="Parent folder containing album/single targets")
    batch.add_argument("--apply", action="store_true", help="Apply each accepted child-target enrichment plan")
    batch.add_argument("--recursive", action="store_true", help="Discover targets recursively instead of direct children only")
    batch.add_argument("--yes", action="store_true", help="Skip interactive confirmation questions where the batch workflow asks")
    batch.add_argument("--continue-on-review", action="store_true", help="Continue processing later targets when one target needs manual review")

    cleanup = subparsers.add_parser(
        "cleanup",
        help="Clean empty or malformed metadata; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge cleanup [OPTIONS] path",
        description="""Plan cleanup of empty or malformed metadata values in supported audio tags.

Dry-run is the default and should be reviewed before applying. With --apply, cleanup writes tag changes to audio files. It does not move, copy or delete music files.
""",
    )
    cleanup.add_argument("path", type=Path, help="Album, track or folder to inspect")
    cleanup.add_argument("--apply", action="store_true", help="Write planned cleanup changes to tags")
    cleanup.add_argument("--dry-run", action="store_true", help="Review planned cleanup changes without writing; this is the default")
    cleanup.add_argument("--verbose", action="store_true", help="Show detailed cleanup decisions")

    cover = subparsers.add_parser(
        "cover",
        help="Detect, fetch, save and embed album cover; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge cover [OPTIONS] path",
        description="""Plan cover-art detection, fetching, sidecar saving and embedding.

Dry-run is the default posture. Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries.
""",
        epilog="""Examples:
  noqlen-forge cover "$ALBUM"
  noqlen-forge cover "$ALBUM" --save-folder-cover
""",
    )
    cover.add_argument("path", type=Path, help="Album, track or folder to inspect")
    cover.add_argument("--apply", action="store_true", help="Apply planned cover-art writes after review")
    _add_advanced_argument(cover, "Force/refresh options", "--force", action="store_true", help="Refresh cover-art decisions where the workflow permits it")
    cover.add_argument("--embed-cover", dest="embed_cover", action="store_true", default=None, help="Plan embedded cover-art writes")
    cover.add_argument("--no-embed-cover", dest="embed_cover", action="store_false", help="Skip embedded cover-art writes")
    cover.add_argument("--save-folder-cover", dest="save_folder_cover", action="store_true", default=None, help="Plan a folder cover image sidecar")
    cover.add_argument("--no-folder-cover", dest="save_folder_cover", action="store_false", help="Skip folder cover sidecar writes")
    _add_advanced_argument(cover, "Cover options", "--force-folder-cover", action="store_true", help="Refresh the folder cover decision even when one exists")
    _add_advanced_argument(cover, "Cover options", "--remove-folder-cover", action="store_true", help="Plan removal of stale folder cover sidecars when supported")
    _add_advanced_argument(cover, "Provider options", "--cover-source", action="append", choices=("local", "musicbrainz", "itunes", "deezer", "spotify"), help="Prefer a specific cover-art source; may be repeated")
    _add_advanced_argument(cover, "Cover options", "--min-cover-confidence", choices=("high", "medium", "low"), help="Minimum confidence accepted for cover-art decisions")
    cover.add_argument("--verbose", action="store_true", help="Show detailed cover-art decisions")
    _add_advanced_help_switch(cover)
    _add_debug_argument(cover)

    lyrics = subparsers.add_parser(
        "lyrics",
        help="Detect, fetch, save and embed lyrics; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge lyrics [OPTIONS] path",
        description="""Plan lyrics discovery, sidecar saving and embedding.

Dry-run is the default posture. Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries. Use `noqlen-forge lyrics providers` to list configured providers.
""",
        epilog="""Examples:
  noqlen-forge lyrics "$ALBUM"
  noqlen-forge lyrics providers
""",
    )
    lyrics.add_argument("path", nargs="?", type=Path, help="Album, track, folder, or `providers` to inspect provider setup")
    lyrics.add_argument("--apply", action="store_true", help="Apply planned lyrics writes after review")
    _add_advanced_argument(lyrics, "Force/refresh options", "--force", action="store_true", help="Refresh lyrics decisions where the workflow permits it")
    lyrics.add_argument("--embed-lyrics", dest="embed_lyrics", action="store_true", default=None, help="Plan embedded lyrics writes")
    lyrics.add_argument("--no-embed-lyrics", dest="embed_lyrics", action="store_false", help="Skip embedded lyrics writes")
    lyrics.add_argument("--save-lrc", dest="save_lrc", action="store_true", default=None, help="Plan synced .lrc sidecar writes")
    lyrics.add_argument("--no-save-lrc", dest="save_lrc", action="store_false", help="Skip synced .lrc sidecar writes")
    _add_advanced_argument(lyrics, "Lyrics options", "--write-sidecar-lrc", dest="save_lrc", action="store_true", help="Alias for --save-lrc")
    _add_advanced_argument(lyrics, "Lyrics options", "--embed", dest="embed_lyrics", action="store_true", help="Alias for --embed-lyrics")
    _add_advanced_argument(lyrics, "Lyrics options", "--save-txt", dest="save_txt", action="store_true", default=None, help="Plan plain-text lyrics sidecar writes")
    _add_advanced_argument(lyrics, "Lyrics options", "--no-save-txt", dest="save_txt", action="store_false", help="Skip plain-text lyrics sidecar writes")
    lyrics.add_argument("--prefer-synced", dest="prefer_synced", action="store_true", default=None, help="Prefer synced lyrics when available")
    lyrics.add_argument("--prefer-unsynced", dest="prefer_synced", action="store_false", help="Prefer unsynced lyrics when available")
    _add_advanced_argument(lyrics, "Lyrics options", "--unsynced", dest="prefer_synced", action="store_false", help="Alias for --prefer-unsynced")
    lyrics.add_argument("--prefer-local", dest="prefer_local", action="store_true", default=None, help="Prefer local lyrics before provider lookups")
    _add_advanced_argument(lyrics, "Lyrics options", "--no-prefer-local", dest="prefer_local", action="store_false", help="Do not prefer local lyrics before provider lookups")
    _add_advanced_argument(lyrics, "Lyrics options", "--allow-instrumental", action="store_true", default=None, help="Accept provider results marked as instrumental")
    _add_advanced_argument(lyrics, "Lyrics options", "--allow-empty", action="store_true", default=None, help="Accept empty lyrics results when a provider explicitly returns them")
    _add_advanced_argument(lyrics, "Provider options", "--provider", dest="lyrics_source", action="append", help="Prefer a specific lyrics provider; may be repeated")
    _add_advanced_argument(lyrics, "Provider options", "--lyrics-source", action="append", help="Prefer a specific lyrics source; may be repeated")
    _add_advanced_argument(lyrics, "Provider options", "--providers", help="Restrict lyrics lookup to a provider list")
    _add_advanced_argument(lyrics, "Lyrics options", "--min-lyrics-confidence", choices=("high", "medium", "low"), help="Minimum confidence accepted for lyrics decisions")
    _add_advanced_argument(lyrics, "Output/debug options", "--format", choices=("text", "json"), default="text", help="Choose text or JSON output")
    lyrics.add_argument("--verbose", action="store_true", help="Show detailed lyrics decisions")
    _add_advanced_help_switch(lyrics)
    _add_debug_argument(lyrics)

    analyze = subparsers.add_parser(
        "analyze",
        help="Analyze optional local audio features; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge analyze [OPTIONS] path",
        description="""Analyze optional local audio features and enrichment signals.

Dry-run is the default. With --apply, selected analysis results may write supported tags or local metadata fields. Some options may call configured external services, such as Last.fm. Key detection uses native optional backends: `portable_basic` is the lightweight default, `disabled` skips analysis, and `auto` follows config order.
Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries.
""",
        epilog="""Examples:
  noqlen-forge analyze "$ALBUM" --bpm
  noqlen-forge analyze "$ALBUM" --key --features
""",
    )
    analyze.add_argument("path", type=Path, help="Album, track or folder to analyze")
    analyze.add_argument("--apply", action="store_true", help="Write selected analysis results instead of previewing them")
    analyze.add_argument("--bpm", action="store_true", help="Analyze BPM")
    analyze.add_argument("--key", action="store_true", help="Analyze optional KEY/INITIALKEY metadata")
    _add_advanced_argument(analyze, "Audio analysis options", "--backend", metavar="BACKEND", help="Optional key detection backend used with --key: auto, portable_basic, or disabled")
    analyze.add_argument("--features", action="store_true", help="Analyze local audio feature fields")
    analyze.add_argument("--lastfm-tags", action="store_true", help="Fetch Last.fm tags when configured")
    analyze.add_argument("--mood", action="store_true", help="Infer mood metadata from available signals")
    _add_advanced_argument(analyze, "Provider options", "--skip-lastfm", action="store_true", help="Skip Last.fm calls even when Last.fm options are selected")
    _add_advanced_argument(analyze, "Audio analysis options", "--energy", action="store_true", help="Analyze energy when local feature extraction supports it")
    _add_advanced_argument(analyze, "Audio analysis options", "--danceability", action="store_true", help="Analyze danceability when local feature extraction supports it")
    _add_advanced_argument(analyze, "Audio analysis options", "--skip-existing", action="store_true", help="Skip tracks that already have the requested analysis fields")
    _add_advanced_argument(analyze, "Force/refresh options", "--force", action="store_true", help="Refresh analysis decisions even when existing data is present")
    _add_advanced_argument(analyze, "Audio analysis options", "--bpm-range", nargs=2, type=float, metavar=("MIN", "MAX"), default=(70, 180), help="Limit acceptable BPM detection range")
    _add_advanced_argument(analyze, "Audio analysis options", "--bpm-round", choices=("int", "1dp"), default="1dp", help="Choose BPM rounding precision")
    _add_advanced_argument(analyze, "Audio analysis options", "--feature-confidence", choices=("low", "medium", "high"), default="medium", help="Minimum confidence for audio feature decisions")
    _add_advanced_argument(analyze, "Force/refresh options", "--force-lastfm", action="store_true", help="Refresh Last.fm decisions even when existing data is present")
    _add_advanced_argument(analyze, "Force/refresh options", "--force-mood", action="store_true", help="Refresh mood decisions even when existing data is present")
    _add_advanced_argument(analyze, "Provider options", "--lastfm-min-count", type=int, default=3, help="Minimum Last.fm tag count accepted for tag decisions")
    _add_advanced_argument(analyze, "Provider options", "--lastfm-max-tags", type=int, default=10, help="Maximum Last.fm tags considered for decisions")
    _add_advanced_argument(analyze, "Output/debug options", "--lastfm-debug", action="store_true", help="Show Last.fm diagnostic details")
    _add_advanced_argument(analyze, "Output/debug options", "--lastfm-raw", action="store_true", help="Show raw Last.fm provider output for debugging")
    _add_advanced_argument(analyze, "Provider options", "--lastfm-no-fallback", action="store_true", help="Disable Last.fm fallback behavior for this run")
    analyze.add_argument("--no-progress", action="store_true", help="Disable progress rendering")
    analyze.add_argument("--no-spinner", action="store_true", help="Disable spinner rendering")
    analyze.add_argument("--plain", action="store_true", help="Use plain output for logs or scripts")
    _add_advanced_help_switch(analyze)

    replaygain = subparsers.add_parser(
        "replaygain",
        help="Analyze ReplayGain/loudness; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge replaygain [OPTIONS] path",
        description="""Plan ReplayGain/loudness analysis for supported audio files.

Dry-run is the default posture. Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries.
""",
        epilog="""Examples:
  noqlen-forge replaygain "$ALBUM"
  noqlen-forge replaygain "$ALBUM" --album
""",
    )
    replaygain.add_argument("path", type=Path, help="Album, track or folder to analyze")
    replaygain.add_argument("--apply", action="store_true", help="Apply planned ReplayGain writes after review")
    replaygain.add_argument("--force", action="store_true", help="Refresh ReplayGain analysis where the workflow permits it")
    replaygain.add_argument("--album", action="store_true", help="Analyze album-level ReplayGain")
    replaygain.add_argument("--tracks", action="store_true", help="Analyze track-level ReplayGain")
    replaygain.add_argument("--verbose", action="store_true", help="Show detailed ReplayGain decisions")
    _add_debug_argument(replaygain)

    set_style = subparsers.add_parser(
        "set-style",
        help="Set STYLE manually; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge set-style [OPTIONS] path style",
        description="""Plan a manual STYLE tag value for supported audio files.

Dry-run is the default and should be reviewed first. With --apply, this writes the STYLE tag to audio files that do not already have STYLE unless --force is used.
Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries.
""",
        epilog="""Examples:
  noqlen-forge set-style "$ALBUM" "Progressive Metal"
""",
    )
    set_style.add_argument("path", type=Path, help="Album, track or folder to update")
    set_style.add_argument("style", help="STYLE value to write, for example a semicolon-separated style list")
    set_style.add_argument("--apply", action="store_true", help="Write STYLE tags to audio files")
    set_style.add_argument("--dry-run", action="store_true", help="Preview STYLE tag writes without changing files; this is the default")
    set_style.add_argument("--force", action="store_true", help="Overwrite existing STYLE values where supported")

    candidates = subparsers.add_parser(
        "candidates",
        help="List MusicBrainz release candidates without writing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge candidates [OPTIONS] path",
        description="""List ranked MusicBrainz release candidates for a target.

Read-only. This command reads local tags and calls MusicBrainz, then prints candidate IDs for review. It does not write tags, alter the SQLite database, or modify files.
""",
    )
    candidates.add_argument("path", type=Path, help="Album, single, track or folder to match")

    apply_mbid = subparsers.add_parser(
        "apply-mbid",
        help="Apply MusicBrainz IDs; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge apply-mbid [OPTIONS] path",
        description="""Plan MusicBrainz ID tag updates from a selected or ranked release.

Dry-run is the default and should be reviewed first. With --apply, this writes MusicBrainz identifier tags to audio files. Use --release-id when you have already reviewed candidates and want a specific release.
Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries.
""",
        epilog="""Examples:
  noqlen-forge candidates "$ALBUM"
  noqlen-forge apply-mbid "$ALBUM" --release-id RELEASE_MBID
""",
    )
    apply_mbid.add_argument("path", type=Path, help="Album, single, track or folder to update")
    apply_mbid.add_argument("--release-id", help="MusicBrainz release UUID to apply after review")
    apply_mbid.add_argument("--apply", action="store_true", help="Write planned MusicBrainz ID tags to audio files")
    apply_mbid.add_argument("--dry-run", action="store_true", help="Preview MusicBrainz ID tag writes without changing files; this is the default")
    apply_mbid.add_argument("--force", action="store_true", help="Overwrite existing MusicBrainz IDs where supported")

    enrich = subparsers.add_parser(
        "enrich",
        help="Enrich tags, cover, lyrics and audio features; dry-run unless --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="noqlen-forge enrich [OPTIONS] path",
        description="""Run the safe native enrichment pipeline.

Dry-run is the default. --apply is required before writing tags, cover, lyrics or audio feature fields. Existing valid tags are not overwritten without explicit force flags.
The flow uses Noqlen Forge Core native providers through the CLI. AcoustID Identify uses fpcalc/Chromaprint when configured. Key detection is optional and skips cleanly when no configured backend is available.
Review planned writes before using --apply. Use MusicLab/fakes before testing workflows on important libraries.
""",
        epilog="""Examples:
  noqlen-forge enrich "$ALBUM" --full
  noqlen-forge enrich "$ALBUM" --cover --lyrics
""",
    )
    enrich.add_argument("path", type=Path, help="Album, track or folder to enrich")
    enrich.add_argument("--apply", action="store_true", help="Apply planned writes after review")
    enrich.add_argument("--dry-run", action="store_true", help="Preview changes without writing; this is the default posture")
    _add_advanced_argument(enrich, "Force/refresh options", "--force", action="store_true", help="Allow refresh or replacement where selected stages permit it")
    enrich.add_argument("--full", action="store_true", help="Run the standard full workflow preset")
    _add_advanced_argument(enrich, "Metadata matching options", "--acoustid-identify", action="store_true", help="Use AcoustID fingerprint identification when configured")
    _add_advanced_argument(enrich, "Metadata matching options", "--skip-acoustid-identify", action="store_true", help="Skip AcoustID fingerprint identification")
    enrich.add_argument("--analyze-bpm", action="store_true", help="Include BPM analysis")
    enrich.add_argument("--analyze-key", action="store_true", help="Run optional key detection; unavailable backends are skipped")
    enrich.add_argument("--analyze-features", action="store_true", help="Include local audio feature analysis")
    _add_advanced_argument(enrich, "Stage selection", "--skip-bpm", action="store_true", help="Skip BPM analysis in --full")
    _add_advanced_argument(enrich, "Stage selection", "--skip-key", action="store_true", help="Skip optional key detection in --full")
    _add_advanced_argument(enrich, "Stage selection", "--skip-features", action="store_true", help="Skip audio feature analysis in --full")
    _add_advanced_argument(enrich, "Stage selection", "--with-lastfm", action="store_true", help="Include Last.fm tag enrichment")
    _add_advanced_argument(enrich, "Stage selection", "--with-mood", action="store_true", help="Include mood inference")
    _add_advanced_argument(enrich, "Stage selection", "--skip-lastfm", action="store_true", help="Skip Last.fm calls even when selected by presets")
    _add_advanced_argument(enrich, "Stage selection", "--skip-mood", action="store_true", help="Skip mood inference even when selected by presets")
    enrich.add_argument("--cover", action="store_true", help="Include cover-art enrichment")
    _add_advanced_argument(enrich, "Stage selection", "--skip-cover", action="store_true", help="Skip cover-art work in --full")
    enrich.add_argument("--lyrics", action="store_true", help="Include lyrics enrichment")
    _add_advanced_argument(enrich, "Stage selection", "--skip-lyrics", action="store_true", help="Skip lyrics work in --full")
    _add_advanced_argument(enrich, "Stage selection", "--metadata-providers", action="store_true", help="Include provider metadata lookup")
    _add_advanced_argument(enrich, "Stage selection", "--skip-metadata-providers", action="store_true", help="Skip provider metadata lookup")
    enrich.add_argument("--replaygain", action="store_true", help="Include ReplayGain analysis")
    _add_advanced_argument(enrich, "Stage selection", "--skip-replaygain", action="store_true", help="Skip ReplayGain analysis in --full")
    _add_advanced_argument(enrich, "Force/refresh options", "--force-cover", action="store_true", help="Refresh cover-art decisions even when existing data is present")
    _add_advanced_argument(enrich, "Force/refresh options", "--force-lyrics", action="store_true", help="Refresh lyrics decisions even when existing data is present")
    _add_advanced_argument(enrich, "Force/refresh options", "--force-acoustid", action="store_true", help="Refresh AcoustID decisions even when existing data is present")
    _add_advanced_argument(enrich, "Force/refresh options", "--force-identity", action="store_true", help="Refresh identity decisions even when existing data is present")
    _add_advanced_argument(enrich, "Provider options", "--provider", action="append", choices=("musicbrainz", "acoustid", "discogs", "itunes", "deezer", "beatport"), help="Restrict metadata lookup to a provider; may be repeated")
    _add_advanced_argument(enrich, "Provider options", "--allow-more-providers", action="store_true", help="Allow provider fan-out beyond the configured active-provider limit")
    _add_advanced_argument(enrich, "Metadata matching options", "--min-confidence", choices=("high", "medium", "low"), help="Minimum provider confidence accepted for metadata decisions")
    _add_advanced_argument(enrich, "Cover options", "--cover-source", action="append", choices=("local", "musicbrainz", "itunes", "deezer", "spotify"), help="Prefer a specific cover-art source; may be repeated")
    _add_advanced_argument(enrich, "Lyrics options", "--lyrics-source", action="append", choices=("local", "lrclib", "genius", "musixmatch", "audd"), help="Prefer a specific lyrics source; may be repeated")
    _add_advanced_argument(enrich, "Cover options", "--min-cover-confidence", choices=("high", "medium", "low"), help="Minimum confidence accepted for cover-art decisions")
    _add_advanced_argument(enrich, "Lyrics options", "--min-lyrics-confidence", choices=("high", "medium", "low"), help="Minimum confidence accepted for lyrics decisions")
    _add_advanced_argument(enrich, "Force/refresh options", "--force-bpm", action="store_true", help="Refresh BPM decisions even when existing data is present")
    _add_advanced_argument(enrich, "Force/refresh options", "--force-key", action="store_true", help="Refresh key decisions even when existing data is present")
    _add_advanced_argument(enrich, "Force/refresh options", "--force-features", action="store_true", help="Refresh audio feature decisions even when existing data is present")
    _add_advanced_argument(enrich, "Audio analysis options", "--bpm-range", nargs=2, type=float, metavar=("MIN", "MAX"), default=(70, 180), help="Limit acceptable BPM detection range")
    _add_advanced_argument(enrich, "Audio analysis options", "--bpm-round", choices=("int", "1dp"), default="1dp", help="Choose BPM rounding precision")
    _add_advanced_argument(enrich, "Audio analysis options", "--feature-confidence", choices=("low", "medium", "high"), default="medium", help="Minimum confidence for audio feature decisions")
    _add_advanced_argument(enrich, "Force/refresh options", "--force-lastfm", action="store_true", help="Refresh Last.fm decisions even when existing data is present")
    _add_advanced_argument(enrich, "Force/refresh options", "--force-mood", action="store_true", help="Refresh mood decisions even when existing data is present")
    _add_advanced_argument(enrich, "Provider options", "--lastfm-min-count", type=int, default=3, help="Minimum Last.fm tag count accepted for tag decisions")
    _add_advanced_argument(enrich, "Provider options", "--lastfm-max-tags", type=int, default=10, help="Maximum Last.fm tags considered for decisions")
    _add_advanced_argument(enrich, "Output/debug options", "--lastfm-debug", action="store_true", help="Show Last.fm diagnostic details")
    _add_advanced_argument(enrich, "Output/debug options", "--lastfm-raw", action="store_true", help="Show raw Last.fm provider output for debugging")
    _add_advanced_argument(enrich, "Provider options", "--lastfm-no-fallback", action="store_true", help="Disable Last.fm fallback behavior for this run")
    enrich.add_argument("--verbose", action="store_true", help="Show detailed enrichment decisions")
    _add_debug_argument(enrich)
    enrich.add_argument("--no-progress", action="store_true", help="Disable progress rendering")
    enrich.add_argument("--no-spinner", action="store_true", help="Disable spinner rendering")
    enrich.add_argument("--plain", action="store_true", help="Use plain output for logs or scripts")
    enrich.add_argument("--no-color", action="store_true", help="Disable color output")
    _add_advanced_help_switch(enrich)

    _hide_top_level_command_choices(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(raw_argv)
    config = load_cli_config()
    explicit_flags = _explicit_flags(raw_argv)
    try:
        _guard_automated_apply(args)
    except SafetyError as exc:
        print(str(exc))
        return 1
    if args.command == "config":
        return config_command(args.config_command, force=getattr(args, "force", False), config=config)
    if args.command == "navidrome":
        return navidrome_command(args, config=config)
    if args.command == "playlist":
        return playlist_command(args, config=config)
    if args.command == "db":
        return db_command(args, config=config)
    if args.command == "jobs":
        return jobs_command(args, config=config)
    if args.command == "query":
        return query_command(args, config=config)
    if args.command == "export":
        return export_command(args, config=config)
    if args.command == "fields":
        return fields_command(args)
    if args.command == "review":
        return review_command(args, config=config)
    if args.command == "sync":
        return sync_command(args, config=config)
    if args.command == "maintain":
        return maintain_command(args, config=config)
    if args.command == "report":
        return report_command(args, config=config)
    if args.command == "duplicates":
        return duplicates_command(args, config=config)
    if args.command == "missing":
        return missing_command(args, config=config)
    if args.command == "untracked":
        return untracked_command(args, config=config)
    if args.command == "missing-files":
        return missing_files_command(args, config=config)
    if args.command == "lab":
        return lab_command(args)
    if args.command == "dev":
        if args.dev_command == "lab":
            return lab_command(args)
        return dev_command(args)
    if args.command == "audit":
        if args.job:
            result = run_workflow_as_job(
                JobStore(config),
                JobOptions(kind="audit", target=str(args.path), target_type="path", mode="read-only", options={"path": str(args.path), "verbose": args.verbose, "advanced": args.advanced}),
                lambda job_context: run_audit_service(AuditOptions(path=args.path, config=config, verbose=args.verbose, advanced=args.advanced)),
            )
        else:
            result = run_audit_service(AuditOptions(path=args.path, config=config, verbose=args.verbose, advanced=args.advanced))
        if args.format == "json":
            if args.job:
                output = workflow_result_to_json(result)
                code = 0 if result.status in {Status.OK, Status.WARN} else 2 if result.status == Status.REVIEW else 1
            else:
                code, output = render_structured_service_result(result)
            print(output)
            return code
        output = render_audit(audit_result_from_workflow(result), verbose=args.verbose, advanced=args.advanced)
        if args.job and result.job.get("job_id"):
            output += f"\nJob: {result.job['job_id']}"
        print(output)
        return 0
    if args.command == "organize":
        mode = "move" if args.move else "copy" if args.copy else None
        result = run_organize_service(OrganizeOptions(path=args.path, config=config, apply=args.apply, mode=mode, library=args.library, template=args.template, singleton_template=args.singleton_template, conflict_policy=args.conflict_policy, verbose=args.verbose, debug=args.debug))
        code, output = render_service_result(result)
        print(output)
        return code
    if args.command == "import":
        mode = "move" if args.move else "copy" if args.copy else None

        def run_import_enrich(target: Path, apply: bool, force: bool, cover_in_full: bool, lyrics_in_full: bool, replaygain_in_full: bool, verbose: bool, debug: bool) -> int:
            return enrich(
                target,
                apply=apply,
                force=force,
                full=True,
                cover=False,
                skip_cover=not cover_in_full,
                lyrics=False,
                skip_lyrics=not lyrics_in_full,
                replaygain=replaygain_in_full,
                skip_replaygain=not replaygain_in_full,
                verbose=verbose,
                debug=debug,
                no_progress=True,
                plain=True,
                config=config,
                explicit_flags={"--full"},
            )

        result = run_import_service(ImportOptions(path=args.path, config=config, apply=args.apply, library=args.library, mode=mode, replaygain=args.replaygain, skip_enrich=args.skip_enrich, skip_cover=args.skip_cover, skip_lyrics=args.skip_lyrics, skip_organize=args.skip_organize, allow_review=args.allow_review, force=args.force, verbose=args.verbose, debug=args.debug, enrich_runner=run_import_enrich))
        code, output = render_service_result(result)
        print(output)
        return code
    if args.command == "metadata":
        result = run_metadata_service(MetadataOptions(args.path, config=config, apply=args.apply, force=args.force, providers=args.provider, min_confidence=args.min_confidence or str(get_config_value(config, "metadata_providers", "min_confidence", "medium")), verbose=args.verbose or bool(get_config_value(config, "output", "verbose", False)), debug=args.debug or bool(get_config_value(config, "output", "debug", False)), allow_more_providers=args.allow_more_providers, discogs_release_id=args.discogs_release_id or "", candidate_index=args.candidate, itunes_storefront=args.itunes_storefront or ""))
        code, output = render_service_result(result)
        print(output)
        return code
    if args.command == "batch":
        return batch_command(args.path, apply=args.apply, recursive=args.recursive, yes=args.yes, continue_on_review=args.continue_on_review)
    if args.command == "cleanup":
        return cleanup_metadata(args.path, apply=args.apply, verbose=args.verbose)
    if args.command == "cover":
        result = run_cover_service(
            CoverOptions(
                path=args.path,
                config=config,
                apply=args.apply,
                force=args.force,
                embed_cover=args.embed_cover if args.embed_cover is not None else get_config_value(config, "cover", "embed", True),
                save_folder_cover=args.save_folder_cover if args.save_folder_cover is not None else get_config_value(config, "cover", "save_folder_cover", False),
                folder_cover_filename=get_config_value(config, "cover", "filename", "cover"),
                sources=args.cover_source or list(get_config_value(config, "cover", "sources", ["local", "musicbrainz", "itunes", "deezer"])),
                min_confidence=args.min_cover_confidence or str(get_config_value(config, "cover", "min_confidence", "medium")),
                prefer_front=bool(get_config_value(config, "cover", "prefer_front", True)),
                max_size_mb=int(get_config_value(config, "cover", "max_size_mb", 10)),
                force_folder_cover=args.force_folder_cover,
                remove_folder_cover=args.remove_folder_cover,
                verbose=args.verbose,
                debug=args.debug,
            )
        )
        code, output = render_service_result(result)
        print(output)
        return code
    if args.command == "lyrics":
        if args.path is not None and str(args.path) == "providers":
            print(render_provider_list(config, verbose=args.verbose))
            return 0
        if args.path is None:
            print("No path provided. Use `noqlen-forge lyrics providers` to list providers.")
            return 1
        result = run_lyrics_service(
            LyricsOptions(
                path=args.path,
                apply=args.apply,
                force=args.force or bool(get_config_value(config, "lyrics", "overwrite_existing", get_config_value(config, "lyrics", "overwrite", False))),
                embed_lyrics=args.embed_lyrics if args.embed_lyrics is not None else bool(get_config_value(config, "lyrics", "embed_lyrics", get_config_value(config, "lyrics", "embed", True))),
                save_lrc=args.save_lrc if args.save_lrc is not None else bool(get_config_value(config, "lyrics", "write_sidecar_lrc", get_config_value(config, "lyrics", "save_lrc", False))),
                save_txt=args.save_txt if args.save_txt is not None else bool(get_config_value(config, "lyrics", "save_txt", False)),
                prefer_synced=args.prefer_synced if args.prefer_synced is not None else bool(get_config_value(config, "lyrics", "prefer_synced", True)),
                allow_unsynced=bool(get_config_value(config, "lyrics", "allow_unsynced", True)),
                sources=_lyrics_sources_from_args(args, config),
                min_confidence=args.min_lyrics_confidence or str(get_config_value(config, "lyrics", "min_confidence", "medium")),
                verbose=args.verbose,
                debug=args.debug,
                config=config,
                prefer_local=args.prefer_local,
                allow_instrumental=args.allow_instrumental,
                allow_empty=args.allow_empty,
            )
        )
        if args.format == "json":
            code, output = render_structured_service_result(result)
            print(output)
            return code
        code, output = render_lyrics_service_result(result)
        print(output)
        return code
    if args.command == "analyze":
        return analyze_audio(args.path, apply=args.apply, bpm=args.bpm, key=args.key, key_backend=args.backend, features=args.features, energy=args.energy, danceability=args.danceability, lastfm_tags=args.lastfm_tags, mood=args.mood, skip_lastfm=args.skip_lastfm, skip_existing=args.skip_existing, force=args.force, force_lastfm=args.force_lastfm, force_mood=args.force_mood, bpm_range=tuple(args.bpm_range), bpm_round=args.bpm_round, feature_confidence=args.feature_confidence, lastfm_min_count=args.lastfm_min_count, lastfm_max_tags=args.lastfm_max_tags, lastfm_debug=args.lastfm_debug, lastfm_raw=args.lastfm_raw, lastfm_no_fallback=args.lastfm_no_fallback, no_progress=args.no_progress, no_spinner=args.no_spinner, plain=args.plain, config=config)
    if args.command == "replaygain":
        return replaygain_command(args, config=config)
    if args.command == "set-style":
        return set_style(args.path, style=args.style, apply=args.apply, force=args.force)
    if args.command == "candidates":
        return candidates(args.path)
    if args.command == "apply-mbid":
        return apply_mbid(args.path, release_id=args.release_id, apply=args.apply, force=args.force)
    if args.command == "enrich":
        return enrich(
            args.path,
            apply=args.apply,
            force=args.force,
            acoustid_identify=args.acoustid_identify,
            skip_acoustid_identify=args.skip_acoustid_identify,
            analyze_bpm=args.analyze_bpm,
            analyze_key=args.analyze_key,
            analyze_features=args.analyze_features,
            full=args.full,
            skip_bpm=args.skip_bpm,
            skip_key=args.skip_key,
            skip_features=args.skip_features,
            force_bpm=args.force_bpm,
            force_key=args.force_key,
            force_features=args.force_features,
            with_lastfm=args.with_lastfm,
            with_mood=args.with_mood,
            skip_lastfm=args.skip_lastfm,
            skip_mood=args.skip_mood,
            cover=args.cover,
            skip_cover=args.skip_cover,
            lyrics=args.lyrics,
            skip_lyrics=args.skip_lyrics,
            metadata_providers=args.metadata_providers,
            skip_metadata_providers=args.skip_metadata_providers,
            replaygain=args.replaygain,
            skip_replaygain=args.skip_replaygain,
            force_lastfm=args.force_lastfm,
            force_mood=args.force_mood,
            force_cover=args.force_cover,
            force_lyrics=args.force_lyrics,
            force_acoustid=args.force_acoustid,
            force_identity=args.force_identity,
            metadata_provider_sources=args.provider,
            allow_more_providers=args.allow_more_providers,
            min_metadata_confidence=args.min_confidence,
            cover_sources=args.cover_source,
            lyrics_sources=args.lyrics_source,
            min_cover_confidence=args.min_cover_confidence,
            min_lyrics_confidence=args.min_lyrics_confidence,
            bpm_range=tuple(args.bpm_range),
            bpm_round=args.bpm_round,
            feature_confidence=args.feature_confidence,
            lastfm_min_count=args.lastfm_min_count,
            lastfm_max_tags=args.lastfm_max_tags,
            lastfm_debug=args.lastfm_debug,
            lastfm_raw=args.lastfm_raw,
            lastfm_no_fallback=args.lastfm_no_fallback,
            verbose=args.verbose or bool(get_config_value(config, "output", "verbose", False)),
            debug=args.debug or bool(get_config_value(config, "output", "debug", False)),
            advanced=args.advanced,
            no_progress=args.no_progress or not bool(get_config_value(config, "output", "progress", True)),
            no_spinner=args.no_spinner,
            plain=args.plain or args.no_color or not bool(get_config_value(config, "output", "color", True)),
            config=config,
            explicit_flags=explicit_flags,
        )
    return 1


def config_command(command: str, force: bool = False, config: dict | None = None) -> int:
    path = config_path()
    if command == "path":
        print(path)
        return 0
    if command == "init":
        if path.exists() and not force:
            print(f"Config already exists: {path}")
            print("Use --force to overwrite.")
            return 1
        saved = save_default_config(path)
        print(f"Created config: {saved}")
        return 0
    if command == "show":
        print(render_config(masked_config(config or load_config()), mask_secrets=False))
        return 0
    return 1


def db_command(args: argparse.Namespace, config: dict | None = None) -> int:
    active_config = config or load_config()
    if args.db_command == "path":
        print(database_path(active_config))
        return 0
    if args.db_command == "init":
        print(f"Initialized database: {init_db(active_config)}")
        return 0
    if args.db_command == "status":
        print(render_status(db_status(active_config)))
        return 0
    if args.db_command == "scan":
        code, output = scan_library(active_config, args.path, apply=args.apply, verbose=args.verbose)
        print(output)
        return code
    if args.db_command == "query":
        target = "albums" if args.albums else "files" if args.files else "tracks"
        code, output = db_query(active_config, args.query, target=target, missing_field=args.missing, limit=args.limit, output_format=args.format, verbose=args.verbose, debug=args.debug)
        print(output)
        return code
    if args.db_command == "explain":
        code, output = db_explain(active_config, args.path, field=args.field, verbose=args.verbose, debug=args.debug)
        print(output)
        return code
    return 1


def playlist_command(args: argparse.Namespace, config: dict | None = None) -> int:
    active_config = config or load_config()
    if args.playlist_command != "smart":
        return 1
    command = args.smart_command
    if command == "create":
        code, output = smart_create(active_config, args.name, args.query, apply=args.apply, default_format=args.default_format, sort=args.sort, reverse=args.reverse, limit=args.limit, path_mode=args.path_mode, library_root=args.library_root, force=args.force, output_format=args.format, verbose=args.verbose, debug=args.debug)
    elif command == "list":
        code, output = smart_list(active_config, output_format=args.format, verbose=args.verbose, debug=args.debug)
    elif command == "show":
        code, output = smart_show(active_config, args.name, output_format=args.format, verbose=args.verbose, debug=args.debug)
    elif command == "export":
        result = run_playlist_export_service(PlaylistExportOptions(active_config, args.name, export_format=args.format, output=args.output, force=args.force, path_mode=args.path_mode, library_root=args.library_root, verbose=args.verbose, debug=args.debug))
        code, output = render_structured_service_result(result) if args.format == "json" else render_playlist_export_result(result, name=args.name)
    elif command == "refresh":
        result = run_playlist_export_service(PlaylistExportOptions(active_config, args.name, export_format=args.format, output=args.output, force=args.force, path_mode=args.path_mode, library_root=args.library_root, verbose=args.verbose, debug=args.debug, command="playlist smart refresh"))
        code, output = render_structured_service_result(result) if args.format == "json" else render_playlist_export_result(result, name=args.name)
    elif command == "delete":
        code, output = smart_delete(active_config, args.name, apply=args.apply, output_format=args.format, verbose=args.verbose, debug=args.debug)
    elif command == "rename":
        code, output = smart_rename(active_config, args.old_name, args.new_name, apply=args.apply, force=args.force, output_format=args.format, verbose=args.verbose, debug=args.debug)
    else:
        return 1
    print(output)
    return code


def navidrome_command(args: argparse.Namespace, config: dict | None = None) -> int:
    active_config = config or load_config()
    if args.navidrome_command == "ping":
        code, output = navidrome_ping(active_config)
        print(output)
        return code
    if args.navidrome_command == "ratings":
        if args.ratings_command == "backup":
            if getattr(args, "include_all", False):
                print("Navidrome ratings backup\nStatus: FAIL\n--include-all is reserved for a future read-only full-library scan")
                return 1
            export_format = "json" if args.format == "text" and args.output else args.format
            code, output = navidrome_ratings_backup(active_config, apply=args.apply, output=args.output, output_format=export_format)
            print(output)
            return code
        if args.ratings_command == "status":
            code, output = navidrome_ratings_status(active_config)
            print(output)
            return code
        if args.ratings_command == "diff":
            code, output = navidrome_ratings_diff(active_config, server=args.server, backup_only=args.backup_only, output_format=args.format, output=args.output, verbose=args.verbose, debug=args.debug)
            print(output)
            return code
        if args.ratings_command == "export":
            code, output = navidrome_ratings_export(active_config, output_format=args.format, output=args.output)
            print(output)
            return code
        if args.ratings_command == "restore":
            restore_ratings = not args.starred
            restore_starred = not args.ratings
            code, output = navidrome_ratings_restore(active_config, apply=args.apply, restore_ratings=restore_ratings, restore_starred=restore_starred, only_matched=args.only_matched, allow_medium_confidence=args.allow_medium_confidence, force=args.force, preserve_server=args.preserve_server, output_format=args.format, output=args.output, verbose=args.verbose, debug=args.debug)
            print(output)
            return code
    if args.navidrome_command == "playlists":
        if args.playlists_command == "list":
            code, output = navidrome_playlists_list(active_config, output_format=args.format, output=args.output, verbose=args.verbose, debug=args.debug)
            print(output)
            return code
        if args.playlists_command == "backup":
            export_format = "json" if args.format == "text" and args.output else args.format
            code, output = navidrome_playlists_backup(active_config, apply=args.apply, playlist_id=args.playlist_id, name=args.name, output_format=export_format, output=args.output, verbose=args.verbose, debug=args.debug)
            print(output)
            return code
        if args.playlists_command == "status":
            code, output = navidrome_playlists_status(active_config)
            print(output)
            return code
        if args.playlists_command == "export":
            code, output = navidrome_playlists_export(active_config, output_format=args.format, output=args.output)
            print(output)
            return code
        if args.playlists_command == "push":
            code, output = navidrome_playlists_push(active_config, args.query, name=args.name, playlist_id=args.playlist_id, apply=args.apply, replace=args.replace, append=args.append, preserve_existing=args.preserve_existing, allow_medium_confidence=args.allow_medium_confidence, force=args.force, sort=args.sort, reverse=args.reverse, limit=args.limit, path_mode=args.path_mode, library_root=args.library_root, output_format=args.format, output=args.output, verbose=args.verbose, debug=args.debug)
            print(output)
            return code
        if args.playlists_command == "diff":
            code, output = navidrome_playlists_diff(active_config, args.query, name=args.name, playlist_id=args.playlist_id, sort=args.sort, reverse=args.reverse, limit=args.limit, path_mode=args.path_mode, library_root=args.library_root, output_format=args.format, output=args.output, verbose=args.verbose, debug=args.debug)
            print(output)
            return code
        if args.playlists_command == "push-smart":
            code, output = navidrome_playlists_push_smart(active_config, args.name, apply=args.apply, replace=args.replace, append=args.append, preserve_existing=args.preserve_existing, allow_medium_confidence=args.allow_medium_confidence, force=args.force, output_format=args.format, output=args.output, verbose=args.verbose, debug=args.debug)
            print(output)
            return code
    return 1


def query_command(args: argparse.Namespace, config: dict | None = None) -> int:
    active_config = config or load_config()
    target = "albums" if args.albums else "files" if args.files else "tracks"
    result = run_query_service(QueryOptions(active_config, args.query, target=target, limit=args.limit, output_format=args.format, verbose=args.verbose, debug=args.debug))
    code, output = render_service_result(result)
    print(output)
    return code


def export_command(args: argparse.Namespace, config: dict | None = None) -> int:
    active_config = config or load_config()
    result = run_export_service(build_export_options(active_config, args.query, albums=args.albums, files=args.files, export_format=args.format, output=args.output, force=args.force, all_data=args.all, missing=args.missing, duplicates=args.duplicates, reviews=args.reviews, library=args.library, fields=args.fields, exclude_fields=args.exclude_fields, include_tags=args.include_tags, include_audio=args.include_audio, include_assets=args.include_assets, include_provider_history=args.include_provider_history, verbose=args.verbose, debug=args.debug))
    code, output = render_service_result(result)
    print(output)
    return code


def fields_command(args: argparse.Namespace) -> int:
    print(render_fields(category=args.category, scope=args.scope))
    return 0


def review_command(args: argparse.Namespace, config: dict | None = None) -> int:
    active_config = config or load_config()
    result = run_review_service(ReviewOptions(active_config, list(args.review_args or []), output_format=args.format, verbose=args.verbose, action=args.action, value=args.value, field=args.field, apply=args.apply, force=args.force))
    code, output = render_service_result(result)
    print(output)
    return code


def report_command(args: argparse.Namespace, config: dict | None = None) -> int:
    if args.report_command == "missing":
        return missing_command(args, config=config, grouped=True)
    if args.report_command == "duplicates":
        return duplicates_command(args, config=config, grouped=True)
    if args.report_command == "untracked":
        return untracked_command(args, config=config, grouped=True)
    if args.report_command == "missing-files":
        return missing_files_command(args, config=config, grouped=True)
    return 1


def maintain_command(args: argparse.Namespace, config: dict | None = None) -> int:
    if args.maintain_command == "sync":
        return sync_command(args, config=config, grouped=True)
    if args.maintain_command == "rewrite":
        return rewrite_command(args, config=config, grouped=True)
    if args.maintain_command == "repair":
        return repair_command(args, config=config)
    return 1


def repair_command(args: argparse.Namespace, config: dict | None = None) -> int:
    active_config = config or load_config()
    kind, target = _repair_kind_and_target(list(args.repair_args or []))
    result = run_repair_service(RepairOptions(active_config, target=target, kind=kind, apply=args.apply, verbose=args.verbose, debug=args.debug))
    code, output = render_service_result(result)
    print(output)
    return code


def _repair_kind_and_target(values: list[str]) -> tuple[str, Path | None]:
    if not values:
        return "all", None
    first = values[0]
    if first in {"missing-files", "missing_files", "duplicates", "duplicate", "db"}:
        return first, Path(values[1]) if len(values) > 1 else None
    if first == "untracked":
        return "untracked", Path(values[1]) if len(values) > 1 else None
    return "path", Path(first)


def rewrite_command(args: argparse.Namespace, config: dict | None = None, grouped: bool = False) -> int:
    active_config = config or load_config()
    fields = parse_fields(args.field, args.fields)
    result = run_rewrite_service(RewriteOptions(args.path, active_config, apply=args.apply, fields=fields, db_only=args.db_only, tags_only=args.tags_only, force=args.force, verbose=args.verbose, debug=args.debug))
    code, output = render_service_result(result)
    print(_with_maintain_rewrite_heading(output) if grouped else output)
    return code


def sync_command(args: argparse.Namespace, config: dict | None = None, grouped: bool = False) -> int:
    active_config = config or load_config()
    direction = "tags-to-db" if args.tags_to_db else "db-to-tags" if args.db_to_tags else "refresh" if args.refresh else None
    fields = parse_fields(args.field, args.fields)
    try:
        result = run_sync_service(SyncOptions(args.path, active_config, direction=direction, apply=args.apply, force=args.force, fields=fields, conflict_policy=args.conflict_policy, verbose=args.verbose, debug=args.debug))
        code, output = render_service_result(result)
    except ValueError as exc:
        print(str(exc))
        return 1
    print(_with_maintain_heading(output, direction) if grouped else output)
    return code


def _with_maintain_rewrite_heading(output: str) -> str:
    return f"Maintenance: Rewrite metadata\n\n{output}"


def duplicates_command(args: argparse.Namespace, config: dict | None = None, grouped: bool = False) -> int:
    active_config = config or load_config()
    options = build_duplicates_options(active_config, target=args.path, albums=args.albums, tracks=args.tracks, by=args.by, strategy=args.strategy, output_format=args.format, verbose=args.verbose, debug=args.debug)
    result = run_duplicates_service(options)
    code, output = render_report_result(result, title="Duplicate Tracks/Albums", scope=report_scope_label(args.path), output_format=args.format) if grouped else render_service_result(result)
    print(output)
    return code


def missing_command(args: argparse.Namespace, config: dict | None = None, grouped: bool = False) -> int:
    active_config = config or load_config()
    options = build_missing_options(active_config, field=args.field, field_option=args.field_option, fields_csv=args.fields, library=args.library, tracks=args.tracks, output_format=args.format, verbose=args.verbose, debug=args.debug)
    result = run_missing_service(options)
    code, output = render_report_result(result, title=missing_report_title(options.fields), scope=report_scope_label(args.library), output_format=args.format) if grouped else render_service_result(result)
    print(output)
    return code


def untracked_command(args: argparse.Namespace, config: dict | None = None, grouped: bool = False) -> int:
    active_config = config or load_config()
    options = build_untracked_options(active_config, path=args.path, library=args.library, output_format=args.format, verbose=args.verbose)
    result = run_untracked_service(options)
    code, output = render_report_result(result, title="Untracked Files", scope=report_scope_label(options.path), output_format=args.format) if grouped else render_service_result(result)
    print(output)
    return code


def missing_files_command(args: argparse.Namespace, config: dict | None = None, grouped: bool = False) -> int:
    active_config = config or load_config()
    result = run_missing_files_service(build_missing_files_options(active_config, output_format=args.format, verbose=args.verbose))
    code, output = render_report_result(result, title="Missing Files", scope="database", output_format=args.format) if grouped else render_service_result(result)
    print(output)
    return code


def jobs_command(args: argparse.Namespace, config: dict | None = None) -> int:
    active_config = config or load_config()
    command = args.jobs_command
    output_format = getattr(args, "format", "text")
    result = run_jobs_service(JobsOptions(active_config, command, job_id=getattr(args, "job_id", ""), status=getattr(args, "status", None), limit=getattr(args, "limit", 20), apply=bool(getattr(args, "apply", False)), verbose=bool(getattr(args, "verbose", False))))
    payload = result.details
    if command == "list":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) if output_format == "json" else _render_jobs_list(payload.get("jobs", [])))
        return 0 if result.status == Status.OK else 1
    if command in {"status", "show"}:
        if result.status == Status.FAIL:
            print(f"Job not found: {args.job_id}")
            return 1
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) if output_format == "json" else _render_job_status(payload, verbose=command == "show" or getattr(args, "verbose", False)))
        return 0
    if command == "cancel":
        if result.status == Status.FAIL:
            print(result.errors[0] if result.errors else f"Job not found: {args.job_id}")
            return 1
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if output_format == "json" else f"Job {args.job_id} canceled")
        return 0
    if command == "resume":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if output_format == "json" else str(payload.get("message", "")))
        return 0 if result.status == Status.OK else 1
    if command == "prune":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) if output_format == "json" else _render_jobs_prune(payload))
        return 0
    print(f"Unknown jobs command: {command}")
    return 1


def _render_jobs_list(jobs: list[dict]) -> str:
    lines = ["Noqlen Forge jobs", "Mode: READ-ONLY", f"Jobs: {len(jobs)}"]
    if not jobs:
        lines.extend(["", "No jobs found.", "Next: run a supported workflow with `--job` to create a job record.", "", "Status: OK"])
        return "\n".join(lines)
    lines.extend(["", "ID           Kind              Status     Created              Summary"])
    for job in jobs:
        lines.append(f"{job['id']:<12} {job['kind'][:17]:<17} {job['status'][:10]:<10} {str(job.get('created_at') or '')[:19]:<19} {job.get('summary') or ''}")
    lines.extend(["", "Status: OK"])
    return "\n".join(lines)


def _render_job_status(payload: dict, *, verbose: bool = False) -> str:
    job = payload["job"]
    lines = ["Noqlen Forge job", "Mode: READ-ONLY", f"Job: {job['id']}", f"Kind: {job['kind']}", f"Job status: {job['status']}", f"Progress: {job.get('progress_current', 0)}/{job.get('progress_total', 0)}", f"Summary: {job.get('summary') or ''}", "", "Steps:"]
    for step in payload.get("steps", []):
        lines.append(f"- {step['name']} {step['status']}" + (f": {step.get('summary') or ''}" if step.get("summary") else ""))
    if not payload.get("steps"):
        lines.append("No steps recorded yet.")
    if verbose:
        lines.append("")
        lines.append("Events:")
        for event in payload.get("events", []):
            lines.append(f"- {event.get('created_at', '')[:19]} {event.get('event_type')}: {event.get('message') or ''}")
    lines.extend(["", f"Status: {str(job['status']).upper()}"])
    return "\n".join(lines)


def _render_jobs_prune(result: dict) -> str:
    mode = "APPLY" if result.get("apply") else "DRY-RUN"
    lines = [f"Jobs prune: {mode}", f"Eligible: {result.get('count', 0)}"]
    for job in result.get("jobs", []):
        lines.append(f"- {job['id']} {job['kind']} {job['status']} {job.get('created_at')}")
    if not result.get("apply"):
        lines.append("No jobs removed. Use --apply to prune eligible history.")
    return "\n".join(lines)


def _with_maintain_heading(output: str, direction: str | None) -> str:
    title = {"tags-to-db": "Sync tags to database", "db-to-tags": "Sync database to tags", "refresh": "Refresh sync state"}.get(direction or "", "Sync database and tags")
    return f"Maintenance: {title}\n\n{output}"


def _explicit_flags(argv: list[str]) -> set[str]:
    return {item for item in argv if item.startswith("--")}


def _guard_automated_apply(args: argparse.Namespace) -> None:
    if not automated_validation_enabled() or not bool(getattr(args, "apply", False)):
        return
    path = getattr(args, "path", None)
    if isinstance(path, Path):
        require_lab_path_for_automated_apply(path, context=f"noqlen-forge {args.command}")


def _lyrics_sources_from_args(args: argparse.Namespace, config: dict) -> list[str]:
    if getattr(args, "providers", None):
        return [source.strip() for source in args.providers.split(",") if source.strip()]
    if getattr(args, "lyrics_source", None):
        return list(args.lyrics_source)
    providers = get_config_value(config, "lyrics", "providers", None)
    if isinstance(providers, list) and providers:
        values = [str(provider) for provider in providers]
        if any(provider in {"local", "embedded", "sidecar"} for provider in values):
            return values
        return ["local", *[provider for provider in values if provider != "local"]]
    return list(get_config_value(config, "lyrics", "sources", ["lrclib"]))


def candidates(path: Path) -> int:
    result = run_candidates_service(CandidatesOptions(path))
    code, output = render_service_result(result)
    print(output)
    return code


def apply_mbid(path: Path, release_id: str | None, apply: bool, force: bool = False) -> int:
    result = run_apply_mbid_service(ApplyMBIDOptions(path, release_id=release_id, apply=apply, force=force))
    if result.summary.get("requires_confirmation"):
        output = str(result.details.get("output_text", ""))
        if output:
            print(output)
        answer = input("Apply medium-confidence MusicBrainz match? [y/N] ").strip().lower()
        if answer not in {"y", "yes", "s", "sim"}:
            print("Cancelled")
            return 1
        result = run_apply_mbid_service(ApplyMBIDOptions(path, release_id=release_id, apply=apply, force=force, confirm_medium_confidence=True))
    code, output = render_service_result(result)
    print(output)
    return code


def cleanup_metadata(path: Path, apply: bool, verbose: bool = False) -> int:
    result = run_cleanup_service(CleanupOptions(path=path, apply=apply, verbose=verbose))
    code, output = render_service_result(result)
    print(output)
    return code


def batch_command(path: Path, apply: bool, recursive: bool = False, yes: bool = False, continue_on_review: bool = False) -> int:
    if recursive and apply and not yes:
        targets = batch_targets(path, recursive=recursive)
        if len(targets) > 20:
            answer = input(f"Apply batch to {len(targets)} targets? [y/N] ").strip().lower()
            if answer in {"y", "yes", "s", "sim"}:
                yes = True

    def process(target: Path, target_apply: bool) -> int:
        return enrich(target, apply=target_apply, force=False)

    result = run_batch_service(BatchOptions(path=path, apply=apply, recursive=recursive, yes=yes, continue_on_review=continue_on_review, process=process))
    code, output = render_service_result(result)
    print(output)
    return code


def analyze_audio(path: Path, apply: bool, bpm: bool, key: bool = False, key_backend: str | None = None, features: bool = False, energy: bool = False, danceability: bool = False, lastfm_tags: bool = False, mood: bool = False, skip_lastfm: bool = False, skip_existing: bool = False, force: bool = False, force_lastfm: bool = False, force_mood: bool = False, bpm_range: tuple[float, float] = (70, 180), bpm_round: str = "1dp", feature_confidence: str = "medium", lastfm_min_count: int = 3, lastfm_max_tags: int = 10, lastfm_debug: bool = False, lastfm_raw: bool = False, lastfm_no_fallback: bool = False, no_progress: bool = False, no_spinner: bool = False, plain: bool = False, config: dict | None = None) -> int:
    progress = _Progress(no_progress=no_progress, no_spinner=no_spinner, plain=plain)
    total = len(audio_files(path))
    if not bpm and not key and not features and not energy and not danceability and not lastfm_tags and not mood:
        bpm = True
    if bpm:
        with progress.bar("", "BPM", total) as advance:
            code, output = analyze_bpm_path(path, apply=apply, skip_existing=skip_existing, force=force, bpm_range=bpm_range, bpm_round=bpm_round, progress=advance)
        print(output)
        if code != 0:
            return code
    if key:
        with progress.spinner("", "Key"):
            code, output = analyze_key_path(path, apply=apply, force=force, config=config, backend=key_backend)
        print(output)
        if code != 0:
            return code
    if features or energy or danceability:
        with progress.bar("", "Features", total) as advance:
            code, output = analyze_features_path(path, apply=apply, energy=features or energy, danceability=features or danceability, force=force, minimum_confidence=feature_confidence, bpm_range=bpm_range, bpm_round=bpm_round, progress=advance)
        print(output)
        if code != 0:
            return code
    if lastfm_tags and skip_lastfm:
        print("Last.fm: skipped")
        return 0
    if lastfm_tags:
        with progress.spinner("", "Last.fm"):
            code, output = analyze_lastfm_tags(path, apply=apply, force=force_lastfm, min_count=lastfm_min_count, max_tags=lastfm_max_tags, debug=lastfm_debug, raw=lastfm_raw, allow_fallback=not lastfm_no_fallback)
        print(output)
        if code != 0:
            return code
    if mood:
        with progress.spinner("", "Mood"):
            code, output = analyze_mood_path(path, apply=apply, force=force_mood)
        print(output)
        return code
    return 0


def replaygain_command(args: argparse.Namespace, config: dict) -> int:
    run_album = args.album or not args.tracks
    run_tracks = args.tracks or not args.album
    result = run_replaygain_service(ReplayGainOptions(path=args.path, config=config, apply=args.apply, force=args.force, album=run_album, tracks=run_tracks, verbose=args.verbose, debug=args.debug))
    code, output = render_service_result(result)
    print(output)
    return code


def set_style(path: Path, style: str, apply: bool, force: bool) -> int:
    code, output = set_style_path(path, style=style, apply=apply, force=force)
    print(output)
    return code


def enrich(
    path: Path,
    apply: bool,
    force: bool,
    acoustid_identify: bool = False,
    skip_acoustid_identify: bool = False,
    analyze_bpm: bool = False,
    analyze_key: bool = False,
    analyze_features: bool = False,
    full: bool = False,
    skip_bpm: bool = False,
    skip_key: bool = False,
    skip_features: bool = False,
    force_bpm: bool = False,
    force_key: bool = False,
    force_features: bool = False,
    with_lastfm: bool = False,
    with_mood: bool = False,
    skip_lastfm: bool = False,
    skip_mood: bool = False,
    cover: bool = False,
    skip_cover: bool = False,
    lyrics: bool = False,
    skip_lyrics: bool = False,
    metadata_providers: bool = False,
    skip_metadata_providers: bool = False,
    replaygain: bool = False,
    skip_replaygain: bool = False,
    force_lastfm: bool = False,
    force_mood: bool = False,
    force_cover: bool = False,
    force_lyrics: bool = False,
    force_acoustid: bool = False,
    force_identity: bool = False,
    metadata_provider_sources: list[str] | None = None,
    allow_more_providers: bool = False,
    min_metadata_confidence: str | None = None,
    cover_sources: list[str] | None = None,
    lyrics_sources: list[str] | None = None,
    min_cover_confidence: str | None = None,
    min_lyrics_confidence: str | None = None,
    bpm_range: tuple[float, float] = (70, 180),
    bpm_round: str = "1dp",
    feature_confidence: str = "medium",
    lastfm_min_count: int = 3,
    lastfm_max_tags: int = 10,
    lastfm_debug: bool = False,
    lastfm_raw: bool = False,
    lastfm_no_fallback: bool = False,
    verbose: bool = False,
    debug: bool = False,
    advanced: bool = False,
    no_progress: bool = False,
    no_spinner: bool = False,
    plain: bool = False,
    config: dict | None = None,
    explicit_flags: set[str] | None = None,
) -> int:
    progress = _Progress(no_progress=no_progress, no_spinner=no_spinner, plain=plain)
    active_contexts: dict[tuple[int, str], object] = {}
    rendered_events = False

    def on_event(event: dict) -> None:
        nonlocal rendered_events
        if event["event"] == "target_start":
            rendered_events = True
            target = event.get("target") or {}
            target_name = target.get("target_name") or ""
            if target_name:
                print(f"Target: {target_name}")
            album = target.get("album") or "unknown"
            artist = target.get("artist") or "unknown"
            if album != "unknown" or artist != "unknown":
                print(f"Album: {artist} - {album}" if artist != "unknown" else f"Album: {album}")
            print(f"Files: {target.get('files', 0)}")
            print(f"Mode: {target.get('mode', 'DRY-RUN')}")
            print("")
            return
        if event["event"] == "stage_start":
            prefix = "[optional]" if event.get("optional") else f"[{event['index']}/{event['total']}]"
            context = progress.spinner(prefix, str(event["name"]))
            active_contexts[(int(event["index"]), str(event["name"]))] = context
            context.__enter__()
            return
        context = active_contexts.pop((int(event["index"]), str(event["name"])), None)
        if context is not None:
            context.__exit__(None, None, None)
        if event.get("optional"):
            _print_optional_done(str(event["name"]), str(event["status"]), str(event.get("summary") or ""))
        else:
            _print_stage_done(int(event["index"]), int(event["total"]), str(event["name"]), str(event["status"]), str(event.get("summary") or ""))
        _print_verbose(str(event.get("detail") or ""), bool(event.get("detail")))

    _sync_enrich_service_dependencies()
    result = run_enrich_service(
        EnrichOptions(
            path=path,
            config=config,
            apply=apply,
            force=force,
            acoustid_identify=acoustid_identify,
            skip_acoustid_identify=skip_acoustid_identify,
            analyze_bpm=analyze_bpm,
            analyze_key=analyze_key,
            analyze_features=analyze_features,
            full=full,
            skip_bpm=skip_bpm,
            skip_key=skip_key,
            skip_features=skip_features,
            force_bpm=force_bpm,
            force_key=force_key,
            force_features=force_features,
            with_lastfm=with_lastfm,
            with_mood=with_mood,
            skip_lastfm=skip_lastfm,
            skip_mood=skip_mood,
            cover=cover,
            skip_cover=skip_cover,
            lyrics=lyrics,
            skip_lyrics=skip_lyrics,
            metadata_providers=metadata_providers,
            skip_metadata_providers=skip_metadata_providers,
            replaygain=replaygain,
            skip_replaygain=skip_replaygain,
            force_lastfm=force_lastfm,
            force_mood=force_mood,
            force_cover=force_cover,
            force_lyrics=force_lyrics,
            force_acoustid=force_acoustid,
            force_identity=force_identity,
            metadata_provider_sources=metadata_provider_sources,
            allow_more_providers=allow_more_providers,
            min_metadata_confidence=min_metadata_confidence,
            cover_sources=cover_sources,
            lyrics_sources=lyrics_sources,
            min_cover_confidence=min_cover_confidence,
            min_lyrics_confidence=min_lyrics_confidence,
            bpm_range=bpm_range,
            bpm_round=bpm_round,
            feature_confidence=feature_confidence,
            lastfm_min_count=lastfm_min_count,
            lastfm_max_tags=lastfm_max_tags,
            lastfm_debug=lastfm_debug,
            lastfm_raw=lastfm_raw,
            lastfm_no_fallback=lastfm_no_fallback,
            verbose=verbose,
            debug=debug,
            advanced=advanced,
            explicit_flags=explicit_flags or set(),
            event_handler=on_event,
        )
    )
    for context in list(active_contexts.values()):
        context.__exit__(None, None, None)
    return _render_enrich_result(result, stages_already_rendered=rendered_events)


def _sync_enrich_service_dependencies() -> None:
    enrich_service_module.read_tracks = read_tracks
    enrich_service_module.target_kind = target_kind
    enrich_service_module.mb_album_ids = mb_album_ids
    enrich_service_module.get_tag = get_tag
    enrich_service_module.get_release = get_release
    enrich_service_module.search_releases = search_releases
    enrich_service_module.hydrate_releases = hydrate_releases
    enrich_service_module.rank_releases = rank_releases
    enrich_service_module.plan_cleanup = plan_cleanup
    enrich_service_module.apply_cleanup = apply_cleanup
    enrich_service_module.summarize_cleanup = summarize_cleanup
    enrich_service_module.audit_path = audit_path
    enrich_service_module.analyze_bpm_path = analyze_bpm_path
    enrich_service_module.analyze_key_path = analyze_key_path
    enrich_service_module.analyze_features_path = analyze_features_path
    enrich_service_module.analyze_lastfm_tags = analyze_lastfm_tags
    enrich_service_module.analyze_mood_path = analyze_mood_path
    enrich_service_module.replaygain_path = replaygain_path
    enrich_service_module.database_path = database_path
    enrich_service_module.scan_library = scan_library
    enrich_service_module.process_cover = process_cover
    enrich_service_module.process_lyrics = process_lyrics

    def metadata_stage(target: Path, options: EnrichOptions, config: dict, selected: dict[str, bool]) -> tuple[str, str, str, int]:
        status, summary, detail = _run_metadata_provider_stage(target, apply=options.apply, force=options.force, providers=options.metadata_provider_sources, min_confidence=options.min_metadata_confidence or str(get_config_value(config, "metadata_providers", "min_confidence", "medium")), verbose=options.verbose, debug=options.debug, config=config, allow_more_providers=options.allow_more_providers, exclude_musicbrainz=True, exclude_acoustid=selected["run_acoustid_identify"] or selected["skip_acoustid_identify"])
        return status, summary, detail, 0

    def acoustid_stage(target: Path, options: EnrichOptions, config: dict) -> tuple[str, str, str, int]:
        status, summary, detail = _run_acoustid_identify_stage(target, apply=options.apply, force_acoustid=options.force_acoustid, force_identity=options.force_identity, min_confidence=options.min_metadata_confidence or str(get_config_value(config, "metadata_providers", "min_confidence", "medium")), verbose=options.verbose, debug=options.debug, config=config)
        if status == "FAIL" and summary == "no supported audio files found":
            return "SKIP", summary, detail, 0
        return status, summary, detail, 0

    enrich_service_module._metadata_stage = metadata_stage
    enrich_service_module._acoustid_stage = acoustid_stage


def _print_enrich_header(tracks, apply: bool, target: Path | None = None) -> None:
    if target is not None:
        print(f"Target: {target.name}")
    album = _common_track_value(tracks, "album")
    artist = _common_track_value(tracks, "albumartist") or _common_track_value(tracks, "artist")
    if album != "unknown" or artist != "unknown":
        print(f"Album: {artist} - {album}" if artist != "unknown" else f"Album: {album}")
    print(f"Files: {len(tracks)}")
    print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")
    print("")


def _render_enrich_result(result, *, stages_already_rendered: bool = False) -> int:
    if result.errors and not result.safe_details.get("targets"):
        print(result.errors[0])
        return 1
    for target in result.details.get("targets", []):
        if not stages_already_rendered:
            target_name = target.get("target_name") or ""
            if target_name:
                print(f"Target: {target_name}")
            album = target.get("album") or "unknown"
            artist = target.get("artist") or "unknown"
            if album != "unknown" or artist != "unknown":
                print(f"Album: {artist} - {album}" if artist != "unknown" else f"Album: {album}")
            print(f"Files: {target.get('files', 0)}")
            print(f"Mode: {target.get('mode', 'DRY-RUN')}")
            print("")
            for stage in target.get("stages", []):
                if stage.get("optional"):
                    _print_optional_done(str(stage["name"]), str(stage["status"]), str(stage.get("summary") or ""))
                else:
                    _print_stage_done(int(stage["index"]), int(stage["total"]), str(stage["name"]), str(stage["status"]), str(stage.get("summary") or ""))
                _print_verbose(str(stage.get("detail") or ""), bool(stage.get("detail")))
        warnings = list(target.get("warnings") or [])
        if warnings:
            print("\nWarnings:")
            for warning in warnings:
                print(f"- {warning}")
        final_audit = str(target.get("final_audit") or "")
        if final_audit:
            print("\n" + final_audit)
    return 1 if result.status == Status.FAIL else 0


class _Progress:
    def __init__(self, no_progress: bool = False, no_spinner: bool = False, plain: bool = False) -> None:
        self.enabled = False
        self.no_spinner = no_spinner
        self.console = None
        self._progress_class = None
        self._columns = None
        if no_progress or plain or not sys.stdout.isatty():
            return
        try:
            from rich.console import Console
            from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn
        except ImportError:
            return
        self.enabled = True
        self.console = Console()
        self._progress_class = Progress
        self._columns = (
            TextColumn("{task.description:<18}"),
            BarColumn(complete_style="green", finished_style="green"),
            TextColumn("{task.completed:.0f}/{task.total:.0f}"),
            TaskProgressColumn(show_speed=False),
        )

    @contextmanager
    def spinner(self, prefix: str, name: str) -> Iterator[None]:
        if self.enabled and not self.no_spinner and self.console is not None:
            label = f"{prefix} {name}: running...".strip()
            with self.console.status(label, spinner="dots"):
                yield
            return
        _print_progress_start(prefix, name)
        yield

    @contextmanager
    def bar(self, prefix: str, name: str, total: int) -> Iterator[Callable[[int, int], None]]:
        if self.enabled and self._progress_class is not None and self._columns is not None:
            description = f"{prefix} {name}".strip()
            with self._progress_class(*self._columns, console=self.console) as bar:
                task_id = bar.add_task(description, total=max(total, 1))

                def advance(done: int, current_total: int) -> None:
                    bar.update(task_id, completed=done, total=max(current_total, 1))

                yield advance
            return
        _print_progress_start(prefix, name)
        yield lambda done, current_total: None


def _print_progress_start(prefix: str, name: str) -> None:
    if prefix.startswith("[") and prefix.endswith("]"):
        print(_stage_line(prefix, name, "running...", ""))
        return
    print(f"{name}: running...")


def _print_stage_start(index: int, total: int, name: str) -> None:
    print(_stage_line(f"[{index}/{total}]", name, "running...", ""))


def _print_stage_done(index: int, total: int, name: str, status: str, summary: str) -> None:
    print(_stage_line(f"[{index}/{total}]", name, status, summary))


def _print_optional_start(name: str) -> None:
    print(_stage_line("[optional]", name, "running...", ""))


def _print_optional_done(name: str, status: str, summary: str) -> None:
    print(_stage_line("[optional]", name, status, summary))


def _stage_line(prefix: str, name: str, status: str, summary: str) -> str:
    detail = f"     {summary}" if summary else ""
    return f"{prefix} {name:<18} {status:<6}{detail}".rstrip()


def _print_verbose(output: str, enabled: bool) -> None:
    if enabled and output:
        print(output)


def _run_metadata_provider_stage(path: Path, apply: bool, force: bool, providers: list[str] | None, min_confidence: str, verbose: bool, debug: bool, config: dict, allow_more_providers: bool, exclude_musicbrainz: bool = True, exclude_acoustid: bool = False) -> tuple[str, str, str]:
    tracks = read_tracks(path)
    if not tracks:
        return "FAIL", "no supported audio files found", ""
    context = build_context(path, tracks)
    selection = resolve_metadata_providers(config, providers=providers, allow_more_providers=allow_more_providers)
    if exclude_musicbrainz and "musicbrainz" in selection.active:
        selection.active = [source for source in selection.active if source != "musicbrainz"]
        selection.skipped.append(("musicbrainz", "identity handled by MusicBrainz stage"))
    if exclude_acoustid and "acoustid" in selection.active:
        selection.active = [source for source in selection.active if source != "acoustid"]
        selection.skipped.append(("acoustid", "identifier handled by AcoustID Identify stage"))
    if not selection.active:
        detail = render_metadata_output(context, [], [], apply=apply, status="WARN", verbose=verbose, debug=debug, selection=selection)
        return "SKIP", "no active catalog/fallback providers", detail
    attempts = fetch_metadata_with_providers(context, selection.active, config=config, debug=debug)
    selected = _metadata_selected_candidate(attempts, min_confidence)
    decisions = merge_candidate(context, selected, min_confidence=min_confidence, force=force) if selected else merge_ambiguous_discogs_common_fields(context, attempts, min_confidence=min_confidence, force=force)
    plans = acoustid_plans_from_candidate(tracks, selected, force=False) if selected and selected.provider == "acoustid" else plans_from_decisions(tracks, decisions)
    errors = apply_musicbrainz_writes(plans, apply=apply)
    if errors:
        return "FAIL", errors[0], "\n".join(errors)
    status = metadata_status(attempts, decisions, selected)
    detail = render_metadata_output(context, attempts, decisions, apply=apply, status=status, verbose=verbose, debug=debug, selection=selection)
    return status, _metadata_provider_summary(selection.active, attempts, decisions), detail


def _run_acoustid_identify_stage(path: Path, apply: bool, force_acoustid: bool, force_identity: bool, min_confidence: str, verbose: bool, debug: bool, config: dict) -> tuple[str, str, str]:
    tracks = read_tracks(path)
    if not tracks:
        return "FAIL", "no supported audio files found", ""
    context = build_context(path, tracks)
    attempts = fetch_metadata_with_providers(context, ["acoustid"], config=config, debug=debug)
    selected = attempts[0].candidates[0] if attempts and attempts[0].candidates else None
    decisions = merge_candidate(context, selected, min_confidence=min_confidence, force=False) if selected else []
    plans = acoustid_plans_from_candidate(tracks, selected, force=False, force_acoustid=force_acoustid, force_identity=force_identity)
    errors = apply_musicbrainz_writes(plans, apply=apply)
    if errors:
        return "FAIL", errors[0], "\n".join(errors)
    status = metadata_status(attempts, decisions, selected)
    if attempts and attempts[0].status == "WARN" and "fpcalc not found" in attempts[0].message:
        status = "SKIP"
    detail = render_metadata_output(context, attempts, decisions, apply=apply, status=status, verbose=verbose, debug=debug)
    return status, _acoustid_identify_summary(attempts, selected), detail


def _acoustid_identify_summary(attempts, candidate) -> str:
    total = 0
    if candidate:
        decisions = candidate.extra.get("decisions", [])
        total = len(decisions) if isinstance(decisions, list) else 0
    attempt = attempts[0] if attempts else None
    if attempt and "fpcalc not found" in attempt.message:
        return "fpcalc not found"
    fingerprint_count = int(candidate.extra.get("fingerprint_count", 0)) if candidate else 0
    match_count = int(candidate.extra.get("match_count", 0)) if candidate else 0
    if attempt and "lookup skipped" in attempt.message:
        return f"fingerprints {fingerprint_count}/{total}, lookup skipped no API key"
    if candidate and candidate.extra.get("conflicts"):
        return "recording IDs conflict with existing MBIDs"
    return f"fingerprints {fingerprint_count}/{total}, matches {match_count}/{total}"


def _metadata_selected_candidate(attempts, min_confidence: str):
    allowed = {"high": 3, "medium": 2, "low": 1}
    minimum = allowed.get(min_confidence, 2)
    candidates = [candidate for attempt in attempts if attempt.status == "OK" for candidate in attempt.candidates]
    candidates = [candidate for candidate in candidates if candidate.provider != "musicbrainz" and allowed.get(candidate.confidence, 0) >= minimum]
    return max(candidates, key=lambda item: item.score, default=None)


def _metadata_provider_summary(active: list[str], attempts, decisions) -> str:
    if any(attempt.status == "REVIEW" for attempt in attempts):
        writes = sum(1 for decision in decisions if decision.action == "write")
        if writes:
            return "discogs ambiguous editions, wrote safe fields only"
        return "discogs ambiguous editions"
    warnings = [attempt for attempt in attempts if attempt.status in {"WARN", "SKIP"}]
    if warnings:
        return ", ".join(f"{attempt.provider} {attempt.message}" for attempt in warnings[:2])
    selected_fields = [decision.field.replace("_", " ") for decision in decisions if decision.action == "write"]
    roles = {"discogs": "catalog", "deezer": "fallback", "itunes": "fallback", "musicbrainz": "identity"}
    provider_summary = ", ".join(f"{source} {roles.get(source, 'fallback')}" for source in active)
    if selected_fields:
        return f"{provider_summary}, selected {'/'.join(selected_fields)}"
    return provider_summary


def _common_track_value(tracks, attr: str) -> str:
    values = [getattr(track, attr, "") for track in tracks if getattr(track, attr, "")]
    return max(set(values), key=values.count) if values else "unknown"


def resolve_enrich_options(
    config: dict,
    full: bool,
    analyze_bpm: bool,
    analyze_key: bool,
    analyze_features: bool,
    with_lastfm: bool,
    with_mood: bool,
    acoustid_identify: bool = False,
    skip_acoustid_identify: bool = False,
    skip_bpm: bool = False,
    skip_key: bool = False,
    skip_features: bool = False,
    skip_lastfm: bool = False,
    skip_mood: bool = False,
    cover: bool = False,
    skip_cover: bool = False,
    lyrics: bool = False,
    skip_lyrics: bool = False,
    metadata_providers: bool = False,
    skip_metadata_providers: bool = False,
    replaygain: bool = False,
    skip_replaygain: bool = False,
    explicit_flags: set[str] | None = None,
) -> dict[str, bool]:
    explicit_flags = explicit_flags or set()

    def include(name: str, default: bool = True) -> bool:
        return bool(get_config_value(config, "enrich", f"full_includes_{name}", default))

    run_bpm = analyze_bpm or (full and include("bpm"))
    run_key = analyze_key or (full and include("key", False))
    run_features = analyze_features or (full and include("features"))
    run_lastfm = with_lastfm or (full and include("lastfm"))
    run_mood = with_mood or (full and include("mood"))
    run_cover = cover or (full and include("cover", False) and bool(get_config_value(config, "cover", "enabled", False)))
    run_lyrics = lyrics or (full and include("lyrics", False) and bool(get_config_value(config, "lyrics", "enabled", False)))
    run_metadata_providers = metadata_providers or (full and include("metadata_providers") and bool(get_config_value(config, "metadata_providers", "enabled", True)))
    run_replaygain = replaygain or (full and include("replaygain", False) and bool(get_config_value(config, "audio", "replaygain_enabled", True)))
    run_acoustid_identify = acoustid_identify or (full and include("acoustid_identification") and bool(get_config_value(config, "metadata_providers", "enabled", True)))
    run_cleanup = not (full and not include("cleanup"))
    if "--skip-bpm" in explicit_flags or skip_bpm:
        run_bpm = False
    if "--skip-key" in explicit_flags or skip_key:
        run_key = False
    if "--skip-features" in explicit_flags or skip_features:
        run_features = False
    if "--skip-lastfm" in explicit_flags or skip_lastfm:
        run_lastfm = False
    if "--skip-mood" in explicit_flags or skip_mood:
        run_mood = False
    if "--cover" in explicit_flags or cover:
        run_cover = True
    if "--skip-cover" in explicit_flags or skip_cover:
        run_cover = False
    if "--lyrics" in explicit_flags or lyrics:
        run_lyrics = True
    if "--skip-lyrics" in explicit_flags or skip_lyrics:
        run_lyrics = False
    if "--metadata-providers" in explicit_flags or metadata_providers:
        run_metadata_providers = True
    if "--skip-metadata-providers" in explicit_flags or skip_metadata_providers:
        run_metadata_providers = False
    if "--replaygain" in explicit_flags or replaygain:
        run_replaygain = True
    if "--skip-replaygain" in explicit_flags or skip_replaygain:
        run_replaygain = False
    if "--acoustid-identify" in explicit_flags or acoustid_identify:
        run_acoustid_identify = True
    if "--skip-acoustid-identify" in explicit_flags or skip_acoustid_identify:
        run_acoustid_identify = False
    return {
        "run_bpm": run_bpm,
        "run_key": run_key,
        "run_features": run_features,
        "run_lastfm": run_lastfm,
        "run_mood": run_mood,
        "run_cover": run_cover,
        "run_lyrics": run_lyrics,
        "run_metadata_providers": run_metadata_providers,
        "run_replaygain": run_replaygain,
        "run_acoustid_identify": run_acoustid_identify,
        "skip_acoustid_identify": skip_acoustid_identify,
        "run_cleanup": run_cleanup,
    }


def _first_line(output: str) -> str:
    return next((line.strip() for line in output.splitlines() if line.strip()), "failed")


def _generic_stage_status(output: str, ok_summary: str) -> tuple[str, str]:
    lower = output.lower()
    if "skipped" in lower:
        return "SKIP", _compact_skip_reason(output)
    if "failed" in lower or "error" in lower or "not found" in lower:
        return "WARN", _first_line(output)
    return "OK", ok_summary


def _compact_skip_reason(output: str) -> str:
    lower = output.lower()
    if "not installed" in lower or "not set" in lower or "not available" in lower:
        return "optional backend unavailable"
    return "skipped"


def _musicbrainz_status(plans, total: int, skipped: bool = False, existing_ids: bool = False) -> tuple[str, str]:
    if skipped:
        return "SKIP", "IDs already present"
    if not plans:
        return "WARN", "no IDs written"
    written = sum(1 for plan in plans if plan.changes)
    fields = sorted({field for plan in plans for field in plan.changes})
    original_date = sum(1 for plan in plans if "Original Date" in plan.changes)
    if existing_ids and original_date:
        extra_fields = [field for field in fields if field != "Original Date"]
        summary = f"existing IDs, repaired Original Date {original_date}/{total}"
        if extra_fields:
            names = ", ".join(field.removeprefix("MusicBrainz ").lower() for field in extra_fields[:3])
            if len(extra_fields) > 3:
                names += ", ..."
            summary += f", repaired {names}"
        return "OK", summary
    names = ", ".join(field.removeprefix("MusicBrainz ").lower() for field in fields[:4])
    if len(fields) > 4:
        names += ", ..."
    return "OK", f"{written}/{total} files, wrote {names}" if names else f"{written}/{total} files"


def _musicbrainz_identity_complete(tracks) -> bool:
    return bool(tracks) and all(get_tag(track, "mb_album_id") and get_tag(track, "mb_track_id") and get_tag(track, "mb_release_group_id") for track in tracks)


def _cleanup_summary(plans) -> str:
    removed = 0
    normalized = 0
    for plan in plans:
        removed += len(getattr(plan, "remove", []))
        removed += sum(len(values) for values in getattr(plan, "remove_values", {}).values())
        normalized += len(getattr(plan, "set_values", {}))
    if normalized:
        return f"removed {removed} empty/bad fields, normalized {normalized} tags"
    return f"removed {removed} empty/bad fields"


def _bpm_status(output: str, total: int) -> tuple[str, str]:
    written = _count_actions(output)
    existing = _count_matching_lines(output, "skipped existing BPM")
    warnings = _count_matching_lines(output, "warning=")
    final = existing + written
    status = "WARN" if warnings or final < total else "OK"
    summary = f"existing {existing}/{total}, written {written}" if existing else f"{written}/{total} written"
    if warnings:
        summary += f", {warnings} half-time warnings"
    return status, summary


def _features_status(output: str, total: int) -> tuple[str, str]:
    energy = _count_feature_actions(output, "ENERGY")
    danceability = _count_feature_actions(output, "DANCEABILITY")
    low = _count_matching_lines(output, "action=skipped")
    status = "WARN" if low else "OK"
    return status, f"energy {energy}/{total}, danceability {danceability}/{total}"


def _replaygain_status(output: str, total: int) -> tuple[str, str]:
    lower = output.lower()
    if "ffmpeg not found" in lower:
        return "SKIP", "optional backend unavailable"
    if "status: warn" in lower:
        return "WARN", _first_line(output)
    match = re.search(r"ReplayGain Track:\s*(\d+)/(\d+).*ReplayGain Album:\s*(\d+)/(\d+)", output, flags=re.DOTALL)
    if match:
        return "OK", f"track {match.group(1)}/{match.group(2)}, album {match.group(3)}/{match.group(4)}"
    return "OK", f"{total}/{total} tracks"


def _lastfm_status(output: str, total: int) -> tuple[str, str]:
    if "LASTFM_API_KEY not set" in output:
        return "SKIP", "optional backend unavailable"
    written = sum(1 for line in output.splitlines() if " tags=" in line and "action=" in line)
    existing = _count_matching_lines(output, "skipped existing LASTFM_TAGS")
    sources = re.findall(r"source=([^\s]+)", output)
    if sources:
        unique = sorted(set(sources))
        source_summary = f", source={unique[0]}" if len(unique) == 1 else ", sources: " + ", ".join(f"{source} {sources.count(source)}" for source in unique)
    else:
        source_summary = ""
    final = existing + written
    status = "OK" if final == total else "WARN"
    summary = f"existing {existing}/{total}, written {written}" if existing else f"tags {written}/{total}"
    return status, f"{summary}{source_summary}"


def _mood_status(output: str, total: int) -> tuple[str, str]:
    written = sum(1 for line in output.splitlines() if "mood=" in line and "mood=none" not in line and "action=" in line and "skipped" not in line)
    existing = _count_matching_lines(output, "skipped existing MOOD")
    low = _count_matching_lines(output, "confidence=low")
    final = existing + written
    status = "WARN" if low or final < total else "OK"
    summary = f"existing {existing}/{total}, written {written}" if existing else f"mood {written}/{total}"
    if low:
        summary += f", low confidence {low}/{total}"
    return status, summary


def _cover_status(result: CoverResult, apply: bool, force: bool) -> tuple[str, str]:
    total = result.total
    folder = "folder cover found" if result.local_cover else "folder cover skipped"
    if result.save_folder_cover:
        folder = f"saved {result.saved_path.name}" if result.saved_path else "folder cover missing"
    if result.embedded_existing == total and not force and not result.save_folder_cover:
        return "SKIP", f"embedded cover already present {total}/{total}"
    if result.image is None:
        return "WARN", "no cover found"
    if apply:
        return ("WARN" if result.errors or result.existing_after < total else "OK"), f"embedded {result.existing_after}/{total}, {folder}"
    write_count = total if force else max(0, total - result.embedded_existing)
    return "DRY", f"would write embedded {write_count}/{total}, {folder}"


def _lyrics_status(result: LyricsStats, apply: bool, force: bool) -> tuple[str, str]:
    total = result.total
    if result.embedded_existing == total and not force:
        return "SKIP", f"existing lyrics already present {total}/{total}"
    if result.errors:
        return "FAIL", result.errors[0]
    if not result.per_file:
        return "WARN", "no lyrics found"
    synced = result.synced_found
    if apply:
        status = "OK" if result.lyrics_after == total else "WARN"
        return status, f"embedded {result.lyrics_after}/{total}, synced {synced}/{total}"
    write_count = sum(1 for track in result.tracks if track.path in result.per_file and (force or not (has_embedded_lyrics(track.path) or get_tag(track, "lyrics"))))
    status = "DRY" if write_count else "SKIP"
    return status, f"would write embedded {write_count}/{total}, synced {synced}/{total}"


def _key_status(output: str, total: int) -> tuple[str, str]:
    written = _count_actions(output)
    skipped = _count_matching_lines(output, "action=skipped")
    return ("WARN" if skipped else "OK"), f"key {written}/{total}"


def _count_actions(output: str) -> int:
    return sum(1 for line in output.splitlines() if "action=wrote" in line or "action=would write" in line)


def _count_feature_actions(output: str, name: str) -> int:
    return sum(1 for line in output.splitlines() if name in line and ("action=wrote" in line or "action=would write" in line))


def _count_matching_lines(output: str, needle: str) -> int:
    return sum(1 for line in output.splitlines() if needle in line)


def _apply_best_musicbrainz(path: Path, tracks, apply: bool, force: bool, verbose: bool = False):
    ranked = rank_releases(tracks, hydrate_releases(search_releases(tracks)))
    if not ranked:
        if verbose:
            print("No matching release candidates were found. Try --release-id UUID or check artist/album/title tags.")
        return []
    scored = ranked[0]
    if verbose:
        print(f"MusicBrainz candidate score={scored.score} release={scored.release.get('id')}")
    if scored.score < 80:
        if verbose:
            print("Score below 80; review required before applying MusicBrainz IDs.")
        return []
    if 80 <= scored.score < 95 and apply:
        answer = input("Apply medium-confidence MusicBrainz match? [y/N] ").strip().lower()
        if answer not in {"y", "yes", "s", "sim"}:
            print("Skipped MusicBrainz ID application.")
            return []
    plans = plan_musicbrainz_writes(tracks, scored.release, force=force)
    errors = apply_musicbrainz_writes(plans, apply=apply)
    if errors:
        print("MusicBrainz write verification failed:")
        for error in errors:
            print(f"- {error}")
        return plans
    if verbose:
        print(summarize_plans(plans, apply=apply, verbose=True))
    return plans


def _repair_partial_musicbrainz(tracks, release_id: str, apply: bool, verbose: bool = False):
    try:
        release = get_release(release_id)
    except Exception as exc:
        print("MusicBrainz partial repair:")
        print(f"- REVIEW: could not fetch existing release {release_id}: {exc}")
        return []
    release_tracks = sum(len(medium.get("tracks", []) or []) for medium in release.get("media", []) or [])
    if release_tracks != len(tracks):
        print("MusicBrainz partial repair:")
        print(f"- REVIEW: existing release {release_id} has {release_tracks} tracks, local target has {len(tracks)}")
        return []
    plans = plan_partial_musicbrainz_repair(tracks, release)
    errors = apply_musicbrainz_writes(plans, apply=apply)
    if errors:
        print("MusicBrainz partial repair:")
        print("- REVIEW: write verification failed")
        for error in errors:
            print(f"- {error}")
        return plans
    if verbose:
        print(summarize_partial_repair(plans, apply=apply))
    return plans


def _musicbrainz_repaired_fields(plans) -> dict[Path, set[str]]:
    fields_by_path: dict[Path, set[str]] = {}
    field_names = {
        "MusicBrainz Album Id": "mb_album_id",
        "MusicBrainz Release Group Id": "mb_release_group_id",
        "MusicBrainz Track Id": "mb_track_id",
        "MusicBrainz Release Track Id": "mb_release_track_id",
        "MusicBrainz Album Artist Id": "mb_album_artist_id",
        "Original Date": "originaldate",
        "Label": "label",
    }
    for plan in plans:
        fields = {field_names[field] for field in plan.changes if field in field_names}
        if fields:
            fields_by_path[plan.path] = fields
    return fields_by_path


def _enrichment_targets(path: Path, kind: str) -> list[Path]:
    if kind in {"single", "album"}:
        return [path]
    targets: list[Path] = []
    for child in sorted(path.iterdir()):
        child_kind = target_kind(child)
        if child_kind in {"single", "album"}:
            targets.append(child)
    return targets


if __name__ == "__main__":
    raise SystemExit(main())
