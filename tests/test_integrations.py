import json
import sys
import types
from pathlib import Path

from mutagen.id3 import ID3

from noqlen_forge.audit import AuditResult
from noqlen_forge.audio import Track, target_kind
from noqlen_forge import cli
from noqlen_forge.cli import build_parser
from noqlen_forge.cover import detect_embedded_cover, validate_image_bytes
from noqlen_forge.metadata_providers import MetadataCandidate, ProviderAttempt
from noqlen_forge.writers import WritePlan
from noqlen_forge.lyrics import has_embedded_lyrics, read_embedded_lyrics


JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32


def test_target_kind_treats_one_audio_file_as_single(tmp_path) -> None:
    folder = tmp_path / "Single"
    folder.mkdir()
    track = folder / "01 Song.mp3"
    track.touch()

    assert target_kind(track) == "single"
    assert target_kind(folder) == "single"


def test_target_kind_treats_two_audio_files_as_album(tmp_path) -> None:
    folder = tmp_path / "Album"
    folder.mkdir()
    (folder / "01 Song.mp3").touch()
    (folder / "02 Song.mp3").touch()

    assert target_kind(folder) == "album"


def test_cleanup_command_defaults_to_dry_run() -> None:
    args = build_parser().parse_args(["cleanup", "Album"])

    assert args.command == "cleanup"
    assert args.path == Path("Album")
    assert args.apply is False


def test_audit_accepts_verbose_flag() -> None:
    args = build_parser().parse_args(["audit", "Album", "--verbose"])

    assert args.verbose is True


def test_cleanup_accepts_verbose_flag() -> None:
    args = build_parser().parse_args(["cleanup", "Album", "--verbose"])

    assert args.verbose is True


def test_cover_accepts_apply_force_verbose_debug_flags() -> None:
    args = build_parser().parse_args(["cover", "Album", "--apply", "--force", "--embed-cover", "--save-folder-cover", "--force-folder-cover", "--remove-folder-cover", "--verbose", "--debug"])

    assert args.command == "cover"
    assert args.apply is True
    assert args.force is True
    assert args.embed_cover is True
    assert args.save_folder_cover is True
    assert args.force_folder_cover is True
    assert args.remove_folder_cover is True
    assert args.verbose is True
    assert args.debug is True


def test_cover_accepts_no_embed_and_no_folder_flags() -> None:
    args = build_parser().parse_args(["cover", "Album", "--no-embed-cover", "--no-folder-cover"])

    assert args.embed_cover is False
    assert args.save_folder_cover is False


def test_analyze_accepts_lastfm_flags() -> None:
    args = build_parser().parse_args(["analyze", "Album", "--lastfm-tags", "--skip-lastfm", "--lastfm-min-count", "4", "--lastfm-max-tags", "5", "--lastfm-debug", "--lastfm-raw"])

    assert args.lastfm_tags is True
    assert args.skip_lastfm is True
    assert args.lastfm_min_count == 4
    assert args.lastfm_max_tags == 5
    assert args.lastfm_debug is True
    assert args.lastfm_raw is True


def test_analyze_accepts_mood_flags() -> None:
    args = build_parser().parse_args(["analyze", "Album", "--mood", "--force-mood"])

    assert args.mood is True
    assert args.force_mood is True


def test_enrich_accepts_skip_mood_flag() -> None:
    args = build_parser().parse_args(["enrich", "Album", "--full", "--skip-mood"])

    assert args.full is True
    assert args.skip_mood is True


def test_enrich_accepts_verbose_flag() -> None:
    args = build_parser().parse_args(["enrich", "Album", "--full", "--verbose"])

    assert args.verbose is True


def test_enrich_accepts_debug_flag() -> None:
    args = build_parser().parse_args(["enrich", "Album", "--full", "--debug"])

    assert args.debug is True


def test_enrich_accepts_progress_flags() -> None:
    args = build_parser().parse_args(["enrich", "Album", "--full", "--no-progress", "--no-spinner", "--plain"])

    assert args.no_progress is True
    assert args.no_spinner is True
    assert args.plain is True


def test_enrich_accepts_cover_and_lyrics_flags() -> None:
    args = build_parser().parse_args(["enrich", "Album", "--full", "--cover", "--skip-cover", "--lyrics", "--skip-lyrics", "--force-cover", "--force-lyrics"])

    assert args.cover is True
    assert args.skip_cover is True
    assert args.lyrics is True
    assert args.skip_lyrics is True
    assert args.force_cover is True
    assert args.force_lyrics is True


def test_enrich_accepts_metadata_provider_flags() -> None:
    args = build_parser().parse_args(["enrich", "Album", "--full", "--metadata-providers", "--skip-metadata-providers", "--provider", "musicbrainz", "--provider", "itunes", "--allow-more-providers", "--min-confidence", "high", "--advanced"])

    assert args.metadata_providers is True
    assert args.skip_metadata_providers is True
    assert args.provider == ["musicbrainz", "itunes"]
    assert args.allow_more_providers is True
    assert args.min_confidence == "high"
    assert args.advanced is True


def test_analyze_accepts_progress_flags() -> None:
    args = build_parser().parse_args(["analyze", "Album", "--bpm", "--no-progress", "--no-spinner", "--plain"])

    assert args.no_progress is True
    assert args.no_spinner is True
    assert args.plain is True


def test_progress_uses_rich_when_stdout_is_tty(monkeypatch) -> None:
    status_labels = []
    updates = []

    class FakeStatus:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConsole:
        def status(self, label, spinner):
            status_labels.append((label, spinner))
            return FakeStatus()

    class FakeProgress:
        def __init__(self, *columns, console):
            self.columns = columns
            self.console = console

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add_task(self, description, total):
            updates.append(("add", description, total))
            return 1

        def update(self, task_id, completed, total):
            updates.append(("update", task_id, completed, total))

    class FakeColumn:
        def __init__(self, *args, **kwargs):
            pass

    console_module = types.ModuleType("rich.console")
    console_module.Console = FakeConsole
    progress_module = types.ModuleType("rich.progress")
    progress_module.BarColumn = FakeColumn
    progress_module.Progress = FakeProgress
    progress_module.TaskProgressColumn = FakeColumn
    progress_module.TextColumn = FakeColumn
    monkeypatch.setitem(sys.modules, "rich.console", console_module)
    monkeypatch.setitem(sys.modules, "rich.progress", progress_module)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)

    progress = cli._Progress()
    with progress.spinner("[1/8]", "MusicBrainz"):
        pass
    with progress.bar("[5/8]", "BPM", 2) as advance:
        advance(1, 2)
        advance(2, 2)

    assert status_labels == [("[1/8] MusicBrainz: running...", "dots")]
    assert updates == [("add", "[5/8] BPM", 2), ("update", 1, 1, 2), ("update", 1, 2, 2)]


def test_non_tty_progress_falls_back_to_plain_lines(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    _patch_enrich_dependencies(monkeypatch, track, [])

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "MusicBrainz" in output
    assert "running..." in output
    assert "\r" not in output
    assert "\x1b" not in output


def test_enrich_uses_summarized_cleanup_by_default(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "plan_cleanup", lambda *args, **kwargs: [object()])
    monkeypatch.setattr(cli, "summarize_cleanup", lambda *args, **kwargs: "cleanup verbose" if kwargs.get("verbose") else "cleanup summarized")

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "Cleanup" in output
    assert "removed 0 empty/bad fields" in output
    assert "cleanup verbose" not in output


def test_enrich_verbose_expands_cleanup(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "plan_cleanup", lambda *args, **kwargs: [object()])
    monkeypatch.setattr(cli, "summarize_cleanup", lambda *args, **kwargs: "cleanup verbose" if kwargs.get("verbose") else "cleanup summarized")

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, verbose=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "cleanup verbose" in output


def test_enrich_full_reports_cleanup_once(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "summarize_cleanup", lambda *args, **kwargs: "cleanup summarized")

    code = cli.enrich(track, apply=False, force=False, full=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True)
    output = capsys.readouterr().out

    assert code == 0
    assert output.count("Cleanup            OK") == 1


def test_enrich_full_runs_metadata_provider_stage_when_config_true(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    calls = []
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "_run_metadata_provider_stage", lambda *args, **kwargs: calls.append(kwargs) or ("OK", "discogs catalog", "provider detail"))

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, config={"enrich": {"full_includes_metadata_providers": True}})
    output = capsys.readouterr().out

    assert code == 0
    assert calls and calls[0]["exclude_musicbrainz"] is True
    assert "Metadata providers" in output


def test_enrich_full_includes_acoustid_identify_and_skips_legacy_step(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    identify_calls = []
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "_run_acoustid_identify_stage", lambda *args, **kwargs: identify_calls.append(kwargs) or ("WARN", "fingerprints 1/1, lookup skipped no API key", "detail"))
    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True)
    output = capsys.readouterr().out

    assert code == 0
    assert identify_calls
    assert "AcoustID Identify" in output
    assert "lookup skipped no API key" in output


def test_enrich_skip_acoustid_identify_has_no_legacy_fallback(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "_run_acoustid_identify_stage", lambda *args, **kwargs: _raise_unexpected())

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, skip_acoustid_identify=True, explicit_flags={"--skip-acoustid-identify"})

    assert code == 0
    output = capsys.readouterr().out
    assert "AcoustID Identify" not in output
    assert "Legacy TuneUp" not in output
    assert "OneTagger" not in output


def test_enrich_skip_acoustid_identify_excludes_provider_source(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    calls = []
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "_run_metadata_provider_stage", lambda *args, **kwargs: calls.append(kwargs) or ("OK", "discogs catalog", "detail"))

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, skip_acoustid_identify=True, metadata_provider_sources=["acoustid", "discogs"], config={"metadata_providers": {"sources": ["acoustid", "discogs"]}}, explicit_flags={"--skip-acoustid-identify"})

    assert code == 0
    assert calls[0]["exclude_acoustid"] is True


def test_enrich_full_skips_metadata_provider_stage_when_config_false(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "_run_metadata_provider_stage", lambda *args, **kwargs: _raise_unexpected())

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, config={"enrich": {"full_includes_metadata_providers": False}})
    output = capsys.readouterr().out

    assert code == 0
    assert "Metadata providers" not in output.split("Final audit:", 1)[0]


def test_enrich_metadata_provider_flags_override_config(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    calls = []
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "_run_metadata_provider_stage", lambda *args, **kwargs: calls.append(kwargs) or ("OK", "itunes fallback", "provider detail"))
    config = {"enrich": {"full_includes_metadata_providers": False}, "metadata_providers": {"sources": ["discogs"], "max_active": 1}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, metadata_providers=True, metadata_provider_sources=["musicbrainz", "itunes"], allow_more_providers=True, min_metadata_confidence="high", config=config, explicit_flags={"--metadata-providers"})

    assert code == 0
    assert calls[0]["providers"] == ["musicbrainz", "itunes"]
    assert calls[0]["allow_more_providers"] is True
    assert calls[0]["min_confidence"] == "high"
    assert "Metadata providers" in capsys.readouterr().out


def test_enrich_skip_metadata_providers_overrides_config(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "_run_metadata_provider_stage", lambda *args, **kwargs: _raise_unexpected())

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, skip_metadata_providers=True, config={"enrich": {"full_includes_metadata_providers": True}}, explicit_flags={"--skip-metadata-providers"})

    assert code == 0
    assert "Metadata providers" not in capsys.readouterr().out.split("Final audit:", 1)[0]


def test_enrich_metadata_stage_filters_musicbrainz_and_respects_max_active(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    local = Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={})
    seen = []
    monkeypatch.setattr(cli, "read_tracks", lambda path: [local])
    monkeypatch.setattr(cli, "fetch_metadata_with_providers", lambda context, sources, **kwargs: seen.append(sources) or [])

    status, summary, detail = cli._run_metadata_provider_stage(track, apply=False, force=False, providers=["musicbrainz", "discogs", "deezer"], min_confidence="medium", verbose=True, debug=False, config={"metadata_providers": {"max_active": 2, "deezer": {"enabled": True}}}, allow_more_providers=False)

    assert seen == [["discogs"]]
    assert status == "WARN"
    assert "deezer: over max_active limit" in detail
    assert "musicbrainz: identity handled by MusicBrainz stage" in detail


def test_enrich_metadata_stage_allow_more_providers_keeps_fallback(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    local = Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={})
    seen = []
    monkeypatch.setattr(cli, "read_tracks", lambda path: [local])
    monkeypatch.setattr(cli, "fetch_metadata_with_providers", lambda context, sources, **kwargs: seen.append(sources) or [])

    cli._run_metadata_provider_stage(track, apply=False, force=False, providers=["musicbrainz", "discogs", "deezer"], min_confidence="medium", verbose=False, debug=False, config={"metadata_providers": {"max_active": 2, "deezer": {"enabled": True}}}, allow_more_providers=True)

    assert seen == [["discogs", "deezer"]]


def test_enrich_metadata_stage_dry_run_does_not_write_and_apply_writes(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    local = Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={})
    candidate = MetadataCandidate(provider="discogs", source_id="1", confidence="high", score=95, genre="Rock", mb_album_id="not-authoritative")
    applied = []
    monkeypatch.setattr(cli, "read_tracks", lambda path: [local])
    monkeypatch.setattr(cli, "fetch_metadata_with_providers", lambda *args, **kwargs: [ProviderAttempt("discogs", "OK", "candidate score=95", [candidate])])
    monkeypatch.setattr(cli, "apply_musicbrainz_writes", lambda plans, apply=False: applied.append((plans, apply)) or [])

    dry = cli._run_metadata_provider_stage(track, apply=False, force=False, providers=["discogs"], min_confidence="medium", verbose=False, debug=False, config={"metadata_providers": {"discogs": {"enabled": True}}}, allow_more_providers=False)
    live = cli._run_metadata_provider_stage(track, apply=True, force=False, providers=["discogs"], min_confidence="medium", verbose=False, debug=False, config={"metadata_providers": {"discogs": {"enabled": True}}}, allow_more_providers=False)

    assert dry[0] == "OK"
    assert live[0] == "OK"
    assert applied[0][1] is False
    assert applied[1][1] is True
    assert applied[1][0][0].changes == {"Genre": "Rock"}


def test_enrich_metadata_stage_provider_warn_does_not_fail(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    local = Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={})
    monkeypatch.setattr(cli, "read_tracks", lambda path: [local])
    monkeypatch.setattr(cli, "fetch_metadata_with_providers", lambda *args, **kwargs: [ProviderAttempt("deezer", "WARN", "search skipped: timeout")])

    status, summary, _detail = cli._run_metadata_provider_stage(track, apply=False, force=False, providers=["deezer"], min_confidence="medium", verbose=False, debug=False, config={}, allow_more_providers=False)

    assert status == "WARN"
    assert "deezer search skipped" in summary


def test_enrich_metadata_stage_ambiguous_discogs_is_review(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    local = Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={})
    first = MetadataCandidate(provider="discogs", source_id="1", confidence="high", score=95, genre="Rock", country="US")
    second = MetadataCandidate(provider="discogs", source_id="2", confidence="high", score=94, genre="Rock", country="UK")
    monkeypatch.setattr(cli, "read_tracks", lambda path: [local])
    monkeypatch.setattr(cli, "fetch_metadata_with_providers", lambda *args, **kwargs: [ProviderAttempt("discogs", "REVIEW", "ambiguous", [first, second])])

    status, summary, detail = cli._run_metadata_provider_stage(track, apply=False, force=False, providers=["discogs"], min_confidence="medium", verbose=True, debug=False, config={"metadata_providers": {"discogs": {"enabled": True}}}, allow_more_providers=False)

    assert status == "REVIEW"
    assert "discogs ambiguous editions" in summary
    assert "country" in detail


def test_enrich_metadata_stage_debug_masks_tokens(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    local = Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={})
    candidate = MetadataCandidate(provider="discogs", source_id="1", confidence="medium", score=72, genre="Rock")
    attempt = ProviderAttempt("discogs", "WARN", "search skipped", [candidate], debug=["discogs search url: https://api.discogs.com/database/search?token=***"])
    monkeypatch.setattr(cli, "read_tracks", lambda path: [local])
    monkeypatch.setattr(cli, "fetch_metadata_with_providers", lambda *args, **kwargs: [attempt])

    _status, _summary, detail = cli._run_metadata_provider_stage(track, apply=False, force=False, providers=["discogs"], min_confidence="medium", verbose=False, debug=True, config={"metadata_providers": {"discogs": {"token": "secret-token"}}}, allow_more_providers=False)

    assert "secret-token" not in detail
    assert "Debug:" in detail
    assert "score: 72" in detail


def test_enrich_dry_run_explains_final_audit_reflects_current_files(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    _patch_enrich_dependencies(monkeypatch, track, [])
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: (0, "DRY-RUN: Last.fm tags\n- 01 Song.mp3: would write LASTFM_TAGS=Pop"))

    code = cli.enrich(track, apply=False, force=False, with_lastfm=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "Audit reflects current files; planned dry-run changes are not applied" in output
    assert "Warning: Last.fm Tags missing" not in output


def test_enrich_default_is_compact_and_hides_file_paths(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "read_tracks", lambda path: [Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={"mb_album_id": ["album"], "mb_track_id": ["track"], "mb_release_group_id": ["group"]})])
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: (0, f"DRY-RUN: Last.fm tags\n- {track}: tags=rock source=track action=would write"))
    monkeypatch.setattr(cli, "analyze_mood_path", lambda *args, **kwargs: (0, f"DRY-RUN: MOOD analysis\n- {track}: raw_tags=rock mood=Energetic confidence=high action=would write"))

    code = cli.enrich(track, apply=False, force=False, full=True, skip_key=True)
    output = capsys.readouterr().out

    assert code == 0
    assert str(track) not in output
    assert "MusicBrainz" in output
    assert "Metadata providers" in output
    assert "OneTagger" not in output
    assert "Legacy TuneUp" not in output
    assert "Cleanup" in output
    assert "BPM" in output
    assert "Features" in output
    assert "Last.fm" in output
    assert "Mood" in output
    assert "BPM" in output and "1/1 written" in output
    assert "Features" in output and "energy 1/1, danceability 1/1" in output
    assert "Last.fm" in output and "tags 1/1" in output
    assert "Mood" in output and "mood 1/1" in output


def test_enrich_verbose_shows_file_paths(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)

    code = cli.enrich(track, apply=False, force=False, full=True, skip_key=True, skip_lastfm=True, skip_mood=True, verbose=True)
    output = capsys.readouterr().out

    assert code == 0
    assert str(track) in output


def test_enrich_groups_half_time_warnings(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_bpm_path", lambda *args, **kwargs: (0, f"DRY-RUN: BPM analysis\n- {track}: raw=140 final=140 confidence=high warning=possible half-time alternative 70 action=would write"))

    code = cli.enrich(track, apply=False, force=False, analyze_bpm=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "BPM" in output
    assert "WARN" in output
    assert "1 half-time warnings" in output
    assert "possible half-time alternative" not in output


def test_enrich_key_unavailable_is_compact(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_key_path", lambda *args, **kwargs: (0, "KEY: skipped, ffmpeg is not available for portable key detection.\nInstall/configure an optional key backend to enable key detection."))

    code = cli.enrich(track, apply=False, force=False, analyze_key=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "[optional] Key" in output
    assert "SKIP" in output
    assert "optional backend unavailable" in output
    assert "Install/configure" not in output


def test_enrich_musicbrainz_dry_run_is_summarized_by_default(tmp_path, monkeypatch, capsys) -> None:
    track1 = tmp_path / "01 Song.mp3"
    track2 = tmp_path / "02 Song.mp3"
    track1.touch()
    track2.touch()
    local_tracks = [
        Track(path=track1, format="mp3", album="Album", artist="Artist", title="Song 1", tracknumber=1),
        Track(path=track2, format="mp3", album="Album", artist="Artist", title="Song 2", tracknumber=2),
    ]
    _patch_musicbrainz_enrich(monkeypatch, local_tracks, track1)

    code = cli.enrich(tmp_path, apply=False, force=False)
    output = capsys.readouterr().out

    assert code == 0
    assert "MusicBrainz" in output
    assert "OK         2/2 files" in output
    assert f"{track1}: MusicBrainz Album Artist Id" not in output


def test_enrich_musicbrainz_dry_run_verbose_shows_per_file(tmp_path, monkeypatch, capsys) -> None:
    track1 = tmp_path / "01 Song.mp3"
    track2 = tmp_path / "02 Song.mp3"
    track1.touch()
    track2.touch()
    local_tracks = [
        Track(path=track1, format="mp3", album="Album", artist="Artist", title="Song 1", tracknumber=1),
        Track(path=track2, format="mp3", album="Album", artist="Artist", title="Song 2", tracknumber=2),
    ]
    _patch_musicbrainz_enrich(monkeypatch, local_tracks, track1)

    code = cli.enrich(tmp_path, apply=False, force=False, verbose=True)
    output = capsys.readouterr().out

    assert code == 0
    assert f"{track1}: MusicBrainz Album Artist Id" in output
    assert "MusicBrainz" in output


def test_enrich_marks_empty_musicbrainz_fields_repaired_by_same_dry_run(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    local_track = Track(
        path=track,
        format="mp3",
        album="Album",
        artist="Artist",
        title="Song",
        tracknumber=1,
        tags={"mb_album_id": [""], "mb_track_id": [""], "mb_release_group_id": [""]},
    )
    _patch_musicbrainz_enrich(monkeypatch, [local_track], track, patch_cleanup=False)

    code = cli.enrich(track, apply=False, force=False)
    output = capsys.readouterr().out

    assert code == 0
    assert "Cleanup" in output
    assert "removed" in output


def test_enrich_does_not_fail_when_style_is_missing(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()

    monkeypatch.setattr(cli, "read_tracks", lambda path: [])
    monkeypatch.setattr(cli, "mb_album_ids", lambda tracks: {"album"})
    monkeypatch.setattr(cli, "get_release", lambda release_id: {"id": release_id, "media": []})
    monkeypatch.setattr(cli, "plan_cleanup", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "apply_cleanup", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "summarize_cleanup", lambda *args, **kwargs: "would remove:\n- nothing\nwould write:\n- nothing")
    monkeypatch.setattr(
        cli,
        "audit_path",
        lambda path: AuditResult(
            tracks=[
                Track(
                    path=track,
                    format="mp3",
                    album="Album",
                    artist="Artist",
                    title="Song",
                    tags={"mb_album_id": ["album"], "mb_track_id": ["track"], "mb_release_group_id": ["group"], "label": ["Label"], "originaldate": ["2024"], "bpm": ["120"]},
                )
            ],
            bad_fields=[],
        ),
    )

    code = cli.enrich(track, apply=True, force=False)
    output = capsys.readouterr().out

    assert code == 0
    assert "Style missing: no reliable style found from configured metadata sources" in output


def _patch_enrich_dependencies(monkeypatch, track: Path, bpm_calls: list) -> None:
    monkeypatch.setattr(cli, "read_tracks", lambda path: [])
    monkeypatch.setattr(cli, "mb_album_ids", lambda tracks: {"album"})
    monkeypatch.setattr(cli, "get_release", lambda release_id: {"id": release_id, "media": []})
    monkeypatch.setattr(cli, "plan_cleanup", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "apply_cleanup", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "summarize_cleanup", lambda *args, **kwargs: "would remove:\n- nothing\nwould write:\n- nothing")
    monkeypatch.setattr(
        cli,
        "audit_path",
        lambda path: AuditResult(
            tracks=[Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={"mb_album_id": ["album"], "mb_track_id": ["track"], "mb_release_group_id": ["group"], "label": ["Label"], "style": ["Pop"], "originaldate": ["2024"], "bpm": ["120"]})],
            bad_fields=[],
        ),
    )
    monkeypatch.setattr(cli, "analyze_bpm_path", lambda *args, **kwargs: bpm_calls.append((args, kwargs)) or (0, "DRY-RUN: BPM analysis\n- song.mp3: raw=120 final=120 confidence=high action=would write"))
    monkeypatch.setattr(cli, "analyze_key_path", lambda *args, **kwargs: (0, "DRY-RUN: KEY analysis\n- song.mp3: raw=C scale=major final=C Major confidence=high action=would write"))
    monkeypatch.setattr(cli, "analyze_features_path", lambda *args, **kwargs: (0, "DRY-RUN: feature analysis\n- song.mp3: ENERGY raw_data=bpm=120 final=82 confidence=medium action=would write\n- song.mp3: DANCEABILITY raw_data=bpm=120 final=74 confidence=medium action=would write"))
    monkeypatch.setattr(cli, "analyze_mood_path", lambda *args, **kwargs: (0, "DRY-RUN: MOOD analysis"))
    monkeypatch.setattr(cli, "_run_metadata_provider_stage", lambda *args, **kwargs: ("OK", "discogs catalog", "Metadata providers:\n- discogs: catalog"))


def _patch_cover_enrich_dependencies(monkeypatch, track: Path, track_info: Track | None = None) -> list:
    cover_track = track_info or Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={"mb_album_id": ["album"], "mb_track_id": ["track"], "mb_release_group_id": ["group"], "style": ["Pop"]})
    cover_calls = []
    monkeypatch.setattr(cli, "read_tracks", lambda path: [cover_track])
    monkeypatch.setattr(cli, "mb_album_ids", lambda tracks: {"album"})
    monkeypatch.setattr(cli, "get_release", lambda release_id: {"id": release_id, "media": []})
    monkeypatch.setattr(cli, "plan_cleanup", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "apply_cleanup", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "summarize_cleanup", lambda *args, **kwargs: "would remove:\n- nothing\nwould write:\n- nothing")
    monkeypatch.setattr(cli, "analyze_bpm_path", lambda *args, **kwargs: (0, "DRY-RUN: BPM analysis\n- song.mp3: raw=120 final=120 confidence=high action=would write"))
    monkeypatch.setattr(cli, "analyze_key_path", lambda *args, **kwargs: (0, "KEY: skipped, ffmpeg is not available for portable key detection."))
    monkeypatch.setattr(cli, "analyze_features_path", lambda *args, **kwargs: (0, "DRY-RUN: feature analysis\n- song.mp3: ENERGY raw_data=bpm=120 final=82 confidence=medium action=would write\n- song.mp3: DANCEABILITY raw_data=bpm=120 final=74 confidence=medium action=would write"))
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: (0, "Last.fm: skipped, LASTFM_API_KEY not set."))
    monkeypatch.setattr(cli, "analyze_mood_path", lambda *args, **kwargs: (0, "DRY-RUN: MOOD analysis"))
    monkeypatch.setattr(cli, "audit_path", lambda path: AuditResult(tracks=[cover_track], bad_fields=[]))
    monkeypatch.setattr(cli, "_run_metadata_provider_stage", lambda *args, **kwargs: ("OK", "discogs catalog", "Metadata providers:\n- discogs: catalog"))

    original_process_cover = cli.process_cover

    def fake_process_cover(*args, **kwargs):
        cover_calls.append((args, kwargs))
        return original_process_cover(*args, **kwargs)

    monkeypatch.setattr(cli, "process_cover", fake_process_cover)
    monkeypatch.setattr("noqlen_forge.cover.fetch_cover_from_musicbrainz", lambda release_id, debug=False: (validate_image_bytes(JPEG), []))
    return cover_calls


def test_enrich_full_does_not_run_cover_when_config_false(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    cover_calls = _patch_cover_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_cover": False}, "cover": {"enabled": True, "embed": True, "save_folder_cover": False, "sources": ["local", "musicbrainz"]}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, config=config)
    output = capsys.readouterr().out

    assert code == 0
    assert cover_calls == []
    assert "Cover" not in output.split("Final audit:", 1)[0]


def test_enrich_full_runs_cover_when_config_true(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    cover_calls = _patch_cover_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_cover": True}, "cover": {"enabled": True, "embed": True, "save_folder_cover": False, "sources": ["local", "musicbrainz"]}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, config=config)
    output = capsys.readouterr().out

    assert code == 0
    assert len(cover_calls) == 1
    assert "Cover" in output


def test_enrich_cover_flag_forces_cover_when_config_false(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    cover_calls = _patch_cover_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_cover": False}, "cover": {"enabled": False, "embed": True, "save_folder_cover": False, "sources": ["local", "musicbrainz"]}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, cover=True, config=config, explicit_flags={"--cover"})

    assert code == 0
    assert len(cover_calls) == 1
    assert "Cover" in capsys.readouterr().out


def test_enrich_skip_cover_overrides_config_true(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    cover_calls = _patch_cover_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_cover": True}, "cover": {"enabled": True, "embed": True, "save_folder_cover": False, "sources": ["local", "musicbrainz"]}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, skip_cover=True, config=config, explicit_flags={"--skip-cover"})
    output = capsys.readouterr().out

    assert code == 0
    assert cover_calls == []
    assert "Cover" not in output.split("Final audit:", 1)[0]


def test_enrich_cover_dry_run_does_not_write_cover(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    _patch_cover_enrich_dependencies(monkeypatch, track)

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, cover=True, explicit_flags={"--cover"})

    assert code == 0
    assert detect_embedded_cover(track) is False
    assert not (tmp_path / "cover.jpg").exists()


def test_enrich_cover_apply_embeds_without_folder_cover(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    _patch_cover_enrich_dependencies(monkeypatch, track)

    code = cli.enrich(track, apply=True, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, cover=True, explicit_flags={"--cover"})
    output = capsys.readouterr().out

    assert code == 0
    assert detect_embedded_cover(track) is True
    assert not (tmp_path / "cover.jpg").exists()
    assert "Cover" in output
    assert "embedded 1/1, folder cover skipped" in output
    assert "Folder Cover: skipped" in output


def test_enrich_cover_warning_does_not_fail_pipeline(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    _patch_cover_enrich_dependencies(monkeypatch, track)
    monkeypatch.setattr("noqlen_forge.cover.fetch_cover_from_musicbrainz", lambda release_id, debug=False: (None, []))

    code = cli.enrich(track, apply=True, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, cover=True, explicit_flags={"--cover"})
    output = capsys.readouterr().out

    assert code == 0
    assert "Cover" in output
    assert "WARN" in output
    assert "no cover found" in output


def test_enrich_cover_compact_output_and_final_audit(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    _patch_cover_enrich_dependencies(monkeypatch, track)

    code = cli.enrich(track, apply=True, force=False, full=True, skip_bpm=True, skip_features=True, skip_lastfm=True, skip_mood=True, cover=True, analyze_key=True, explicit_flags={"--cover", "--analyze-key"})
    output = capsys.readouterr().out

    assert code == 0
    assert "Cover" in output
    assert "embedded 1/1, folder cover skipped" in output
    assert "Final audit:" in output
    assert "Cover: 1/1" in output


def _patch_lyrics_enrich_dependencies(monkeypatch, track: Path, track_info: Track | None = None) -> list:
    lyrics_track = track_info or Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tags={"mb_album_id": ["album"], "mb_track_id": ["track"], "mb_release_group_id": ["group"], "style": ["Pop"]})
    lyrics_calls = []
    monkeypatch.setattr(cli, "read_tracks", lambda path: [lyrics_track])
    monkeypatch.setattr(cli, "mb_album_ids", lambda tracks: {"album"})
    monkeypatch.setattr(cli, "get_release", lambda release_id: {"id": release_id, "media": []})
    monkeypatch.setattr(cli, "plan_cleanup", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "apply_cleanup", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "summarize_cleanup", lambda *args, **kwargs: "would remove:\n- nothing\nwould write:\n- nothing")
    monkeypatch.setattr(cli, "analyze_bpm_path", lambda *args, **kwargs: (0, "DRY-RUN: BPM analysis\n- song.mp3: raw=120 final=120 confidence=high action=would write"))
    monkeypatch.setattr(cli, "analyze_key_path", lambda *args, **kwargs: (0, "KEY: skipped, ffmpeg is not available for portable key detection."))
    monkeypatch.setattr(cli, "analyze_features_path", lambda *args, **kwargs: (0, "DRY-RUN: feature analysis\n- song.mp3: ENERGY raw_data=bpm=120 final=82 confidence=medium action=would write\n- song.mp3: DANCEABILITY raw_data=bpm=120 final=74 confidence=medium action=would write"))
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: (0, "Last.fm: skipped, LASTFM_API_KEY not set."))
    monkeypatch.setattr(cli, "analyze_mood_path", lambda *args, **kwargs: (0, "DRY-RUN: MOOD analysis"))
    monkeypatch.setattr(cli, "audit_path", lambda path: AuditResult(tracks=[lyrics_track], bad_fields=[]))
    monkeypatch.setattr(cli, "_run_metadata_provider_stage", lambda *args, **kwargs: ("OK", "discogs catalog", "Metadata providers:\n- discogs: catalog"))

    original_process_lyrics = cli.process_lyrics

    def fake_process_lyrics(*args, **kwargs):
        lyrics_calls.append((args, kwargs))
        return original_process_lyrics(*args, **kwargs)

    monkeypatch.setattr(cli, "process_lyrics", fake_process_lyrics)
    return lyrics_calls


def test_enrich_full_does_not_run_lyrics_when_config_false(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    (tmp_path / "01 Song.lrc").write_text("[00:01.00]Line\n", encoding="utf-8")
    lyrics_calls = _patch_lyrics_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_lyrics": False}, "lyrics": {"enabled": True, "embed": True, "save_lrc": True, "save_txt": False, "prefer_synced": True, "sources": ["local"]}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, config=config)
    output = capsys.readouterr().out

    assert code == 0
    assert lyrics_calls == []
    assert "Lyrics" not in output.split("Final audit:", 1)[0]


def test_enrich_full_runs_lyrics_when_config_true(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    (tmp_path / "01 Song.lrc").write_text("[00:01.00]Line\n", encoding="utf-8")
    lyrics_calls = _patch_lyrics_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_lyrics": True}, "lyrics": {"enabled": True, "embed": True, "save_lrc": True, "save_txt": False, "prefer_synced": True, "sources": ["local"]}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, config=config)
    output = capsys.readouterr().out

    assert code == 0
    assert len(lyrics_calls) == 1
    assert "Lyrics" in output


def test_enrich_lyrics_flag_forces_lyrics_when_config_false(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    (tmp_path / "01 Song.lrc").write_text("[00:01.00]Line\n", encoding="utf-8")
    lyrics_calls = _patch_lyrics_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_lyrics": False}, "lyrics": {"enabled": False, "embed": True, "save_lrc": True, "save_txt": False, "prefer_synced": True, "sources": ["local"]}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, lyrics=True, config=config, explicit_flags={"--lyrics"})

    assert code == 0
    assert len(lyrics_calls) == 1
    assert "Lyrics" in capsys.readouterr().out


def test_enrich_skip_lyrics_overrides_config_true(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    (tmp_path / "01 Song.lrc").write_text("[00:01.00]Line\n", encoding="utf-8")
    lyrics_calls = _patch_lyrics_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_lyrics": True}, "lyrics": {"enabled": True, "embed": True, "save_lrc": True, "save_txt": False, "prefer_synced": True, "sources": ["local"]}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, skip_lyrics=True, config=config, explicit_flags={"--skip-lyrics"})
    output = capsys.readouterr().out

    assert code == 0
    assert lyrics_calls == []
    assert "Lyrics" not in output.split("Final audit:", 1)[0]


def test_enrich_lyrics_dry_run_does_not_write_lyrics(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    (tmp_path / "01 Song.lrc").write_text("[00:01.00]Line\n", encoding="utf-8")
    _patch_lyrics_enrich_dependencies(monkeypatch, track)

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, lyrics=True, explicit_flags={"--lyrics"})

    assert code == 0
    assert has_embedded_lyrics(track) is False


def test_enrich_lyrics_apply_embeds_lyrics(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    (tmp_path / "01 Song.lrc").write_text("[00:01.00]Line\n", encoding="utf-8")
    _patch_lyrics_enrich_dependencies(monkeypatch, track)

    code = cli.enrich(track, apply=True, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, lyrics=True, explicit_flags={"--lyrics"})
    output = capsys.readouterr().out

    assert code == 0
    assert read_embedded_lyrics(track) == "Line"
    assert "Lyrics" in output
    assert "embedded 1/1, synced 1/1" in output
    assert "Sidecar LRC: 1/1" in output


def test_enrich_lyrics_warning_does_not_fail_pipeline(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    _patch_lyrics_enrich_dependencies(monkeypatch, track)
    monkeypatch.setattr("noqlen_forge.lyrics.fetch_lrclib_lyrics", lambda track, prefer_synced=True, timeout=10, debug=False: (None, []))
    config = {"lyrics": {"embed": True, "save_lrc": True, "save_txt": False, "prefer_synced": True, "sources": ["lrclib"]}}

    code = cli.enrich(track, apply=True, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, lyrics=True, config=config, explicit_flags={"--lyrics"})
    output = capsys.readouterr().out

    assert code == 0
    assert "Lyrics" in output
    assert "WARN" in output
    assert "no lyrics found" in output


def test_enrich_cover_and_lyrics_compact_output(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    (tmp_path / "01 Song.lrc").write_text("[00:01.00]Line\n", encoding="utf-8")
    _patch_cover_enrich_dependencies(monkeypatch, track)

    code = cli.enrich(track, apply=True, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, cover=True, lyrics=True, explicit_flags={"--cover", "--lyrics"})
    output = capsys.readouterr().out

    assert code == 0
    assert "Cover" in output
    assert "Lyrics" in output
    assert "embedded 1/1, folder cover skipped" in output
    assert "embedded 1/1, synced 1/1" in output
    stage_output = output.split("Final audit:", 1)[0]
    assert sum(1 for line in stage_output.splitlines() if "] Cover" in line and "embedded 1/1" in line) == 1
    assert sum(1 for line in stage_output.splitlines() if "] Lyrics" in line and "embedded 1/1" in line) == 1


def test_enrich_uses_cover_provider_config(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    cover_calls = _patch_cover_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_cover": True}, "cover": {"enabled": True, "embed": True, "save_folder_cover": False, "sources": ["itunes", "deezer"], "min_confidence": "high"}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, config=config)

    assert code == 0
    assert cover_calls[0][1]["sources"] == ["itunes", "deezer"]
    assert cover_calls[0][1]["min_confidence"] == "high"


def test_enrich_uses_lyrics_provider_config(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    (tmp_path / "01 Song.lrc").write_text("[00:01.00]Line\n", encoding="utf-8")
    lyrics_calls = _patch_lyrics_enrich_dependencies(monkeypatch, track)
    config = {"enrich": {"full_includes_lyrics": True}, "lyrics": {"enabled": True, "embed": True, "save_lrc": True, "save_txt": False, "prefer_synced": False, "sources": ["lrclib", "local"], "min_confidence": "high"}}

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, config=config)

    assert code == 0
    assert lyrics_calls[0][1]["sources"] == ["lrclib", "local"]
    assert lyrics_calls[0][1]["min_confidence"] == "high"
    assert lyrics_calls[0][1]["prefer_synced"] is False


def test_enrich_provider_source_flags_override_config(tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_enrich(*args, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "load_config", lambda: {"cover": {"sources": ["musicbrainz"]}, "lyrics": {"sources": ["local"]}})
    monkeypatch.setattr(cli, "enrich", fake_enrich)

    code = cli.main(["enrich", str(tmp_path), "--cover", "--lyrics", "--cover-source", "itunes", "--lyrics-source", "lrclib", "--min-cover-confidence", "high", "--min-lyrics-confidence", "low"])

    assert code == 0
    assert captured["cover_sources"] == ["itunes"]
    assert captured["lyrics_sources"] == ["lrclib"]
    assert captured["min_cover_confidence"] == "high"
    assert captured["min_lyrics_confidence"] == "low"


def test_enrich_cover_provider_fallback_uses_next_source(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    cover_calls = _patch_cover_enrich_dependencies(monkeypatch, track)
    monkeypatch.setattr("noqlen_forge.cover.fetch_cover_from_musicbrainz", lambda release_id, debug=False: (None, []))

    def fake_get_bytes(url, accept, max_bytes=10 * 1024 * 1024):
        if "itunes.apple.com" in url:
            return json.dumps({"results": [{"artistName": "Artist", "collectionName": "Album", "artworkUrl100": "https://img/100x100bb.jpg"}]}).encode()
        return JPEG

    monkeypatch.setattr("noqlen_forge.cover_providers.get_bytes", fake_get_bytes)
    config = {"cover": {"embed": True, "save_folder_cover": False, "sources": ["musicbrainz", "itunes"], "min_confidence": "medium"}}

    code = cli.enrich(track, apply=True, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, cover=True, config=config, explicit_flags={"--cover"})
    output = capsys.readouterr().out

    assert code == 0
    assert cover_calls[0][1]["sources"] == ["musicbrainz", "itunes"]
    assert detect_embedded_cover(track) is True
    assert "embedded 1/1, folder cover skipped" in output


def test_enrich_lyrics_provider_fallback_uses_next_source(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    ID3().save(track)
    (tmp_path / "01 Song.lrc").write_text("[00:01.00]Line\n", encoding="utf-8")
    lyrics_calls = _patch_lyrics_enrich_dependencies(monkeypatch, track)
    monkeypatch.setattr("noqlen_forge.lyrics.fetch_lrclib_lyrics", lambda track, prefer_synced=True, timeout=10, debug=False: (None, []))
    config = {"lyrics": {"embed": True, "save_lrc": True, "save_txt": False, "prefer_synced": True, "sources": ["lrclib", "local"], "min_confidence": "medium"}}

    code = cli.enrich(track, apply=True, force=False, full=True, skip_bpm=True, skip_features=True, skip_key=True, skip_lastfm=True, skip_mood=True, lyrics=True, config=config, explicit_flags={"--lyrics"})

    assert code == 0
    assert lyrics_calls[0][1]["sources"] == ["lrclib", "local"]
    assert read_embedded_lyrics(track) == "Line"


def _patch_musicbrainz_enrich(monkeypatch, tracks: list[Track], audit_track: Path, patch_cleanup: bool = True) -> None:
    release_tracks = [{"id": f"rt-{index}", "recording": {"id": f"rec-{index}"}} for index, _track in enumerate(tracks, start=1)]
    release = {"id": "album", "release-group": {"id": "rg"}, "artist-credit": [{"artist": {"id": "artist"}}], "media": [{"tracks": release_tracks}]}

    ranked = type("Ranked", (), {"score": 100, "release": release})()

    monkeypatch.setattr(cli, "read_tracks", lambda path: tracks)
    monkeypatch.setattr(cli, "search_releases", lambda tracks: [])
    monkeypatch.setattr(cli, "hydrate_releases", lambda releases: [])
    monkeypatch.setattr(cli, "rank_releases", lambda tracks, releases: [ranked])
    if patch_cleanup:
        monkeypatch.setattr(cli, "plan_cleanup", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "apply_cleanup", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "audit_path", lambda path: AuditResult(tracks=[Track(path=audit_track, format="mp3", album="Album", artist="Artist", title="Song", tags={"mb_album_id": ["album"], "mb_track_id": ["rec"], "mb_release_group_id": ["rg"], "style": ["Pop"]})], bad_fields=[]))


def test_enrich_without_full_or_analyze_bpm_does_not_call_bpm(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)

    code = cli.enrich(track, apply=False, force=False)

    assert code == 0
    assert bpm_calls == []


def test_enrich_apply_without_full_does_not_call_lastfm_or_mood(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    lastfm_calls = []
    mood_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: lastfm_calls.append((args, kwargs)) or (0, "DRY-RUN: Last.fm tags"))
    monkeypatch.setattr(cli, "analyze_mood_path", lambda *args, **kwargs: mood_calls.append((args, kwargs)) or (0, "DRY-RUN: MOOD analysis"))

    code = cli.enrich(track, apply=True, force=False)

    assert code == 0
    assert lastfm_calls == []
    assert mood_calls == []


def test_enrich_repairs_partial_musicbrainz_when_album_id_exists(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    local_track = Track(path=track, format="mp3", album="Album", artist="Artist", title="Song", tracknumber=1, tags={"mb_album_id": ["album"], "mb_track_id": ["rec"]})
    release = {"id": "album", "release-group": {"id": "rg"}, "media": [{"tracks": [{"id": "rt", "recording": {"id": "different-rec"}}]}]}
    monkeypatch.setattr(cli, "read_tracks", lambda path: [local_track])
    monkeypatch.setattr(cli, "get_release", lambda release_id: release)
    monkeypatch.setattr(cli, "_apply_best_musicbrainz", lambda *args, **kwargs: _raise_unexpected())
    monkeypatch.setattr(cli, "plan_cleanup", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, "apply_cleanup", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "summarize_cleanup", lambda *args, **kwargs: "would remove:\n- nothing\nwould write:\n- nothing")
    monkeypatch.setattr(cli, "audit_path", lambda path: AuditResult(tracks=[local_track], bad_fields=[]))

    code = cli.enrich(track, apply=False, force=False)
    output = capsys.readouterr().out

    assert code == 0
    assert "MusicBrainz" in output
    assert "wrote release group id" in output
    assert "MusicBrainz Album Id already present" not in output


def test_enrich_analyze_bpm_calls_bpm(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)

    code = cli.enrich(track, apply=False, force=False, analyze_bpm=True)

    assert code == 0
    assert len(bpm_calls) == 1


def test_enrich_full_calls_bpm(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)

    code = cli.enrich(track, apply=False, force=False, full=True)

    assert code == 0
    assert len(bpm_calls) == 1


def test_enrich_full_skip_bpm_does_not_call_bpm(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)

    code = cli.enrich(track, apply=False, force=False, full=True, skip_bpm=True)

    assert code == 0
    assert bpm_calls == []


def test_enrich_with_lastfm_calls_analysis(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    lastfm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: lastfm_calls.append((args, kwargs)) or (0, "DRY-RUN: Last.fm tags"))

    code = cli.enrich(track, apply=True, force=False, with_lastfm=True, force_lastfm=True, lastfm_min_count=4, lastfm_max_tags=5)

    assert code == 0
    assert len(lastfm_calls) == 1
    assert lastfm_calls[0][1]["force"] is True
    assert lastfm_calls[0][1]["min_count"] == 4
    assert lastfm_calls[0][1]["max_tags"] == 5


def test_enrich_full_calls_lastfm(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    lastfm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: lastfm_calls.append((args, kwargs)) or (0, "DRY-RUN: Last.fm tags"))

    code = cli.enrich(track, apply=False, force=False, full=True)

    assert code == 0
    assert len(lastfm_calls) == 1


def test_enrich_skip_lastfm_prevents_call(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    lastfm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: lastfm_calls.append((args, kwargs)) or (0, "DRY-RUN: Last.fm tags"))

    code = cli.enrich(track, apply=False, force=False, with_lastfm=True, skip_lastfm=True)

    assert code == 0
    assert lastfm_calls == []


def test_enrich_full_skip_lastfm_prevents_call(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    lastfm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: lastfm_calls.append((args, kwargs)) or (0, "DRY-RUN: Last.fm tags"))

    code = cli.enrich(track, apply=False, force=False, full=True, skip_lastfm=True)

    assert code == 0
    assert lastfm_calls == []


def test_enrich_with_mood_calls_analysis(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    mood_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_mood_path", lambda *args, **kwargs: mood_calls.append((args, kwargs)) or (0, "DRY-RUN: MOOD analysis"))

    code = cli.enrich(track, apply=True, force=False, with_mood=True, force_mood=True)

    assert code == 0
    assert len(mood_calls) == 1
    assert mood_calls[0][1]["force"] is True


def test_enrich_with_lastfm_and_mood_passes_lastfm_to_mood(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    mood_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: (0, "DRY-RUN: Last.fm tags"))
    monkeypatch.setattr(cli, "analyze_mood_path", lambda *args, **kwargs: mood_calls.append((args, kwargs)) or (0, "DRY-RUN: MOOD analysis"))

    code = cli.enrich(track, apply=False, force=False, with_lastfm=True, with_mood=True)

    assert code == 0
    assert mood_calls[0][1]["with_lastfm"] is True


def test_enrich_full_calls_mood(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    mood_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_mood_path", lambda *args, **kwargs: mood_calls.append((args, kwargs)) or (0, "DRY-RUN: MOOD analysis"))

    code = cli.enrich(track, apply=False, force=False, full=True)

    assert code == 0
    assert len(mood_calls) == 1


def test_enrich_full_skip_mood_prevents_call(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    mood_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_mood_path", lambda *args, **kwargs: mood_calls.append((args, kwargs)) or (0, "DRY-RUN: MOOD analysis"))

    code = cli.enrich(track, apply=False, force=False, full=True, skip_mood=True)

    assert code == 0
    assert mood_calls == []


def test_enrich_full_without_lastfm_api_warns_and_continues(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_lastfm_tags", lambda *args, **kwargs: (0, "Last.fm: skipped, LASTFM_API_KEY not set."))

    code = cli.enrich(track, apply=False, force=False, full=True, skip_mood=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "Last.fm" in output
    assert "SKIP" in output
    assert "optional backend unavailable" in output


def test_enrich_passes_force_bpm_options(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)

    code = cli.enrich(track, apply=True, force=False, full=True, force_bpm=True, bpm_range=(80, 170), bpm_round="int")

    assert code == 0
    assert bpm_calls[0][1]["force"] is True
    assert bpm_calls[0][1]["bpm_range"] == (80, 170)
    assert bpm_calls[0][1]["bpm_round"] == "int"


def test_enrich_returns_clear_bpm_error(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_bpm_path", lambda *args, **kwargs: (1, "aubio not found. Install with: apt install aubio-tools"))

    code = cli.enrich(track, apply=True, force=False, analyze_bpm=True)
    output = capsys.readouterr().out

    assert code == 1
    assert "aubio not found. Install with: apt install aubio-tools" in output


def test_enrich_analyze_key_calls_key(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    key_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_key_path", lambda *args, **kwargs: key_calls.append((args, kwargs)) or (0, "DRY-RUN: KEY analysis"))

    code = cli.enrich(track, apply=False, force=False, analyze_key=True)

    assert code == 0
    assert len(key_calls) == 1


def test_enrich_full_skip_key_does_not_call_key(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    key_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_key_path", lambda *args, **kwargs: key_calls.append((args, kwargs)) or (0, "DRY-RUN: KEY analysis"))

    code = cli.enrich(track, apply=False, force=False, full=True, skip_key=True)

    assert code == 0
    assert key_calls == []


def test_enrich_passes_force_key(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    key_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_key_path", lambda *args, **kwargs: key_calls.append((args, kwargs)) or (0, "DRY-RUN: KEY analysis"))

    code = cli.enrich(track, apply=True, force=False, analyze_key=True, force_key=True)

    assert code == 0
    assert key_calls[0][1]["force"] is True


def test_enrich_full_continues_when_key_backend_missing(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_key_path", lambda *args, **kwargs: (0, "KEY: skipped, ffmpeg is not available for portable key detection.\nInstall/configure an optional key backend to enable key detection."))

    code = cli.enrich(track, apply=True, force=False, full=True)
    output = capsys.readouterr().out

    assert code == 0
    assert "[optional] Key" in output
    assert "SKIP" in output
    assert "optional backend unavailable" in output


def test_enrich_full_calls_features(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    feature_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_features_path", lambda *args, **kwargs: feature_calls.append((args, kwargs)) or (0, "DRY-RUN: feature analysis"))

    code = cli.enrich(track, apply=False, force=False, full=True)

    assert code == 0
    assert len(feature_calls) == 1


def test_enrich_skip_features_does_not_call_features(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    feature_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "analyze_features_path", lambda *args, **kwargs: feature_calls.append((args, kwargs)) or (0, "DRY-RUN: feature analysis"))

    code = cli.enrich(track, apply=False, force=False, full=True, skip_features=True)

    assert code == 0
    assert feature_calls == []


def test_enrich_full_replaygain_flag_calls_stage(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    replaygain_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(
        cli,
        "replaygain_path",
        lambda *args, **kwargs: replaygain_calls.append((args, kwargs)) or (0, "ReplayGain Track: 1/1\nReplayGain Album: 1/1\nLoudness: 1/1\nStatus: OK"),
    )

    code = cli.enrich(
        track,
        apply=False,
        force=False,
        full=True,
        replaygain=True,
        explicit_flags={"--replaygain"},
    )

    assert code == 0
    assert len(replaygain_calls) == 1
    assert replaygain_calls[0][1]["apply"] is False


def test_enrich_full_replaygain_apply_scans_database(tmp_path, monkeypatch) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    db_path = tmp_path / "library.db"
    db_path.touch()
    bpm_calls = []
    scan_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "database_path", lambda config: db_path)
    monkeypatch.setattr(cli, "scan_library", lambda config, path, apply=False, verbose=False: scan_calls.append((path, apply)) or (0, "scan ok"))
    monkeypatch.setattr(cli, "replaygain_path", lambda *args, **kwargs: (0, "ReplayGain Track: 1/1\nReplayGain Album: 1/1\nLoudness: 1/1\nStatus: OK"))

    code = cli.enrich(
        track,
        apply=True,
        force=False,
        full=True,
        replaygain=True,
        explicit_flags={"--replaygain"},
    )

    assert code == 0
    assert scan_calls == [(track, True)]


def test_enrich_full_replaygain_backend_missing_warns_without_failing(tmp_path, monkeypatch, capsys) -> None:
    track = tmp_path / "01 Song.mp3"
    track.touch()
    bpm_calls = []
    _patch_enrich_dependencies(monkeypatch, track, bpm_calls)
    monkeypatch.setattr(cli, "replaygain_path", lambda *args, **kwargs: (0, "ReplayGain: skipped, ffmpeg not found. Install ffmpeg to enable loudness analysis."))

    code = cli.enrich(
        track,
        apply=False,
        force=False,
        full=True,
        replaygain=True,
        explicit_flags={"--replaygain"},
    )
    output = capsys.readouterr().out

    assert code == 0
    assert "ReplayGain" in output
    assert "SKIP" in output
    assert "optional backend unavailable" in output


def test_import_noqlen_forge_without_removed_key_backend() -> None:
    import noqlen_forge

    assert noqlen_forge is not None


def _raise_unexpected():
    raise AssertionError("unexpected call")
