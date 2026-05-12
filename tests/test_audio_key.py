import math
import shutil
import wave
from pathlib import Path

import pytest

from noqlen_forge.audio_key import AutoKeyDetectionBackend, DisabledKeyDetectionBackend, KEY_DETECTION_BACKENDS, KeyDetectionBackendRegistry, KeyDetectionQuery, KeyDetectionResult, KeyDetectionStatus, PortableBasicKeyDetectionBackend
from noqlen_forge.config import default_config


def test_key_detection_registry_contains_default_backends() -> None:
    assert set(KEY_DETECTION_BACKENDS.names()) == {"auto", "disabled", "portable_basic"}


def test_disabled_backend_returns_skip(tmp_path: Path) -> None:
    result = DisabledKeyDetectionBackend().analyze(tmp_path / "song.mp3", default_config())

    assert result.status == KeyDetectionStatus.SKIP
    assert result.backend == "disabled"
    assert result.reason == "key detection disabled"


def test_unknown_backend_returns_clear_error(tmp_path: Path) -> None:
    registry = KeyDetectionBackendRegistry()

    result = registry.analyze(KeyDetectionQuery(tmp_path / "song.mp3", backend="missing"))

    assert result.status == KeyDetectionStatus.FAIL
    assert result.reason == "unknown key detection backend: missing"


def test_removed_essentia_backend_returns_clear_error(tmp_path: Path) -> None:
    result = KEY_DETECTION_BACKENDS.analyze(KeyDetectionQuery(tmp_path / "song.mp3", backend="essentia"))

    assert result.status == KeyDetectionStatus.FAIL
    assert result.reason == "Essentia backend was removed. Use portable_basic or auto."


def test_auto_uses_available_backend(tmp_path: Path) -> None:
    class FakePortable:
        name = "portable_basic"

        def available(self, config=None):
            return True

        def analyze(self, path, config=None):
            return KeyDetectionResult(KeyDetectionStatus.OK, key="C Major", scale="major", confidence="high", backend=self.name, raw_key="C")

    config = default_config()
    config["audio"]["key_detection"]["enabled"] = True
    registry = KeyDetectionBackendRegistry()
    registry.register(DisabledKeyDetectionBackend())
    registry.register(FakePortable())
    registry.register(AutoKeyDetectionBackend(registry))

    result = registry.analyze(KeyDetectionQuery(tmp_path / "song.mp3", config=config))

    assert result.status == KeyDetectionStatus.OK
    assert result.backend == "portable_basic"
    assert result.key == "C Major"


def test_auto_returns_skip_when_portable_unavailable(tmp_path: Path) -> None:
    class MissingPortable:
        name = "portable_basic"

        def available(self, config=None):
            return False

        def analyze(self, path, config=None):
            return KeyDetectionResult(KeyDetectionStatus.SKIP, backend=self.name, reason="ffmpeg is not available for portable key detection")

    config = default_config()
    config["audio"]["key_detection"]["enabled"] = True
    registry = KeyDetectionBackendRegistry()
    registry.register(DisabledKeyDetectionBackend())
    registry.register(MissingPortable())
    registry.register(AutoKeyDetectionBackend(registry))

    result = registry.analyze(KeyDetectionQuery(tmp_path / "song.mp3", config=config))

    assert result.status == KeyDetectionStatus.SKIP
    assert result.backend == "auto"
    assert result.reason == "ffmpeg is not available for portable key detection"


def test_auto_uses_portable_and_ignores_removed_config_backend(tmp_path: Path) -> None:
    class FakeBackend:
        def __init__(self, name: str, key: str) -> None:
            self.name = name
            self.key = key

        def available(self, config=None):
            return True

        def analyze(self, path, config=None):
            return KeyDetectionResult(KeyDetectionStatus.OK, key=self.key, scale="major", confidence="high", backend=self.name, raw_key=self.key.split()[0])

    config = default_config()
    config["audio"]["key_detection"]["enabled"] = True
    config["audio"]["key_detection"]["backends"] = ["essentia", "portable_basic"]
    registry = KeyDetectionBackendRegistry()
    registry.register(FakeBackend("portable_basic", "C Major"))
    registry.register(AutoKeyDetectionBackend(registry))

    result = registry.analyze(KeyDetectionQuery(tmp_path / "song.wav", config=config))

    assert result.status == KeyDetectionStatus.OK
    assert result.backend == "portable_basic"
    assert result.key == "C Major"


def test_portable_basic_missing_ffmpeg_returns_skip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("noqlen_forge.audio_key.shutil.which", lambda command: None)

    result = PortableBasicKeyDetectionBackend().analyze(tmp_path / "song.wav", default_config())

    assert result.status == KeyDetectionStatus.SKIP
    assert result.reason == "ffmpeg is not available for portable key detection"


def test_portable_basic_invalid_file_returns_warn(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    path = tmp_path / "invalid.wav"
    path.write_text("not audio", encoding="utf-8")

    result = PortableBasicKeyDetectionBackend().analyze(path, _portable_test_config())

    assert result.status == KeyDetectionStatus.WARN
    assert "ffmpeg" in result.reason.lower() or "invalid" in result.reason.lower()


def test_portable_basic_detects_synthetic_c_major(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    path = tmp_path / "c-major.wav"
    _write_synthetic_wav(path, [261.63, 329.63, 392.00, 261.63])

    result = PortableBasicKeyDetectionBackend().analyze(path, _portable_test_config())

    assert result.status == KeyDetectionStatus.OK
    assert result.key == "C Major"
    assert result.confidence in {"medium", "high"}
    assert result.backend == "portable_basic"
    assert result.method == "portable_chroma"
    assert result.analyzed_seconds > 0
    assert result.sample_rate == 11025


def test_portable_basic_detects_synthetic_a_minor(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    path = tmp_path / "a-minor.wav"
    _write_synthetic_wav(path, [220.00, 261.63, 329.63, 440.00])

    result = PortableBasicKeyDetectionBackend().analyze(path, _portable_test_config())

    assert result.status == KeyDetectionStatus.OK
    assert result.key == "A Minor"


def test_portable_basic_silence_does_not_generate_key(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    path = tmp_path / "silence.wav"
    _write_synthetic_wav(path, [0.0], amplitude=0.0)

    result = PortableBasicKeyDetectionBackend().analyze(path, _portable_test_config())

    assert result.status == KeyDetectionStatus.WARN
    assert result.key == ""
    assert "silent" in result.reason


def test_portable_basic_short_audio_returns_warn(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    path = tmp_path / "short.wav"
    _write_synthetic_wav(path, [261.63], duration_per_tone=0.03)

    result = PortableBasicKeyDetectionBackend().analyze(path, _portable_test_config())

    assert result.status == KeyDetectionStatus.WARN
    assert "too short" in result.reason


def _portable_test_config() -> dict:
    config = default_config()
    config["audio"]["key_detection"]["enabled"] = True
    config["audio"]["key_detection"]["portable_basic"]["max_seconds"] = 5
    config["audio"]["key_detection"]["portable_basic"]["timeout_seconds"] = 10
    return config


def _write_synthetic_wav(path: Path, frequencies: list[float], *, sample_rate: int = 11025, duration_per_tone: float = 0.45, amplitude: float = 0.6) -> None:
    frames = bytearray()
    samples_per_tone = max(1, int(sample_rate * duration_per_tone))
    for frequency in frequencies:
        for index in range(samples_per_tone):
            if frequency <= 0 or amplitude <= 0:
                sample = 0
            else:
                sample = int(32767 * amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
