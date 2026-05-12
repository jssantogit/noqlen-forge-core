from pathlib import Path

from mutagen.id3 import ID3

from noqlen_forge.audio import Track
from noqlen_forge.mood import _write_mp3_mood, analyze_mood_path, infer_mood, normalize_moods, write_mood


def test_lastfm_energetic_dance_generates_energetic() -> None:
    analysis = infer_mood(["energetic; dance"])

    assert analysis.moods == ["Energetic"]
    assert analysis.confidence == "high"


def test_technical_death_metal_generates_aggressive_intense() -> None:
    analysis = infer_mood(["technical death metal"])

    assert analysis.moods == ["Aggressive", "Intense"]
    assert analysis.confidence == "medium"


def test_dreamy_chill_generates_dreamy_chill() -> None:
    analysis = infer_mood(["dreamy; chill"])

    assert analysis.moods == ["Dreamy", "Chill"]
    assert analysis.confidence == "high"


def test_weak_tags_do_not_write_mood(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    monkeypatch.setattr("noqlen_forge.mood.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"lastfm_tags": ["pop; korean"]}))
    monkeypatch.setattr("noqlen_forge.mood.write_mood", lambda file_path, moods: written.append((file_path, moods)))

    code, output = analyze_mood_path(path, apply=True)

    assert code == 0
    assert written == []
    assert "confidence=low" in output
    assert "action=skipped" in output


def test_mood_uses_filtered_lastfm_tags() -> None:
    analysis = infer_mood(["hit; vocal; dreamy"])

    assert analysis.raw_tags == ["Dreamy"]
    assert analysis.moods == ["Dreamy", "Chill"]


def test_hit_and_vocal_do_not_generate_mood() -> None:
    analysis = infer_mood(["hit; vocal"])

    assert analysis.raw_tags == []
    assert analysis.moods == []
    assert analysis.confidence == "low"


def test_mood_preserves_existing_without_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.mood.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"mood": ["Happy"], "lastfm_tags": ["dark"]}))

    code, output = analyze_mood_path(path, apply=True)

    assert code == 0
    assert "skipped existing MOOD=Happy" in output


def test_mood_force_overwrites_existing(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    monkeypatch.setattr("noqlen_forge.mood.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"mood": ["Happy"], "lastfm_tags": ["dark"]}))
    monkeypatch.setattr("noqlen_forge.mood.write_mood", lambda file_path, moods: written.append((file_path, moods)))

    code, output = analyze_mood_path(path, apply=True, force=True)

    assert code == 0
    assert written == [(path, ["Melancholic", "Dark"])]
    assert "action=wrote" in output


def test_mood_uses_calculated_lastfm_tags_when_requested(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.mood.read_track", lambda file_path: Track(Path(file_path), "mp3", album="Album", artist="Artist", title="Song", tags={}))
    monkeypatch.setattr("noqlen_forge.mood.fetch_best_lastfm_tags_debug", lambda *args, **kwargs: type("Result", (), {"tags": [{"name": "dance", "count": "5"}]})())

    code, output = analyze_mood_path(path, with_lastfm=True)

    assert code == 0
    assert "raw_tags=dance" in output
    assert "mood=Energetic" in output


def test_mood_calculated_lastfm_filters_noise(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.mood.read_track", lambda file_path: Track(Path(file_path), "mp3", album="Album", artist="Artist", title="Song", tags={}))
    monkeypatch.setattr("noqlen_forge.mood.fetch_best_lastfm_tags_debug", lambda *args, **kwargs: type("Result", (), {"tags": [{"name": "hit", "count": "99"}, {"name": "dreamy", "count": "1"}], "source": "track"})())

    code, output = analyze_mood_path(path, with_lastfm=True)

    assert code == 0
    assert "raw_tags=Dreamy" in output
    assert "hit" not in output


def test_write_mood_mp3_uses_txxx(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    write_mood(path, ["Dreamy", "Chill"])

    assert ID3(path).getall("TXXX:MOOD")[0].text == ["Dreamy; Chill"]


def test_normalize_moods_deduplicates_and_joins_cleanup_style() -> None:
    assert normalize_moods(["Dreamy, Chill; dreamy"]) == ["Dreamy", "Chill"]


def test_write_mp3_mood_replaces_existing(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    _write_mp3_mood(path, "Energetic")
    _write_mp3_mood(path, "Happy")

    assert ID3(path).getall("TXXX:MOOD")[0].text == ["Happy"]
