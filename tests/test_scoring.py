from pathlib import Path

from noqlen_forge.audio import Track
from noqlen_forge.scoring import normalize_text, rank_releases, score_release


def test_normalize_text_removes_accents_and_punctuation() -> None:
    assert normalize_text("Gloire Éternelle!") == "gloire eternelle"


def test_score_release_rewards_matching_tracks_and_duration() -> None:
    tracks = [
        Track(Path("01.flac"), "flac", album="Get Up", albumartist="NewJeans", title="New Jeans", tracknumber=1, duration=108),
        Track(Path("02.flac"), "flac", album="Get Up", albumartist="NewJeans", title="Super Shy", tracknumber=2, duration=154),
    ]
    release = {
        "id": "release-id",
        "title": "Get Up",
        "status": "Official",
        "country": "XW",
        "date": "2023-07-21",
        "artist-credit": [{"name": "NewJeans"}],
        "media": [
            {
                "tracks": [
                    {"id": "t1", "position": 1, "title": "New Jeans", "length": 108000, "recording": {"id": "r1"}},
                    {"id": "t2", "position": 2, "title": "Super Shy", "length": 154000, "recording": {"id": "r2"}},
                ]
            }
        ],
    }

    assert score_release(tracks, release).score >= 95


def test_rank_releases_prefers_matching_track_count() -> None:
    tracks = [Track(Path("01.flac"), "flac", album="Runaway", albumartist="RESCENE", title="Runaway", tracknumber=1)]
    one_track = {"id": "one", "title": "Runaway", "artist-credit": [{"name": "RESCENE"}], "media": [{"tracks": [{"title": "Runaway"}]}]}
    two_tracks = {
        "id": "two",
        "title": "Runaway",
        "artist-credit": [{"name": "RESCENE"}],
        "media": [{"tracks": [{"title": "Runaway"}, {"title": "Other"}]}],
    }

    assert rank_releases(tracks, [two_tracks, one_track])[0].release["id"] == "one"


def test_single_scores_high_when_album_has_single_suffix() -> None:
    tracks = [Track(Path("RESCENE - Runaway/01 Runaway.mp3"), "mp3", album="Runaway - Single", albumartist="RESCENE", artist="RESCENE", title="Runaway", tracknumber=1)]
    release = {
        "id": "runaway-release",
        "title": "Runaway",
        "status": "Official",
        "country": "XW",
        "date": "2024-08-27",
        "artist-credit": [{"name": "RESCENE"}],
        "media": [{"tracks": [{"id": "t1", "position": 1, "title": "Runaway", "recording": {"id": "r1", "title": "Runaway"}}]}],
    }

    assert score_release(tracks, release).score >= 95
