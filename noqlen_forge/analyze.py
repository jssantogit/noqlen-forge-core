from __future__ import annotations

import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TBPM, TKEY, TXXX
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .audio import audio_files, get_tag, is_valid_bpm, is_valid_percent, read_track
from .audio_key import KEY_DETECTION_BACKENDS, KeyDetectionQuery, KeyDetectionStatus, normalize_key


@dataclass(slots=True)
class BpmPlan:
    path: Path
    raw_bpm: float = 0
    final_bpm: float = 0
    confidence: str = ""
    note: str = ""
    warning: str = ""
    skipped: str = ""


@dataclass(slots=True)
class BpmAnalysis:
    raw_bpm: float
    final_bpm: float
    confidence: str
    note: str = ""
    warning: str = ""


@dataclass(slots=True)
class KeyPlan:
    path: Path
    raw_key: str = ""
    scale: str = ""
    key: str = ""
    confidence: str = ""
    skipped: str = ""


@dataclass(slots=True)
class KeyAnalysis:
    raw_key: str
    scale: str
    key: str
    confidence: str


@dataclass(slots=True)
class FeatureResult:
    name: str
    value: int
    confidence: str
    raw_data: str = ""
    note: str = ""


@dataclass(slots=True)
class FeaturePlan:
    path: Path
    result: FeatureResult | None = None
    skipped: str = ""


ProgressCallback = Callable[[int, int], None]


def analyze_bpm_path(path: Path, apply: bool, skip_existing: bool = False, force: bool = False, bpm_range: tuple[float, float] = (70, 180), bpm_round: str = "1dp", progress: ProgressCallback | None = None) -> tuple[int, str]:
    if shutil.which("aubio") is None:
        return 1, "aubio not found. Install with: apt install aubio-tools"
    files = audio_files(path)
    if not files:
        return 1, "No supported audio files found"
    plans: list[BpmPlan] = []
    errors: list[str] = []
    total = len(files)
    for index, file_path in enumerate(files, start=1):
        track = read_track(file_path)
        if get_tag(track, "bpm") and (skip_existing or not force):
            plans.append(BpmPlan(path=file_path, skipped=f"BPM: skipped existing BPM={'; '.join(get_tag(track, 'bpm'))}"))
            if progress:
                progress(index, total)
            continue
        bpm, error = detect_bpm(file_path)
        if error:
            errors.append(f"{file_path}: {error}")
            if progress:
                progress(index, total)
            continue
        if bpm is None:
            errors.append(f"{file_path}: invalid BPM")
            if progress:
                progress(index, total)
            continue
        analysis = analyze_bpm_value(bpm, preferred_range=bpm_range)
        analysis.final_bpm = round_bpm(analysis.final_bpm, bpm_round)
        plans.append(BpmPlan(path=file_path, raw_bpm=analysis.raw_bpm, final_bpm=analysis.final_bpm, confidence=analysis.confidence, note=analysis.note, warning=analysis.warning))
        if apply and (analysis.confidence != "low" or force):
            write_bpm(file_path, analysis.final_bpm, bpm_round=bpm_round)
        if progress:
            progress(index, total)
    return 0, summarize_bpm(plans, errors, apply=apply, force=force, bpm_round=bpm_round)


def analyze_key_path(path: Path, apply: bool, force: bool = False, config: dict | None = None, backend: str | None = None) -> tuple[int, str]:
    files = audio_files(path)
    if not files:
        return 1, "No supported audio files found"
    plans: list[KeyPlan] = []
    errors: list[str] = []
    failed = False
    skipped_backend = ""
    backend_name = ""
    minimum_confidence = str(_key_detection_config_value(config, "min_confidence", "medium"))
    write_low_confidence = bool(_key_detection_config_value(config, "write_low_confidence", False))
    for file_path in files:
        track = read_track(file_path)
        existing = get_tag(track, "key")
        if existing and not force:
            plans.append(KeyPlan(path=file_path, skipped=f"KEY: skipped existing KEY={'; '.join(existing)}"))
            continue
        result = KEY_DETECTION_BACKENDS.analyze(KeyDetectionQuery(file_path, config=config, backend=backend))
        backend_name = result.backend or backend_name
        if result.status == KeyDetectionStatus.SKIP:
            skipped_backend = result.reason or "key detection unavailable"
            continue
        if result.status == KeyDetectionStatus.FAIL:
            failed = True
            errors.append(f"{file_path}: {result.reason or 'key detection failed'}")
            continue
        if result.status == KeyDetectionStatus.WARN:
            errors.append(f"{file_path}: {result.reason or 'key detection warning'}")
            continue
        plans.append(KeyPlan(path=file_path, raw_key=result.raw_key or result.key, scale=result.scale, key=result.key, confidence=result.confidence))
        writes = _key_should_write(result.confidence, minimum_confidence, force=force, write_low_confidence=write_low_confidence)
        if apply and writes:
            write_key(file_path, result.key)
    if skipped_backend and not plans and not errors:
        return 0, f"KEY: skipped, {skipped_backend}.\nInstall/configure an optional key backend to enable key detection."
    return 1 if failed and not plans else 0, summarize_key(plans, errors, apply=apply, backend=backend_name, minimum_confidence=minimum_confidence, force=force, write_low_confidence=write_low_confidence)


def analyze_features_path(path: Path, apply: bool, energy: bool = True, danceability: bool = True, force: bool = False, minimum_confidence: str = "medium", bpm_range: tuple[float, float] = (70, 180), bpm_round: str = "1dp", progress: ProgressCallback | None = None) -> tuple[int, str]:
    files = audio_files(path)
    if not files:
        return 1, "No supported audio files found"
    plans: list[FeaturePlan] = []
    total = len(files)
    for index, file_path in enumerate(files, start=1):
        track = read_track(file_path)
        existing_energy = get_tag(track, "energy")
        existing_danceability = get_tag(track, "danceability")
        metrics = feature_metrics(file_path, bpm_range=bpm_range, bpm_round=bpm_round)
        results: list[FeatureResult] = []
        if energy:
            results.append(score_energy(metrics))
        if danceability:
            results.append(score_danceability(metrics))
        for result in results:
            existing = existing_energy if result.name == "ENERGY" else existing_danceability
            if existing and not force:
                plans.append(FeaturePlan(path=file_path, skipped=f"skipped existing {result.name}={'; '.join(existing)}"))
                continue
            plans.append(FeaturePlan(path=file_path, result=result))
            if apply and (_confidence_rank(result.confidence) >= _confidence_rank(minimum_confidence) or force):
                write_feature(file_path, result.name, result.value)
        if progress:
            progress(index, total)
    return 0, summarize_features(plans, apply=apply, force=force, minimum_confidence=minimum_confidence)


def feature_metrics(path: Path, bpm_range: tuple[float, float] = (70, 180), bpm_round: str = "1dp") -> dict:
    raw_bpm, bpm_error = detect_bpm(path)
    bpm_analysis = analyze_bpm_value(raw_bpm, preferred_range=bpm_range) if raw_bpm else BpmAnalysis(0, 0, "low", warning=bpm_error or "missing BPM")
    bpm_analysis.final_bpm = round_bpm(bpm_analysis.final_bpm, bpm_round) if bpm_analysis.final_bpm else 0
    loudness = detect_loudness(path)
    intervals = detect_beat_intervals(path)
    variance = interval_variance(intervals)
    return {"bpm": bpm_analysis, "loudness": loudness, "interval_variance": variance, "beat_count": len(intervals) + 1 if intervals else 0}


def score_energy(metrics: dict) -> FeatureResult:
    bpm = metrics["bpm"].final_bpm
    bpm_confidence = metrics["bpm"].confidence
    loudness = metrics["loudness"]
    if not bpm:
        return FeatureResult("ENERGY", 0, "low", raw_data="bpm=missing", note="missing BPM")
    bpm_score = _scale_score(bpm, [(70, 45), (100, 65), (150, 88), (180, 92)])
    loudness_score = 55 if loudness is None else max(10, min(100, int((loudness + 45) * 2.2)))
    value = max(1, min(100, round(bpm_score * 0.65 + loudness_score * 0.35)))
    confidence = "high" if bpm_confidence == "high" and loudness is not None else "medium" if bpm_confidence in {"high", "medium"} else "low"
    return FeatureResult("ENERGY", value, confidence, raw_data=f"bpm={_format_bpm(bpm)} loudness={loudness if loudness is not None else 'unknown'}")


def score_danceability(metrics: dict) -> FeatureResult:
    bpm = metrics["bpm"].final_bpm
    bpm_confidence = metrics["bpm"].confidence
    variance = metrics["interval_variance"]
    if not bpm:
        return FeatureResult("DANCEABILITY", 0, "low", raw_data="bpm=missing", note="missing BPM")
    if 90 <= bpm <= 130:
        base = 82
    elif 70 <= bpm < 90 or 130 < bpm <= 160:
        base = 62
    else:
        base = 42
    regularity = 0 if variance is None else max(-25, min(18, round((0.04 - variance) * 450)))
    value = max(1, min(100, base + regularity))
    confidence = "high" if bpm_confidence == "high" and variance is not None and variance <= 0.025 else "medium" if bpm_confidence in {"high", "medium"} else "low"
    return FeatureResult("DANCEABILITY", value, confidence, raw_data=f"bpm={_format_bpm(bpm)} beat_variance={variance if variance is not None else 'unknown'}")


def detect_loudness(path: Path) -> float | None:
    if shutil.which("ffmpeg") is None:
        return None
    command = ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", result.stderr + result.stdout)
    return float(match.group(1)) if match else None


def detect_beat_intervals(path: Path) -> list[float]:
    command = ["aubio", "tempo", str(path)]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        return []
    times = [float(item) for item in re.findall(r"(?m)^\s*(\d+(?:\.\d+)?)\s*$", result.stdout)]
    return [right - left for left, right in zip(times, times[1:]) if right > left]


def interval_variance(intervals: list[float]) -> float | None:
    if len(intervals) < 3:
        return None
    mean = sum(intervals) / len(intervals)
    return sum((item - mean) ** 2 for item in intervals) / len(intervals)


def detect_key(path: Path) -> tuple[KeyAnalysis, str]:
    result = KEY_DETECTION_BACKENDS.analyze(KeyDetectionQuery(path, backend="portable_basic"))
    if result.status != KeyDetectionStatus.OK:
        return KeyAnalysis("", "", "", "low"), result.reason
    return KeyAnalysis(result.raw_key or result.key, result.scale, result.key, result.confidence), ""


def analyze_key_value(raw_key: str, scale: str, strength: float = 1.0) -> KeyAnalysis:
    normalized = normalize_key(raw_key, scale)
    confidence = "high" if normalized and strength >= 0.6 else "low"
    return KeyAnalysis(raw_key=raw_key, scale=scale, key=normalized, confidence=confidence)


def detect_bpm(path: Path) -> tuple[float | None, str]:
    command = ["aubio", "tempo", str(path)]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        return None, str(exc)
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        return None, output or f"aubio exited with {result.returncode}"
    bpm = parse_aubio_tempo(result.stdout + "\n" + result.stderr)
    if bpm is None:
        return None, "invalid BPM"
    return bpm, ""


def parse_aubio_tempo(output: str) -> float | None:
    for match in re.finditer(r"(?<![A-Za-z])(?:nan|[-+]?\d+(?:\.\d+)?)(?![A-Za-z])", output, flags=re.IGNORECASE):
        raw = match.group(0)
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isfinite(value) and value > 0:
            return value
    return None


def analyze_bpm_value(raw_bpm: float, preferred_range: tuple[float, float] = (70, 180)) -> BpmAnalysis:
    low, high = preferred_range
    if not math.isfinite(raw_bpm) or raw_bpm <= 0:
        return BpmAnalysis(raw_bpm=raw_bpm, final_bpm=raw_bpm, confidence="low", warning="invalid BPM")
    if raw_bpm > high and low <= raw_bpm / 2 <= high:
        return BpmAnalysis(raw_bpm=raw_bpm, final_bpm=raw_bpm / 2, confidence="medium", note="normalized half tempo")
    if raw_bpm < low and low <= raw_bpm * 2 <= high:
        return BpmAnalysis(raw_bpm=raw_bpm, final_bpm=raw_bpm * 2, confidence="medium", note="normalized double tempo")
    if not is_valid_bpm(raw_bpm):
        return BpmAnalysis(raw_bpm=raw_bpm, final_bpm=raw_bpm, confidence="low", warning="invalid BPM")
    if not low <= raw_bpm <= high:
        return BpmAnalysis(raw_bpm=raw_bpm, final_bpm=raw_bpm, confidence="low", warning=f"outside preferred BPM range {low:g}-{high:g}")
    if 120 <= raw_bpm <= 180:
        return BpmAnalysis(raw_bpm=raw_bpm, final_bpm=raw_bpm, confidence="high", warning=f"possible half-time alternative {_format_bpm(raw_bpm / 2)}")
    if 70 <= raw_bpm <= 100:
        return BpmAnalysis(raw_bpm=raw_bpm, final_bpm=raw_bpm, confidence="high", warning=f"possible double-time alternative {_format_bpm(raw_bpm * 2)}")
    return BpmAnalysis(raw_bpm=raw_bpm, final_bpm=raw_bpm, confidence="high")


def round_bpm(bpm: float, bpm_round: str) -> float:
    if bpm_round == "int":
        return float(round(bpm))
    return round(bpm, 1)


def write_bpm(path: Path, bpm: float, bpm_round: str = "1dp") -> None:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _write_mp3_bpm(path, bpm, bpm_round=bpm_round)
        return
    audio = MutagenFile(path, easy=False)
    if audio is None:
        return
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        _write_mp4_bpm(audio, bpm, bpm_round=bpm_round)
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        audio["BPM"] = [_format_bpm(bpm, bpm_round=bpm_round)]
    audio.save()


def write_key(path: Path, key: str) -> None:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _write_mp3_key(path, key)
        return
    audio = MutagenFile(path, easy=False)
    if audio is None:
        return
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        _write_mp4_key(audio, key)
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        audio["INITIALKEY"] = [key]
        audio["KEY"] = [key]
    audio.save()


def write_feature(path: Path, name: str, value: int) -> None:
    suffix = path.suffix.lower()
    text = str(value)
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall(f"TXXX:{name}")
        tags.add(TXXX(encoding=3, desc=name, text=[text]))
        tags.save(path)
        return
    audio = MutagenFile(path, easy=False)
    if audio is None:
        return
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        if audio.tags is None:
            audio.add_tags()
        audio.tags[f"----:com.apple.iTunes:{name}"] = [MP4FreeForm(text.encode("utf-8"))]
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        audio[name] = [text]
    audio.save()


def summarize_bpm(plans: list[BpmPlan], errors: list[str], apply: bool, force: bool = False, bpm_round: str = "1dp") -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    lines = [f"{mode}: BPM analysis"]
    if not plans and not errors:
        lines.append("- nothing")
    for plan in plans:
        if plan.skipped:
            lines.append(f"- {plan.path}: {plan.skipped}")
        else:
            writes = plan.confidence != "low" or force
            if not writes:
                action = "skipped"
            else:
                action = "wrote" if apply else "would write"
            parts = [f"- {plan.path}: raw={_format_bpm(plan.raw_bpm)}", f"final={_format_bpm(plan.final_bpm, bpm_round=bpm_round)}", f"confidence={plan.confidence}"]
            if plan.note:
                parts.append(f"note={plan.note}")
            if plan.warning:
                parts.append(f"warning={plan.warning}")
            parts.append(f"action={action}")
            if plan.confidence == "low" and not force:
                parts.append("reason=low confidence")
            lines.append(" ".join(parts))
    for error in errors:
        lines.append(f"- {error}")
    return "\n".join(lines)


def summarize_key(plans: list[KeyPlan], errors: list[str], apply: bool, backend: str = "", minimum_confidence: str = "medium", force: bool = False, write_low_confidence: bool = False) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    lines = [f"{mode}: KEY analysis"]
    if backend:
        lines.append(f"Backend: {backend}")
    if not plans and not errors:
        lines.append("- nothing")
    for plan in plans:
        if plan.skipped:
            lines.append(f"- {plan.path}: {plan.skipped}")
            continue
        writes = _key_should_write(plan.confidence, minimum_confidence, force=force, write_low_confidence=write_low_confidence)
        action = "wrote" if apply and writes else "would write" if writes else "skipped"
        lines.append(f"- {plan.path}: raw={plan.raw_key} scale={plan.scale} final={plan.key} confidence={plan.confidence} action={action}")
    for error in errors:
        lines.append(f"- {error}")
    return "\n".join(lines)


def summarize_features(plans: list[FeaturePlan], apply: bool, force: bool = False, minimum_confidence: str = "medium") -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    lines = [f"{mode}: feature analysis"]
    if not plans:
        lines.append("- nothing")
    for plan in plans:
        if plan.skipped:
            lines.append(f"- {plan.path}: {plan.skipped}")
            continue
        result = plan.result
        assert result is not None
        writes = _confidence_rank(result.confidence) >= _confidence_rank(minimum_confidence) or force
        action = "wrote" if apply and writes else "would write" if writes else "skipped"
        lines.append(f"- {plan.path}: {result.name} raw_data={result.raw_data} final={result.value} confidence={result.confidence} action={action}" + (f" note={result.note}" if result.note else ""))
    return "\n".join(lines)


def _confidence_rank(confidence: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(confidence, 1)


def _key_should_write(confidence: str, minimum_confidence: str, *, force: bool = False, write_low_confidence: bool = False) -> bool:
    if force:
        return True
    if confidence == "low" and not write_low_confidence:
        return False
    return _confidence_rank(confidence) >= _confidence_rank(minimum_confidence)


def _key_detection_config_value(config: dict | None, key: str, default):
    if not config:
        return default
    audio = config.get("audio", {}) if isinstance(config, dict) else {}
    key_detection = audio.get("key_detection", {}) if isinstance(audio, dict) else {}
    if isinstance(key_detection, dict) and key in key_detection:
        return key_detection.get(key, default)
    return default


def _scale_score(value: float, points: list[tuple[float, int]]) -> int:
    if value <= points[0][0]:
        return points[0][1]
    for (left_value, left_score), (right_value, right_score) in zip(points, points[1:]):
        if left_value <= value <= right_value:
            ratio = (value - left_value) / (right_value - left_value)
            return round(left_score + ratio * (right_score - left_score))
    return points[-1][1]


def _write_mp3_bpm(path: Path, bpm: float, bpm_round: str = "1dp") -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("TBPM")
    tags.add(TBPM(encoding=3, text=[_format_bpm(bpm, bpm_round=bpm_round)]))
    tags.save(path)


def _write_mp4_bpm(audio: MP4, bpm: float, bpm_round: str = "1dp") -> None:
    if audio.tags is None:
        audio.add_tags()
    text = _format_bpm(bpm, bpm_round=bpm_round)
    audio.tags["tmpo"] = [round(bpm)]
    audio.tags["----:com.apple.iTunes:BPM"] = [MP4FreeForm(text.encode("utf-8"))]


def _write_mp3_key(path: Path, key: str) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("TKEY")
    tags.add(TKEY(encoding=3, text=[key]))
    tags.save(path)


def _write_mp4_key(audio: MP4, key: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    value = MP4FreeForm(key.encode("utf-8"))
    audio.tags["----:com.apple.iTunes:INITIALKEY"] = [value]
    audio.tags["----:com.apple.iTunes:KEY"] = [MP4FreeForm(key.encode("utf-8"))]


def _format_bpm(bpm: float, bpm_round: str = "1dp") -> str:
    rounded = round_bpm(bpm, bpm_round)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"
