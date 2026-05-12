from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class LabContext:
    root: Path
    incoming: Path
    library: Path
    output: Path
    reports: Path
    config_path: Path
    db_path: Path
    fake_navidrome_config: dict[str, str]
    env: dict[str, str]
    run_cli: Callable[..., object] | None = None

    @classmethod
    def from_root(cls, root: Path, env: dict[str, str] | None = None, run_cli: Callable[..., object] | None = None) -> "LabContext":
        lab = root.expanduser().resolve(strict=False)
        return cls(
            root=lab,
            incoming=lab / "Incoming",
            library=lab / "Library",
            output=lab / "Output",
            reports=lab / "Reports",
            config_path=lab / "config.toml",
            db_path=lab / "library.db",
            fake_navidrome_config={
                "base_url": "http://127.0.0.1:4533",
                "username": "musiclab",
                "password": "musiclab-password",
            },
            env=dict(env or os.environ),
            run_cli=run_cli,
        )

    def assert_inside_lab(self, *paths: Path) -> None:
        root = self.root.resolve(strict=False)
        for path in paths:
            resolved = path.resolve(strict=False)
            if resolved != root and root not in resolved.parents:
                raise ValueError(f"Path is outside MusicLab: {path}")
