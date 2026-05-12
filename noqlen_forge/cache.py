from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import APP_SLUG

CACHE_ROOT = Path.home() / ".cache" / APP_SLUG / "musicbrainz"


def cache_key(*parts: str) -> Path:
    raw = "\0".join(parts).encode("utf-8")
    return CACHE_ROOT / f"{hashlib.sha256(raw).hexdigest()}.json"


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
