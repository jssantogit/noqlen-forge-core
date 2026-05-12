from pathlib import Path
from urllib.error import HTTPError

import pytest
from mutagen.id3 import ID3

from noqlen_forge.audio import Track
from noqlen_forge.lastfm import _lastfm_url, _request_lastfm, _write_mp4_lastfm_tags, analyze_lastfm_tags, fetch_best_lastfm_tags_debug, fetch_track_top_tags, filter_lastfm_tags, normalize_lastfm_tags, write_lastfm_tags

pytestmark = pytest.mark.provider


class FakeMP4:
    def __init__(self):
        self.tags = {}

    def add_tags(self):
        self.tags = {}


def test_lastfm_missing_api_key_does_not_break_analyze(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)

    code, output = analyze_lastfm_tags(path)

    assert code == 0
    assert output == "Last.fm: skipped, LASTFM_API_KEY not set."


def test_filter_lastfm_tags_sorts_by_count_and_limits() -> None:
    tags = [
        {"name": "pop", "count": "12"},
        {"name": "dance", "count": "9"},
        {"name": "k-pop", "count": "20"},
        {"name": "low", "count": "2"},
    ]

    assert filter_lastfm_tags(tags, min_count=3, max_tags=2) == ["K-pop", "Pop"]


def test_filter_lastfm_tags_removes_noise() -> None:
    tags = [{"name": "seen live", "count": "99"}, {"name": "youtube", "count": "99"}, {"name": "technical death metal", "count": "4"}]

    assert filter_lastfm_tags(tags) == ["Technical Death Metal"]


def test_filter_lastfm_tags_deduplicates_case_insensitive() -> None:
    tags = [{"name": "Pop", "count": "3"}, {"name": "pop", "count": "12"}, {"name": "POP", "count": "5"}]

    assert filter_lastfm_tags(tags) == ["Pop"]


def test_filter_lastfm_tags_removes_current_artist_name() -> None:
    tags = [{"name": "RESCENE", "count": "99"}, {"name": "k-pop", "count": "4"}]

    assert filter_lastfm_tags(tags, artist="RESCENE") == ["K-pop"]


def test_filter_lastfm_tags_normalizes_girl_group_without_duplicate() -> None:
    tags = [
        {"name": "girl group", "count": "4"},
        {"name": "Girl Groups", "count": "8"},
        {"name": "girl groups", "count": "6"},
    ]

    assert filter_lastfm_tags(tags) == ["Girl Group"]


def test_normalize_lastfm_tags_joins_semicolon_space_and_dedupes() -> None:
    assert normalize_lastfm_tags(["pop, Dance; pop ; k-pop; Kpop"]) == ["K-pop; Pop; Dance"]


def test_write_lastfm_tags_mp3(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    write_lastfm_tags(path, ["k-pop", "pop", "dance"])

    assert ID3(path).getall("TXXX:LASTFM_TAGS")[0].text == ["K-pop; Pop; dance"]


def test_write_lastfm_tags_m4a_freeform() -> None:
    audio = FakeMP4()

    _write_mp4_lastfm_tags(audio, "k-pop; pop")

    assert bytes(audio.tags["----:com.apple.iTunes:LASTFM_TAGS"][0]) == b"k-pop; pop"


def test_analyze_lastfm_tags_preserves_existing_without_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.read_track", lambda file_path: Track(Path(file_path), "mp3", artist="A", title="T", tags={"lastfm_tags": ["pop"]}))
    monkeypatch.setattr("noqlen_forge.lastfm.fetch_track_top_tags", lambda *args, **kwargs: _raise_unexpected())

    code, output = analyze_lastfm_tags(path)

    assert code == 0
    assert "skipped existing LASTFM_TAGS=Pop" in output


def test_analyze_lastfm_tags_writes_when_apply(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.read_track", lambda file_path: Track(Path(file_path), "mp3", artist="A", title="T", tags={}))
    monkeypatch.setattr("noqlen_forge.lastfm.fetch_best_lastfm_tags_debug", lambda *args, **kwargs: _lastfm_result([{"name": "pop", "count": "5"}], source="track", confidence="high"))
    monkeypatch.setattr("noqlen_forge.lastfm.write_lastfm_tags", lambda file_path, tags: written.append((file_path, tags)))

    code, output = analyze_lastfm_tags(path, apply=True)

    assert code == 0
    assert written == [(path, ["Pop"])]
    assert "wrote LASTFM_TAGS=Pop" in output
    assert "source=track confidence=high" in output


def test_analyze_lastfm_tags_filters_rescene_artist_tag(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.read_track", lambda file_path: Track(Path(file_path), "mp3", artist="RESCENE", title="Runaway", tags={}))
    monkeypatch.setattr(
        "noqlen_forge.lastfm.fetch_best_lastfm_tags_debug",
        lambda *args, **kwargs: _lastfm_result(
            [
                {"name": "K-pop", "count": "99"},
                {"name": "Pop", "count": "50"},
                {"name": "girl group", "count": "20"},
                {"name": "Girl Groups", "count": "15"},
                {"name": "RESCENE", "count": "99"},
                {"name": "Korean", "count": "10"},
            ],
            source="artist",
            confidence="medium",
        ),
    )
    monkeypatch.setattr("noqlen_forge.lastfm.write_lastfm_tags", lambda file_path, tags: written.append((file_path, tags)))

    code, output = analyze_lastfm_tags(path, apply=True)

    assert code == 0
    assert written == [(path, ["K-pop", "Pop", "Girl Group", "Korean"])]
    assert "LASTFM_TAGS" in output
    assert "RESCENE" not in output


def test_lastfm_cache_is_used(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.CACHE_ROOT", tmp_path)
    monkeypatch.setattr("noqlen_forge.lastfm._request_lastfm", lambda params, **kwargs: calls.append(params) or {"toptags": {"tag": [{"name": "pop", "count": "5"}]}})

    assert fetch_track_top_tags("Artist", "Song") == [{"name": "pop", "count": "5"}]
    assert fetch_track_top_tags("Artist", "Song") == [{"name": "pop", "count": "5"}]
    assert len(calls) == 1


def test_fetch_lastfm_does_not_send_empty_mbid(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.CACHE_ROOT", tmp_path)
    monkeypatch.setattr("noqlen_forge.lastfm._request_lastfm", lambda params, **kwargs: calls.append(params) or {"toptags": {"tag": []}})

    fetch_track_top_tags("Artist", "Song", mbid="")

    assert calls == [{"artist": "Artist", "track": "Song", "api_key": "key"}]


def test_lastfm_track_without_tags_falls_back_to_album(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.CACHE_ROOT", tmp_path)

    def fake_request(params, **kwargs):
        calls.append(params)
        if params["method"] == "album.getTopTags":
            return {"toptags": {"tag": [{"name": "k-rnb", "count": "4"}]}}
        return {"toptags": {"tag": []}}

    monkeypatch.setattr("noqlen_forge.lastfm._request_lastfm", fake_request)

    result = fetch_best_lastfm_tags_debug("RESCENE", "Runaway", album="Dearest", min_count=3)

    assert filter_lastfm_tags(result.tags) == ["K-R&B"]
    assert result.source == "album"
    assert result.confidence == "high"
    assert [call["method"] for call in calls] == ["track.getTopTags", "album.getTopTags"]


def test_lastfm_album_without_tags_falls_back_to_artist(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.CACHE_ROOT", tmp_path)

    def fake_request(params, **kwargs):
        calls.append(params)
        if params["method"] == "artist.getTopTags":
            return {"toptags": {"tag": [{"name": "technical death metal", "count": "8"}]}}
        return {"toptags": {"tag": []}}

    monkeypatch.setattr("noqlen_forge.lastfm._request_lastfm", fake_request)

    result = fetch_best_lastfm_tags_debug("First Fragment", "Gloire Eternelle", album="Gloire Eternelle", min_count=3)

    assert filter_lastfm_tags(result.tags) == ["Technical Death Metal"]
    assert result.source == "artist"
    assert result.confidence == "medium"
    assert [call["method"] for call in calls] == ["track.getTopTags", "album.getTopTags", "artist.getTopTags"]


def test_lastfm_source_appears_in_report(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.read_track", lambda file_path: Track(Path(file_path), "mp3", album="A", artist="Artist", title="T", tags={}))
    monkeypatch.setattr("noqlen_forge.lastfm.fetch_best_lastfm_tags_debug", lambda *args, **kwargs: _lastfm_result([{"name": "technical death metal", "count": "5"}], source="artist", confidence="medium"))

    code, output = analyze_lastfm_tags(path)

    assert code == 0
    assert "tags=Technical Death Metal source=artist confidence=medium" in output


def test_lastfm_no_fallback_does_not_use_album_or_artist(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.CACHE_ROOT", tmp_path)
    monkeypatch.setattr("noqlen_forge.lastfm._request_lastfm", lambda params, **kwargs: calls.append(params) or {"toptags": {"tag": []}})

    result = fetch_best_lastfm_tags_debug("Artist", "Song", album="Album", allow_fallback=False)

    assert result.tags == []
    assert [call["method"] for call in calls] == ["track.getTopTags"]


def test_lastfm_does_not_call_empty_album_or_artist(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.CACHE_ROOT", tmp_path)
    monkeypatch.setattr("noqlen_forge.lastfm._request_lastfm", lambda params, **kwargs: calls.append(params) or {"toptags": {"tag": []}})

    fetch_best_lastfm_tags_debug("unknown", "Song", album="unknown")

    assert calls == []


def test_lastfm_url_encodes_accented_track() -> None:
    url = _lastfm_url({"artist": "First Fragment", "track": "Gloire Éternelle", "api_key": "key"})

    assert "format=json" in url
    assert "Gloire+%C3%89ternelle" in url


def test_lastfm_url_encodes_apostrophe() -> None:
    url = _lastfm_url({"artist": "First Fragment", "track": "In'El", "api_key": "key"})

    assert "In%27El" in url


def test_http_400_does_not_break_process(monkeypatch) -> None:
    def raise_http_error(url, timeout):
        raise HTTPError(url, 400, "Bad Request", {}, _Body(b'{"error":6,"message":"Invalid parameters"}'))

    monkeypatch.setattr("noqlen_forge.lastfm.urllib.request.urlopen", raise_http_error)

    data = _request_lastfm({"artist": "A", "track": "T", "api_key": "key"})

    assert data["_http_status"] == 400


def test_http_400_debug_shows_response_body(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setattr("noqlen_forge.lastfm.CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr("noqlen_forge.lastfm.read_track", lambda file_path: Track(Path(file_path), "mp3", artist="A", title="T", tags={}))
    monkeypatch.setattr("noqlen_forge.lastfm.urllib.request.urlopen", lambda url, timeout: (_ for _ in ()).throw(HTTPError(url, 400, "Bad Request", {}, _Body(b"bad body"))))

    code, output = analyze_lastfm_tags(path, debug=True)

    assert code == 0
    assert "http_status=400" in output
    assert "http_body=bad body" in output
    assert "api_key" not in output


def test_filter_lastfm_tags_removes_requested_noise() -> None:
    tags = [
        {"name": "2023", "count": "99"},
        {"name": "favs", "count": "99"},
        {"name": "maris song", "count": "99"},
        {"name": "you don't even know my name do ya", "count": "99"},
        {"name": "One time flamengo", "count": "99"},
        {"name": "hit", "count": "99"},
        {"name": "vocal", "count": "99"},
        {"name": "aespa", "count": "99"},
        {"name": "ive", "count": "99"},
        {"name": "k-pop", "count": "4"},
    ]

    assert filter_lastfm_tags(tags, artist="NewJeans") == ["K-pop"]


def test_filter_lastfm_tags_normalizes_rnb_variants() -> None:
    tags = [{"name": "rnb", "count": "1"}, {"name": "contemporary rnb", "count": "1"}, {"name": "alternative rnb", "count": "1"}]

    assert filter_lastfm_tags(tags) == ["R&B", "Contemporary R&B", "Alternative R&B"]


def test_filter_lastfm_tags_normalizes_k_rnb() -> None:
    tags = [{"name": "k-rnb", "count": "1"}, {"name": "korean rnb", "count": "1"}]

    assert filter_lastfm_tags(tags) == ["K-R&B", "Korean R&B"]


def test_filter_lastfm_tags_normalizes_requested_genres() -> None:
    tags = [
        {"name": "future bass", "count": "1"},
        {"name": "future house", "count": "1"},
        {"name": "bedroom pop", "count": "1"},
        {"name": "hip hop soul", "count": "1"},
        {"name": "neo-soul", "count": "1"},
    ]

    assert filter_lastfm_tags(tags) == ["Future Bass", "Future House", "Bedroom Pop", "Hip Hop Soul", "Neo-Soul"]


def test_filter_lastfm_tags_places_descriptors_after_genres() -> None:
    tags = [{"name": "catchy", "count": "99"}, {"name": "Korean", "count": "99"}, {"name": "k-pop", "count": "3"}]

    assert filter_lastfm_tags(tags) == ["K-pop", "Korean", "catchy"]


def test_filter_lastfm_tags_preserves_allowlisted_low_count_tags() -> None:
    tags = [{"name": "UK Garage", "count": "1"}, {"name": "jersey club", "count": "1"}, {"name": "Technical Death Metal", "count": "1"}]

    assert filter_lastfm_tags(tags) == ["UK Garage", "Jersey Club", "Technical Death Metal"]


class _Body:
    def __init__(self, value: bytes):
        self.value = value

    def read(self) -> bytes:
        return self.value

    def close(self) -> None:
        return None


def _raise_unexpected():
    raise AssertionError("unexpected call")


def _lastfm_result(tags, source="", confidence=""):
    from noqlen_forge.lastfm import LastfmFetchResult

    return LastfmFetchResult(tags=tags, source=source, confidence=confidence)
