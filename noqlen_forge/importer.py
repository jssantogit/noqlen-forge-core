from __future__ import annotations

import io
import shutil
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .audit import audit_path
from .audio import audio_files, read_tracks, target_kind
from .config import get_config_value
from .cover import cover_path
from .db import connect_readonly, normalize_path, record_import_operation, scan_library
from .lyrics import lyrics_path
from .organize import OrganizeItem, organize_path
from .replaygain import replaygain_path
from .safety import SafetyError, automated_validation_enabled, is_dangerous_real_library_path, is_noqlen_forge_lab_path, require_lab_path_for_automated_apply

EnrichRunner = Callable[[Path, bool, bool, bool, bool, bool, bool, bool], int]


@dataclass(slots=True)
class ImportResult:
    code: int
    output: str
    status: str = "OK"
    imported: int = 0
    copied: int = 0
    moved: int = 0
    skipped: int = 0
    conflicts: int = 0


def import_path(
    path: Path,
    config: dict[str, Any],
    apply: bool = False,
    library: Path | None = None,
    mode: str | None = None,
    replaygain: bool = False,
    skip_enrich: bool = False,
    skip_cover: bool = False,
    skip_lyrics: bool = False,
    skip_organize: bool = False,
    allow_review: bool = False,
    force: bool = False,
    verbose: bool = False,
    debug: bool = False,
    enrich_runner: EnrichRunner | None = None,
) -> ImportResult:
    if not bool(get_config_value(config, "import", "enabled", True)):
        return ImportResult(1, "Import is disabled in config", status="FAIL")

    source = path.expanduser().resolve(strict=False)
    library_root = _library_root(config, library)
    active_mode = (mode or str(get_config_value(config, "import", "mode", "copy"))).strip().casefold()
    if active_mode not in {"copy", "move"}:
        return ImportResult(1, f"Invalid import mode: {active_mode}", status="FAIL")
    if library_root is None:
        return ImportResult(1, "Import --apply requires a library destination. Set [import].library_path or pass --library DEST.", status="FAIL")
    try:
        _validate_paths(source, library_root, apply=apply)
    except SafetyError as exc:
        return ImportResult(1, str(exc), status="FAIL")

    files = audio_files(source)
    kind = target_kind(source)
    title = _import_title(source)
    run_enrich = bool(get_config_value(config, "import", "run_enrich", True)) and not skip_enrich
    run_cover = bool(get_config_value(config, "import", "run_cover", True)) and not skip_cover
    run_lyrics = bool(get_config_value(config, "import", "run_lyrics", True)) and not skip_lyrics
    run_replaygain = (bool(get_config_value(config, "import", "run_replaygain", False)) or replaygain)
    run_organize = bool(get_config_value(config, "import", "run_organize", True)) and not skip_organize
    stop_on_review = bool(get_config_value(config, "import", "stop_on_review", True)) and not allow_review
    enrich_has_cover = run_enrich and bool(get_config_value(config, "enrich", "full_includes_cover", False))
    enrich_has_lyrics = run_enrich and bool(get_config_value(config, "enrich", "full_includes_lyrics", False))
    enrich_has_replaygain = run_enrich and bool(get_config_value(config, "enrich", "full_includes_replaygain", False)) and run_replaygain

    lines = [f"Import: {title}", f"Files: {len(files)}", f"Target type: {kind}", f"Mode: {'APPLY' if apply else 'DRY-RUN'}", f"Library: {library_root}", ""]
    if not files:
        lines.append(_step(1, "Discover files", "FAIL", "no supported audio files"))
        lines.extend(["", "Final:", "Status: FAIL"])
        return ImportResult(1, "\n".join(lines), status="FAIL")
    lines.append(_step(1, "Discover files", "OK", f"{len(files)} files"))

    initial = audit_path(source)
    lines.append(_step(2, "Initial audit", initial.status, _audit_detail(initial)))

    if run_enrich:
        if apply and enrich_runner is not None:
            code, enrich_output = _capture_enrich(enrich_runner, source, apply, force, enrich_has_cover, enrich_has_lyrics, enrich_has_replaygain, verbose, debug)
            if code != 0:
                lines.append(_step(3, "Enrich full", "FAIL", _first_line(enrich_output)))
                return _finish(lines, ImportResult(1, "", status="FAIL"))
            lines.append(_step(3, "Enrich full", "OK", "metadata/features updated"))
        else:
            lines.append(_step(3, "Enrich full", "DRY", "would write metadata/features" if not apply else "no runner configured"))
    else:
        lines.append(_step(3, "Enrich full", "SKIP", "disabled"))

    if run_cover and not enrich_has_cover:
        if apply:
            code, cover_output = cover_path(source, apply=True, force=force, sources=list(get_config_value(config, "cover", "sources", ["local"])), save_folder_cover=bool(get_config_value(config, "cover", "save_folder_cover", False)))
            lines.append(_step(4, "Cover", "OK" if code == 0 else "FAIL", _first_line(cover_output)))
            if code != 0:
                return _finish(lines, ImportResult(1, "", status="FAIL"))
        else:
            lines.append(_step(4, "Cover", "DRY", f"would embed cover {len(files)}/{len(files)}"))
    else:
        lines.append(_step(4, "Cover", "SKIP", "disabled" if not run_cover else "included in enrich"))

    if run_lyrics and not enrich_has_lyrics:
        if apply:
            code, lyrics_output = lyrics_path(source, apply=True, force=force, sources=list(get_config_value(config, "lyrics", "sources", ["local"])), save_txt=bool(get_config_value(config, "lyrics", "save_txt", False)))
            lines.append(_step(5, "Lyrics", "OK" if code == 0 else "FAIL", _first_line(lyrics_output)))
            if code != 0:
                return _finish(lines, ImportResult(1, "", status="FAIL"))
        else:
            lines.append(_step(5, "Lyrics", "DRY", f"would embed lyrics {len(files)}/{len(files)}"))
    else:
        lines.append(_step(5, "Lyrics", "SKIP", "disabled" if not run_lyrics else "included in enrich"))

    if run_replaygain and not enrich_has_replaygain:
        if apply:
            code, rg_output = replaygain_path(source, apply=True, force=force)
            status = "OK" if code == 0 else "SKIP" if rg_output.startswith("ReplayGain: skipped") or rg_output.startswith("ReplayGain: SKIP") else "FAIL"
            lines.append(_step(6, "ReplayGain", status, _first_line(rg_output)))
            if status == "FAIL":
                return _finish(lines, ImportResult(1, "", status="FAIL"))
        else:
            lines.append(_step(6, "ReplayGain", "DRY", "would write ReplayGain"))
    else:
        lines.append(_step(6, "ReplayGain", "SKIP", "disabled" if not run_replaygain else "included in enrich"))

    final_audit = audit_path(source)
    final_status = final_audit.status
    planned_status = "DRY" if not apply else final_status
    lines.append(_step(7, "Final audit", planned_status, "planned state OK" if not apply else _audit_detail(final_audit)))
    if apply and final_status == "REVIEW" and stop_on_review:
        lines.extend(["", "Status: REVIEW", "Import stopped before organization.", "Reason:", f"- {_audit_detail(final_audit)}", "Use --allow-review to continue, or resolve metadata first.", "Try: noqlen-forge db explain PATH"])
        return ImportResult(1, "\n".join(lines), status="REVIEW")

    plan = organize_path(source, config=config, apply=False, mode=active_mode, library=library_root, conflict_policy="review", verbose=verbose, debug=debug)
    already = _already_imported(plan.items or [], config)
    if run_organize:
        if plan.conflicts and not already:
            lines.append(_step(8, "Organize", "REVIEW", f"{plan.conflicts} conflicts"))
            lines.extend(["", "Final:", "Conflicts: " + str(plan.conflicts), "Status: REVIEW"])
            return ImportResult(1, "\n".join(lines), status="REVIEW", conflicts=plan.conflicts)
        if apply and not already:
            org = organize_path(source, config=config, apply=True, mode=active_mode, library=library_root, conflict_policy="review", verbose=verbose, debug=debug)
            lines.append(_step(8, "Organize", org.status, f"copied {org.copied} files" if active_mode == "copy" else f"moved {org.moved} files"))
            if org.code != 0:
                return _finish(lines, ImportResult(1, "", status=org.status, conflicts=org.conflicts))
            copied, moved, skipped = org.copied, org.moved, org.skipped
        else:
            copied = moved = 0
            skipped = len(files) if already else 0
            detail = "already organized / already in library" if already else f"would {active_mode} {len(files)} files"
            lines.append(_step(8, "Organize", "OK" if apply and already else "DRY", detail))
    else:
        copied = moved = skipped = 0
        lines.append(_step(8, "Organize", "SKIP", "disabled"))

    db_target = library_root if run_organize else source
    if apply:
        if bool(get_config_value(config, "import", "auto_scan_db", True)):
            scan_library(config, db_target, apply=True, verbose=verbose)
        record_import_operation(config, db_target, active_mode, "OK", f"imported {len(files)} files from {source}")
        lines.append(_step(9, "DB update", "OK", f"updated {len(files)} files"))
    else:
        lines.append(_step(9, "DB update", "DRY", f"would update {len(files)} files"))

    if apply and active_mode == "move" and bool(get_config_value(config, "import", "delete_source_empty_dirs", False)):
        _delete_empty_source_dirs(source, library_root)

    imported = copied + moved + skipped if apply else 0
    lines.append(_step(10, "Summary", "OK", f"imported {imported} files" if apply else "ready to apply"))
    lines.extend(["", "Final:"])
    if apply:
        lines.extend([f"Imported: {imported}", f"Copied: {copied}", f"Moved: {moved}", f"Skipped: {skipped}", "Conflicts: 0", "Status: OK"])
    else:
        lines.extend(["Would write tags: " + ("yes" if run_enrich or run_cover or run_lyrics or run_replaygain else "no"), f"Would copy: {len(files) if active_mode == 'copy' and run_organize else 0}", f"Would move: {len(files) if active_mode == 'move' and run_organize else 0}", f"Review blockers: {1 if final_status == 'REVIEW' and stop_on_review else 0}", "Status: OK"])
    return ImportResult(0, "\n".join(lines), imported=imported, copied=copied, moved=moved, skipped=skipped)


def _library_root(config: dict[str, Any], library: Path | None) -> Path | None:
    configured = str(get_config_value(config, "import", "library_path", "") or "").strip() or str(get_config_value(config, "organize", "library_path", "") or "").strip()
    if library is not None:
        return library.expanduser().resolve(strict=False)
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return None


def _validate_paths(source: Path, library: Path, apply: bool) -> None:
    if source == library:
        raise SafetyError("Refusing import where destination equals source")
    if is_dangerous_real_library_path(library):
        raise SafetyError(f"Refusing dangerous import library: {library}")
    if apply and automated_validation_enabled() and not is_noqlen_forge_lab_path(library):
        require_lab_path_for_automated_apply(library, context="noqlen-forge import --library")


def _capture_enrich(runner: EnrichRunner, path: Path, apply: bool, force: bool, cover_in_full: bool, lyrics_in_full: bool, replaygain_in_full: bool, verbose: bool, debug: bool) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = runner(path, apply, force, cover_in_full, lyrics_in_full, replaygain_in_full, verbose, debug)
    return code, buffer.getvalue()


def _already_imported(items: list[OrganizeItem], config: dict[str, Any]) -> bool:
    if not items or not all(item.action == "conflict" and item.reason == "destination exists" for item in items):
        return False
    conn = connect_readonly(config)
    if conn is None:
        return False
    with conn:
        for item in items:
            row = conn.execute("SELECT 1 FROM files WHERE path = ? AND status = 'active'", (normalize_path(item.destination),)).fetchone()
            if row is None:
                return False
    return True


def _delete_empty_source_dirs(source: Path, library: Path) -> None:
    if source.is_file() or normalize_path(source).startswith(normalize_path(library)):
        return
    for candidate in sorted([p for p in source.rglob("*") if p.is_dir()], reverse=True):
        try:
            candidate.rmdir()
        except OSError:
            pass


def _step(index: int, name: str, status: str, detail: str) -> str:
    return f"[{index}/10] {name:<18} {status:<7} {detail}".rstrip()


def _audit_detail(result) -> str:
    if result.bad_fields:
        return "bad fields found"
    if result.status == "REVIEW":
        return "missing or inconsistent essential metadata"
    return "complete" if result.status == "OK" else "optional metadata missing"


def _first_line(output: str) -> str:
    return next((line.strip() for line in output.splitlines() if line.strip()), "OK")


def _import_title(path: Path) -> str:
    try:
        tracks = read_tracks(path)
    except Exception:
        tracks = []
    albums = {track.album for track in tracks if track.album}
    return next(iter(albums)) if len(albums) == 1 else path.name


def _finish(lines: list[str], result: ImportResult) -> ImportResult:
    lines.extend(["", "Final:", f"Status: {result.status}"])
    result.output = "\n".join(lines)
    return result
