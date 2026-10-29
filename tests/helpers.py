"""测试公共helper：用 MockProvider 跑完整场景，不联网、结果确定。"""

from __future__ import annotations

import json
from pathlib import Path

from src.agent import run_agent
from src.provider import MockProvider
from src.store import DataStore, FaultInjector
from src.tools import ToolBox
from src.trace import TraceRecorder

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCENARIOS = DATA / "scenarios"


def load_scenario(name: str) -> dict:
    return json.loads((SCENARIOS / f"{name}.json").read_text(encoding="utf-8"))


def run_scenario(name: str, provider_factory=None):
    scenario = load_scenario(name)
    seed = scenario.get("seed") or {}
    store = DataStore(
        DATA, seed_bookings=seed.get("bookings"), seed_drafts=seed.get("drafts")
    )
    faults = FaultInjector(scenario.get("faults"))
    toolbox = ToolBox(store, faults)
    provider = provider_factory(scenario) if provider_factory else MockProvider(scenario)
    max_steps = scenario.get("max_steps") or 14
    recorder = TraceRecorder(
        scenario["scenario_id"], provider.name, provider.model, max_steps, 12
    )
    result = run_agent(scenario, provider, toolbox, recorder, max_steps, 12)
    return result, recorder.to_dict(), store, toolbox


def tool_sequence(trace: dict) -> list[str]:
    return [step["action"]["tool"] for step in trace["steps"]]
