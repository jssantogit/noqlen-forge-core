from pathlib import Path

from mutagen.id3 import ID3, TALB, TIT2, TPE1

from noqlen_forge.audit import audit_path, render_audit
from noqlen_forge.audio import Track
from noqlen_forge.writers import WritePlan, apply_musicbrainz_writes, plan_musicbrainz_writes, plan_partial_musicbrainz_repair, summarize_partial_repair


def test_does_not_overwrite_existing_mbid_without_force() -> None:
    tracks = [
        Track(Path("01.flac"), "flac", tags={"musicbrainz_albumid": ["existing"]}),
        Track(Path("02.flac"), "flac", tags={"musicbrainz_albumid": ["existing"]}),
    ]
    release = {"id": "new", "release-group": {"id": "rg"}, "media": [{"tracks": [{"recording": {"id": "r1"}}, {"recording": {"id": "r2"}}]}]}

    assert plan_musicbrainz_writes(tracks, release, force=False) == []


def test_force_plans_mbid_writes() -> None:
    tracks = [Track(Path("01.flac"), "flac", tags={"musicbrainz_albumid": ["existing"]})]
    release = {"id": "new", "release-group": {"id": "rg"}, "media": [{"tracks": [{"id": "rt", "recording": {"id": "rec"}}]}]}

    plans = plan_musicbrainz_writes(tracks, release, force=True)

    assert plans[0].changes["MusicBrainz Album Id"] == "new"
    assert plans[0].changes["MusicBrainz Track Id"] == "rec"


def test_full_musicbrainz_write_includes_original_date_from_release_group() -> None:
    tracks = [Track(Path("01.flac"), "flac")]
    release = {
        "id": "album",
        "date": "2018",
        "release-group": {"id": "rg", "first-release-date": "2017-10-27"},
        "media": [{"tracks": [{"id": "rt", "recording": {"id": "rec"}}]}],
    }

    plans = plan_musicbrainz_writes(tracks, release)

    assert plans[0].changes["Original Date"] == "2017-10-27"


def test_full_musicbrainz_write_falls_back_to_release_date() -> None:
    tracks = [Track(Path("01.flac"), "flac")]
    release = {
        "id": "album",
        "date": "2018",
        "release-group": {"id": "rg"},
        "media": [{"tracks": [{"id": "rt", "recording": {"id": "rec"}}]}],
    }

    plans = plan_musicbrainz_writes(tracks, release)

    assert plans[0].changes["Original Date"] == "2018"


def test_full_musicbrainz_write_does_not_write_zero_original_date() -> None:
    tracks = [Track(Path("01.flac"), "flac")]
    release = {
        "id": "album",
        "date": "0000",
        "release-group": {"id": "rg", "first-release-date": "0000"},
        "media": [{"tracks": [{"id": "rt", "recording": {"id": "rec"}}]}],
    }

    plans = plan_musicbrainz_writes(tracks, release)

    assert "Original Date" not in plans[0].changes


def test_apply_mp3_musicbrainz_tags_persist_and_audit_sees_them(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)
    plan = WritePlan(
        path=path,
        changes={
            "MusicBrainz Album Id": "album-id",
            "MusicBrainz Release Group Id": "release-group-id",
            "MusicBrainz Track Id": "recording-id",
            "MusicBrainz Release Track Id": "release-track-id",
            "MusicBrainz Album Artist Id": "album-artist-id",
        },
    )

    errors = apply_musicbrainz_writes([plan], apply=True)
    reopened = ID3(path)
    output = render_audit(audit_path(path))

    assert errors == []
    assert reopened.getall("TXXX:MusicBrainz Album Id")[0].text == ["album-id"]
    assert reopened.getall("TXXX:MusicBrainz Release Group Id")[0].text == ["release-group-id"]
    assert reopened.getall("TXXX:MusicBrainz Track Id")[0].text == ["recording-id"]
    assert reopened.getall("TXXX:MusicBrainz Release Track Id")[0].text == ["release-track-id"]
    assert reopened.getall("TXXX:MusicBrainz Album Artist Id")[0].text == ["album-artist-id"]
    assert reopened.getall("UFID:http://musicbrainz.org")[0].data == b"recording-id"
    assert "MB Album Id: 1/1" in output
    assert "MB Track Id: 1/1" in output
    assert "Release Group Id: 1/1" in output


def test_partial_musicbrainz_repair_fills_missing_release_group_track_id_and_original_date() -> None:
    tracks = [
        Track(Path("01.flac"), "flac", tags={"mb_album_id": ["album"], "mb_track_id": ["rec1"]}),
        Track(Path("02.flac"), "flac", tags={"mb_album_id": ["album"], "mb_track_id": ["rec2"], "mb_release_group_id": ["rg"]}),
    ]
    release = {
        "id": "album",
        "release-group": {"id": "rg", "first-release-date": "2017-10-27"},
        "artist-credit": [{"artist": {"id": "artist-id"}}],
        "label-info": [{"label": {"name": "Season of Mist"}}],
        "media": [{"tracks": [{"id": "rt1", "recording": {"id": "new-rec1"}}, {"id": "rt2", "recording": {"id": "new-rec2"}}]}],
    }

    plans = plan_partial_musicbrainz_repair(tracks, release)

    assert plans[0].changes == {
        "MusicBrainz Release Group Id": "rg",
        "MusicBrainz Release Track Id": "rt1",
        "MusicBrainz Album Artist Id": "artist-id",
        "Original Date": "2017-10-27",
        "Label": "Season of Mist",
    }
    assert plans[1].changes == {
        "MusicBrainz Release Track Id": "rt2",
        "MusicBrainz Album Artist Id": "artist-id",
        "Original Date": "2017-10-27",
        "Label": "Season of Mist",
    }
    assert all("MusicBrainz Album Id" not in plan.changes for plan in plans)
    assert all("MusicBrainz Track Id" not in plan.changes for plan in plans)


def test_partial_musicbrainz_repair_dry_run_summary_lists_values() -> None:
    plan = WritePlan(Path("01.flac"), {"MusicBrainz Release Group Id": "rg", "MusicBrainz Release Track Id": "rt1"})

    output = summarize_partial_repair([plan], apply=False)

    assert "MusicBrainz partial repair:" in output
    assert "- 01.flac: would write Release Group Id=rg" in output
    assert "- 01.flac: would write Release Track Id=rt1" in output


def test_partial_musicbrainz_repair_treats_zero_original_date_as_missing_but_writes_real_date() -> None:
    tracks = [Track(Path("01.flac"), "flac", tags={"mb_album_id": ["album"], "mb_track_id": ["rec"], "originaldate": ["0000"]})]
    release = {
        "id": "album",
        "date": "2017",
        "release-group": {"id": "rg", "first-release-date": "2017-10-27"},
        "media": [{"tracks": [{"id": "rt", "recording": {"id": "rec"}}]}],
    }

    plans = plan_partial_musicbrainz_repair(tracks, release)

    assert plans[0].changes["Original Date"] == "2017-10-27"


def test_audit_moves_from_review_to_warn_after_partial_musicbrainz_repair(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    tags = ID3()
    tags.add(TALB(encoding=3, text=["Album"]))
    tags.add(TPE1(encoding=3, text=["Artist"]))
    tags.add(TIT2(encoding=3, text=["Song"]))
    tags.save(path)
    apply_musicbrainz_writes([WritePlan(path, {"MusicBrainz Album Id": "album", "MusicBrainz Track Id": "rec"})], apply=True)
    before = audit_path(path)
    track = before.tracks[0]
    release = {"id": "album", "release-group": {"id": "rg"}, "media": [{"tracks": [{"id": "rt", "recording": {"id": "different-rec"}}]}]}

    plans = plan_partial_musicbrainz_repair([track], release)
    errors = apply_musicbrainz_writes(plans, apply=True)
    after = audit_path(path)

    assert before.status == "REVIEW"
    assert errors == []
    assert after.status == "WARN"
    assert ID3(path).getall("TXXX:MusicBrainz Album Id")[0].text == ["album"]
    assert ID3(path).getall("TXXX:MusicBrainz Track Id")[0].text == ["rec"]
    assert ID3(path).getall("TXXX:MusicBrainz Release Group Id")[0].text == ["rg"]
    assert ID3(path).getall("TXXX:MusicBrainz Release Track Id")[0].text == ["rt"]


def test_apply_mp3_original_date_writes_tdor_and_originaldate_txxx(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    errors = apply_musicbrainz_writes([WritePlan(path, {"Original Date": "2017-10-27"})], apply=True)
    reopened = ID3(path)

    assert errors == []
    assert [str(item) for item in reopened.getall("TDOR")[0].text] == ["2017-10-27"]
    assert reopened.getall("TXXX:ORIGINALDATE")[0].text == ["2017-10-27"]
