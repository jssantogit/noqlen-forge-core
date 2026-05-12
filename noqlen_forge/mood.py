from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .audio import audio_files, get_tag, read_track
from .lastfm import fetch_best_lastfm_tags_debug
from .lastfm_filter import TagContext, clean_existing_lastfm_tags, filter_tags

TAG_SEPARATOR = "; "

MOOD_TAG_MAP = {
    "energetic": ["Energetic"],
    "dance": ["Energetic"],
    "dance-pop": ["Energetic"],
    "jersey club": ["Energetic"],
    "upbeat": ["Energetic"],
    "party": ["Energetic"],
    "dreamy": ["Dreamy", "Chill"],
    "atmospheric": ["Dreamy", "Chill"],
    "ambient": ["Dreamy", "Chill"],
    "chill": ["Dreamy", "Chill"],
    "relaxing": ["Dreamy", "Chill"],
    "melancholic": ["Melancholic", "Dark"],
    "sad": ["Melancholic", "Dark"],
    "dark": ["Melancholic", "Dark"],
    "aggressive": ["Aggressive", "Intense"],
    "metal": ["Aggressive", "Intense"],
    "death metal": ["Aggressive", "Intense"],
    "technical death metal": ["Aggressive", "Intense"],
    "brutal death metal": ["Aggressive", "Intense"],
    "happy": ["Happy"],
}


@dataclass(slots=True)
class MoodAnalysis:
    raw_tags: list[str] = field(default_factory=list)
    moods: list[str] = field(default_factory=list)
    confidence: str = "low"
    strong_tags: list[str] = field(default_factory=list)
    note: str = ""


@dataclass(slots=True)
class MoodPlan:
    path: Path
    analysis: MoodAnalysis | None = None
    existing: str = ""
    skipped: str = ""


def analyze_mood_path(path: Path, apply: bool = False, force: bool = False, with_lastfm: bool = False) -> tuple[int, str]:
    files = audio_files(path)
    if not files:
        return 1, "No supported audio files found"
    plans: list[MoodPlan] = []
    for file_path in files:
        track = read_track(file_path)
        existing = TAG_SEPARATOR.join(normalize_moods(get_tag(track, "mood")))
        if existing and not force:
            plans.append(MoodPlan(path=file_path, existing=existing, skipped=f"skipped existing MOOD={existing}"))
            continue
        lastfm_tags = get_tag(track, "lastfm_tags")
        if not lastfm_tags and with_lastfm:
            lastfm_tags = _calculated_lastfm_tags(track)
        context = TagContext(artist=track.artist, albumartist=track.albumartist, album=track.album, title=track.title)
        cleaned_lastfm = clean_existing_lastfm_tags(lastfm_tags, context)
        analysis = infer_mood([cleaned_lastfm] if cleaned_lastfm else [], bpm=_first_float(get_tag(track, "bpm")), energy=_first_float(get_tag(track, "energy")), danceability=_first_float(get_tag(track, "danceability")))
        plans.append(MoodPlan(path=file_path, analysis=analysis))
        if apply and analysis.moods and analysis.confidence != "low":
            write_mood(file_path, analysis.moods)
    return 0, summarize_mood(plans, apply=apply)


def infer_mood(lastfm_tags: list[str], bpm: float = 0, energy: float = 0, danceability: float = 0) -> MoodAnalysis:
    cleaned = clean_existing_lastfm_tags(lastfm_tags, TagContext())
    raw_tags = _split_tags([cleaned] if cleaned else [])
    mood_values: list[str] = []
    strong_tags: list[str] = []
    for tag in raw_tags:
        mapped = MOOD_TAG_MAP.get(tag.lower())
        if not mapped:
            continue
        strong_tags.append(tag)
        for mood in mapped:
            if mood not in mood_values:
                mood_values.append(mood)
    if not strong_tags:
        note = "no strong mood tags"
        if bpm or energy or danceability:
            note = "audio features are support only"
        return MoodAnalysis(raw_tags=raw_tags, moods=[], confidence="low", strong_tags=[], note=note)
    confidence = "high" if len(strong_tags) >= 2 else "medium"
    if energy >= 80 or danceability >= 80 or bpm >= 140:
        confidence = "high"
    return MoodAnalysis(raw_tags=raw_tags, moods=mood_values, confidence=confidence, strong_tags=strong_tags)


def write_mood(path: Path, moods: list[str]) -> None:
    value = TAG_SEPARATOR.join(normalize_moods(moods))
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _write_mp3_mood(path, value)
        return
    audio = MutagenFile(path, easy=False)
    if audio is None:
        return
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        if audio.tags is None:
            audio.add_tags()
        audio.tags["----:com.apple.iTunes:MOOD"] = [MP4FreeForm(value.encode("utf-8"))]
    elif isinstance(audio, (FLAC, OggVorbis, OggOpus)) or suffix in {".flac", ".ogg", ".opus"}:
        audio["MOOD"] = [value]
    audio.save()


def normalize_moods(values: list[str]) -> list[str]:
    unique: dict[str, str] = {}
    for value in values:
        for part in value.replace(",", ";").split(";"):
            clean = part.strip()
            if clean and clean.lower() not in unique:
                unique[clean.lower()] = clean
    return list(unique.values())


def summarize_mood(plans: list[MoodPlan], apply: bool) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    lines = [f"{mode}: MOOD analysis"]
    if not plans:
        lines.append("- nothing")
    for plan in plans:
        if plan.skipped:
            lines.append(f"- {plan.path}: {plan.skipped}")
            continue
        analysis = plan.analysis or MoodAnalysis()
        value = TAG_SEPARATOR.join(analysis.moods)
        writes = bool(analysis.moods) and analysis.confidence != "low"
        action = "wrote" if apply and writes else "would write" if writes else "skipped"
        raw = TAG_SEPARATOR.join(analysis.raw_tags) if analysis.raw_tags else "none"
        final = value or "none"
        parts = [f"- {plan.path}:", f"raw_tags={raw}", f"mood={final}", f"confidence={analysis.confidence}", f"action={action}"]
        if analysis.note:
            parts.append(f"note={analysis.note}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _write_mp3_mood(path: Path, value: str) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("TXXX:MOOD")
    tags.add(TXXX(encoding=3, desc="MOOD", text=[value]))
    tags.save(path)


def _split_tags(values: list[str]) -> list[str]:
    tags: list[str] = []
    for value in values:
        for part in value.replace(",", ";").split(";"):
            clean = part.strip()
            if clean and clean.lower() not in {tag.lower() for tag in tags}:
                tags.append(clean)
    return tags


def _first_float(values: list[str]) -> float:
    if not values:
        return 0
    try:
        return float(values[0])
    except ValueError:
        return 0


def _calculated_lastfm_tags(track) -> list[str]:
    artist = track.artist or track.albumartist
    mbid = get_tag(track, "mb_track_id")
    result = fetch_best_lastfm_tags_debug(artist, track.title, album=track.album, mbid=mbid[0] if mbid else "")
    context = TagContext(artist=track.artist, albumartist=track.albumartist, album=track.album, title=track.title, source=getattr(result, "source", ""))
    tags = filter_tags(result.tags, context=context, min_count=3, max_tags=10).kept
    return [TAG_SEPARATOR.join(tags)] if tags else []
