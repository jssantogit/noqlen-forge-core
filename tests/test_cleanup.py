from pathlib import Path

from mutagen.mp4 import MP4FreeForm

from noqlen_forge.audio import Track, _read_tags
from noqlen_forge.audit import AuditResult, render_audit
from noqlen_forge.cleanup import normalize_style, plan_cleanup, summarize_cleanup


class FakeAudio:
    def __init__(self, tags):
        self.tags = tags


def test_cleanup_removes_0000_tbpm_zero_and_empty_musicbrainz_ufid() -> None:
    track = Track(
        Path("song.mp3"),
        "mp3",
        tags={"date": ["0000"], "TBPM": ["0"], "UFID:http://musicbrainz.org": [""]},
    )

    plan = plan_cleanup([track])[0]

    assert "date" in plan.remove
    assert "TBPM" in plan.remove
    assert "UFID:http://musicbrainz.org" in plan.remove


def test_cleanup_removes_empty_mp4_freeform_logical_fields() -> None:
    track = Track(
        Path("song.m4a"),
        "m4a",
        tags={"label": [""], "style": [""], "originaldate": [""]},
    )

    plan = plan_cleanup([track])[0]

    assert "label" in plan.remove
    assert "style" in plan.remove
    assert "originaldate" in plan.remove


def test_cleanup_removes_tdor_0000() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"originaldate": ["0000"]})

    plan = plan_cleanup([track])[0]

    assert "originaldate" in plan.remove


def test_cleanup_removes_tcmp_zero() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"compilation": ["0"]})

    plan = plan_cleanup([track])[0]

    assert "compilation" in plan.remove


def test_cleanup_removes_invalid_bpm() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"bpm": ["300"]})

    plan = plan_cleanup([track])[0]

    assert "bpm" in plan.remove
    assert plan.remove_values["bpm"] == ["300"]


def test_cleanup_removes_invalid_energy() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"energy": ["0"]})

    plan = plan_cleanup([track])[0]

    assert "energy" in plan.remove


def test_cleanup_removes_invalid_danceability() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"danceability": ["nan"]})

    plan = plan_cleanup([track])[0]

    assert "danceability" in plan.remove


def test_cleanup_removes_empty_lastfm_tags() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"lastfm_tags": [""]})

    plan = plan_cleanup([track])[0]

    assert "lastfm_tags" in plan.remove


def test_cleanup_removes_empty_mood() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"mood": [""]})

    plan = plan_cleanup([track])[0]

    assert "mood" in plan.remove


def test_cleanup_normalizes_lastfm_tags() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"lastfm_tags": ["pop, Dance; pop"]})

    plan = plan_cleanup([track])[0]

    assert plan.set_values["LASTFM_TAGS"] == ["Pop; Dance"]


def test_cleanup_prunes_bad_lastfm_tags_and_dedupes() -> None:
    track = Track(Path("song.mp3"), "mp3", artist="RESCENE", tags={"lastfm_tags": ["K-pop; Pop; girl group; Girl Groups; RESCENE; Korean"]})

    plan = plan_cleanup([track])[0]

    assert plan.set_values["LASTFM_TAGS"] == ["K-pop; Pop; Girl Group; Korean"]


def test_cleanup_removes_lastfm_tags_when_filter_leaves_empty() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"lastfm_tags": ["hit; vocal; 2023"]})

    plan = plan_cleanup([track])[0]

    assert "lastfm_tags" in plan.remove
    assert plan.remove_values["lastfm_tags"] == ["hit; vocal; 2023"]


def test_cleanup_summary_shows_lastfm_before_after() -> None:
    track = Track(Path("song.mp3"), "mp3", artist="RESCENE", tags={"lastfm_tags": ["K-pop; RESCENE; Korean"]})
    plans = plan_cleanup([track])

    output = summarize_cleanup(plans, apply=False)

    assert "- song.mp3: LASTFM_TAGS: K-pop; RESCENE; Korean -> K-pop; Korean" in output


def test_cleanup_normalizes_lastfm_tag_capitalization() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"lastfm_tags": ["kpop, rnb; contemporary rnb; UK garage"]})

    plan = plan_cleanup([track])[0]

    assert plan.set_values["LASTFM_TAGS"] == ["K-pop; R&B; Contemporary R&B; UK Garage"]


def test_cleanup_normalizes_mood_separator_and_dedupes() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"mood": ["Dreamy, Chill; dreamy"]})

    plan = plan_cleanup([track])[0]

    assert plan.set_values["MOOD"] == ["Dreamy; Chill"]


def test_cleanup_removes_empty_mp4_freeform_label_publisher_aliases() -> None:
    tags = _read_tags(
        FakeAudio(
            {
                "----:com.apple.iTunes:LABEL": [MP4FreeForm(b"ADOR")],
                "----:com.apple.iTunes:publisher": [MP4FreeForm(b"")],
            }
        )
    )
    track = Track(Path("song.m4a"), "m4a", tags=tags)

    plan = plan_cleanup([track])[0]

    assert "label" not in plan.remove
    assert "----:com.apple.itunes:publisher" in plan.remove


def test_cleanup_does_not_remove_valid_label_style_originaldate() -> None:
    track = Track(
        Path("song.m4a"),
        "m4a",
        tags={"label": ["ADOR"], "style": ["K-Pop"], "originaldate": ["2023"]},
    )

    assert plan_cleanup([track]) == []


def test_cleanup_removes_edition_flac_when_local_codec_differs() -> None:
    track = Track(Path("song.m4a"), "m4a", tags={"edition": ["FLAC"]})

    plan = plan_cleanup([track])[0]

    assert "edition" in plan.remove
    assert plan.remove_values["edition"] == ["FLAC"]


def test_cleanup_does_not_remove_deluxe_edition() -> None:
    track = Track(Path("song.m4a"), "m4a", tags={"edition": ["Deluxe Edition"]})

    assert plan_cleanup([track]) == []


def test_audit_has_no_bad_fields_after_empty_alias_cleanup() -> None:
    track = Track(
        Path("song.m4a"),
        "m4a",
        tags={"label": ["ADOR"], "style": ["K-Pop"], "originaldate": ["2023"]},
    )

    output = render_audit(AuditResult(tracks=[track], bad_fields=[]))

    assert "Bad fields: none" in output


def test_cleanup_summary_uses_dry_run_remove_write_sections() -> None:
    track = Track(Path("song.mp3"), "mp3", tags={"compilation": ["0"]})
    plans = plan_cleanup([track])

    output = summarize_cleanup(plans, apply=False)

    assert "would remove:" in output
    assert "- 1 file: compilation=0" in output
    assert "would write:" in output
    assert "- nothing" in output


def test_cleanup_groups_removals_by_default() -> None:
    tracks = [
        Track(Path("01.mp3"), "mp3", tags={"bpm": ["0"], "tmpo": ["0"]}),
        Track(Path("02.mp3"), "mp3", tags={"bpm": ["0"], "soco": [""]}),
        Track(Path("03.mp3"), "mp3", tags={"bpm": ["0"]}),
    ]
    plans = plan_cleanup(tracks)

    output = summarize_cleanup(plans, apply=False)

    assert "would remove:" in output
    assert "- 3 files: bpm=0" in output
    assert "- 1 file: tmpo=0" in output
    assert "- 1 file: empty soco" in output
    assert "- 01.mp3: bpm=0" not in output


def test_cleanup_verbose_shows_per_file_removals() -> None:
    tracks = [
        Track(Path("01.mp3"), "mp3", tags={"bpm": ["0"]}),
        Track(Path("02.mp3"), "mp3", tags={"bpm": ["0"]}),
    ]
    plans = plan_cleanup(tracks)

    output = summarize_cleanup(plans, apply=False, verbose=True)

    assert "- 01.mp3: bpm=0" in output
    assert "- 02.mp3: bpm=0" in output
    assert "- 2 files: bpm=0" not in output


def test_normalize_style_joins_with_semicolon_space() -> None:
    assert normalize_style(["House, Deep House;Techno"]) == ["House; Deep House; Techno"]


def test_label_majority_applies_to_missing_tracks() -> None:
    tracks = [
        Track(Path("01.flac"), "flac", tags={"label": ["ADOR"]}),
        Track(Path("02.flac"), "flac", tags={"label": ["ADOR"]}),
        Track(Path("03.flac"), "flac", tags={}),
    ]

    plans = plan_cleanup(tracks)

    missing_plan = next(plan for plan in plans if plan.path.name == "03.flac")
    assert missing_plan.set_values["LABEL"] == ["ADOR"]
