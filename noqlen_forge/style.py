from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .audio import audio_files, get_tag, read_track
from .cleanup import normalize_style


@dataclass(slots=True)
class StylePlan:
    path: Path
    style: str
    existing: str = ""


def set_style_path(path: Path, style: str, apply: bool, force: bool = False) -> tuple[int, str]:
    normalized = normalize_style([style])
    if not normalized:
        return 1, "No STYLE value provided"
    files = audio_files(path)
    if not files:
        return 1, "No supported audio files found"
    plans: list[StylePlan] = []
    for file_path in files:
        track = read_track(file_path)
        existing = get_tag(track, "style")
        plans.append(StylePlan(path=file_path, style=normalized[0], existing="; ".join(existing) if existing and not force else ""))
    if apply:
        for plan in plans:
            if not plan.existing:
                write_style(plan.path, plan.style)
    return 0, summarize_style(plans, apply=apply)


def summarize_style(plans: list[StylePlan], apply: bool) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    lines = [f"{mode}:"]
    for plan in plans:
        if plan.existing:
            lines.append(f"- {plan.path}: skipped: existing STYLE={plan.existing}")
        else:
            action = "wrote" if apply else "would write"
            lines.append(f"- {plan.path}: {action} STYLE={plan.style}")
    return "\n".join(lines)


def write_style(path: Path, style: str) -> None:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _write_mp3_style(path, style)
        return
    audio = MutagenFile(path, easy=False)
    if audio is None:
        return
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        _write_mp4_style(audio, style)
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        audio["STYLE"] = [style]
    audio.save()


def _write_mp3_style(path: Path, style: str) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("TXXX:STYLE")
    tags.add(TXXX(encoding=3, desc="STYLE", text=[style]))
    tags.save(path)


def _write_mp4_style(audio: MP4, style: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    audio.tags["----:com.apple.iTunes:STYLE"] = [MP4FreeForm(style.encode("utf-8"))]
