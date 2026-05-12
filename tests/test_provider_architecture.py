import json
from pathlib import Path

import pytest
from mutagen.id3 import ID3

from noqlen_forge import cli
from noqlen_forge.audio import Track
from noqlen_forge.config import default_config, get_api_credential, masked_config, merge_config, render_config
from noqlen_forge.cover import process_cover, validate_image_bytes
from noqlen_forge.cover_providers import DeezerCoverProvider, ITunesCoverProvider, MusicBrainzCoverProvider, fetch_cover_with_providers
from noqlen_forge.lyrics import lyrics_path, process_lyrics
from noqlen_forge.workflow import Status, WorkflowResult

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
LYRICS = "First private line\nSecond private line"
LRC = "[00:01.00]First private line\n[00:02.00]Second private line"

pytestmark = pytest.mark.provider


def mp3_track(path: Path, **kwargs) -> Track:
    values = {"album": "Album", "artist": "Artist", "title": "Song"}
    values.update(kwargs)
    return Track(path=path, format="mp3", **values)


def test_cover_local_provider_uses_cover_jpg_without_saving_by_default(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    (tmp_path / "cover.jpg").write_bytes(JPEG)

    result = process_cover(path, tracks=[mp3_track(path)], apply=True, sources=["local"])

    assert result.provider == "local"
    assert result.saved_path is None
    assert (tmp_path / "cover.jpg").read_bytes() == JPEG


def test_musicbrainz_provider_uses_release_id(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_get(url, accept, max_bytes=10 * 1024 * 1024):
        calls.append(url)
        if url.endswith("/release-id"):
            return b'{"images":[{"front":true,"image":"https://img.example/cover.jpg"}]}'
        return JPEG

    monkeypatch.setattr("noqlen_forge.cover_providers.get_bytes", fake_get)
    track = mp3_track(tmp_path / "song.mp3", tags={"mb_album_id": ["release-id"]})

    attempt = MusicBrainzCoverProvider().fetch([track], tmp_path)

    assert attempt.result is not None
    assert attempt.result.confidence == "high"
    assert calls[0].endswith("/release-id")


def test_itunes_provider_selects_matching_album(monkeypatch, tmp_path) -> None:
    def fake_get(url, accept, max_bytes=10 * 1024 * 1024):
        if "itunes.apple.com" in url:
            return json.dumps({"results": [{"artistName": "Other", "collectionName": "Wrong", "artworkUrl100": "https://img/wrong.jpg"}, {"artistName": "Artist", "collectionName": "Album", "artworkUrl100": "https://img/100x100bb.jpg", "collectionId": 1}]}).encode()
        return JPEG

    monkeypatch.setattr("noqlen_forge.cover_providers.get_bytes", fake_get)

    attempt = ITunesCoverProvider().fetch([mp3_track(tmp_path / "song.mp3")], tmp_path)

    assert attempt.result is not None
    assert attempt.result.provider == "itunes"
    assert attempt.result.confidence == "high"
    assert attempt.result.external_url == "https://img/1000x1000bb.jpg"


def test_deezer_provider_uses_cover_xl(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_get(url, accept, max_bytes=10 * 1024 * 1024):
        calls.append(url)
        if "api.deezer.com" in url:
            return json.dumps({"data": [{"title": "Song", "artist": {"name": "Artist"}, "album": {"title": "Album", "cover_xl": "https://img/xl.jpg", "cover_big": "https://img/big.jpg"}}]}).encode()
        return PNG

    monkeypatch.setattr("noqlen_forge.cover_providers.get_bytes", fake_get)

    attempt = DeezerCoverProvider().fetch([mp3_track(tmp_path / "song.mp3")], tmp_path)

    assert attempt.result is not None
    assert attempt.result.mime == "image/png"
    assert calls[-1] == "https://img/xl.jpg"


def test_cover_low_confidence_is_not_selected_by_default(monkeypatch, tmp_path) -> None:
    def fake_get(url, accept, max_bytes=10 * 1024 * 1024):
        if "itunes.apple.com" in url:
            return json.dumps({"results": [{"artistName": "Wrong", "trackName": "Song", "artworkUrl100": "https://img/100x100bb.jpg"}]}).encode()
        return JPEG

    monkeypatch.setattr("noqlen_forge.cover_providers.get_bytes", fake_get)
    result, attempts = fetch_cover_with_providers([mp3_track(tmp_path / "song.mp3", album="")], tmp_path, ["itunes"], min_confidence="medium")

    assert result is None
    assert attempts[-1].message == "confidence low below minimum medium"


def test_cover_fallback_tries_next_provider(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    track = mp3_track(path, tags={"mb_album_id": ["release"]})
    monkeypatch.setattr("noqlen_forge.cover.fetch_cover_from_musicbrainz", lambda release_id, debug=False: (None, []))

    def fake_get(url, accept, max_bytes=10 * 1024 * 1024):
        if "itunes.apple.com" in url:
            return json.dumps({"results": [{"artistName": "Artist", "collectionName": "Album", "artworkUrl100": "https://img/100x100bb.jpg"}]}).encode()
        return JPEG

    monkeypatch.setattr("noqlen_forge.cover_providers.get_bytes", fake_get)

    result = process_cover(path, tracks=[track], apply=False, sources=["musicbrainz", "itunes"])

    assert result.provider == "itunes"
    assert [attempt.provider for attempt in result.provider_attempts] == ["musicbrainz", "itunes"]


def test_invalid_html_image_is_rejected() -> None:
    assert validate_image_bytes(b"<html>not an image</html>") is None


def test_optional_cover_provider_without_credentials_is_skip(tmp_path) -> None:
    result, attempts = fetch_cover_with_providers([mp3_track(tmp_path / "song.mp3")], tmp_path, ["spotify"])

    assert result is None
    assert attempts[0].status == "SKIP"


def test_cli_cover_source_limits_sources(monkeypatch, tmp_path, capsys) -> None:
    captured = {}

    def fake_cover_service(options):
        captured["sources"] = options.sources
        captured["min_confidence"] = options.min_confidence
        return WorkflowResult(Status.OK, [], command="cover", details={"exit_code": 0, "output_text": "cover ok"})

    monkeypatch.setattr(cli, "run_cover_service", fake_cover_service)
    code = cli.main(["cover", str(tmp_path), "--cover-source", "local", "--cover-source", "itunes", "--min-cover-confidence", "high"])

    assert code == 0
    assert captured["sources"] == ["local", "itunes"]
    assert captured["min_confidence"] == "high"
    assert "cover ok" in capsys.readouterr().out


def test_cover_config_sources_control_order(tmp_path) -> None:
    result, attempts = fetch_cover_with_providers([mp3_track(tmp_path / "song.mp3")], tmp_path, ["spotify", "unknown"])

    assert result is None
    assert [attempt.provider for attempt in attempts] == ["spotify", "unknown"]


def test_lyrics_local_provider_detects_lrc_and_txt(tmp_path) -> None:
    lrc_path = tmp_path / "01 Song.mp3"
    txt_path = tmp_path / "02 Song.mp3"
    ID3().save(lrc_path)
    ID3().save(txt_path)
    (tmp_path / "01 Song.lrc").write_text(LRC, encoding="utf-8")
    (tmp_path / "02 Song.txt").write_text(LYRICS, encoding="utf-8")

    lrc_result = process_lyrics([mp3_track(lrc_path)], sources=["local"])
    txt_result = process_lyrics([mp3_track(txt_path, title="Song")], sources=["local"])

    assert lrc_result.per_file[lrc_path].synced is True
    assert txt_result.per_file[txt_path].synced is False


def test_lrclib_prefers_synced_and_falls_back_plain(monkeypatch, tmp_path) -> None:
    rows = [{"id": 1, "artistName": "Artist", "trackName": "Song", "albumName": "Album", "duration": 100, "syncedLyrics": LRC, "plainLyrics": LYRICS}]
    monkeypatch.setattr("noqlen_forge.lyrics.fetch_lrclib_lyrics", lambda track, prefer_synced=True, timeout=10, debug=False: (__import__("noqlen_forge.lyrics", fromlist=["LyricsResult"]).LyricsResult(LRC if prefer_synced else LYRICS, prefer_synced, "lrclib", "high", "lrclib", duration=100, match_reason="artist, title, album and duration match", external_id="1"), []))

    synced = process_lyrics([mp3_track(tmp_path / "song.mp3", duration=100)], sources=["lrclib"], prefer_synced=True)
    plain = process_lyrics([mp3_track(tmp_path / "song.mp3", duration=100)], sources=["lrclib"], prefer_synced=False)

    assert rows
    assert synced.per_file[tmp_path / "song.mp3"].synced is True
    assert plain.per_file[tmp_path / "song.mp3"].synced is False


def test_lyrics_low_confidence_not_applied_and_fallback_works(tmp_path, monkeypatch) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    (tmp_path / "song.txt").write_text(LYRICS, encoding="utf-8")
    monkeypatch.setattr("noqlen_forge.lyrics.fetch_lrclib_lyrics", lambda track, prefer_synced=True, timeout=10, debug=False: (None, []))

    result = process_lyrics([mp3_track(path)], sources=["lrclib", "local"], min_confidence="medium")

    assert result.per_file[path].provider == "local"
    assert [attempt.provider for attempt in result.provider_attempts[path]] == ["lrclib", "local"]


def test_lyrics_provider_unavailable_is_warn_skip(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    result = process_lyrics([mp3_track(path)], sources=["genius"])

    assert result.provider_attempts[path][0].status == "SKIP"
    assert result.status == "WARN"


def test_lyrics_output_never_prints_full_lyrics_in_debug(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    (tmp_path / "song.txt").write_text(LYRICS, encoding="utf-8")

    code, output = lyrics_path(path, debug=True)

    assert code == 0
    assert "First private line" not in output
    assert "Second private line" not in output


def test_cli_lyrics_source_limits_sources(monkeypatch, tmp_path, capsys) -> None:
    captured = {}

    def fake_lyrics_service(options):
        captured["sources"] = options.sources
        captured["min_confidence"] = options.min_confidence
        captured["prefer_synced"] = options.prefer_synced
        return WorkflowResult(Status.DRY, [], command="lyrics", details={"output_text": "lyrics ok"})

    monkeypatch.setattr(cli, "run_lyrics_service", fake_lyrics_service)
    code = cli.main(["lyrics", str(tmp_path), "--lyrics-source", "local", "--min-lyrics-confidence", "high", "--prefer-unsynced"])

    assert code == 0
    assert captured["sources"] == ["local"]
    assert captured["min_confidence"] == "high"
    assert captured["prefer_synced"] is False
    assert "lyrics ok" in capsys.readouterr().out


def test_config_sources_and_credentials(monkeypatch) -> None:
    config = merge_config(default_config(), {"cover": {"sources": ["deezer", "itunes"]}, "lyrics": {"sources": ["lrclib"]}, "apis": {"genius_access_token": "abcdefghijkl1234"}})
    monkeypatch.setenv("GENIUS_ACCESS_TOKEN", "from-env")

    assert config["cover"]["sources"] == ["deezer", "itunes"]
    assert config["lyrics"]["sources"] == ["lrclib"]
    assert get_api_credential(config, "genius_access_token") == "from-env"
    rendered = render_config(masked_config(config), mask_secrets=False)
    assert "abcd...1234" in rendered
    assert "abcdefghijkl1234" not in rendered
