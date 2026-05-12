from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


ScenarioRunner = Callable[[object], None]


@dataclass(frozen=True, slots=True)
class LabScenario:
    name: str
    area: str
    description: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    quick: bool = False
    slow: bool = False
    requires_apply: bool = False
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    estimated_cost: str = "low"
    run: ScenarioRunner | None = None

    @property
    def requires_apply_in_lab(self) -> bool:
        return self.requires_apply

    @property
    def validates(self) -> str:
        return self.description


def _noop_scenario(_: object) -> None:
    return None


LAB_SCENARIOS: tuple[LabScenario, ...] = (
    LabScenario("db-scan", "db", "isolated SQLite init, dry-run/apply scan, and idempotency", ("db", "filesystem", "safety"), quick=True, requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("jobs", "jobs", "persistent job creation, status, cancellation, resume, prune, and safety", ("db", "workflow", "safety"), quick=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("organize", "organize", "copy/move/conflict/safety organize flow", ("filesystem", "safety"), requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("import", "import", "copy/move/review/replaygain/safety import flow", ("filesystem", "workflow", "safety"), slow=True, requires_apply=True, estimated_cost="medium", run=_noop_scenario),
    LabScenario("clean-album", "services", "rich tags remain idempotent", ("metadata", "audit"), quick=True, requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("dirty-album", "services", "bad-field cleanup and full enrich path", ("metadata", "audit", "safety"), quick=True, requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("partial-album", "core", "partial identity preservation", ("metadata",), requires_apply=True, estimated_cost="medium", run=_noop_scenario),
    LabScenario("core-api", "core", "stable internal Core API manifest, dry-run/apply safety, jobs, and JSON sanitization", ("api", "workflow", "safety"), quick=True, requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("cover", "services", "local cover service dry-run/apply", ("cover", "filesystem"), requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("lyrics", "lyrics", "local lyrics service, embedding, and idempotency", ("lyrics", "filesystem"), quick=True, requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("lyrics-providers", "lyrics", "fake lyrics provider fallback/conflict/idempotency", ("lyrics", "provider", "network_fake"), quick=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("audio-key", "audio", "native optional key detection backends", ("audio", "provider", "network_fake"), quick=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("existing-media", "core", "existing cover and lyrics are preserved", ("cover", "lyrics", "safety"), requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("metadata-providers", "core", "ambiguous, fallback, and AcoustID metadata paths", ("provider", "metadata", "network_fake"), requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("native-independence", "core", "native enrich flow does not require legacy external tools", ("provider", "metadata", "safety"), quick=True, requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("replaygain", "services", "ReplayGain optional tag application", ("replaygain", "metadata"), slow=True, requires_apply=True, estimated_cost="medium", run=_noop_scenario),
    LabScenario("sync", "sync", "tags-to-db/db-to-tags conflict and safety flow", ("db", "metadata", "safety"), slow=True, requires_apply=True, estimated_cost="medium", run=_noop_scenario),
    LabScenario("rewrite", "rewrite", "rewrite tags/db/protected/idempotent flow", ("metadata", "db", "safety"), slow=True, requires_apply=True, estimated_cost="medium", run=_noop_scenario),
    LabScenario("db-query", "db", "query/explain read-only language behavior", ("query", "read-only"), estimated_cost="low", run=_noop_scenario),
    LabScenario("review", "review", "manual review list/show/resolve flow", ("review", "safety"), requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("duplicates", "reports", "duplicates reports and DB stability", ("duplicates", "read-only"), estimated_cost="low", run=_noop_scenario),
    LabScenario("reports", "reports", "missing/untracked/missing-files reports", ("reports", "read-only"), slow=True, estimated_cost="high", run=_noop_scenario),
    LabScenario("repair", "repair", "repair reports, safety, and idempotency", ("repair", "db", "safety"), slow=True, requires_apply=True, estimated_cost="high", run=_noop_scenario),
    LabScenario("export", "export", "read-only CSV/JSON export flows", ("export", "read-only"), estimated_cost="low", run=_noop_scenario),
    LabScenario("navidrome", "navidrome", "fake Navidrome ratings and playlist flows", ("navidrome", "network_fake"), quick=True, requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("smart-playlists", "playlists", "smart playlist CRUD/export/refresh safety", ("playlist", "db", "export"), quick=True, requires_apply=True, estimated_cost="low", run=_noop_scenario),
    LabScenario("safety", "safety", "safe paths, no secrets, and offline fixture checks", ("safety", "read-only"), quick=True, estimated_cost="low", run=_noop_scenario),
)


SCENARIO_BY_NAME = {scenario.name: scenario for scenario in LAB_SCENARIOS}


def list_areas() -> tuple[str, ...]:
    return tuple(sorted({scenario.area for scenario in LAB_SCENARIOS}))


def select_scenarios(*, quick: bool = False, full: bool = False, scenario: str | None = None, area: str | None = None, tag: str | None = None) -> tuple[LabScenario, ...]:
    selectors = [bool(scenario), bool(area), bool(tag), bool(quick)]
    if full and any(selectors):
        raise ValueError("Choose only one MusicLab selector: --full, --quick, --scenario, --area, or --tag")
    if sum(selectors) > 1:
        raise ValueError("Choose only one MusicLab selector: --quick, --scenario, --area, or --tag")
    if scenario:
        if scenario not in SCENARIO_BY_NAME:
            raise ValueError(f"Unknown MusicLab scenario: {scenario}")
        selected = [SCENARIO_BY_NAME[scenario]]
    elif area:
        selected = [item for item in LAB_SCENARIOS if item.area == area]
        if not selected:
            raise ValueError(f"Unknown MusicLab area: {area}")
    elif tag:
        selected = [item for item in LAB_SCENARIOS if tag in item.tags]
        if not selected:
            raise ValueError(f"Unknown MusicLab tag: {tag}")
    elif quick:
        selected = [item for item in LAB_SCENARIOS if item.quick]
    else:
        selected = list(LAB_SCENARIOS)

    if selected and not any(item.name == "safety" for item in selected) and (scenario or area or tag):
        selected.append(SCENARIO_BY_NAME["safety"])
    return tuple(dict.fromkeys(selected))
