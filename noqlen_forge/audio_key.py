from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from .config import get_config_value


class KeyDetectionStatus(str, Enum):
    OK = "OK"
    SKIP = "SKIP"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(slots=True)
class KeyDetectionQuery:
    path: Path
    config: dict | None = None
    backend: str | None = None


@dataclass(slots=True)
class KeyDetectionResult:
    status: KeyDetectionStatus
    key: str = ""
    scale: str = ""
    confidence: str = "low"
    backend: str = ""
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    raw_summary_json: str = "{}"
    raw_key: str = ""
    analyzed_seconds: float = 0.0
    sample_rate: int = 0
    method: str = ""


class KeyDetectionBackend(Protocol):
    name: str

    def available(self, config: dict | None = None) -> bool:
        ...

    def analyze(self, path: Path, config: dict | None = None) -> KeyDetectionResult:
        ...


class DisabledKeyDetectionBackend:
    name = "disabled"

    def available(self, config: dict | None = None) -> bool:
        return True

    def analyze(self, path: Path, config: dict | None = None) -> KeyDetectionResult:
        return KeyDetectionResult(status=KeyDetectionStatus.SKIP, backend=self.name, reason="key detection disabled")


class PortableBasicKeyDetectionBackend:
    name = "portable_basic"
    method = "portable_chroma"

    def available(self, config: dict | None = None) -> bool:
        return shutil.which(_portable_config_value(config, "ffmpeg", "ffmpeg")) is not None

    def analyze(self, path: Path, config: dict | None = None) -> KeyDetectionResult:
        started = time.perf_counter()
        ffmpeg = str(_portable_config_value(config, "ffmpeg", "ffmpeg") or "ffmpeg")
        if shutil.which(ffmpeg) is None:
            return KeyDetectionResult(status=KeyDetectionStatus.SKIP, backend=self.name, reason="ffmpeg is not available for portable key detection", elapsed_seconds=_elapsed(started), method=self.method)

        sample_rate = _positive_int(_portable_config_value(config, "sample_rate", 11025), 11025)
        max_seconds = _positive_float(_portable_config_value(config, "max_seconds", 90), 90.0)
        segment_seconds = _positive_float(_portable_config_value(config, "segment_seconds", 10), 10.0)
        segments = _positive_int(_portable_config_value(config, "segments", 6), 6)
        decode_seconds = min(max_seconds, segment_seconds * segments)
        timeout_seconds = _positive_float(_portable_config_value(config, "timeout_seconds", 30), 30.0)
        pcm, decode_warning = _decode_pcm(path, ffmpeg=ffmpeg, sample_rate=sample_rate, max_seconds=decode_seconds, timeout_seconds=timeout_seconds)
        analyzed_seconds = round(len(pcm) / sample_rate, 3) if sample_rate else 0.0
        if decode_warning:
            return KeyDetectionResult(status=KeyDetectionStatus.WARN, backend=self.name, reason=decode_warning, elapsed_seconds=_elapsed(started), analyzed_seconds=analyzed_seconds, sample_rate=sample_rate, method=self.method)
        if analyzed_seconds < 0.2:
            return KeyDetectionResult(status=KeyDetectionStatus.WARN, backend=self.name, reason="audio too short for portable key detection", elapsed_seconds=_elapsed(started), analyzed_seconds=analyzed_seconds, sample_rate=sample_rate, method=self.method)

        chroma = _build_chroma(pcm, sample_rate)
        total_energy = sum(chroma)
        if total_energy <= 1e-9:
            return KeyDetectionResult(status=KeyDetectionStatus.WARN, backend=self.name, reason="audio is silent or too quiet for key detection", elapsed_seconds=_elapsed(started), analyzed_seconds=analyzed_seconds, sample_rate=sample_rate, method=self.method)

        estimate = _estimate_key(chroma)
        raw_key = _PITCH_CLASS_NAMES[estimate["tonic"]]
        scale = str(estimate["scale"])
        key = normalize_key(raw_key, scale)
        confidence_value = float(estimate["confidence_value"])
        confidence = _confidence_label(confidence_value)
        summary = {
            "method": self.method,
            "chroma": [round(value / total_energy, 5) for value in chroma],
            "confidence_value": round(confidence_value, 4),
            "top_score": round(float(estimate["top_score"]), 4),
            "runner_up_score": round(float(estimate["runner_up_score"]), 4),
            "analyzed_seconds": analyzed_seconds,
            "sample_rate": sample_rate,
            "segment_seconds": segment_seconds,
            "segments": segments,
        }
        if confidence == "low":
            return KeyDetectionResult(
                status=KeyDetectionStatus.WARN,
                key="",
                raw_key=raw_key,
                scale=scale,
                confidence=confidence,
                backend=self.name,
                reason=f"low confidence key estimate: {raw_key} {scale}",
                warnings=["low confidence key estimate was not selected"],
                elapsed_seconds=_elapsed(started),
                raw_summary_json=json.dumps(summary, sort_keys=True),
                analyzed_seconds=analyzed_seconds,
                sample_rate=sample_rate,
                method=self.method,
            )
        return KeyDetectionResult(
            status=KeyDetectionStatus.OK,
            key=key,
            raw_key=raw_key,
            scale=scale,
            confidence=confidence,
            backend=self.name,
            reason="",
            elapsed_seconds=_elapsed(started),
            raw_summary_json=json.dumps(summary, sort_keys=True),
            analyzed_seconds=analyzed_seconds,
            sample_rate=sample_rate,
            method=self.method,
        )


class AutoKeyDetectionBackend:
    name = "auto"

    def __init__(self, registry: KeyDetectionBackendRegistry) -> None:
        self.registry = registry

    def available(self, config: dict | None = None) -> bool:
        return True

    def analyze(self, path: Path, config: dict | None = None) -> KeyDetectionResult:
        first_skip = ""
        for backend_name in _configured_backends(config):
            if backend_name == self.name:
                continue
            backend = self.registry.get(backend_name)
            if backend is None:
                continue
            result = backend.analyze(path, config)
            if result.status == KeyDetectionStatus.OK:
                return result
            if result.status == KeyDetectionStatus.SKIP and not first_skip:
                first_skip = result.reason
            if result.status in {KeyDetectionStatus.WARN, KeyDetectionStatus.FAIL}:
                return result
        return KeyDetectionResult(status=KeyDetectionStatus.SKIP, backend=self.name, reason=first_skip or "no configured key detection backend is available")


class KeyDetectionBackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, KeyDetectionBackend] = {}

    def register(self, backend: KeyDetectionBackend) -> None:
        self._backends[backend.name] = backend

    def get(self, name: str) -> KeyDetectionBackend | None:
        return self._backends.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))

    def analyze(self, query: KeyDetectionQuery) -> KeyDetectionResult:
        backend_name = resolve_key_backend_name(query.config, query.backend)
        backend = self.get(backend_name)
        if backend_name == "essentia":
            return KeyDetectionResult(status=KeyDetectionStatus.FAIL, backend=backend_name, reason="Essentia backend was removed. Use portable_basic or auto.")
        if backend is None:
            return KeyDetectionResult(status=KeyDetectionStatus.FAIL, backend=backend_name, reason=f"unknown key detection backend: {backend_name}")
        if backend_name != "disabled" and not _key_detection_enabled(query.config, query.backend):
            disabled = self.get("disabled") or DisabledKeyDetectionBackend()
            return disabled.analyze(query.path, query.config)
        return backend.analyze(query.path, query.config)


def default_key_detection_registry() -> KeyDetectionBackendRegistry:
    registry = KeyDetectionBackendRegistry()
    registry.register(DisabledKeyDetectionBackend())
    registry.register(PortableBasicKeyDetectionBackend())
    registry.register(AutoKeyDetectionBackend(registry))
    return registry


KEY_DETECTION_BACKENDS = default_key_detection_registry()


def resolve_key_backend_name(config: dict | None = None, backend: str | None = None) -> str:
    if backend:
        return backend
    return str(_key_config_value(config, "backend", "auto") or "auto")


def normalize_key(raw_key: str, scale: str) -> str:
    key = raw_key.strip().replace("♯", "#").replace("♭", "b")
    if not key:
        return ""
    key = key[0].upper() + key[1:]
    normalized_scale = scale.strip().lower()
    if normalized_scale in {"major", "maj"}:
        return f"{key} Major"
    if normalized_scale in {"minor", "min"}:
        return f"{key} Minor"
    return ""


def _key_detection_enabled(config: dict | None, explicit_backend: str | None) -> bool:
    if explicit_backend:
        return True
    return bool(_key_config_value(config, "enabled", False))


def _configured_backends(config: dict | None) -> list[str]:
    backends = _key_config_value(config, "backends", ["portable_basic"])
    if isinstance(backends, list):
        configured = [str(item) for item in backends]
    else:
        configured = [str(backends)]
    filtered = [name for name in configured if name != "essentia"]
    return filtered + ([] if "disabled" in filtered else ["disabled"])


def _key_config_value(config: dict | None, key: str, default):
    if not config:
        return default
    audio = config.get("audio", {}) if isinstance(config, dict) else {}
    key_detection = audio.get("key_detection", {}) if isinstance(audio, dict) else {}
    if isinstance(key_detection, dict) and key in key_detection:
        return key_detection.get(key, default)
    return get_config_value(config, "audio", f"key_detection_{key}", default)


def _portable_config_value(config: dict | None, key: str, default):
    if not config:
        return default
    audio = config.get("audio", {}) if isinstance(config, dict) else {}
    key_detection = audio.get("key_detection", {}) if isinstance(audio, dict) else {}
    portable = key_detection.get("portable_basic", {}) if isinstance(key_detection, dict) else {}
    if isinstance(portable, dict) and key in portable:
        return portable.get(key, default)
    return default


def _elapsed(started: float) -> float:
    return round(time.perf_counter() - started, 4)


_PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def _decode_pcm(path: Path, *, ffmpeg: str, sample_rate: int, max_seconds: float, timeout_seconds: float) -> tuple[list[float], str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-t",
        f"{max_seconds:g}",
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return [], f"ffmpeg timed out after {timeout_seconds:g}s"
    except OSError as exc:
        return [], str(exc)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()
        return [], message or f"ffmpeg exited with {result.returncode}"
    if len(result.stdout) < 2:
        return [], "ffmpeg decoded no audio"
    count = len(result.stdout) // 2
    samples = struct.unpack(f"<{count}h", result.stdout[: count * 2])
    return [sample / 32768.0 for sample in samples], ""


def _build_chroma(samples: list[float], sample_rate: int) -> list[float]:
    frame_size = min(len(samples), 4096 if sample_rate >= 11025 else 2048)
    if frame_size < 256:
        return [0.0] * 12
    hop = frame_size
    chroma = [0.0] * 12
    for start in range(0, max(0, len(samples) - frame_size + 1), hop):
        frame = samples[start : start + frame_size]
        rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame)) if frame else 0.0
        if rms < 0.002:
            continue
        windowed = [sample * (0.5 - 0.5 * math.cos((2.0 * math.pi * index) / (len(frame) - 1))) for index, sample in enumerate(frame)]
        for pitch_class in range(12):
            energy = 0.0
            for frequency in _pitch_class_frequencies(pitch_class):
                if frequency >= sample_rate / 2:
                    continue
                energy += _goertzel_power(windowed, sample_rate, frequency)
            chroma[pitch_class] += energy
    return chroma


def _pitch_class_frequencies(pitch_class: int) -> list[float]:
    frequencies = []
    for midi in range(36 + pitch_class, 97, 12):
        frequencies.append(440.0 * (2.0 ** ((midi - 69) / 12.0)))
    return frequencies


def _goertzel_power(samples: list[float], sample_rate: int, frequency: float) -> float:
    normalized = frequency / sample_rate
    coeff = 2.0 * math.cos(2.0 * math.pi * normalized)
    prev = 0.0
    prev2 = 0.0
    for sample in samples:
        current = sample + coeff * prev - prev2
        prev2 = prev
        prev = current
    return prev2 * prev2 + prev * prev - coeff * prev * prev2


def _estimate_key(chroma: list[float]) -> dict[str, float | int | str]:
    normalized = _normalize_vector(chroma)
    candidates: list[tuple[float, int, str]] = []
    for tonic in range(12):
        candidates.append((_cosine_similarity(normalized, _rotate(_MAJOR_PROFILE, tonic)), tonic, "major"))
        candidates.append((_cosine_similarity(normalized, _rotate(_MINOR_PROFILE, tonic)), tonic, "minor"))
    candidates.sort(reverse=True, key=lambda item: item[0])
    top_score, tonic, scale = candidates[0]
    runner_up_score = candidates[1][0]
    confidence_value = max(0.0, min(1.0, (top_score - runner_up_score) * 3.0))
    return {"top_score": top_score, "runner_up_score": runner_up_score, "tonic": tonic, "scale": scale, "confidence_value": confidence_value}


def _normalize_vector(values: list[float] | tuple[float, ...]) -> list[float]:
    total = sum(values)
    if total <= 0:
        return [0.0 for _ in values]
    return [value / total for value in values]


def _rotate(values: tuple[float, ...], tonic: int) -> list[float]:
    rotated = [0.0] * 12
    for index, value in enumerate(values):
        rotated[(index + tonic) % 12] = value
    return _normalize_vector(rotated)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _confidence_label(value: float) -> str:
    if value >= 0.55:
        return "high"
    if value >= 0.12:
        return "medium"
    return "low"


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_float(value, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
