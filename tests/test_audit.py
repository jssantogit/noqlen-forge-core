from pathlib import Path

from noqlen_forge.audit import AuditResult, render_audit, render_final_audit
from noqlen_forge.audio import Track


def test_audit_reports_missing_musicbrainz_fields() -> None:
    track = Track(path=Path("song.flac"), format="flac", album="Album", artist="Artist", title="Song")
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result)

    assert "Files: 1" in output
    assert "Noqlen Forge audit" in output
    assert "Mode: READ-ONLY" in output
    assert "MB Album Id: 0/1" in output
    assert "Status: REVIEW" in output
    assert "Next: resolve required identity/bad-field issues" in output


def test_audit_counts_filled_label_style_originaldate() -> None:
    track = Track(
        path=Path("song.m4a"),
        format="m4a",
        tags={"label": ["ADOR"], "style": ["K-Pop"], "originaldate": ["2023"]},
    )
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result)

    assert "Label: 1/1" in output
    assert "Style: 1/1" in output
    assert "Original Date: 1/1" in output


def test_audit_advanced_includes_catalog_fields() -> None:
    track = Track(
        path=Path("song.flac"),
        format="flac",
        tags={"label": ["Season Of Mist"], "catalog_number": ["SOM 432"], "barcode": ["822603143229"], "country": ["Europe"], "media": ["CD"], "release_type": ["Album"], "isrc": ["FRX123"]},
    )

    output = render_audit(AuditResult(tracks=[track], bad_fields=[]), advanced=True)

    assert "Catalog:" in output
    assert "Catalog Number: 1/1" in output
    assert "Barcode: 1/1" in output
    assert "Country: 1/1" in output
    assert "Media: 1/1" in output
    assert "Release Type: 1/1" in output
    assert "ISRC: 1/1" in output


def test_final_audit_advanced_includes_catalog_fields() -> None:
    track = Track(path=Path("song.flac"), format="flac", tags={"catalog_number": ["SOM 432"], "barcode": ["822603143229"]})

    output = render_final_audit(AuditResult(tracks=[track], bad_fields=[]), advanced=True)

    assert "Final audit:" in output
    assert "Catalog:" in output
    assert "Catalog Number: 1/1" in output


def test_audit_counts_bpm() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", tags={"bpm": ["123.4"]})
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result)

    assert "BPM: 1/1" in output


def test_audit_counts_key() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", tags={"key": ["C Major"]})
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result)

    assert "Key: 1/1" in output


def test_audit_counts_energy_and_danceability() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", tags={"energy": ["82"], "danceability": ["74"]})
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result)

    assert "Energy: 1/1" in output
    assert "Danceability: 1/1" in output


def test_audit_counts_lastfm_tags() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", tags={"lastfm_tags": ["pop; dance"]})
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result)

    assert "Last.fm Tags: 1/1" in output


def test_audit_counts_mood() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", tags={"mood": ["Energetic"]})
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result)

    assert "Mood: 1/1" in output


def test_audit_counts_replaygain_and_loudness() -> None:
    track = Track(path=Path("song.flac"), format="flac", tags={"replaygain_track_gain": ["-2.00 dB"], "replaygain_track_peak": ["0.900000"], "replaygain_album_gain": ["-3.00 dB"], "replaygain_album_peak": ["0.950000"], "loudness": ["-16.00 LUFS"]})

    output = render_audit(AuditResult(tracks=[track], bad_fields=[]), advanced=True)

    assert "ReplayGain Track: 1/1" in output
    assert "ReplayGain Album: 1/1" in output
    assert "Loudness: 1/1" in output
    assert "Track Gain: 1/1" in output
    assert "Album Peak: 1/1" in output


def test_audit_warns_when_rich_metadata_is_incomplete() -> None:
    track = Track(
        path=Path("song.mp3"),
        format="mp3",
        album="Album",
        artist="Artist",
        title="Song",
        tags={"mb_album_id": ["album"], "mb_track_id": ["track"], "mb_release_group_id": ["group"], "label": ["Label"]},
    )
    result = AuditResult(tracks=[track], bad_fields=[])

    assert result.status == "WARN"


def test_audit_warns_when_only_style_is_missing() -> None:
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
            "originaldate": ["2024"],
            "bpm": ["120"],
            "key": ["C Major"],
            "energy": ["82"],
            "danceability": ["74"],
        },
    )
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result)

    assert result.status == "WARN"
    assert "Warnings:" in output
    assert "- Style missing: 1/1" in output
    assert "Next: optional metadata is incomplete" in output


def test_audit_default_shows_sections() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result)

    assert "Required:" in output
    assert "Enrichment:" in output
    assert "Warnings:" in output


def test_audit_warning_reports_missing_mood_count() -> None:
    tracks = [Track(path=Path(f"{index}.mp3"), format="mp3", tags={"mood": ["Happy"], "cover": ["1"]}) for index in range(4)]
    tracks.extend(Track(path=Path(f"{index}.mp3"), format="mp3", tags={"cover": ["1"]}) for index in range(4, 6))

    output = render_audit(AuditResult(tracks=tracks, bad_fields=[]))

    assert "Mood: 4/6" in output
    assert "- Mood missing: 2/6" in output


def test_audit_warning_reports_missing_key_count() -> None:
    tracks = [Track(path=Path(f"{index}.mp3"), format="mp3", tags={"cover": ["1"]}) for index in range(6)]

    output = render_audit(AuditResult(tracks=tracks, bad_fields=[]))

    assert "Key: 0/6" in output
    assert "- Key missing: 6/6" in output
    assert "Next: enable native key detection" in output


def test_audit_no_cover_warning_when_cover_complete() -> None:
    tracks = [Track(path=Path(f"{index}.mp3"), format="mp3", tags={"cover": ["1"]}) for index in range(6)]

    output = render_audit(AuditResult(tracks=tracks, bad_fields=[]))

    assert "Cover: 6/6" in output
    assert "Cover missing" not in output


def test_audit_skipped_folder_cover_does_not_warn_by_default() -> None:
    tracks = [Track(path=Path(f"{index}.mp3"), format="mp3", tags={"cover": ["1"]}) for index in range(6)]

    output = render_audit(AuditResult(tracks=tracks, bad_fields=[]))

    assert "Folder Cover: skipped" in output
    assert "Folder Cover missing" not in output


def test_audit_verbose_keeps_file_details() -> None:
    track = Track(path=Path("/music/Album/01 Song.mp3"), format="mp3", album="Album", artist="Artist", title="Song")
    result = AuditResult(tracks=[track], bad_fields=[])

    output = render_audit(result, verbose=True)

    assert "Files:\n- /music/Album/01 Song.mp3" in output


def test_audit_reviews_when_mb_album_id_is_missing() -> None:
    track = Track(
        path=Path("song.mp3"),
        format="mp3",
        album="Album",
        artist="Artist",
        title="Song",
        tags={"mb_track_id": ["track"], "mb_release_group_id": ["group"], "label": ["Label"], "style": ["Pop"], "originaldate": ["2024"], "bpm": ["120"]},
    )
    result = AuditResult(tracks=[track], bad_fields=[])

    assert result.status == "REVIEW"


def test_audit_reviews_when_bad_fields_exist() -> None:
    track = Track(
        path=Path("song.mp3"),
        format="mp3",
        album="Album",
        artist="Artist",
        title="Song",
        tags={"mb_album_id": ["album"], "mb_track_id": ["track"], "mb_release_group_id": ["group"], "label": ["Label"], "style": ["Pop"], "originaldate": ["2024"], "bpm": ["120"]},
    )
    result = AuditResult(tracks=[track], bad_fields=["song.mp3:bpm=0"])

    assert result.status == "REVIEW"


def test_audit_groups_bad_fields_by_default() -> None:
    result = AuditResult(
        tracks=[
            Track(path=Path("01.mp3"), format="mp3"),
            Track(path=Path("02.mp3"), format="mp3"),
            Track(path=Path("03.mp3"), format="mp3"),
        ],
        bad_fields=["01.mp3:bpm=0", "02.mp3:bpm=0", "03.mp3:tmpo=0", "01.mp3:soco="],
    )

    output = render_audit(result)

    assert "Bad fields:\n" in output
    assert "- 2 files: bpm=0" in output
    assert "- 1 file: tmpo=0" in output
    assert "- 1 file: empty soco" in output
    assert "Use --verbose to show per-file details." in output
    assert "01.mp3:bpm=0" not in output


def test_audit_verbose_shows_per_file_bad_fields() -> None:
    result = AuditResult(
        tracks=[Track(path=Path("01.mp3"), format="mp3"), Track(path=Path("02.mp3"), format="mp3")],
        bad_fields=["01.mp3:bpm=0", "02.mp3:bpm=0"],
    )

    output = render_audit(result, verbose=True)

    assert "Bad fields: 01.mp3:bpm=0, 02.mp3:bpm=0" in output
    assert "- 2 files: bpm=0" not in output


def test_audit_ok_when_essential_and_rich_metadata_are_complete() -> None:
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
            "danceability": ["70"],
            "lastfm_tags": ["Pop"],
            "mood": ["Happy"],
            "replaygain_track_gain": ["-2.00 dB"],
            "replaygain_track_peak": ["0.900000"],
            "replaygain_album_gain": ["-2.50 dB"],
            "replaygain_album_peak": ["0.950000"],
            "loudness": ["-16.00 LUFS"],
            "cover": ["1"],
            "lyrics": ["Lyrics"],
        },
    )
    result = AuditResult(tracks=[track], bad_fields=[])

    assert result.status == "OK"


def test_audit_warns_when_key_missing_but_rest_ok() -> None:
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
        },
    )
    result = AuditResult(tracks=[track], bad_fields=[])

    assert result.status == "WARN"


def test_audit_warns_when_mood_missing_but_essential_ok() -> None:
    track = Track(
        path=Path("song.mp3"),
        format="mp3",
        album="Album",
        artist="Artist",
        title="Song",
        tags={"mb_album_id": ["album"], "mb_track_id": ["track"], "mb_release_group_id": ["group"]},
    )
    result = AuditResult(tracks=[track], bad_fields=[])

    assert result.status == "WARN"


def test_audit_warns_when_lastfm_tags_missing_but_essential_ok() -> None:
    track = Track(
        path=Path("song.mp3"),
        format="mp3",
        album="Album",
        artist="Artist",
        title="Song",
        tags={"mb_album_id": ["album"], "mb_track_id": ["track"], "mb_release_group_id": ["group"], "mood": ["Happy"]},
    )
    result = AuditResult(tracks=[track], bad_fields=[])

    assert result.status == "WARN"


def test_audit_reviews_when_mb_album_id_is_inconsistent() -> None:
    tracks = [
        Track(path=Path("01.mp3"), format="mp3", album="Album", artist="Artist", title="One", tags={"mb_album_id": ["album-a"], "mb_track_id": ["track-a"], "mb_release_group_id": ["group"]}),
        Track(path=Path("02.mp3"), format="mp3", album="Album", artist="Artist", title="Two", tags={"mb_album_id": ["album-b"], "mb_track_id": ["track-b"], "mb_release_group_id": ["group"]}),
    ]
    result = AuditResult(tracks=tracks, bad_fields=[])

    assert result.status == "REVIEW"


def test_audit_marks_invalid_key_bad_field() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", tags={"key": ["not-a-key"]})

    assert "song.mp3:key=not-a-key" in __import__("noqlen_forge.audit", fromlist=["find_bad_fields"]).find_bad_fields([track])


def test_audit_marks_invalid_features_bad_field() -> None:
    track = Track(path=Path("song.mp3"), format="mp3", tags={"energy": ["101"], "danceability": ["nan"]})

    bad = __import__("noqlen_forge.audit", fromlist=["find_bad_fields"]).find_bad_fields([track])

    assert "song.mp3:energy=101" in bad
    assert "song.mp3:danceability=nan" in bad
