from __future__ import annotations

from pathlib import Path

import pytest

from noqlen_forge.lab_assertions import assert_csv_valid, assert_exists, assert_json_valid, assert_no_real_paths, assert_not_exists, assert_status
from noqlen_forge.lab_context import LabContext
from noqlen_forge.lab_registry import LAB_SCENARIOS, select_scenarios


pytestmark = pytest.mark.unit


def test_lab_registry_declares_metadata_and_callable() -> None:
    scenario = next(item for item in LAB_SCENARIOS if item.name == "lyrics")

    assert scenario.area == "lyrics"
    assert scenario.quick is True
    assert "lyrics" in scenario.tags
    assert scenario.run is not None


def test_select_scenarios_filters_quick_area_tag_and_full() -> None:
    quick = select_scenarios(quick=True)
    lyrics = select_scenarios(area="lyrics")
    filesystem = select_scenarios(tag="filesystem")
    full = select_scenarios(full=True)

    assert {item.name for item in quick} < {item.name for item in full}
    assert {item.name for item in lyrics} == {"lyrics", "lyrics-providers", "safety"}
    assert "lyrics" in {item.name for item in filesystem}
    assert len(full) == len(LAB_SCENARIOS)


def test_select_scenarios_unknown_selector_errors() -> None:
    with pytest.raises(ValueError, match="Unknown MusicLab scenario"):
        select_scenarios(scenario="missing")

    with pytest.raises(ValueError, match="Unknown MusicLab tag"):
        select_scenarios(tag="missing")


def test_lab_context_derives_only_musiclab_paths(tmp_path: Path) -> None:
    lab = tmp_path / "noqlen-forge-lab"
    context = LabContext.from_root(lab)

    context.assert_inside_lab(context.incoming, context.library, context.output, context.reports, context.config_path, context.db_path)
    with pytest.raises(ValueError):
        context.assert_inside_lab(tmp_path / "outside-lab")


def test_lab_assertions_basic_helpers(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("ok\n", encoding="utf-8")

    assert_exists(path)
    assert_not_exists(tmp_path / "missing.txt")
    assert_status("Status: OK", "OK")
    assert_json_valid('{"ok": true}')
    assert_csv_valid("name\nvalue\n")
    assert_no_real_paths(str(tmp_path))
