from pathlib import Path

from mutagen.id3 import ID3

from noqlen_forge.analyze import _write_mp4_bpm, _write_mp4_key, analyze_bpm_path, analyze_bpm_value, analyze_features_path, analyze_key_path, normalize_key, parse_aubio_tempo, round_bpm, score_danceability, score_energy, write_bpm, write_key
from noqlen_forge.audio import Track, is_valid_bpm
from noqlen_forge.audio_key import KeyDetectionResult, KeyDetectionStatus


class FakeMP4:
    def __init__(self):
        self.tags = {}

    def add_tags(self):
        self.tags = {}


def test_parse_aubio_tempo_output() -> None:
    assert parse_aubio_tempo("120.251 bpm") == 120.251
    assert parse_aubio_tempo("beats\n87.5\n") == 87.5
    assert parse_aubio_tempo("nan") is None


def test_validate_bpm_range() -> None:
    assert is_valid_bpm("40")
    assert is_valid_bpm("240")
    assert not is_valid_bpm("0")
    assert not is_valid_bpm("nan")
    assert not is_valid_bpm("300")


def test_write_bpm_mp3_uses_tbpm(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    write_bpm(path, 123.4)

    tags = ID3(path)
    assert tags.getall("TBPM")[0].text == ["123.4"]


def test_write_bpm_m4a_uses_tmpo_and_freeform() -> None:
    audio = FakeMP4()

    _write_mp4_bpm(audio, 123.4)

    assert audio.tags["tmpo"] == [123]
    assert bytes(audio.tags["----:com.apple.iTunes:BPM"][0]) == b"123.4"


def test_bpm_normalizes_high_half_tempo() -> None:
    result = analyze_bpm_value(263.4)

    assert result.final_bpm == 131.7
    assert result.confidence == "medium"
    assert result.note == "normalized half tempo"


def test_bpm_normalizes_low_double_tempo() -> None:
    result = analyze_bpm_value(52)

    assert result.final_bpm == 104
    assert result.confidence == "medium"
    assert result.note == "normalized double tempo"


def test_bpm_warns_possible_half_time() -> None:
    result = analyze_bpm_value(150.3)

    assert result.final_bpm == 150.3
    assert result.confidence == "high"
    assert result.warning == "possible half-time alternative 75.2"


def test_bpm_invalid_low_value() -> None:
    result = analyze_bpm_value(20)

    assert result.confidence == "low"
    assert result.warning == "invalid BPM"


def test_bpm_rounding_modes() -> None:
    assert round_bpm(131.7, "int") == 132
    assert round_bpm(131.74, "1dp") == 131.7


def test_existing_bpm_is_preserved_without_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.analyze.shutil.which", lambda command: "/usr/bin/aubio")
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"bpm": ["120"]}))
    monkeypatch.setattr("noqlen_forge.analyze.detect_bpm", lambda file_path: (130.0, ""))

    code, output = analyze_bpm_path(path, apply=False)

    assert code == 0
    assert "BPM: skipped existing BPM=120" in output


def test_force_overwrites_existing_bpm(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    monkeypatch.setattr("noqlen_forge.analyze.shutil.which", lambda command: "/usr/bin/aubio")
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"bpm": ["120"]}))
    monkeypatch.setattr("noqlen_forge.analyze.detect_bpm", lambda file_path: (130.0, ""))
    monkeypatch.setattr("noqlen_forge.analyze.write_bpm", lambda file_path, bpm, bpm_round="1dp": written.append((file_path, bpm, bpm_round)))

    code, output = analyze_bpm_path(path, apply=True, force=True)

    assert code == 0
    assert written == [(path, 130.0, "1dp")]
    assert "raw=130 final=130 confidence=high" in output


def test_low_confidence_bpm_is_not_written_without_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    monkeypatch.setattr("noqlen_forge.analyze.shutil.which", lambda command: "/usr/bin/aubio")
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={}))
    monkeypatch.setattr("noqlen_forge.analyze.detect_bpm", lambda file_path: (20.0, ""))
    monkeypatch.setattr("noqlen_forge.analyze.write_bpm", lambda file_path, bpm, bpm_round="1dp": written.append((file_path, bpm, bpm_round)))

    code, output = analyze_bpm_path(path, apply=True)

    assert code == 0
    assert written == []
    assert "confidence=low" in output
    assert "action=skipped" in output


def test_normalize_key_major_minor() -> None:
    assert normalize_key("C", "major") == "C Major"
    assert normalize_key("a", "minor") == "A Minor"


def test_write_key_mp3_uses_tkey(tmp_path) -> None:
    path = tmp_path / "song.mp3"
    ID3().save(path)

    write_key(path, "C Major")

    assert ID3(path).getall("TKEY")[0].text == ["C Major"]


def test_write_key_m4a_uses_initialkey_and_key() -> None:
    audio = FakeMP4()

    _write_mp4_key(audio, "A Minor")

    assert bytes(audio.tags["----:com.apple.iTunes:INITIALKEY"][0]) == b"A Minor"
    assert bytes(audio.tags["----:com.apple.iTunes:KEY"][0]) == b"A Minor"


def test_key_existing_is_preserved_without_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"key": ["C Major"]}))
    monkeypatch.setattr("noqlen_forge.analyze.KEY_DETECTION_BACKENDS", type("Registry", (), {"analyze": lambda self, query: _raise_unexpected()})())

    code, output = analyze_key_path(path, apply=False)

    assert code == 0
    assert "KEY: skipped existing KEY=C Major" in output


def test_key_force_overwrites_existing(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"key": ["C Major"]}))
    monkeypatch.setattr("noqlen_forge.analyze.KEY_DETECTION_BACKENDS", type("Registry", (), {"analyze": lambda self, query: KeyDetectionResult(KeyDetectionStatus.OK, raw_key="A", scale="minor", key="A Minor", confidence="high", backend="fake")})())
    monkeypatch.setattr("noqlen_forge.analyze.write_key", lambda file_path, key: written.append((file_path, key)))

    code, output = analyze_key_path(path, apply=True, force=True)

    assert code == 0
    assert written == [(path, "A Minor")]
    assert "action=wrote" in output


def test_key_below_configured_confidence_is_not_written(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    config = {"audio": {"key_detection": {"enabled": True, "min_confidence": "high", "write_low_confidence": False}}}
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={}))
    monkeypatch.setattr("noqlen_forge.analyze.KEY_DETECTION_BACKENDS", type("Registry", (), {"analyze": lambda self, query: KeyDetectionResult(KeyDetectionStatus.OK, raw_key="A", scale="minor", key="A Minor", confidence="medium", backend="portable_basic")})())
    monkeypatch.setattr("noqlen_forge.analyze.write_key", lambda file_path, key: written.append((file_path, key)))

    code, output = analyze_key_path(path, apply=True, config=config)

    assert code == 0
    assert written == []
    assert "confidence=medium action=skipped" in output


def test_key_backend_missing_returns_clean_skip(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.analyze.KEY_DETECTION_BACKENDS", type("Registry", (), {"analyze": lambda self, query: KeyDetectionResult(KeyDetectionStatus.SKIP, backend="portable_basic", reason="ffmpeg is not available for portable key detection")})())

    code, output = analyze_key_path(path, apply=False)

    assert code == 0
    assert output == "KEY: skipped, ffmpeg is not available for portable key detection.\nInstall/configure an optional key backend to enable key detection."


def test_removed_essentia_backend_returns_clear_key_error(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={}))

    code, output = analyze_key_path(path, apply=False, backend="essentia")

    assert code == 1
    assert "Essentia backend was removed. Use portable_basic or auto." in output


def test_bpm_does_not_require_key_detection_backend(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.analyze.shutil.which", lambda command: "/usr/bin/aubio")
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={}))
    monkeypatch.setattr("noqlen_forge.analyze.detect_bpm", lambda file_path: (120.0, ""))

    code, output = analyze_bpm_path(path, apply=False)

    assert code == 0
    assert "BPM analysis" in output


def test_energy_score_returns_percent() -> None:
    result = score_energy({"bpm": analyze_bpm_value(128), "loudness": -12.0, "interval_variance": 0.01, "beat_count": 8})

    assert 0 <= result.value <= 100
    assert result.confidence in {"medium", "high"}


def test_danceability_score_returns_percent() -> None:
    result = score_danceability({"bpm": analyze_bpm_value(124), "loudness": -12.0, "interval_variance": 0.005, "beat_count": 8})

    assert 0 <= result.value <= 100
    assert result.confidence == "high"


def test_feature_low_confidence_does_not_write_without_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={}))
    monkeypatch.setattr("noqlen_forge.analyze.feature_metrics", lambda *args, **kwargs: {"bpm": analyze_bpm_value(20), "loudness": None, "interval_variance": None, "beat_count": 0})
    monkeypatch.setattr("noqlen_forge.analyze.write_feature", lambda file_path, name, value: written.append((name, value)))

    code, output = analyze_features_path(path, apply=True)

    assert code == 0
    assert written == []
    assert "confidence=low" in output
    assert "action=skipped" in output


def test_features_preserve_existing_without_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"energy": ["80"], "danceability": ["70"]}))
    monkeypatch.setattr("noqlen_forge.analyze.feature_metrics", lambda *args, **kwargs: {"bpm": analyze_bpm_value(128), "loudness": -12.0, "interval_variance": 0.01, "beat_count": 8})

    code, output = analyze_features_path(path, apply=False)

    assert code == 0
    assert "skipped existing ENERGY=80" in output
    assert "skipped existing DANCEABILITY=70" in output


def test_features_overwrite_with_force(monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mp3"
    path.touch()
    written = []
    monkeypatch.setattr("noqlen_forge.analyze.read_track", lambda file_path: Track(Path(file_path), "mp3", tags={"energy": ["80"]}))
    monkeypatch.setattr("noqlen_forge.analyze.feature_metrics", lambda *args, **kwargs: {"bpm": analyze_bpm_value(128), "loudness": -12.0, "interval_variance": 0.01, "beat_count": 8})
    monkeypatch.setattr("noqlen_forge.analyze.write_feature", lambda file_path, name, value: written.append((name, value)))

    code, output = analyze_features_path(path, apply=True, energy=True, danceability=False, force=True)

    assert code == 0
    assert written and written[0][0] == "ENERGY"
    assert "action=wrote" in output


def _raise_unexpected():
    raise AssertionError("detect_key should not be called")
