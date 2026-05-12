from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .audio import Track, audio_files, get_tag, read_tracks
from .config import APP_USER_AGENT
from .cover_providers import ProviderAttempt, fetch_cover_with_providers
from .cover_providers import validate_image_bytes as provider_validate_image_bytes

MAX_IMAGE_BYTES = 10 * 1024 * 1024
COVER_ART_ARCHIVE_URL = "https://coverartarchive.org/release"
USER_AGENT = f"{APP_USER_AGENT} (Noqlen Forge Core metadata tool)"
LOCAL_COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg", "folder.png", "front.jpg", "front.jpeg", "front.png")


@dataclass(slots=True)
class ImageInfo:
    data: bytes
    mime: str
    extension: str
    width: int | None = None
    height: int | None = None


@dataclass(slots=True)
class CoverResult:
    tracks: list[Track]
    embedded_existing: int
    local_cover: Path | None
    image: ImageInfo | None
    source: str
    provider: str = ""
    confidence: str = ""
    match_reason: str = ""
    written: int = 0
    existing_after: int = 0
    saved_path: Path | None = None
    embed_cover: bool = True
    save_folder_cover: bool = False
    removed_paths: list[Path] = field(default_factory=list)
    remove_candidates: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provider_attempts: list[ProviderAttempt] = field(default_factory=list)
    debug_lines: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.tracks)

    @property
    def status(self) -> str:
        if self.errors and self.written == 0 and self.existing_after < self.total:
            return "WARN"
        return "OK" if self.total and self.existing_after == self.total else "WARN"


def find_audio_files(path: Path) -> list[Path]:
    return audio_files(path)


def album_dir(path: Path) -> Path:
    return path.parent if path.is_file() else path


def detect_embedded_cover(path: Path) -> bool:
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            return bool(ID3(path).getall("APIC"))
        audio = MutagenFile(path, easy=False)
    except (ID3NoHeaderError, Exception):
        return False
    if audio is None or audio.tags is None:
        return False
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        return bool(audio.tags.get("covr"))
    if isinstance(audio, FLAC) or suffix == ".flac":
        return bool(getattr(audio, "pictures", []))
    if isinstance(audio, (OggVorbis, OggOpus)) or suffix in {".ogg", ".opus"}:
        return bool(audio.tags.get("metadata_block_picture"))
    return False


def detect_local_cover(target_dir: Path) -> Path | None:
    for name in LOCAL_COVER_NAMES:
        path = target_dir / name
        if path.is_file() and validate_image_bytes(path.read_bytes()) is not None:
            return path
    return None


def validate_image_bytes(data: bytes, max_bytes: int = MAX_IMAGE_BYTES) -> ImageInfo | None:
    info = provider_validate_image_bytes(data, max_bytes=max_bytes)
    if info is None:
        return None
    return ImageInfo(data=info.data, mime=info.mime, extension=info.extension, width=info.width, height=info.height)


def load_local_cover(path: Path) -> ImageInfo | None:
    return validate_image_bytes(path.read_bytes())


def fetch_cover_from_musicbrainz(release_id: str, debug: bool = False) -> tuple[ImageInfo | None, list[str]]:
    debug_lines: list[str] = []
    url = f"{COVER_ART_ARCHIVE_URL}/{urllib.parse.quote(release_id)}"
    if debug:
        debug_lines.append(f"CAA URL: {url}")
        debug_lines.append(f"MusicBrainz Album Id: {release_id}")
    try:
        payload = _get_bytes(url, accept="application/json")
        data = json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if debug:
            debug_lines.append(f"CAA response: HTTP {exc.code}")
        return None, debug_lines
    except Exception as exc:
        if debug:
            debug_lines.append(f"CAA response rejected: {exc}")
        return None, debug_lines
    images = data.get("images") if isinstance(data, dict) else []
    if debug:
        debug_lines.append(f"CAA images: {len(images or [])}")
    for image in _preferred_images(images or []):
        image_url = _image_url(image)
        if not image_url:
            continue
        if debug:
            debug_lines.append(f"CAA image URL: {image_url}")
        try:
            info = validate_image_bytes(_get_bytes(image_url, accept="image/*"))
        except Exception as exc:
            if debug:
                debug_lines.append(f"CAA image rejected: {exc}")
            continue
        if info is not None:
            return info, debug_lines
        if debug:
            debug_lines.append("CAA image rejected: invalid image bytes")
    return None, debug_lines


def write_cover(path: Path, data: bytes, mime: str, force: bool = False) -> None:
    suffix = path.suffix.lower()
    if detect_embedded_cover(path) and not force:
        return
    if suffix == ".mp3":
        _write_mp3_cover(path, data, mime)
        return
    audio = MutagenFile(path, easy=False)
    if audio is None:
        raise ValueError("unsupported or unreadable audio file")
    if isinstance(audio, MP4) or suffix in {".m4a", ".mp4", ".aac"}:
        _write_mp4_cover(audio, data, mime)
    elif isinstance(audio, FLAC) or suffix == ".flac":
        _write_flac_cover(audio, data, mime)
    elif isinstance(audio, (OggVorbis, OggOpus)) or suffix in {".ogg", ".opus"}:
        _write_vorbis_cover(audio, data, mime)
    else:
        raise ValueError("unsupported cover format")
    audio.save()


def save_folder_cover_file(target_dir: Path, data: bytes, mime: str, filename: str = "cover") -> Path:
    extension = "png" if mime == "image/png" else "jpg"
    name = Path(filename).name or "cover"
    path = target_dir / (name if Path(name).suffix else f"{name}.{extension}")
    path.write_bytes(data)
    return path


def cover_path(path: Path, apply: bool = False, force: bool = False, embed_cover: bool = True, save_folder_cover: bool = False, folder_cover_filename: str = "cover", force_folder_cover: bool = False, remove_folder_cover: bool = False, sources: list[str] | None = None, min_confidence: str = "medium", prefer_front: bool = True, max_size_mb: int = 10, verbose: bool = False, debug: bool = False) -> tuple[int, str]:
    tracks = read_tracks(path)
    if not tracks:
        return 1, "No supported audio files found"
    result = process_cover(path, tracks=tracks, apply=apply, force=force, embed_cover=embed_cover, save_folder_cover=save_folder_cover, folder_cover_filename=folder_cover_filename, force_folder_cover=force_folder_cover, remove_folder_cover=remove_folder_cover, sources=sources, min_confidence=min_confidence, prefer_front=prefer_front, max_size_mb=max_size_mb, debug=debug)
    return 0, render_cover_result(result, apply=apply, force=force, folder_cover_filename=folder_cover_filename, force_folder_cover=force_folder_cover, remove_folder_cover=remove_folder_cover, verbose=verbose, debug=debug)


def process_cover(path: Path, tracks: list[Track] | None = None, apply: bool = False, force: bool = False, embed_cover: bool = True, save_folder_cover: bool = False, folder_cover_filename: str = "cover", force_folder_cover: bool = False, remove_folder_cover: bool = False, sources: list[str] | None = None, min_confidence: str = "medium", prefer_front: bool = True, max_size_mb: int = 10, debug: bool = False) -> CoverResult:
    tracks = tracks or read_tracks(path)
    enabled_sources = list(sources or ["local", "musicbrainz", "itunes", "deezer"])
    target_dir = album_dir(path)
    embedded = sum(1 for track in tracks if detect_embedded_cover(track.path))
    local = detect_local_cover(target_dir)
    image: ImageInfo | None = None
    source = ""
    provider = ""
    confidence = ""
    match_reason = ""
    provider_attempts: list[ProviderAttempt] = []
    debug_lines: list[str] = []
    folder_requested = save_folder_cover or force_folder_cover
    remove_candidates = known_folder_covers(target_dir) if remove_folder_cover else []
    if debug:
        debug_lines.append(f"Target dir: {target_dir}")
    if embedded == len(tracks) and not force and not folder_requested and not remove_folder_cover:
        return CoverResult(tracks=tracks, embedded_existing=embedded, local_cover=local, image=image, source=source or "embedded cover", provider="embedded", confidence="high", match_reason="embedded cover already present", existing_after=embedded, embed_cover=embed_cover, save_folder_cover=save_folder_cover, debug_lines=debug_lines)
    for enabled_source in enabled_sources:
        if enabled_source == "musicbrainz":
            release_id = _common_release_id(tracks)
            if not release_id:
                provider_attempts.append(ProviderAttempt("musicbrainz", "SKIP", "MusicBrainz Album Id missing"))
                continue
            fetched_image, fetched_debug = fetch_cover_from_musicbrainz(release_id, debug=debug)
            provider_attempts.append(ProviderAttempt("musicbrainz", "OK" if fetched_image else "WARN", "release MBID match" if fetched_image else "no valid front cover image", fetched_debug))
            debug_lines.extend(fetched_debug)
            if fetched_image is None:
                continue
            image = fetched_image
            source = "Cover Art Archive"
            provider = "musicbrainz"
            confidence = "high"
            match_reason = "release MBID match"
            break
        fetched, attempts = fetch_cover_with_providers(tracks, target_dir, [enabled_source], min_confidence=min_confidence, prefer_front=prefer_front, max_size_mb=max_size_mb, debug=debug)
        provider_attempts.extend(attempts)
        if fetched is None:
            continue
        image = ImageInfo(fetched.data, fetched.mime, "png" if fetched.mime == "image/png" else "jpg", fetched.width, fetched.height)
        source = fetched.source
        provider = fetched.provider
        confidence = fetched.confidence
        match_reason = fetched.match_reason
        break
    debug_lines.extend(line for attempt in provider_attempts for line in attempt.debug)
    result = CoverResult(tracks=tracks, embedded_existing=embedded, local_cover=local, image=image, source=source, provider=provider, confidence=confidence, match_reason=match_reason, existing_after=embedded, embed_cover=embed_cover, save_folder_cover=save_folder_cover, remove_candidates=remove_candidates, provider_attempts=provider_attempts, debug_lines=debug_lines)
    if apply and remove_folder_cover:
        for candidate in remove_candidates:
            try:
                candidate.unlink()
                result.removed_paths.append(candidate)
            except Exception as exc:
                result.errors.append(f"remove {candidate.name}: {exc}")
    if image is None:
        return result
    targets = [track for track in tracks if embed_cover and (force or not detect_embedded_cover(track.path))]
    if apply:
        if folder_requested:
            try:
                if local is None or force_folder_cover:
                    result.saved_path = save_folder_cover_file(target_dir, image.data, image.mime, filename=folder_cover_filename)
                else:
                    result.saved_path = local
            except Exception as exc:
                result.errors.append(f"folder cover: {exc}")
        for track in targets:
            try:
                write_cover(track.path, image.data, image.mime, force=force)
                result.written += 1
            except Exception as exc:
                result.errors.append(f"{track.path.name}: {exc}")
        result.existing_after = sum(1 for track in tracks if detect_embedded_cover(track.path))
    return result


def render_cover_result(result: CoverResult, apply: bool, force: bool = False, folder_cover_filename: str = "cover", force_folder_cover: bool = False, remove_folder_cover: bool = False, verbose: bool = False, debug: bool = False) -> str:
    total = result.total
    artist = _common_track_value(result.tracks, "albumartist") or _common_track_value(result.tracks, "artist")
    album = _common_track_value(result.tracks, "album")
    lines = []
    if artist or album:
        lines.append(f"Album: {artist} - {album}" if artist and album else f"Album: {album or artist}")
    lines.append(f"Files: {total}")
    lines.append(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")
    lines.append("")
    local_count = 1 if result.local_cover else 0
    lines.append(_stage_line(1, "Detect existing cover", "OK", f"embedded {result.embedded_existing}/{total}, local {local_count}"))
    folder_requested = result.save_folder_cover or force_folder_cover
    if result.embedded_existing == total and not force and not folder_requested and not remove_folder_cover:
        lines.append(_stage_line(2, "Providers", "SKIP", "embedded cover already present"))
        lines.append(_stage_line(3, "Validate image", "SKIP", "embedded cover already present"))
        lines.append(_stage_line(4, "Apply cover", "SKIP", f"embedded cover already present {total}/{total}"))
    elif result.image is None:
        lines.append(_stage_line(2, "Providers", "WARN", "no cover found"))
        lines.append(_stage_line(3, "Validate image", "SKIP", "no valid image"))
        lines.append(_stage_line(4, "Apply cover", "SKIP", "no valid image"))
    else:
        lines.append(_stage_line(2, "Providers", "OK", f"{result.provider or result.source} {result.confidence or 'medium'} confidence"))
        dimensions = f", {result.image.width}x{result.image.height}" if result.image.width and result.image.height else ""
        lines.append(_stage_line(3, "Validate image", "OK", f"{result.image.mime}{dimensions}"))
        if apply:
            existing = max(0, result.existing_after - result.written)
            summary = f"embedded {result.written}/{total}" if result.embed_cover else "embedded skipped"
            if existing:
                summary += f", existing {existing}/{total}"
            if result.saved_path:
                summary += f", saved {result.saved_path.name}"
            elif not folder_requested:
                summary += ", folder cover skipped"
            lines.append(_stage_line(4, "Apply cover", "OK" if not result.errors else "WARN", summary))
        else:
            write_count = total if force else total - result.embedded_existing
            embedded_summary = f"would write embedded {write_count}/{total}" if result.embed_cover else "embedded skipped"
            if folder_requested:
                cover_name = _folder_cover_output_name(result.image, result.local_cover, folder_cover_filename, force_folder_cover)
                folder_summary = f"save {cover_name}"
            else:
                folder_summary = "folder cover skipped"
            lines.append(_stage_line(4, "Apply cover", "DRY", f"{embedded_summary}, {folder_summary}"))
    if remove_folder_cover:
        remove_count = len(result.removed_paths) if apply else len(result.remove_candidates)
        verb = "removed" if apply else "would remove"
        lines.append(_stage_line(4, "Remove folder cover", "OK" if apply else "DRY", f"{verb} {remove_count}"))
    if verbose and result.image is not None:
        lines.append("")
        lines.append(f"Source: {result.source}")
        lines.append(f"Provider: {result.provider}")
        lines.append(f"Confidence: {result.confidence}")
        if result.match_reason:
            lines.append(f"Reason: {result.match_reason}")
        lines.append(f"Image: {result.image.mime}, {len(result.image.data)} bytes")
        if result.saved_path:
            lines.append(f"Saved: {result.saved_path}")
        if result.removed_paths:
            lines.append("Removed folder covers:")
            lines.extend(f"- {path}" for path in result.removed_paths)
        modified = [track.path for track in result.tracks if apply and detect_embedded_cover(track.path)]
        if modified:
            lines.append("Files modified:")
            lines.extend(f"- {path}" for path in modified)
    if debug and result.debug_lines:
        lines.append("")
        lines.append("Debug:")
        lines.extend(f"- {line}" for line in result.debug_lines)
    if (verbose or debug) and result.provider_attempts:
        lines.append("")
        lines.append("Provider Attempts:")
        lines.extend(f"- {attempt.provider}: {attempt.status} {attempt.message}" for attempt in result.provider_attempts)
    local_final = result.saved_path.name if result.saved_path else _folder_cover_final(result, folder_requested)
    embedded_final = result.existing_after if apply else result.embedded_existing
    final_status = "OK" if embedded_final == total else "WARN"
    if result.errors and embedded_final < total:
        final_status = "WARN"
    lines.append("")
    warnings = _cover_warnings(result, embedded_final, total)
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    if result.removed_paths:
        lines.append(f"Folder covers removed: {len(result.removed_paths)}")
    lines.append("Final:")
    lines.append(f"Cover: {embedded_final}/{total} embedded")
    if result.provider:
        lines.append(f"Provider: {result.provider}")
    if result.confidence:
        lines.append(f"Confidence: {result.confidence}")
    lines.append(f"Folder Cover: {local_final}")
    lines.append(f"Status: {final_status}")
    return "\n".join(lines)


def cover_count(tracks: list[Track]) -> int:
    count = 0
    for track in tracks:
        if get_tag(track, "cover") or detect_embedded_cover(track.path):
            count += 1
    return count


def local_cover_status(tracks: list[Track], save_folder_cover: bool = False) -> str:
    dirs = {track.path.parent for track in tracks}
    if any(detect_local_cover(directory) for directory in dirs):
        return "found"
    return "missing" if save_folder_cover else "skipped"


def known_folder_covers(target_dir: Path) -> list[Path]:
    return [target_dir / name for name in LOCAL_COVER_NAMES if (target_dir / name).is_file()]


def _folder_cover_output_name(image: ImageInfo, local_cover: Path | None, filename: str, force_folder_cover: bool) -> str:
    if local_cover is not None and not force_folder_cover:
        return local_cover.name
    name = Path(filename).name or "cover"
    return name if Path(name).suffix else f"{name}.{image.extension}"


def _folder_cover_final(result: CoverResult, folder_requested: bool) -> str:
    if result.local_cover and not result.removed_paths:
        return "found"
    if folder_requested:
        return "missing"
    return "skipped"


def _cover_summary_line(status: str, embedded: int, total: int, folder_status: str) -> str:
    if folder_status.endswith((".jpg", ".jpeg", ".png")):
        folder = f"saved {folder_status}"
    elif folder_status == "found":
        folder = "folder cover found"
    elif folder_status == "missing":
        folder = "folder cover missing"
    else:
        folder = "folder cover skipped"
    return f"Cover {status} embedded {embedded}/{total}, {folder}"


def _cover_warnings(result: CoverResult, embedded: int, total: int) -> list[str]:
    warnings = list(result.errors)
    warnings.extend(f"{attempt.provider}: {attempt.message}" for attempt in result.provider_attempts if attempt.status == "WARN")
    if total and embedded < total:
        warnings.append(f"Cover missing: {total - embedded}/{total} embedded")
    if result.image is None and result.embedded_existing < total:
        if result.local_cover is None and not _common_release_id(result.tracks):
            warnings.append("MusicBrainz Album Id missing: cannot fetch cover from Cover Art Archive")
        warnings.append("No cover found from local files or configured sources")
    return warnings


def _write_mp3_cover(path: Path, data: bytes, mime: str) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("APIC")
    tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    tags.save(path)


def _write_mp4_cover(audio: MP4, data: bytes, mime: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    imageformat = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
    audio.tags["covr"] = [MP4Cover(data, imageformat=imageformat)]


def _write_flac_cover(audio: FLAC, data: bytes, mime: str) -> None:
    audio.clear_pictures()
    audio.add_picture(_picture(data, mime))


def _write_vorbis_cover(audio: OggVorbis | OggOpus, data: bytes, mime: str) -> None:
    audio["metadata_block_picture"] = [base64.b64encode(_picture(data, mime).write()).decode("ascii")]


def _picture(data: bytes, mime: str) -> Picture:
    picture = Picture()
    picture.type = 3
    picture.mime = mime
    picture.desc = "Cover"
    picture.data = data
    return picture


def _preferred_images(images: list[dict]) -> list[dict]:
    front = [image for image in images if image.get("front") or "Front" in (image.get("types") or [])]
    return front + [image for image in images if image not in front]


def _image_url(image: dict) -> str:
    thumbnails = image.get("thumbnails") or {}
    return str(image.get("image") or thumbnails.get("large") or thumbnails.get("small") or "")


def _get_bytes(url: str, accept: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(MAX_IMAGE_BYTES + 1)


def _common_release_id(tracks: list[Track]) -> str:
    values = [value for track in tracks for value in get_tag(track, "mb_album_id")]
    if not values:
        return ""
    return max(set(values), key=values.count)


def _common_track_value(tracks: list[Track], attr: str) -> str:
    values = [getattr(track, attr, "") for track in tracks if getattr(track, attr, "")]
    return max(set(values), key=values.count) if values else ""


def _stage_line(index: int, name: str, status: str, summary: str) -> str:
    total = max(index, 4)
    return f"[{index}/{total}] {name:<25} {status:<6} {summary}".rstrip()
