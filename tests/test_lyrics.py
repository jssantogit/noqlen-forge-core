from pathlib import Path

from mutagen.id3 import ID3, TALB, TIT2, TPE1, USLT

from noqlen_forge.audit import AuditResult, render_audit
from noqlen_forge.audio import Track, read_track
from noqlen_forge.lyrics import (
    LyricsResult,
    LyricsSelectionConfig,
    classify_lyrics_text,
    find_sidecar_lyrics,
    has_embedded_lyrics,
    is_lrc,
    lyrics_path,
    lyrics_text_hash,
    select_best_lyrics_candidate,
    process_lyrics,
    read_embedded_lyrics,
    strip_lrc_timestamps,
    write_embedded_lyrics,
)
from noqlen_forge.lyrics_providers import CustomHttpLyricsProvider, LyricsProvider, LyricsResult as ProviderLyricsResult, LyricsSearchQuery, PROVIDERS, ProviderAttempt, custom_http_candidates

LYRICS = "First private line\nSecond private line"
LRC = "[00:01.00]First private line\n[00:02.50]Second private line"


def mp3_track(path: Path) -> Track:
    return Track(path=path, format="mp3", album="Album", artist="Artist", title="Song")


class FakeLyricsProvider(LyricsProvider):
    def __init__(self, name: str, text: str | None, synced: bool = False, confidence: str = "high") -> None:
        super().__init__(name=name)
        self.text = text
        self.synced = synced
        self.confidence = confidence

    def fetch(self, track: Track, prefer_synced: bool = True, debug: bool = False) -> ProviderAttempt:
        if self.text is None:
            return ProviderAttempt(self.name, "WARN", "not found")
        return ProviderAttempt(self.name, "OK", "fake lyrics", result=ProviderLyricsResult(self.text, self.synced, self.name, self.name, self.confidence, match_reason="fake match"))


def test_detects_embedded_lyrics_existing(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    tags = ID3()
    tags.add(USLT(encoding=3, lang="und", desc="", text=LYRICS))
    tags.save(path)

    assert has_embedded_lyrics(path) is True
    assert read_track(path).tags["lyrics"] == [LYRICS]


def test_detects_sidecar_lrc(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Song"))
    tags.add(TPE1(encoding=3, text="Artist"))
    tags.add(TALB(encoding=3, text="Album"))
    tags.save(path)
    (tmp_path / "01 Song.lrc").write_text(LRC, encoding="utf-8")

    result = find_sidecar_lyrics(path)

    assert result is not None
    assert result.synced is True
    assert result.source == "sidecar:01 Song.lrc"


def test_detects_sidecar_txt(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    (tmp_path / "01 Song.txt").write_text(LYRICS, encoding="utf-8")

    result = find_sidecar_lyrics(path)

    assert result is not None
    assert result.synced is False


def test_is_lrc_recognizes_timestamps() -> None:
    assert is_lrc("[00:12.34]line") is True
    assert is_lrc("plain line") is False


def test_strip_lrc_timestamps_preserves_lines() -> None:
    assert strip_lrc_timestamps(LRC) == LYRICS


def test_select_best_lyrics_candidate_prioritizes_reliable_synced() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")
    synced = LyricsResult(LRC, True, "fake_synced", provider="fake_synced", confidence="high")
    plain = LyricsResult(LYRICS, False, "fake_plain", provider="fake_plain", confidence="high")

    result = select_best_lyrics_candidate(track, [plain, synced], config=LyricsSelectionConfig(prefer_synced=True))

    assert result.selected == synced
    assert result.status == "OK"


def test_select_best_lyrics_candidate_uses_high_unsynced_over_low_synced() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")
    synced = LyricsResult(LRC, True, "fake_synced", provider="fake_synced", confidence="low")
    plain = LyricsResult(LYRICS, False, "fake_plain", provider="fake_plain", confidence="high")

    result = select_best_lyrics_candidate(track, [synced, plain], config=LyricsSelectionConfig(prefer_synced=True, min_confidence="low"))

    assert result.selected == plain
    assert "higher confidence fallback" in result.reason


def test_select_best_lyrics_candidate_preserves_existing_without_force() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")

    result = select_best_lyrics_candidate(track, [LyricsResult(LYRICS, False, "fake", provider="fake")], existing_lyrics="Existing lyric")

    assert result.status == "SKIP"
    assert result.existing_kept is True


def test_same_content_different_format_is_not_conflict() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")
    synced = LyricsResult(LRC, True, "sidecar", provider="sidecar", confidence="high")
    plain = LyricsResult(LYRICS, False, "embedded", provider="embedded", confidence="high")

    result = select_best_lyrics_candidate(track, [plain, synced])

    assert result.status == "OK"
    assert not result.conflicts
    assert lyrics_text_hash(LRC) == lyrics_text_hash(LYRICS)


def test_divergent_high_confidence_lyrics_require_review() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")

    result = select_best_lyrics_candidate(track, [LyricsResult("Alpha lyric line", False, "a", provider="a", confidence="high"), LyricsResult("Completely different beta lyric", False, "b", provider="b", confidence="high")])

    assert result.status == "REVIEW"
    assert result.conflicts


def test_lrc_invalid_generates_warning_not_crash() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")

    result = select_best_lyrics_candidate(track, [LyricsResult("[bad]Private line", True, "sidecar", provider="sidecar", confidence="high")])

    assert result.selected is None
    assert result.status == "WARN"
    assert any("invalid LRC" in warning for warning in result.warnings)


def test_placeholders_are_rejected() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")

    result = select_best_lyrics_candidate(track, [LyricsResult("lyrics not found", False, "fake", provider="fake", confidence="high")])

    assert result.selected is None
    assert result.skipped == ["fake: placeholder"]


def test_instrumental_respects_config() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")
    candidate = LyricsResult("Instrumental", False, "fake", provider="fake", confidence="high")

    blocked = select_best_lyrics_candidate(track, [candidate], config=LyricsSelectionConfig(allow_instrumental=False))
    allowed = select_best_lyrics_candidate(track, [candidate], config=LyricsSelectionConfig(allow_instrumental=True))

    assert blocked.selected is None
    assert allowed.selected is candidate


def test_classify_lyrics_text_detects_valid_lrc_metadata() -> None:
    kind, warnings = classify_lyrics_text("[ar:Artist]\n[00:01.00][00:02.00]Line")

    assert kind == "synced_valid"
    assert warnings == []


def test_dry_run_does_not_write(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    (tmp_path / "01 Song.lrc").write_text(LRC, encoding="utf-8")

    result = process_lyrics([mp3_track(path)], apply=False)

    assert result.embedded_written == 0
    assert has_embedded_lyrics(path) is False


def test_apply_embeds_lyrics(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    (tmp_path / "01 Song.txt").write_text(LYRICS, encoding="utf-8")

    result = process_lyrics([mp3_track(path)], apply=True)

    assert result.embedded_written == 1
    assert read_embedded_lyrics(path) == LYRICS


def test_apply_saves_lrc_when_save_lrc_true(tmp_path, monkeypatch) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)

    monkeypatch.setattr("noqlen_forge.lyrics.fetch_lrclib_lyrics", lambda track, prefer_synced=True, timeout=10, debug=False: (LyricsResult(LRC, True, "lrclib"), []))
    result = process_lyrics([mp3_track(path)], apply=True, sources=["lrclib"], save_lrc=True)

    assert result.sidecar_written == 1
    assert (tmp_path / "01 Song.lrc").read_text(encoding="utf-8").strip() == LRC


def test_does_not_overwrite_without_force(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    write_embedded_lyrics(path, "Existing line", force=True)

    write_embedded_lyrics(path, LYRICS, force=False)

    assert read_embedded_lyrics(path) == "Existing line"


def test_overwrites_with_force(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    write_embedded_lyrics(path, "Existing line", force=True)

    write_embedded_lyrics(path, LYRICS, force=True)

    assert read_embedded_lyrics(path) == LYRICS


def test_audit_counts_lyrics(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    write_embedded_lyrics(path, LYRICS, force=True)
    track = read_track(path)

    output = render_audit(AuditResult(tracks=[track], bad_fields=[]))

    assert "Lyrics: 1/1" in output


def test_audit_counts_synced_lyrics(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    (tmp_path / "01 Song.lrc").write_text(LRC, encoding="utf-8")
    track = read_track(path)

    output = render_audit(AuditResult(tracks=[track], bad_fields=[]))

    assert "Synced Lyrics: 1/1" in output
    assert "Sidecar LRC: 1/1" in output


def test_missing_lyrics_warns_not_review() -> None:
    track = Track(
        path=Path("song.mp3"),
        format="mp3",
        album="Album",
        artist="Artist",
        title="Song",
        tags={
            "mb_album_id": ["album"],
            "mb_track_id": ["track"],
            "mb_release_group_id": ["group"],
            "label": ["Label"],
            "style": ["Pop"],
            "originaldate": ["2024"],
            "bpm": ["120"],
            "key": ["C Major"],
            "energy": ["80"],
            "danceability": ["80"],
            "lastfm_tags": ["pop"],
            "mood": ["Happy"],
            "cover": ["1"],
        },
    )

    result = AuditResult(tracks=[track], bad_fields=[])

    assert result.status == "WARN"
    assert "- Lyrics missing: 1/1" in render_audit(result)


def test_standard_output_does_not_print_lyrics_content(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    (tmp_path / "01 Song.txt").write_text(LYRICS, encoding="utf-8")

    code, output = lyrics_path(path)

    assert code == 0
    assert "First private line" not in output
    assert "Second private line" not in output


def test_verbose_shows_source_and_file_but_not_full_lyrics(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    (tmp_path / "01 Song.txt").write_text(LYRICS, encoding="utf-8")

    code, output = lyrics_path(path, verbose=True)

    assert code == 0
    assert "source=sidecar:01 Song.txt" in output
    assert str(path) in output
    assert "First private line" not in output


def test_provider_unavailable_is_warn_not_fail(tmp_path, monkeypatch) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)

    monkeypatch.setattr("noqlen_forge.lyrics.fetch_lrclib_lyrics", lambda track, prefer_synced=True, timeout=10, debug=False: (None, []))
    code, output = lyrics_path(path, sources=["lrclib"])

    assert code == 0
    assert "Status: WARN" in output
    assert "no lyrics from lrclib" in output


def test_provider_registry_lists_lrclib() -> None:
    assert "lrclib" in PROVIDERS


def test_provider_registry_lists_custom_http() -> None:
    assert "custom_http" in PROVIDERS
    assert PROVIDERS["custom_http"].enabled is False


def test_custom_http_disabled_is_skipped(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)

    result = process_lyrics([mp3_track(path)], sources=["custom_http"], config={"lyrics": {"provider_settings": {"custom_http": {"enabled": False, "base_url": "http://lyrics.test"}}}})

    assert result.provider_attempts[path][0].status == "SKIP"


def test_custom_http_requires_base_url(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)

    result = process_lyrics([mp3_track(path)], sources=["custom_http"], config={"lyrics": {"provider_settings": {"custom_http": {"enabled": True}}}})

    assert result.provider_attempts[path][0].message == "requires base_url"


def test_custom_http_scores_valid_response() -> None:
    candidates = custom_http_candidates([
        {"artist": "Artist", "title": "Song", "album": "Album", "duration": 100, "synced": LRC, "plain": LYRICS, "language": "en", "source_url": "https://example.test/lyrics"}
    ], LyricsSearchQuery(title="Song", artist="Artist", album="Album", duration=101))

    assert candidates[0].provider == "custom_http"
    assert candidates[0].synced is True
    assert candidates[0].confidence == "high"
    assert candidates[0].language == "en"


def test_custom_http_tolerates_missing_fields() -> None:
    candidates = custom_http_candidates([{"synced": LRC}], LyricsSearchQuery(title="Song", artist="Artist"))

    assert candidates[0].confidence == "low"
    assert candidates[0].text == LRC


def test_custom_http_invalid_response_warns(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return b'{"bad": []}'

    monkeypatch.setattr("noqlen_forge.lyrics_providers.urllib.request.urlopen", lambda request, timeout=20: Response())
    config = {"lyrics": {"online": {"rate_limit_seconds": 0}, "provider_settings": {"custom_http": {"enabled": True, "base_url": "http://lyrics.test"}}}}

    result = process_lyrics([mp3_track(path)], sources=["custom_http"], config=config)

    assert result.provider_attempts[path][0].status == "WARN"
    assert "expected results list" in result.provider_attempts[path][0].message


def test_custom_http_builds_query_and_hides_secret(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Song"))
    tags.add(TPE1(encoding=3, text="Artist"))
    tags.add(TALB(encoding=3, text="Album"))
    tags.save(path)
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return b'{"results":[{"artist":"Artist","title":"Song","album":"Album","synced":"[00:01.00]Private"}]}'

    def fake_urlopen(request, timeout=20):
        seen["url"] = request.full_url
        seen["auth"] = request.headers.get("Authorization")
        return Response()

    monkeypatch.setenv("NOQLEN_FORGE_LYRICS_API_KEY", "secret-token-value")
    monkeypatch.setattr("noqlen_forge.lyrics_providers.urllib.request.urlopen", fake_urlopen)
    config = {"lyrics": {"online": {"rate_limit_seconds": 0}, "provider_settings": {"custom_http": {"enabled": True, "base_url": "http://lyrics.test/search", "api_key_env": "NOQLEN_FORGE_LYRICS_API_KEY"}}}}

    code, output = lyrics_path(path, sources=["custom_http"], config=config, verbose=True)

    assert code == 0
    assert "artist=Artist" in seen["url"]
    assert "title=Song" in seen["url"]
    assert seen["auth"] == "Bearer secret-token-value"
    assert "secret-token-value" not in output
    assert "Private" not in output


def test_custom_http_prefer_synced(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return b'{"results":[{"artist":"Artist","title":"Song","album":"Album","synced":"[00:01.00]Synced private","plain":"Plain private"}]}'

    monkeypatch.setattr("noqlen_forge.lyrics_providers.urllib.request.urlopen", lambda request, timeout=20: Response())
    config = {"lyrics": {"online": {"rate_limit_seconds": 0}, "provider_settings": {"custom_http": {"enabled": True, "base_url": "http://lyrics.test"}}}}

    result = process_lyrics([mp3_track(path)], sources=["custom_http"], config=config, prefer_synced=True)

    assert result.per_file[path].synced is True


def test_unknown_provider_reports_clear_warning(tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)

    result = process_lyrics([mp3_track(path)], sources=["missing-provider"])

    assert result.provider_attempts[path][0].status == "SKIP" or result.provider_attempts[path][0].status == "WARN"
    assert "unknown" in result.provider_attempts[path][0].message


def test_provider_order_and_fallback(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    monkeypatch.setitem(PROVIDERS, "fake_empty", FakeLyricsProvider("fake_empty", None))
    monkeypatch.setitem(PROVIDERS, "fake_ok", FakeLyricsProvider("fake_ok", LRC, synced=True))

    result = process_lyrics([mp3_track(path)], sources=["fake_empty", "fake_ok"])

    assert [attempt.provider for attempt in result.provider_attempts[path]] == ["fake_empty", "fake_ok"]
    assert result.per_file[path].provider == "fake_ok"
    assert result.per_file[path].synced is True


def test_min_confidence_blocks_weak_provider(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    monkeypatch.setitem(PROVIDERS, "fake_low", FakeLyricsProvider("fake_low", LYRICS, confidence="low"))

    result = process_lyrics([mp3_track(path)], sources=["fake_low"], min_confidence="medium")

    assert path not in result.per_file
    assert result.provider_attempts[path][0].message == "confidence low below minimum medium"


def test_allow_unsynced_controls_unsynced_fallback(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    monkeypatch.setitem(PROVIDERS, "fake_plain", FakeLyricsProvider("fake_plain", LYRICS, synced=False))

    blocked = process_lyrics([mp3_track(path)], sources=["fake_plain"], allow_unsynced=False)
    allowed = process_lyrics([mp3_track(path)], sources=["fake_plain"], allow_unsynced=True)

    assert path not in blocked.per_file
    assert allowed.per_file[path].synced is False


def test_conflicting_strong_providers_require_review(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    monkeypatch.setitem(PROVIDERS, "fake_a", FakeLyricsProvider("fake_a", "Alpha line", synced=False))
    monkeypatch.setitem(PROVIDERS, "fake_b", FakeLyricsProvider("fake_b", "Completely different beta", synced=False))

    result = process_lyrics([mp3_track(path)], sources=["fake_a", "fake_b"])

    assert result.status == "REVIEW"
    assert result.conflicts


def test_force_overwrites_existing_provider_lyrics(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    write_embedded_lyrics(path, strip_lrc_timestamps(LRC), force=True)
    monkeypatch.setitem(PROVIDERS, "fake_new", FakeLyricsProvider("fake_new", LYRICS, synced=False))

    result = process_lyrics([mp3_track(path)], sources=["fake_new"], apply=True, force=True)

    assert result.embedded_written == 1
    assert read_embedded_lyrics(path) == LYRICS


def test_force_reviews_existing_provider_mismatch(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    write_embedded_lyrics(path, "Existing line", force=True)
    monkeypatch.setitem(PROVIDERS, "fake_new", FakeLyricsProvider("fake_new", LYRICS, synced=False))

    result = process_lyrics([mp3_track(path)], sources=["fake_new"], apply=True, force=True)

    assert result.status == "REVIEW"
    assert result.embedded_written == 0


def test_sidecar_lrc_requires_flag(monkeypatch, tmp_path) -> None:
    path = tmp_path / "01 Song.mp3"
    ID3().save(path)
    monkeypatch.setitem(PROVIDERS, "fake_synced", FakeLyricsProvider("fake_synced", LRC, synced=True))

    process_lyrics([mp3_track(path)], sources=["fake_synced"], apply=True, save_lrc=False)
    assert not (tmp_path / "01 Song.lrc").exists()
    process_lyrics([mp3_track(path)], sources=["fake_synced"], apply=True, force=True, save_lrc=True)
    assert (tmp_path / "01 Song.lrc").exists()
