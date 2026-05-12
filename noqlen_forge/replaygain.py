from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .audio import Track, audio_files, get_tag, read_track, read_tracks
from .safety import automated_validation_enabled, require_lab_path_for_automated_apply

ProgressCallback = Callable[[int, int], None]


@dataclass(slots=True)
class LoudnessAnalysis:
    path: Path
    lufs: float
    true_peak_db: float
    peak: float
    duration: float = 0.0


@dataclass(slots=True)
class ReplayGainPlan:
    path: Path
    track_gain: float | None = None
    track_peak: float | None = None
    album_gain: float | None = None
    album_peak: float | None = None
    loudness: float | None = None
    changes: dict[str, str] | None = None
    skipped: str = ""
    error: str = ""


def replaygain_path(
    path: Path,
    apply: bool = False,
    force: bool = False,
    album: bool = True,
    tracks: bool = True,
    target_lufs: float = -18.0,
    write_track_gain: bool = True,
    write_track_peak: bool = True,
    write_album_gain: bool = True,
    write_album_peak: bool = True,
    write_loudness: bool = True,
    skip_existing: bool = True,
    verbose: bool = False,
    debug: bool = False,
    progress: ProgressCallback | None = None,
) -> tuple[int, str]:
    if apply and automated_validation_enabled():
        require_lab_path_for_automated_apply(path, context="replaygain")
    files = audio_files(path)
    if not files:
        return 1, "No supported audio files found"
    if shutil.which("ffmpeg") is None:
        return 0, "ReplayGain: skipped, ffmpeg not found. Install ffmpeg to enable loudness analysis."

    plans: list[ReplayGainPlan] = []
    analyses: list[LoudnessAnalysis] = []
    total = len(files)
    for index, file_path in enumerate(files, start=1):
        track = read_track(file_path)
        if skip_existing and not force and _has_requested_existing(track, album=album, tracks=tracks, write_loudness=write_loudness):
            plans.append(ReplayGainPlan(path=file_path, skipped="existing ReplayGain/loudness tags"))
            if progress:
                progress(index, total)
            continue
        analysis, error = analyze_loudness_ffmpeg(file_path)
        if error:
            plans.append(ReplayGainPlan(path=file_path, error=error))
            if progress:
                progress(index, total)
            continue
        assert analysis is not None
        analyses.append(analysis)
        plans.append(ReplayGainPlan(path=file_path, track_gain=target_lufs - analysis.lufs, track_peak=analysis.peak, loudness=analysis.lufs))
        if progress:
            progress(index, total)

    album_gain: float | None = None
    album_peak: float | None = None
    if album and analyses:
        album_lufs = integrated_album_lufs(analyses)
        album_gain = target_lufs - album_lufs
        album_peak = max(item.peak for item in analyses)

    written = 0
    for plan in plans:
        if plan.skipped or plan.error:
            continue
        changes = _changes_for_plan(
            plan,
            track=read_track(plan.path),
            force=force,
            album=album,
            tracks=tracks,
            write_track_gain=write_track_gain,
            write_track_peak=write_track_peak,
            write_album_gain=write_album_gain,
            write_album_peak=write_album_peak,
            write_loudness=write_loudness,
            album_gain=album_gain,
            album_peak=album_peak,
        )
        plan.album_gain = album_gain
        plan.album_peak = album_peak
        plan.changes = changes
        if apply and changes:
            write_replaygain_tags(plan.path, changes)
            written += 1

    return (1 if any(plan.error for plan in plans) else 0), summarize_replaygain(plans, apply=apply, written=written, verbose=verbose, debug=debug)


def analyze_loudness_ffmpeg(path: Path) -> tuple[LoudnessAnalysis | None, str]:
    command = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "loudnorm=I=-18:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        return None, str(exc)
    output = result.stderr + "\n" + result.stdout
    data = parse_loudnorm_json(output)
    if data is None:
        data = parse_ebur128_summary(output)
    if data is None:
        data = analyze_volumedetect_ffmpeg(path)
    if data is None:
        return None, _first_error_line(output) or f"ffmpeg exited with {result.returncode}"
    duration = _probe_duration(path)
    return LoudnessAnalysis(path=path, lufs=data[0], true_peak_db=data[1], peak=db_to_peak(data[1]), duration=duration), ""


def parse_loudnorm_json(output: str) -> tuple[float, float] | None:
    match = re.search(r"\{\s*\"input_i\".*?\}", output, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        lufs = float(data["input_i"])
        true_peak = float(data.get("input_tp", data.get("target_tp", 0)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not math.isfinite(lufs) or not math.isfinite(true_peak):
        return None
    return lufs, true_peak


def parse_ebur128_summary(output: str) -> tuple[float, float] | None:
    integrated = re.search(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", output)
    peak = re.search(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", output)
    if not integrated:
        return None
    try:
        return float(integrated.group(1)), float(peak.group(1)) if peak else 0.0
    except ValueError:
        return None


def analyze_volumedetect_ffmpeg(path: Path) -> tuple[float, float] | None:
    command = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        return None
    output = result.stderr + "\n" + result.stdout
    mean = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", output)
    peak = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", output)
    if not mean:
        return None
    try:
        return float(mean.group(1)), float(peak.group(1)) if peak else 0.0
    except ValueError:
        return None


def integrated_album_lufs(analyses: list[LoudnessAnalysis]) -> float:
    weighted = 0.0
    total_duration = 0.0
    for item in analyses:
        duration = item.duration if item.duration > 0 else 1.0
        weighted += duration * (10 ** (item.lufs / 10))
        total_duration += duration
    if total_duration <= 0 or weighted <= 0:
        return analyses[0].lufs if analyses else -18.0
    return 10 * math.log10(weighted / total_duration)


def db_to_peak(value: float) -> float:
    return max(0.0, 10 ** (value / 20))


def write_replaygain_tags(path: Path, changes: dict[str, str]) -> None:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        for key, value in changes.items():
            tags.delall(f"TXXX:{key}")
            tags.add(TXXX(encoding=3, desc=key, text=[value]))
        tags.save(path)
        return
    audio = MutagenFile(path, easy=False)
    if audio is None:
        return
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        if audio.tags is None:
            audio.add_tags()
        for key, value in changes.items():
            audio.tags[f"----:com.apple.iTunes:{key}"] = [MP4FreeForm(value.encode("utf-8"))]
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        for key, value in changes.items():
            audio[key] = [value]
    audio.save()


def summarize_replaygain(plans: list[ReplayGainPlan], apply: bool, written: int = 0, verbose: bool = False, debug: bool = False) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    total = len(plans)
    analyzed = sum(1 for plan in plans if not plan.skipped and not plan.error)
    existing = sum(1 for plan in plans if plan.skipped)
    track_count = existing + sum(1 for plan in plans if plan.track_gain is not None and plan.track_peak is not None)
    album_count = existing + sum(1 for plan in plans if plan.album_gain is not None and plan.album_peak is not None)
    loudness_count = existing + sum(1 for plan in plans if plan.loudness is not None)
    changed = sum(1 for plan in plans if plan.changes)
    skipped = existing
    errors = [plan for plan in plans if plan.error]
    lines = [f"Files: {total}", f"Mode: {mode}", ""]
    lines.append(f"[1/3] Analyze loudness     {'OK' if not errors else 'WARN':<6} {analyzed}/{total} tracks")
    lines.append(f"[2/3] Compute ReplayGain   {'OK' if analyzed else 'SKIP':<6} track {track_count}/{total}, album {album_count}/{total}")
    action = "wrote" if apply else "would write"
    status = "OK" if apply else "DRY"
    lines.append(f"[3/3] Apply tags           {status:<6} {action} ReplayGain {written if apply else changed}/{total}")
    if skipped:
        lines.append(f"      Existing tags        OK     skipped {skipped}")
    if verbose or debug:
        for plan in plans:
            if plan.skipped:
                lines.append(f"- {plan.path}: skipped {plan.skipped}")
            elif plan.error:
                lines.append(f"- {plan.path}: error {plan.error}")
            else:
                fields = ", ".join(sorted(plan.changes or {})) or "no changed fields"
                lines.append(f"- {plan.path}: LUFS={_format_float(plan.loudness)} track_gain={_format_gain_number(plan.track_gain)} album_gain={_format_gain_number(plan.album_gain)} fields={fields}")
    lines.extend(["", f"ReplayGain Track: {track_count}/{total}", f"ReplayGain Album: {album_count}/{total}", f"Loudness: {loudness_count}/{total}", f"Status: {'WARN' if errors else 'OK'}"])
    return "\n".join(lines)


def _changes_for_plan(
    plan: ReplayGainPlan,
    track: Track,
    force: bool,
    album: bool,
    tracks: bool,
    write_track_gain: bool,
    write_track_peak: bool,
    write_album_gain: bool,
    write_album_peak: bool,
    write_loudness: bool,
    album_gain: float | None,
    album_peak: float | None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    if tracks and write_track_gain and plan.track_gain is not None:
        values["REPLAYGAIN_TRACK_GAIN"] = _format_gain(plan.track_gain)
    if tracks and write_track_peak and plan.track_peak is not None:
        values["REPLAYGAIN_TRACK_PEAK"] = _format_peak(plan.track_peak)
    if album and write_album_gain and album_gain is not None:
        values["REPLAYGAIN_ALBUM_GAIN"] = _format_gain(album_gain)
    if album and write_album_peak and album_peak is not None:
        values["REPLAYGAIN_ALBUM_PEAK"] = _format_peak(album_peak)
    if write_loudness and plan.loudness is not None:
        values["LOUDNESS"] = _format_lufs(plan.loudness)
    return {key: value for key, value in values.items() if force or _first_existing(track, key) != value}


def _has_requested_existing(track: Track, album: bool, tracks: bool, write_loudness: bool) -> bool:
    required: list[str] = []
    if tracks:
        required.extend(["replaygain_track_gain", "replaygain_track_peak"])
    if album:
        required.extend(["replaygain_album_gain", "replaygain_album_peak"])
    if write_loudness:
        required.append("loudness")
    return bool(required) and all(get_tag(track, field) for field in required)


def _first_existing(track: Track, tag_name: str) -> str:
    logical = {
        "REPLAYGAIN_TRACK_GAIN": "replaygain_track_gain",
        "REPLAYGAIN_TRACK_PEAK": "replaygain_track_peak",
        "REPLAYGAIN_ALBUM_GAIN": "replaygain_album_gain",
        "REPLAYGAIN_ALBUM_PEAK": "replaygain_album_peak",
        "LOUDNESS": "loudness",
    }[tag_name]
    values = get_tag(track, logical)
    return values[0] if values else ""


def _probe_duration(path: Path) -> float:
    try:
        track = read_track(path)
    except Exception:
        return 0.0
    return float(track.duration or 0.0)


def _first_error_line(output: str) -> str:
    for line in output.splitlines():
        clean = line.strip()
        if clean and ("error" in clean.lower() or "invalid" in clean.lower()):
            return clean
    return ""


def _format_gain(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f} dB"


def _format_gain_number(value: float | None) -> str:
    return "unknown" if value is None else _format_gain(value)


def _format_peak(value: float) -> str:
    return f"{value:.6f}"


def _format_lufs(value: float) -> str:
    return f"{value:.2f} LUFS"


def _format_float(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.2f}"
