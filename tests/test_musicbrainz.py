from pathlib import Path

import pytest

from noqlen_forge.audio import Track
from noqlen_forge.musicbrainz import _release_queries

pytestmark = pytest.mark.provider


def test_single_release_queries_try_clean_album_title_and_recording() -> None:
    track = Track(
        Path("RESCENE - Runaway/01 Runaway.mp3"),
        "mp3",
        album="Runaway - Single",
        albumartist="RESCENE",
        artist="RESCENE",
        title="Runaway",
    )

    queries = _release_queries([track], "RESCENE", "Runaway - Single")

    assert 'artist:"RESCENE" AND release:"Runaway - Single"' in queries
    assert 'artist:"RESCENE" AND release:"Runaway"' in queries
    assert 'artist:"RESCENE" AND recording:"Runaway"' in queries
    assert len(queries) == len(set(queries))
