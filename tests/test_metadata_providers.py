from pathlib import Path

import pytest

from noqlen_forge.cli import build_parser
from noqlen_forge.audio import Track
from noqlen_forge.config import default_config, masked_config, merge_config, render_config
from noqlen_forge.metadata_providers import (
    AcoustIDMetadataProvider,
    DeezerMetadataProvider,
    DiscogsMetadataProvider,
    ITunesMetadataProvider,
    MetadataCandidate,
    ProviderAttempt,
    build_context,
    acoustid_api_key,
    acoustid_plans_from_candidate,
    candidate_from_acoustid_results,
    candidate_from_deezer_album,
    candidate_from_deezer_track,
    candidate_from_discogs_release,
    candidate_from_itunes_collection,
    candidate_from_itunes_song,
    discogs_token,
    fetch_metadata_with_providers,
    merge_candidate,
    merge_ambiguous_discogs_common_fields,
    metadata_path,
    metadata_status,
    provider_has_authority,
    parse_fpcalc_output,
    select_acoustid_match,
    render_metadata_output,
    resolve_metadata_providers,
    score_discogs_candidate,
    score_digital_candidate,
)

pytestmark = pytest.mark.provider


def tracks(**kwargs) -> list[Track]:
    values = {"album": "Urn", "albumartist": "Ne Obliviscaris", "artist": "Ne Obliviscaris", "title": "Libera", "date": "2017", "tags": {}}
    values.update(kwargs)
    return [Track(Path("01.flac"), "flac", tracknumber=1, **values), Track(Path("02.flac"), "flac", title="Intra Venus", tracknumber=2, **{key: value for key, value in values.items() if key != "title"})]


def discogs_release(**kwargs):
    value = {
        "id": 123,
        "title": "Urn",
        "artists": [{"name": "Ne Obliviscaris"}],
        "released": "2017-10-27",
        "country": "Europe",
        "genres": ["Rock"],
        "styles": ["Progressive Metal", "Melodic Death Metal"],
        "labels": [{"name": "Season Of Mist", "catno": "SOM 432"}],
        "formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"]}],
        "identifiers": [{"type": "Barcode", "value": "822603143229"}],
        "tracklist": [{"position": "1", "title": "Libera", "duration": "8:00"}, {"position": "2", "title": "Intra Venus", "duration": "7:00"}],
    }
    value.update(kwargs)
    return value


def deezer_album(**kwargs):
    value = {
        "id": 10,
        "title": "Runaway",
        "artist": {"name": "RESCENE"},
        "release_date": "2024-08-27",
        "nb_tracks": 1,
        "genres": {"data": [{"name": "K-Pop"}]},
        "cover_xl": "https://cdn.example/cover.jpg",
        "tracks": {"data": [{"id": 20, "title": "Runaway", "track_position": 1, "duration": 180}]},
    }
    value.update(kwargs)
    return value


def itunes_album(**kwargs):
    value = {
        "wrapperType": "collection",
        "collectionId": 100,
        "collectionName": "Runaway - Single",
        "artistName": "RESCENE",
        "collectionArtistName": "RESCENE",
        "releaseDate": "2024-08-27T07:00:00Z",
        "primaryGenreName": "K-Pop",
        "trackCount": 1,
        "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Music/1/100x100bb.jpg",
        "collectionExplicitness": "notExplicit",
        "country": "BRA",
    }
    value.update(kwargs)
    return value


def itunes_song(**kwargs):
    value = {
        "wrapperType": "track",
        "kind": "song",
        "collectionId": 100,
        "trackId": 101,
        "collectionName": "Runaway - Single",
        "trackName": "Runaway",
        "artistName": "RESCENE",
        "releaseDate": "2024-08-27T07:00:00Z",
        "primaryGenreName": "K-Pop",
        "trackNumber": 1,
        "trackCount": 1,
        "discNumber": 1,
        "discCount": 1,
        "trackTimeMillis": 180000,
        "trackExplicitness": "notExplicit",
        "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Music/1/100x100bb.jpg",
        "country": "BRA",
    }
    value.update(kwargs)
    return value


def runaway_tracks(**kwargs) -> list[Track]:
    values = {"album": "Runaway - Single", "albumartist": "RESCENE", "artist": "RESCENE", "title": "Runaway", "date": "2024", "duration": 180.5, "tags": {}}
    values.update(kwargs)
    return [Track(Path("01.mp3"), "mp3", tracknumber=1, **values)]


def acoustid_payload(score=0.94, duration=180000, recording_id="rec-1", release_id="rel-1", release_group_id="rg-1"):
    return {
        "results": [
            {
                "id": "acid-1",
                "score": score,
                "recordings": [
                    {
                        "id": recording_id,
                        "title": "Runaway",
                        "duration": duration,
                        "artists": [{"name": "RESCENE"}],
                        "releases": [{"id": release_id, "releasegroup": {"id": release_group_id}, "mediums": [{"tracks": [{"id": "rt-1"}]}]}],
                    }
                ],
            }
        ]
    }


def test_discogs_candidate_score_uses_title_artist_track_count_and_date() -> None:
    context = build_context(Path("Album"), tracks())
    candidate = candidate_from_discogs_release(discogs_release(), context)

    assert candidate.score >= 85
    assert candidate.confidence == "high"
    assert "track count match" in candidate.match_reason


def test_deezer_album_candidate_parses_safe_fallback_fields() -> None:
    context = build_context(Path("Runaway - Single"), runaway_tracks(tags={}))
    candidate = candidate_from_deezer_album(deezer_album(title="Runaway - Single"), context)

    assert candidate.confidence == "high"
    assert candidate.genre == "K-Pop"
    assert candidate.date == "2024-08-27"
    assert candidate.tracktotal == "1"
    assert candidate.extra["cover_url"].endswith("cover.jpg")
    assert candidate.deezer_album_id == "10"


def test_deezer_track_candidate_scores_duration_and_ids() -> None:
    context = build_context(Path("01.mp3"), runaway_tracks(album="", tags={}))
    candidate = candidate_from_deezer_track({"id": 20, "title": "Runaway", "duration": 181, "explicit_lyrics": True, "artist": {"name": "RESCENE"}, "album": {"id": 10, "title": "Runaway - Single"}}, context)

    assert candidate.score >= 85
    assert "duration close" in candidate.match_reason
    assert candidate.explicit == "true"
    assert candidate.deezer_track_id == "20"


def test_deezer_low_confidence_does_not_apply_and_catalog_fields_are_ignored() -> None:
    context = build_context(Path("Runaway - Single"), runaway_tracks(tags={}))
    candidate = MetadataCandidate(provider="deezer", source_id="1", confidence="low", score=20, genre="Pop", catalog_number="BAD", barcode="123", country="US", media="Streaming")

    decisions = merge_candidate(context, candidate, min_confidence="medium")

    assert any(item.field == "genre" and item.action == "review" for item in decisions)
    assert all(item.field not in {"catalog_number", "barcode", "country", "media"} for item in decisions)


def test_deezer_network_failure_is_warn_skip(monkeypatch) -> None:
    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_json", lambda url: (_ for _ in ()).throw(RuntimeError("timeout")))

    attempt = DeezerMetadataProvider().fetch_album(build_context(Path("Runaway - Single"), runaway_tracks()))

    assert attempt.status == "WARN"
    assert "timeout" in attempt.message


def test_itunes_collection_parses_lookup_tracks_and_never_uses_store_country() -> None:
    context = build_context(Path("Runaway - Single"), runaway_tracks(tags={}))
    candidate = candidate_from_itunes_collection(itunes_album(), [itunes_album(), itunes_song()], context, storefront="br")

    assert candidate.confidence == "high"
    assert candidate.genre == "K-Pop"
    assert candidate.date == "2024-08-27"
    assert candidate.tracklist[0]["title"] == "Runaway"
    assert candidate.extra["cover_url"].endswith("1200x1200bb.jpg")
    assert candidate.country == ""
    assert candidate.extra["storefront"] == "br"
    assert candidate.explicit == "false"


def test_itunes_song_primary_genre_date_explicit_and_ids() -> None:
    context = build_context(Path("01.mp3"), runaway_tracks(album="", tags={}))
    candidate = candidate_from_itunes_song(itunes_song(trackExplicitness="explicit"), context, storefront="br")

    assert candidate.score >= 85
    assert candidate.genre == "K-Pop"
    assert candidate.date == "2024-08-27"
    assert candidate.explicit == "true"
    assert candidate.itunes_collection_id == "100"
    assert candidate.itunes_track_id == "101"


def test_fpcalc_parse_fingerprint_and_duration() -> None:
    parsed = parse_fpcalc_output("DURATION=180\nFINGERPRINT=abcdef\n")

    assert parsed["DURATION"] == "180"
    assert parsed["FINGERPRINT"] == "abcdef"


def test_acoustid_api_key_env_wins_config(monkeypatch) -> None:
    monkeypatch.setenv("ACOUSTID_API_KEY", "env-key")
    config = merge_config(default_config(), {"apis": {"acoustid_api_key": "config-key"}})

    assert acoustid_api_key(config) == "env-key"


def test_acoustid_high_score_generates_high_confidence_candidate() -> None:
    row = select_acoustid_match(runaway_tracks()[0], acoustid_payload(score=0.94), min_score=0.80)

    assert row["confidence"] == "high"
    assert row["mb_track_id"] == "rec-1"
    assert row["acoustid_id"] == "acid-1"


def test_acoustid_low_score_does_not_apply() -> None:
    row = select_acoustid_match(runaway_tracks()[0], acoustid_payload(score=0.60), min_score=0.80)

    assert row["confidence"] == "low"
    assert not row.get("mb_track_id")


def test_acoustid_duration_mismatch_reduces_confidence() -> None:
    row = select_acoustid_match(runaway_tracks()[0], acoustid_payload(score=0.94, duration=220000), min_score=0.80)

    assert row["confidence"] == "low"
    assert "duration mismatch" in row["match_reason"]


def test_acoustid_single_plans_acoustid_and_recording_id() -> None:
    track = runaway_tracks()[0]
    candidate = candidate_from_acoustid_results(build_context(Path("01.mp3"), [track]), [{"track": track, "fingerprint": "abcdef", **select_acoustid_match(track, acoustid_payload(), 0.80)}], default_config()["metadata_providers"]["acoustid"])

    plans = acoustid_plans_from_candidate([track], candidate)

    assert plans[0].changes["ACOUSTID_ID"] == "acid-1"
    assert plans[0].changes["ACOUSTID_FINGERPRINT"] == "abcdef"
    assert plans[0].changes["MusicBrainz Track Id"] == "rec-1"
    assert "MusicBrainz Album Id" not in plans[0].changes


def test_acoustid_album_writes_album_id_only_when_release_consistent() -> None:
    album_tracks = tracks(tags={})
    rows = []
    for track in album_tracks:
        rows.append({"track": track, "fingerprint": "fp", "acoustid_id": "acid", "mb_track_id": f"rec-{track.tracknumber}", "mb_album_id": "rel-1", "mb_release_group_id": "rg-1", "confidence": "high", "score": 95})

    candidate = candidate_from_acoustid_results(build_context(Path("Album"), album_tracks), rows, default_config()["metadata_providers"]["acoustid"])
    plans = acoustid_plans_from_candidate(album_tracks, candidate)

    assert all(plan.changes["MusicBrainz Album Id"] == "rel-1" for plan in plans)


def test_acoustid_conflict_with_existing_mbid_is_review() -> None:
    track = runaway_tracks(tags={"musicbrainz track id": ["existing-rec"]})[0]
    candidate = candidate_from_acoustid_results(build_context(Path("01.mp3"), [track]), [{"track": track, "fingerprint": "fp", **select_acoustid_match(track, acoustid_payload(), 0.80)}], default_config()["metadata_providers"]["acoustid"])

    assert candidate is not None
    assert candidate.extra["conflicts"]


def test_acoustid_without_key_generates_fingerprint_but_skips_lookup(monkeypatch) -> None:
    provider = AcoustIDMetadataProvider(api_key="", fpcalc="fpcalc")
    monkeypatch.setattr("noqlen_forge.metadata_providers.shutil.which", lambda tool: "/usr/bin/fpcalc")
    monkeypatch.setattr("noqlen_forge.metadata_providers.run_fpcalc", lambda *args, **kwargs: {"fingerprint": "abcdef", "duration": 180})

    attempt = provider.fetch_track(build_context(Path("01.mp3"), runaway_tracks()))

    assert attempt.status == "WARN"
    assert "lookup skipped" in attempt.message
    assert attempt.candidates[0].acoustid_fingerprint == "abcdef"


def test_acoustid_missing_fpcalc_is_warn(monkeypatch) -> None:
    provider = AcoustIDMetadataProvider(api_key="key", fpcalc="missing-fpcalc")
    monkeypatch.setattr("noqlen_forge.metadata_providers.shutil.which", lambda tool: None)

    attempt = provider.fetch_track(build_context(Path("01.mp3"), runaway_tracks()))

    assert attempt.status == "WARN"
    assert "fpcalc not found" in attempt.message


def test_fallback_fills_empty_but_does_not_overwrite_existing_genre_or_date() -> None:
    candidate = candidate_from_itunes_song(itunes_song(), build_context(Path("01.mp3"), runaway_tracks(album="", tags={})), storefront="br")
    empty = merge_candidate(build_context(Path("01.mp3"), runaway_tracks(album="", date="", tags={})), candidate)
    existing = merge_candidate(build_context(Path("01.mp3"), runaway_tracks(album="", tags={"genre": ["Rock"], "date": ["2020"]})), candidate)

    assert any(item.field == "genre" and item.action == "write" for item in empty)
    assert any(item.field == "date" and item.action == "write" for item in empty)
    assert any(item.field == "genre" and item.action == "skip" for item in existing)
    assert any(item.field == "date" and item.action == "skip" for item in existing)


def test_barcode_exact_match_increases_score() -> None:
    context = build_context(Path("Album"), tracks(tags={"barcode": ["822603143229"]}))
    no_barcode = MetadataCandidate(provider="discogs", source_id="1", album="Urn", albumartist="Ne Obliviscaris", date="2017", tracklist=[{"title": "Libera"}, {"title": "Intra Venus"}])
    with_barcode = MetadataCandidate(provider="discogs", source_id="2", album="Urn", albumartist="Ne Obliviscaris", date="2017", barcode="822603143229", tracklist=[{"title": "Libera"}, {"title": "Intra Venus"}])

    low, _ = score_discogs_candidate(no_barcode, context)
    high, reasons = score_discogs_candidate(with_barcode, context)

    assert high > low
    assert "barcode exact" in reasons


def test_catalog_number_exact_match_increases_score() -> None:
    context = build_context(Path("Album"), tracks(tags={"catalog_number": ["SOM 432"]}))
    no_catno = MetadataCandidate(provider="discogs", source_id="1", album="Urn", albumartist="Ne Obliviscaris", date="2017", tracklist=[{"title": "Libera"}, {"title": "Intra Venus"}])
    with_catno = MetadataCandidate(provider="discogs", source_id="2", album="Urn", albumartist="Ne Obliviscaris", date="2017", catalog_number="SOM-432", tracklist=[{"title": "Libera"}, {"title": "Intra Venus"}])

    low, _ = score_discogs_candidate(no_catno, context)
    high, reasons = score_discogs_candidate(with_catno, context)

    assert high > low
    assert "catalog number exact" in reasons


def test_ambiguous_candidate_status_is_review(monkeypatch) -> None:
    payloads = {
        "search": {"results": [{"id": 1}, {"id": 2}]},
        "1": discogs_release(id=1),
        "2": discogs_release(id=2),
    }

    def fake_fetch(url, token):
        return payloads["search"] if "database/search" in url else payloads[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_discogs_json", fake_fetch)
    attempt = DiscogsMetadataProvider("token").fetch_album(build_context(Path("Album"), tracks()))

    assert attempt.status == "REVIEW"


def test_ambiguous_review_does_not_write_edition_specific_fields() -> None:
    context = build_context(Path("Album"), tracks())
    first = candidate_from_discogs_release(discogs_release(id=1, country="Europe", labels=[{"name": "Season Of Mist", "catno": "SOM 432"}], identifiers=[{"type": "Barcode", "value": "111"}]), context)
    second = candidate_from_discogs_release(discogs_release(id=2, country="US", labels=[{"name": "Season Of Mist", "catno": "SOM 433"}], identifiers=[{"type": "Barcode", "value": "222"}]), context)
    attempt = ProviderAttempt("discogs", "REVIEW", "ambiguous", [first, second])

    decisions = merge_ambiguous_discogs_common_fields(context, [attempt])

    assert all(item.field not in {"barcode", "catalog_number", "country", "media"} for item in decisions)


def test_ambiguous_common_safe_fields_can_be_selected() -> None:
    context = build_context(Path("Album"), tracks())
    first = candidate_from_discogs_release(discogs_release(id=1, styles=["Progressive Metal"], labels=[{"name": "Season Of Mist", "catno": "SOM 432"}]), context)
    second = candidate_from_discogs_release(discogs_release(id=2, styles=["Progressive Metal"], labels=[{"name": "Season Of Mist", "catno": "SOM 433"}]), context)
    attempt = ProviderAttempt("discogs", "REVIEW", "ambiguous", [first, second])

    decisions = merge_ambiguous_discogs_common_fields(context, [attempt])

    assert any(item.field == "style" and item.action == "write" for item in decisions)
    assert any(item.field == "label" and item.action == "write" for item in decisions)
    assert all(item.field != "catalog_number" for item in decisions)


def test_low_confidence_does_not_apply() -> None:
    context = build_context(Path("Album"), tracks())
    candidate = MetadataCandidate(provider="discogs", source_id="1", confidence="low", score=20, genre="Rock")

    decisions = merge_candidate(context, candidate, min_confidence="medium")

    assert decisions[0].action == "review"


def test_discogs_extracts_genre_style_and_catalog_fields() -> None:
    candidate = candidate_from_discogs_release(discogs_release(), build_context(Path("Album"), tracks()))

    assert candidate.genre == "Rock"
    assert candidate.style == "Progressive Metal; Melodic Death Metal"
    assert candidate.label == "Season Of Mist"
    assert candidate.catalog_number == "SOM 432"
    assert candidate.barcode == "822603143229"
    assert candidate.country == "Europe"
    assert candidate.release_format == "CD"
    assert candidate.release_type == "Album"


def test_discogs_file_album_writes_release_format_and_type() -> None:
    context = build_context(Path("Album"), tracks())
    candidate = candidate_from_discogs_release(discogs_release(formats=[{"name": "File", "qty": "6", "descriptions": ["FLAC", "Album"]}]), context)

    decisions = merge_candidate(context, candidate)

    assert candidate.release_format == "File"
    assert candidate.release_type == "Album"
    assert any(item.field == "release_format" and item.candidate_value == "File" and item.action == "write" for item in decisions)
    assert any(item.field == "release_type" and item.candidate_value == "Album" and item.action == "write" for item in decisions)
    assert not any(item.field == "edition" and item.action == "write" for item in decisions)


def test_discogs_flac_does_not_write_edition_for_m4a_files() -> None:
    local_tracks = [Track(Path("01.m4a"), "m4a", album="Urn", albumartist="Ne Obliviscaris", artist="Ne Obliviscaris", title="Libera", tracknumber=1), Track(Path("02.m4a"), "m4a", album="Urn", albumartist="Ne Obliviscaris", artist="Ne Obliviscaris", title="Intra Venus", tracknumber=2)]
    context = build_context(Path("Album"), local_tracks)
    candidate = candidate_from_discogs_release(discogs_release(formats=[{"name": "File", "qty": "6", "descriptions": ["FLAC", "Album"]}]), context)

    decisions = merge_candidate(context, candidate)

    assert candidate.audio_codec == ""
    assert candidate.edition == ""
    assert not any(item.field in {"media", "audio_codec", "edition"} and item.candidate_value == "FLAC" and item.action == "write" for item in decisions)


def test_discogs_mp3_does_not_write_edition_for_flac_or_m4a_files() -> None:
    for suffix, audio_format in (("flac", "flac"), ("m4a", "m4a")):
        local_tracks = [Track(Path(f"01.{suffix}"), audio_format, album="Urn", albumartist="Ne Obliviscaris", artist="Ne Obliviscaris", title="Libera", tracknumber=1), Track(Path(f"02.{suffix}"), audio_format, album="Urn", albumartist="Ne Obliviscaris", artist="Ne Obliviscaris", title="Intra Venus", tracknumber=2)]
        context = build_context(Path("Album"), local_tracks)
        candidate = candidate_from_discogs_release(discogs_release(formats=[{"name": "File", "qty": "6", "descriptions": ["MP3", "Album"]}]), context)

        decisions = merge_candidate(context, candidate)

        assert candidate.edition == ""
        assert not any(item.field == "edition" and item.candidate_value == "MP3" and item.action == "write" for item in decisions)


def test_discogs_limited_and_remastered_write_edition() -> None:
    context = build_context(Path("Album"), tracks())
    limited = candidate_from_discogs_release(discogs_release(formats=[{"name": "CD", "qty": "1", "descriptions": ["Album", "Limited Edition"]}]), context)
    remastered = candidate_from_discogs_release(discogs_release(formats=[{"name": "CD", "qty": "1", "descriptions": ["Album", "Remastered"]}]), context)

    limited_decisions = merge_candidate(context, limited)
    remastered_decisions = merge_candidate(context, remastered)

    assert limited.edition == "Limited Edition"
    assert remastered.edition == "Remastered"
    assert any(item.field == "edition" and item.candidate_value == "Limited Edition" and item.action == "write" for item in limited_decisions)
    assert any(item.field == "edition" and item.candidate_value == "Remastered" and item.action == "write" for item in remastered_decisions)


def test_discogs_search_uses_barcode_and_catalog_number(monkeypatch) -> None:
    urls = []

    def fake_fetch(url, token):
        urls.append(url)
        if "database/search" in url:
            return {"results": []}
        return discogs_release()

    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_discogs_json", fake_fetch)
    context = build_context(Path("Album"), tracks(tags={"barcode": ["822 603-143229"], "catalog_number": ["SOM 432"]}))

    DiscogsMetadataProvider("token").fetch_album(context)

    assert "barcode=822603143229" in urls[0]
    assert "catno=SOM+432" in urls[0]


def test_discogs_release_id_fetches_exact_release(monkeypatch) -> None:
    urls = []

    def fake_fetch(url, token):
        urls.append(url)
        return discogs_release(id=11086464, labels=[{"name": "Season Of Mist", "catno": "SOM 432"}])

    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_discogs_json", fake_fetch)

    attempt = DiscogsMetadataProvider("token", release_id="11086464").fetch_album(build_context(Path("Album"), tracks()))

    assert attempt.status == "OK"
    assert attempt.candidates[0].source_id == "11086464"
    assert "database/search" not in urls[0]


def test_discogs_candidate_index_selects_ordered_candidate(monkeypatch) -> None:
    payloads = {
        "search": {"results": [{"id": 1}, {"id": 2}]},
        "1": discogs_release(id=1, labels=[{"name": "Season Of Mist", "catno": "SOM 432"}]),
        "2": discogs_release(id=2, labels=[{"name": "Season Of Mist", "catno": "SOM 433"}]),
    }

    def fake_fetch(url, token):
        return payloads["search"] if "database/search" in url else payloads[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_discogs_json", fake_fetch)

    attempt = DiscogsMetadataProvider("token", candidate_index=2).fetch_album(build_context(Path("Album"), tracks()))

    assert attempt.status == "OK"
    assert attempt.candidates[0].source_id == "2"


def test_candidate_verbose_shows_discogs_release_details() -> None:
    context = build_context(Path("Album"), tracks())
    candidate = candidate_from_discogs_release(discogs_release(id=11086464), context)

    output = render_metadata_output(context, [ProviderAttempt("discogs", "OK", "candidate score=100", [candidate])], merge_candidate(context, candidate), apply=False, status="OK", verbose=True)

    assert "1. discogs:11086464" in output
    assert "country: Europe" in output
    assert "Discogs format: CD, Album" in output
    assert "release format: CD" in output
    assert "catalog number: SOM 432" in output
    assert "barcode: 822603143229" in output


def test_candidate_verbose_shows_discogs_and_local_format_decision() -> None:
    local_tracks = [Track(Path("01.m4a"), "m4a", album="Urn", albumartist="Ne Obliviscaris", artist="Ne Obliviscaris", title="Libera", tracknumber=1), Track(Path("02.m4a"), "m4a", album="Urn", albumartist="Ne Obliviscaris", artist="Ne Obliviscaris", title="Intra Venus", tracknumber=2)]
    context = build_context(Path("Album"), local_tracks)
    candidate = candidate_from_discogs_release(discogs_release(id=11086464, formats=[{"name": "File", "qty": "6", "descriptions": ["FLAC", "Album"]}]), context)

    output = render_metadata_output(context, [ProviderAttempt("discogs", "OK", "candidate score=100", [candidate])], merge_candidate(context, candidate), apply=False, status="OK", verbose=True)

    assert "Discogs format: 6xFile, FLAC, Album" in output
    assert "Local format: M4A/AAC" in output
    assert "Decision: skip FLAC as edition, codec/format descriptor only" in output


def test_empty_fields_are_written_and_conflicts_need_force() -> None:
    candidate = MetadataCandidate(provider="discogs", source_id="1", confidence="high", genre="Rock", style="Progressive Metal")
    empty = merge_candidate(build_context(Path("Album"), tracks()), candidate)
    conflict = merge_candidate(build_context(Path("Album"), tracks(tags={"style": ["Black Metal"]})), candidate)
    forced = merge_candidate(build_context(Path("Album"), tracks(tags={"style": ["Black Metal"]})), candidate, force=True)

    assert any(item.field == "style" and item.action == "write" for item in empty)
    assert any(item.field == "style" and item.action == "review" for item in conflict)
    assert any(item.field == "style" and item.action == "write" for item in forced)


def test_discogs_field_config_can_disable_field_writes() -> None:
    context = build_context(Path("Album"), tracks())
    candidate = MetadataCandidate(provider="discogs", source_id="1", confidence="high", genre="Rock", style="Progressive Metal", extra={"field_config": {"use_for_style": False}})

    decisions = merge_candidate(context, candidate)

    assert any(item.field == "genre" and item.action == "write" for item in decisions)
    assert all(item.field != "style" for item in decisions)


def test_musicbrainz_ids_are_never_overwritten_by_discogs() -> None:
    context = build_context(Path("Album"), tracks(tags={"mb_album_id": ["existing"]}))
    candidate = MetadataCandidate(provider="discogs", source_id="1", confidence="high", mb_album_id="discogs-is-not-mbid")

    decisions = merge_candidate(context, candidate, force=True)

    assert any(item.field == "mb_album_id" and item.action == "skip" for item in decisions)


def test_authority_table_prefers_discogs_for_catalog_fields() -> None:
    assert provider_has_authority("discogs", "label") is True
    assert provider_has_authority("discogs", "genre") is True
    assert provider_has_authority("discogs", "mb_album_id") is False


def test_env_discogs_token_wins_and_missing_token_skips(monkeypatch) -> None:
    config = merge_config(default_config(), {"apis": {"discogs_token": "from-config"}, "metadata_providers": {"discogs": {"token": "from-provider"}}})
    monkeypatch.setenv("DISCOGS_TOKEN", "from-env")

    assert discogs_token(config) == "from-env"
    monkeypatch.delenv("DISCOGS_TOKEN")
    assert fetch_metadata_with_providers(build_context(Path("Album"), tracks()), ["discogs"], config=default_config())[0].status == "SKIP"


def test_config_show_masks_discogs_token() -> None:
    config = merge_config(default_config(), {"apis": {"discogs_token": "abcdefghijkl1234"}, "metadata_providers": {"discogs": {"token": "providersecret1234"}}})
    output = render_config(masked_config(config), mask_secrets=False)

    assert "abcdefghijkl1234" not in output
    assert "providersecret1234" not in output
    assert "abcd...1234" in output
    assert "prov...1234" in output


def test_output_does_not_print_payload_and_debug_masks_token() -> None:
    context = build_context(Path("Album"), tracks())
    attempt = fetch_metadata_with_providers(context, ["discogs"], config=merge_config(default_config(), {"metadata_providers": {"discogs": {"token": "secret-token"}}}), debug=True)[0]
    output = render_metadata_output(context, [attempt], [], apply=False, status=metadata_status([attempt], [], None), debug=True)

    assert "secret-token" not in output
    assert "tracklist" not in output


def test_ambiguous_apply_requires_manual_selection_message() -> None:
    context = build_context(Path("Album"), tracks())
    first = candidate_from_discogs_release(discogs_release(id=1), context)
    second = candidate_from_discogs_release(discogs_release(id=2), context)
    attempt = ProviderAttempt("discogs", "REVIEW", "Discogs REVIEW ambiguous editions: 2 equally strong matches. Use --verbose to inspect candidates and --discogs-release-id ID to choose one.", [first, second])

    output = render_metadata_output(context, [attempt], [], apply=True, status="REVIEW", manual_discogs_selection_required=True)

    assert "Apply metadata      SKIP manual Discogs release selection required" in output
    assert "--discogs-release-id ID" in output


def test_review_merge_zero_fields_is_not_ok() -> None:
    context = build_context(Path("Album"), tracks())
    first = candidate_from_discogs_release(discogs_release(id=1), context)
    second = candidate_from_discogs_release(discogs_release(id=2), context)
    attempt = ProviderAttempt("discogs", "REVIEW", "Discogs REVIEW ambiguous editions: 2 equally strong matches. Use --verbose to inspect candidates and --discogs-release-id ID to choose one.", [first, second])

    output = render_metadata_output(context, [attempt], [], apply=False, status="REVIEW")

    assert "[3/4] Merge fields        OK     0 fields selected" not in output
    assert "[3/4] Merge fields        REVIEW     manual edition selection required" in output


def test_ambiguous_apply_without_manual_selection_writes_nothing(monkeypatch, tmp_path) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    (album / "01.flac").touch()
    (album / "02.flac").touch()
    payloads = {
        "search": {"results": [{"id": 1}, {"id": 2}]},
        "1": discogs_release(id=1, labels=[{"name": "Season Of Mist", "catno": "SOM 432"}]),
        "2": discogs_release(id=2, labels=[{"name": "Season Of Mist", "catno": "SOM 433"}]),
    }
    applied = []

    def fake_fetch(url, token):
        return payloads["search"] if "database/search" in url else payloads[url.rsplit("/", 1)[-1]]

    def fake_apply(plans, apply=False):
        applied.append((plans, apply))
        return []

    monkeypatch.setattr("noqlen_forge.metadata_providers.read_tracks", lambda path: tracks())
    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_discogs_json", fake_fetch)
    monkeypatch.setattr("noqlen_forge.metadata_providers.apply_musicbrainz_writes", fake_apply)

    code, output = metadata_path(album, apply=True, providers=["discogs"], config={"metadata_providers": {"discogs": {"token": "token"}}})

    assert code == 1
    assert applied and applied[0][1] is False
    assert "Apply metadata      SKIP manual Discogs release selection required" in output


def test_apply_with_discogs_release_id_writes_catalog_fields(monkeypatch, tmp_path) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    (album / "01.flac").touch()
    (album / "02.flac").touch()
    applied = []

    def fake_apply(plans, apply=False):
        applied.append((plans, apply))
        return []

    monkeypatch.setattr("noqlen_forge.metadata_providers.read_tracks", lambda path: tracks())
    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_discogs_json", lambda url, token: discogs_release(id=11086464))
    monkeypatch.setattr("noqlen_forge.metadata_providers.apply_musicbrainz_writes", fake_apply)

    code, output = metadata_path(album, apply=True, providers=["discogs"], discogs_release_id="11086464", config={"metadata_providers": {"discogs": {"token": "token"}}})

    assert code == 0
    assert applied and applied[0][1] is True
    assert applied[0][0][0].changes["Catalog Number"] == "SOM 432"
    assert "Apply metadata      APPLY" in output


def test_manual_release_id_still_validates_artist_and_album(monkeypatch) -> None:
    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_discogs_json", lambda url, token: discogs_release(id=11086464, title="Other Album", artists=[{"name": "Other Artist"}]))

    attempt = DiscogsMetadataProvider("token", release_id="11086464").fetch_album(build_context(Path("Album"), tracks()))

    assert attempt.status == "REVIEW"
    assert "validation weak" in attempt.message


def test_standard_output_uses_discogs_search_and_fetch_steps() -> None:
    context = build_context(Path("Album"), tracks())
    candidate = MetadataCandidate(provider="discogs", source_id="123", confidence="high", score=94, genre="Rock")
    output = render_metadata_output(context, [ProviderAttempt("discogs", "OK", "candidate score=94", [candidate])], merge_candidate(context, candidate), apply=False, status="OK")

    assert "[1/4] Discogs search" in output
    assert "[2/4] Fetch metadata" in output
    assert "release=123" in output


def test_cli_metadata_accepts_provider_flags() -> None:
    args = build_parser().parse_args(["metadata", "Album", "--provider", "musicbrainz", "--provider", "discogs", "--apply", "--verbose", "--debug", "--allow-more-providers", "--discogs-release-id", "11086464", "--candidate", "1", "--itunes-storefront", "br"])

    assert args.command == "metadata"
    assert args.provider == ["musicbrainz", "discogs"]
    assert args.apply is True
    assert args.verbose is True
    assert args.debug is True
    assert args.allow_more_providers is True
    assert args.discogs_release_id == "11086464"
    assert args.candidate == 1
    assert args.itunes_storefront == "br"


def test_config_sources_define_provider_order() -> None:
    config = merge_config(default_config(), {"metadata_providers": {"sources": ["discogs", "musicbrainz"]}})

    selection = resolve_metadata_providers(config)

    assert selection.active == ["discogs", "musicbrainz"]


def test_config_disables_provider() -> None:
    config = merge_config(default_config(), {"metadata_providers": {"sources": ["musicbrainz", "discogs"], "discogs": {"enabled": False}}})

    selection = resolve_metadata_providers(config)

    assert selection.active == ["musicbrainz"]
    assert ("discogs", "disabled by config") in selection.skipped


def test_max_active_limits_providers_and_allow_more_ignores_limit() -> None:
    config = merge_config(default_config(), {"metadata_providers": {"sources": ["musicbrainz", "discogs", "deezer"], "max_active": 2, "deezer": {"enabled": True}}})

    limited = resolve_metadata_providers(config)
    allowed = resolve_metadata_providers(config, allow_more_providers=True)

    assert limited.active == ["musicbrainz", "discogs"]
    assert ("deezer", "over max_active limit") in limited.skipped
    assert allowed.active == ["musicbrainz", "discogs", "deezer"]


def test_identifier_provider_does_not_count_against_max_active() -> None:
    config = merge_config(default_config(), {"metadata_providers": {"sources": ["musicbrainz", "acoustid", "discogs", "deezer"], "max_active": 2, "deezer": {"enabled": True}}})

    selection = resolve_metadata_providers(config)

    assert selection.active == ["musicbrainz", "acoustid", "discogs"]
    assert ("deezer", "over max_active limit") in selection.skipped


def test_acoustid_force_flags_separate_acoustid_and_identity_overwrites() -> None:
    track = runaway_tracks(tags={"acoustid_id": ["old-acid"], "musicbrainz track id": ["old-rec"]})[0]
    candidate = candidate_from_acoustid_results(build_context(Path("01.mp3"), [track]), [{"track": track, "fingerprint": "fp", **select_acoustid_match(track, acoustid_payload(), 0.80)}], default_config()["metadata_providers"]["acoustid"])

    safe = acoustid_plans_from_candidate([track], candidate)
    forced_acoustid = acoustid_plans_from_candidate([track], candidate, force_acoustid=True)
    forced_identity = acoustid_plans_from_candidate([track], candidate, force_identity=True)

    assert safe[0].changes["ACOUSTID_FINGERPRINT"] == "fp"
    assert "ACOUSTID_ID" not in safe[0].changes
    assert "MusicBrainz Track Id" not in safe[0].changes
    assert forced_acoustid[0].changes["ACOUSTID_ID"] == "acid-1"
    assert forced_acoustid[0].changes["ACOUSTID_FINGERPRINT"] == "fp"
    assert "MusicBrainz Track Id" not in forced_acoustid[0].changes
    assert forced_identity[0].changes["MusicBrainz Track Id"] == "rec-1"


def test_cli_provider_overrides_config_sources_and_disabled_fallbacks() -> None:
    config = merge_config(default_config(), {"metadata_providers": {"sources": ["musicbrainz", "discogs"], "deezer": {"enabled": True}, "itunes": {"enabled": False}}})

    selection = resolve_metadata_providers(config, providers=["deezer", "itunes"])

    assert selection.active == ["deezer", "itunes"]
    assert selection.skipped == []


def test_itunes_storefront_cli_reaches_provider(monkeypatch, tmp_path) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    (album / "01.mp3").touch()
    seen = []

    def fake_fetch(url):
        seen.append(url)
        if "search" in url:
            return {"results": [itunes_album()]}
        return {"results": [itunes_album(), itunes_song()]}

    monkeypatch.setattr("noqlen_forge.metadata_providers.read_tracks", lambda path: runaway_tracks(tags={}))
    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_json", fake_fetch)

    code, output = metadata_path(album, providers=["itunes"], itunes_storefront="br", config=default_config(), verbose=True)

    assert code == 0
    assert "storefront=br" in output
    assert any("country=br" in url for url in seen)


def test_cli_provider_deezer_wins_config_disabled(monkeypatch, tmp_path) -> None:
    album = tmp_path / "Album"
    album.mkdir()
    (album / "01.mp3").touch()

    def fake_fetch(url):
        if "search" in url:
            return {"data": [{"id": 10}]}
        return deezer_album(title="Runaway - Single")

    monkeypatch.setattr("noqlen_forge.metadata_providers.read_tracks", lambda path: runaway_tracks(tags={}))
    monkeypatch.setattr("noqlen_forge.metadata_providers.fetch_json", fake_fetch)

    code, output = metadata_path(album, providers=["deezer"], config=default_config())

    assert code == 0
    assert "- deezer: fallback" in output


def test_metadata_output_lists_active_and_skipped_providers() -> None:
    context = build_context(Path("Album"), tracks())
    selection = resolve_metadata_providers(merge_config(default_config(), {"metadata_providers": {"sources": ["musicbrainz", "discogs", "itunes"], "max_active": 1}}))

    output = render_metadata_output(context, [], [], apply=False, status="WARN", selection=selection)

    assert "Metadata providers:" in output
    assert "- musicbrainz: identity" in output
    assert "Skipped providers:" in output
    assert "- discogs: over max_active limit" in output
    assert "- itunes: disabled by config" in output
