from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from .audio import Track, audio_files, get_tag, read_track
from .config import get_config_value
from .db import database_path, record_organized_file
from .safety import SafetyError, automated_validation_enabled, is_dangerous_real_library_path, is_noqlen_forge_lab_path, require_lab_path_for_automated_apply

VALID_MODES = {"copy", "move"}
VALID_CONFLICT_POLICIES = {"review", "skip", "rename"}
FIELDS = {
    "genre",
    "style",
    "albumartist",
    "artist",
    "album",
    "title",
    "track",
    "tracktotal",
    "disc",
    "disctotal",
    "date",
    "originaldate",
    "year",
    "label",
    "release_type",
}


@dataclass(slots=True)
class OrganizeItem:
    source: Path
    destination: Path
    track: Track
    action: str = "write"
    reason: str = ""


@dataclass(slots=True)
class OrganizeResult:
    code: int
    output: str
    copied: int = 0
    moved: int = 0
    skipped: int = 0
    conflicts: int = 0
    status: str = "OK"
    items: list[OrganizeItem] | None = None


def organize_path(
    path: Path,
    config: dict[str, Any],
    apply: bool = False,
    mode: str | None = None,
    library: Path | None = None,
    template: str | None = None,
    singleton_template: str | None = None,
    conflict_policy: str | None = None,
    verbose: bool = False,
    debug: bool = False,
) -> OrganizeResult:
    if not bool(get_config_value(config, "organize", "enabled", True)):
        return OrganizeResult(1, "Organize is disabled in config", status="FAIL")
    active_mode = (mode or str(get_config_value(config, "organize", "mode", "copy"))).strip().casefold()
    active_policy = (conflict_policy or str(get_config_value(config, "organize", "conflict_policy", "review"))).strip().casefold()
    if active_mode not in VALID_MODES:
        return OrganizeResult(1, f"Invalid organize mode: {active_mode}", status="FAIL")
    if active_policy not in VALID_CONFLICT_POLICIES:
        return OrganizeResult(1, f"Invalid conflict policy: {active_policy}", status="FAIL")
    library_root = _library_root(config, library)
    if library_root is None:
        return OrganizeResult(1, "Organize requires a library destination. Set [organize].library_path or pass --library DEST.", status="FAIL")
    try:
        _validate_library_destination(library_root, apply=apply)
    except SafetyError as exc:
        return OrganizeResult(1, str(exc), status="FAIL")

    files = audio_files(path)
    tracks: list[Track] = []
    errors: list[str] = []
    for file_path in files:
        try:
            tracks.append(read_track(file_path))
        except Exception as exc:
            errors.append(f"{file_path}: {exc}")

    selected_template = template or str(get_config_value(config, "organize", "template", "$genre/$albumartist/$album/$track $title"))
    selected_singleton_template = singleton_template or str(get_config_value(config, "organize", "singleton_template", "$genre/$artist/Singles/$title"))
    compilation_template = str(get_config_value(config, "organize", "compilation_template", "Compilations/$album/$track $title"))
    max_length = int(get_config_value(config, "organize", "max_filename_length", 180) or 180)
    ascii_paths = bool(get_config_value(config, "organize", "ascii_paths", False))

    items: list[OrganizeItem] = []
    for track in tracks:
        try:
            relative = build_destination(track, selected_template, selected_singleton_template, compilation_template, max_length=max_length, ascii_paths=ascii_paths, album_file_count=len(tracks))
            destination = _safe_join(library_root, relative)
            if track.path.expanduser().resolve(strict=False) == destination.expanduser().resolve(strict=False):
                items.append(OrganizeItem(track.path, destination, track, action="conflict", reason="destination equals source"))
            else:
                items.append(OrganizeItem(track.path, destination, track))
        except SafetyError as exc:
            items.append(OrganizeItem(track.path, library_root, track, action="conflict", reason=str(exc)))

    _mark_conflicts(items, active_policy)
    if active_policy == "rename":
        _rename_conflicts(items, library_root)
        _mark_conflicts(items, "review", existing_only=False)

    conflicts = sum(1 for item in items if item.action == "conflict")
    skipped = sum(1 for item in items if item.action == "skip")
    writable = [item for item in items if item.action == "write"]
    status = "WARN" if errors else "REVIEW" if conflicts else "OK"
    lines = _render_plan(path, library_root, files, tracks, items, active_mode, apply, errors, conflicts, skipped, status, verbose=verbose, debug=debug)
    if not apply:
        return OrganizeResult(1 if errors or conflicts else 0, "\n".join(lines), skipped=skipped, conflicts=conflicts, status=status, items=items)
    if conflicts or errors:
        return OrganizeResult(1, "\n".join(lines), skipped=skipped, conflicts=conflicts, status=status, items=items)

    copied = 0
    moved = 0
    for item in writable:
        if item.destination.exists():
            item.action = "skip"
            item.reason = "destination already exists"
            skipped += 1
            continue
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        if active_mode == "move":
            shutil.move(str(item.source), str(item.destination))
            moved += 1
        else:
            shutil.copy2(item.source, item.destination)
            copied += 1
        if database_path(config).exists() or bool(get_config_value(config, "database", "auto_scan", False)):
            record_organized_file(config, item.source, item.destination, item.track, active_mode, "OK", f"{active_mode} {item.source} -> {item.destination}")
    status = "OK" if skipped == 0 else "WARN"
    lines = _render_plan(path, library_root, files, tracks, items, active_mode, apply, errors, 0, skipped, status, verbose=verbose, debug=debug, copied=copied, moved=moved)
    return OrganizeResult(0, "\n".join(lines), copied=copied, moved=moved, skipped=skipped, conflicts=0, status=status, items=items)


def build_destination(track: Track, template: str, singleton_template: str, compilation_template: str, max_length: int = 180, ascii_paths: bool = False, album_file_count: int = 1) -> Path:
    selected = _target_template(track, template, singleton_template, compilation_template, album_file_count)
    values = {field: re.sub(r"[\\/]+", " ", _field_value(track, field)) for field in FIELDS}
    rendered = Template(selected).safe_substitute(values)
    raw_parts = [part.strip() for part in re.split(r"[\\/]+", rendered) if part.strip()]
    if Path(rendered).is_absolute() or any(part == ".." for part in raw_parts):
        raise SafetyError("Destination template attempted path traversal")
    parts = [_sanitize_part(part, ascii_paths=ascii_paths, max_length=max_length) for part in re.split(r"[\\/]+", rendered) if part.strip()]
    if not parts:
        raise SafetyError("Destination template rendered an empty path")
    parts[-1] = _sanitize_part(parts[-1] + track.path.suffix.lower(), ascii_paths=ascii_paths, max_length=max_length)
    relative = Path(*parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise SafetyError("Destination template attempted path traversal")
    return relative


def _library_root(config: dict[str, Any], library: Path | None) -> Path | None:
    configured = str(get_config_value(config, "organize", "library_path", "") or "").strip()
    if library is not None:
        return library.expanduser().resolve(strict=False)
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return None


def _validate_library_destination(library: Path, apply: bool) -> None:
    if str(library).strip() == "":
        raise SafetyError("Refusing empty organize destination")
    resolved = library.expanduser().resolve(strict=False)
    if is_dangerous_real_library_path(resolved):
        raise SafetyError(f"Refusing dangerous organize destination: {resolved}")
    if apply and automated_validation_enabled() and not is_noqlen_forge_lab_path(resolved):
        require_lab_path_for_automated_apply(resolved, context="noqlen-forge organize --library")


def _safe_join(root: Path, relative: Path) -> Path:
    destination = (root / relative).resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    if destination != root_resolved and root_resolved not in destination.parents:
        raise SafetyError("Destination escaped the organize library")
    return destination


def _target_template(track: Track, template: str, singleton_template: str, compilation_template: str, album_file_count: int) -> str:
    albumartist = (track.albumartist or "").strip().casefold()
    compilation = albumartist == "various artists" or any(value.strip().casefold() in {"1", "true", "yes"} for value in track.tags.get("compilation", []))
    if compilation:
        return compilation_template
    release_type = _first(track, "release_type").casefold()
    if album_file_count == 1 or release_type == "single":
        return singleton_template
    return template


def _field_value(track: Track, field: str) -> str:
    if field == "albumartist":
        return track.albumartist or track.artist or "Unknown"
    if field == "artist":
        return track.artist or track.albumartist or "Unknown"
    if field == "album":
        return track.album or "Unknown"
    if field == "title":
        return track.title or track.path.stem or "Unknown"
    if field == "track":
        return f"{track.tracknumber or 0:02d}" if track.tracknumber else "00"
    if field == "date":
        return track.date or "Unknown"
    if field == "year":
        value = track.date or _first(track, "originaldate")
        match = re.search(r"\d{4}", value or "")
        return match.group(0) if match else "Unknown"
    if field in {"genre", "style", "tracktotal", "disc", "disctotal", "originaldate", "label", "release_type"}:
        return _first(track, field) or "Unknown"
    return "Unknown"


def _first(track: Track, field: str) -> str:
    values = get_tag(track, field)
    return values[0] if values else ""


def _sanitize_part(value: str, ascii_paths: bool, max_length: int) -> str:
    text = unicodedata.normalize("NFKD", value) if ascii_paths else value
    if ascii_paths:
        text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = "Unknown"
    if len(text) > max_length:
        suffix = Path(text).suffix
        stem = text[: max(1, max_length - len(suffix))].rstrip(" .")
        text = stem + suffix
    return text


def _mark_conflicts(items: list[OrganizeItem], policy: str, existing_only: bool = True) -> None:
    seen: dict[Path, OrganizeItem] = {}
    for item in items:
        if item.action != "write":
            continue
        if item.destination.exists():
            if policy == "skip":
                item.action = "skip"
                item.reason = "destination exists"
            elif policy == "rename":
                item.action = "rename"
                item.reason = "destination exists"
            else:
                item.action = "conflict"
                item.reason = "destination exists"
        if existing_only:
            continue
        prior = seen.get(item.destination)
        if prior is not None:
            item.action = "conflict"
            item.reason = "duplicate destination in run"
            prior.action = "conflict"
            prior.reason = "duplicate destination in run"
        seen[item.destination] = item
    if existing_only:
        seen.clear()
        for item in items:
            if item.action != "write":
                continue
            prior = seen.get(item.destination)
            if prior is not None:
                item.action = "conflict"
                item.reason = "duplicate destination in run"
                prior.action = "conflict"
                prior.reason = "duplicate destination in run"
            seen[item.destination] = item


def _rename_conflicts(items: list[OrganizeItem], root: Path) -> None:
    used = {item.destination for item in items if item.action == "write"}
    for item in items:
        if item.action != "rename":
            continue
        candidate = item.destination
        stem = candidate.stem
        suffix = candidate.suffix
        for index in range(1, 1000):
            renamed = candidate.with_name(f"{stem} ({index}){suffix}")
            _safe_join(root, renamed.relative_to(root))
            if renamed not in used and not renamed.exists():
                item.destination = renamed
                item.action = "write"
                item.reason = "renamed conflict"
                used.add(renamed)
                break
        if item.action == "rename":
            item.action = "conflict"
            item.reason = "could not find safe rename"


def _render_plan(path: Path, library: Path, files: list[Path], tracks: list[Track], items: list[OrganizeItem], mode: str, apply: bool, errors: list[str], conflicts: int, skipped: int, status: str, verbose: bool, debug: bool, copied: int = 0, moved: int = 0) -> list[str]:
    mode_label = "APPLY" if apply else "DRY-RUN"
    operation = "move" if mode == "move" else "copy"
    writable = sum(1 for item in items if item.action == "write")
    lines = [f"Organize: {path}", f"Library: {library}", f"Files: {len(files)}", f"Mode: {mode_label}", ""]
    read_status = "OK" if not errors else "WARN"
    path_status = "OK" if len(items) == len(tracks) else "WARN"
    conflict_status = "OK" if conflicts == 0 else "REVIEW"
    organize_status = "DRY" if not apply else "OK" if status in {"OK", "WARN"} else status
    lines.append(f"[1/4] Read tags            {read_status:<7} {len(tracks)}/{len(files)} files")
    lines.append(f"[2/4] Build paths          {path_status:<7} {len(items)} destinations")
    lines.append(f"[3/4] Check conflicts      {conflict_status:<7} {conflicts if conflicts else 'no'} conflicts")
    if apply:
        detail = f"copied {copied} files" if mode == "copy" else f"moved {moved} files"
    else:
        detail = f"would {operation} {writable} files"
    lines.append(f"[4/4] Organize             {organize_status:<7} {detail}")
    if errors and (verbose or debug):
        lines.extend(["", "Errors:", *[f"- {error}" for error in errors]])
    lines.extend(["", "Plan:"])
    for item in items[:50]:
        lines.append(f"- {item.source.name}")
        suffix = f" [{item.action}: {item.reason}]" if item.reason else ""
        lines.append(f"  -> {item.destination.relative_to(library) if _contains(library, item.destination) else item.destination}{suffix}")
    if len(items) > 50:
        lines.append(f"- ... {len(items) - 50} more files")
    lines.extend(["", "Final:"])
    if apply:
        lines.extend([f"Copied: {copied}", f"Moved: {moved}", f"Skipped: {skipped}"])
    else:
        lines.extend([f"Would copy: {writable if mode == 'copy' else 0}", f"Would move: {writable if mode == 'move' else 0}"])
    lines.extend([f"Conflicts: {conflicts}", f"Status: {status}"])
    return lines


def _contains(root: Path, path: Path) -> bool:
    root_resolved = root.resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    return path_resolved == root_resolved or root_resolved in path_resolved.parents
