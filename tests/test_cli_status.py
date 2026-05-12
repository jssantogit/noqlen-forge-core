from pathlib import Path

from noqlen_forge.cli import _bpm_status, _lastfm_status, _mood_status, _musicbrainz_status
from noqlen_forge.writers import WritePlan


def test_musicbrainz_existing_ids_summary_reports_original_date_repair() -> None:
    plans = [WritePlan(Path(f"0{index}.flac"), {"Original Date": "2017-10-27"}) for index in range(1, 7)]

    status, summary = _musicbrainz_status(plans, 6, existing_ids=True)

    assert status == "OK"
    assert summary == "existing IDs, repaired Original Date 6/6"


def test_bpm_status_reports_existing_fields_as_ok() -> None:
    output = "\n".join(f"- 0{index}.flac: BPM: skipped existing BPM=120" for index in range(1, 7))

    status, summary = _bpm_status(output, 6)

    assert status == "OK"
    assert summary == "existing 6/6, written 0"


def test_lastfm_status_reports_existing_fields_as_ok() -> None:
    output = "\n".join(f"- 0{index}.flac: skipped existing LASTFM_TAGS=Metal" for index in range(1, 7))

    status, summary = _lastfm_status(output, 6)

    assert status == "OK"
    assert summary == "existing 6/6, written 0"


def test_mood_status_reports_existing_fields_as_ok() -> None:
    output = "\n".join(f"- 0{index}.flac: skipped existing MOOD=Aggressive" for index in range(1, 7))

    status, summary = _mood_status(output, 6)

    assert status == "OK"
    assert summary == "existing 6/6, written 0"
