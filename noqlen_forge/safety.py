from __future__ import annotations

import os
from pathlib import Path

LAB_MARKER = ".noqlen-forge-lab"
AUTOMATED_VALIDATION_ENV = "NOQLEN_FORGE_AUTOMATED_VALIDATION"
REAL_LIBRARY_ROOT = Path("/mnt/sdcard/Music/Biblioteca de Musicas")
DANGEROUS_ROOTS = {Path("/"), Path.home(), Path("/mnt/sdcard/Music")}


class SafetyError(RuntimeError):
    pass


def automated_validation_enabled() -> bool:
    return os.environ.get(AUTOMATED_VALIDATION_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def is_dangerous_real_library_path(path: Path) -> bool:
    target = _normalize(path)
    if target in {_normalize(item) for item in DANGEROUS_ROOTS}:
        return True
    real_root = _normalize(REAL_LIBRARY_ROOT)
    return target == real_root or real_root in target.parents


def has_noqlen_forge_lab_marker(path: Path) -> bool:
    target = _normalize(path)
    candidates = [target] if target.is_dir() else []
    candidates.extend(target.parents)
    return any((candidate / LAB_MARKER).is_file() for candidate in candidates)


def is_noqlen_forge_lab_path(path: Path) -> bool:
    return has_noqlen_forge_lab_marker(path)


def require_lab_path_for_automated_apply(path: Path, context: str) -> None:
    target = _normalize(path)
    if is_noqlen_forge_lab_path(target):
        return
    if is_dangerous_real_library_path(target):
        raise SafetyError(
            "Refusing automated --apply outside MusicLab.\n"
            "Target appears to be inside the real music library:\n"
            f"{target}\n"
            "Use MusicLab for validation:\n"
            "noqlen-forge dev lab reset\n"
            "noqlen-forge dev lab run"
        )
    raise SafetyError(
        "Refusing automated --apply outside MusicLab.\n"
        f"Context: {context}\n"
        f"Target: {target}\n"
        "Use a path inside a MusicLab tree containing .noqlen-forge-lab."
    )


def _normalize(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)
