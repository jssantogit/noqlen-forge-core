from __future__ import annotations

from pathlib import Path

import pytest


BASIC_MARKERS = {"unit", "contract", "integration", "lab"}

FILE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("test_lab.py", ("integration", "lab", "slow")),
    ("test_db.py", ("integration", "db")),
    ("test_review.py", ("integration", "db", "service")),
    ("test_navidrome.py", ("integration", "navidrome", "network_fake")),
    ("test_smart_playlists.py", ("integration", "playlist", "db")),
    ("test_lyrics.py", ("integration", "lyrics", "provider", "filesystem")),
    ("test_provider_architecture.py", ("contract", "provider", "network_fake")),
    ("test_metadata_providers.py", ("integration", "provider", "network_fake")),
    ("test_musicbrainz.py", ("contract", "provider", "network_fake")),
    ("test_lastfm.py", ("contract", "provider", "network_fake")),
    ("test_importer.py", ("integration", "filesystem")),
    ("test_organize.py", ("integration", "filesystem")),
    ("test_sync.py", ("integration", "filesystem", "db")),
    ("test_services.py", ("contract", "service")),
    ("test_cli_ux.py", ("contract", "cli")),
    ("test_cli_status.py", ("contract", "cli")),
    ("test_dev.py", ("contract", "cli")),
    ("test_integrations.py", ("integration", "filesystem")),
    ("test_export.py", ("integration", "filesystem", "db")),
    ("test_reports.py", ("integration", "filesystem", "db")),
    ("test_repair.py", ("integration", "filesystem", "db", "service")),
    ("test_rewrite.py", ("integration", "filesystem", "service")),
    ("test_duplicates.py", ("integration", "db")),
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.fspath)).name
        names = {mark.name for mark in item.iter_markers()}
        for filename, markers in FILE_MARKERS:
            if path == filename:
                for marker in markers:
                    if marker not in names:
                        item.add_marker(getattr(pytest.mark, marker))
                names.update(markers)
                break
        if not names.intersection(BASIC_MARKERS):
            item.add_marker(pytest.mark.unit)
