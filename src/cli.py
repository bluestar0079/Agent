"""命令行入口。

    python -m src.cli list-scenarios
    python -m src.cli run --scenario data/scenarios/conflict_m01.json --provider qwen --out examples/run-02-conflict
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import run_agent
from .config import DATA_DIR, PROJECT_ROOT, Settings, load_env
from .provider import MockProvider, QwenProvider
from .store import DataStore, FaultInjector
from .tools import ToolBox
from .trace import TraceRecorder


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_report(scenario: dict, trace: dict, result: dict) -> str:
    budget = trace["budget"]
    lines = [
        f"场景   : {scenario.get('scenario_id')} — {scenario.get('title', '')}",
        f"模型   : {trace['provider']} / {trace['model']}",
        f"预算   : {budget['used_steps']}/{budget['max_steps']} 步，"
        f"{budget['used_tool_calls']}/{budget['max_tool_calls']} 次工具调用",
        "",
        "执行记录",
    ]
    for step in trace["steps"]:
        action = step["action"]
        args = json.dumps(action["args"], ensure_ascii=False)
        lines.append(f"  {step['step']}. 决策依据：{step['decision_basis']}")
        lines.append(f"     动作：{action['tool']}({args})")
        lines.append(f"     观察：{step['observation_summary']}")
        lines.append(f"     下一步：{step['next_action']}")
    lines += [
        "",
        f"最终状态：{result['task_status']}",
        f"汇总：{result.get('summary', '')}",
    ]
    if result.get("incomplete_reason"):
        lines.append(f"未完成原因：{result['incomplete_reason']}")
    for item in result.get("items", []):
        draft = item.get("draft") or {}
        lines += [
            f"  - 素材 {item['asset_id']}：审核={item['review_status']}，"
            f"要求时段={item.get('required_slot') or '无'}，建议时段={item.get('suggested_slot') or '无'}，"
            f"时段变更={item.get('slot_changed')}",
            f"    风险：{'; '.join(r['rule_id'] + ' ' + r['reason'] for r in item.get('risk_reasons', [])) or '无'}",
            f"    待确认：{'; '.join(item.get('pending_confirmation', [])) or '无'}",
            f"    草稿：saved={draft.get('saved')} id={draft.get('draft_id')} status={draft.get('status')}",
        ]
    return "\n".join(lines)


def cmd_run(args: argparse.Namespace) -> int:
    load_env()
    settings = Settings()
    scenario = load_json(Path(args.scenario))
    data_dir = Path(args.data_dir) if args.data_dir else DATA_DIR

    seed = scenario.get("seed") or {}
    store = DataStore(
        data_dir,
        seed_bookings=seed.get("bookings"),
        seed_drafts=seed.get("drafts"),
    )
    faults = FaultInjector(scenario.get("faults"))
    toolbox = ToolBox(store, faults)

    max_steps = args.max_steps or scenario.get("max_steps") or settings.max_steps
    max_tool_calls = (
        args.max_tool_calls or scenario.get("max_tool_calls") or settings.max_tool_calls
    )

    if args.provider == "mock":
        provider = MockProvider(scenario)
    else:
        provider = QwenProvider(
            api_key=settings.require_api_key(),
            base_url=settings.base_url,
            model=settings.model,
        )

    recorder = TraceRecorder(
        run_id=scenario.get("scenario_id", "run"),
        provider=provider.name,
        model=provider.model,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
    )
    result = run_agent(scenario, provider, toolbox, recorder, max_steps, max_tool_calls)

    trace = recorder.to_dict()
    report = render_report(scenario, trace, result.model_dump())

    out_dir = Path(args.out) if args.out else PROJECT_ROOT / "runs" / scenario["scenario_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "input.json").write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "output.json").write_text(
        json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "drafts.json").write_text(
        json.dumps(store.drafts_snapshot(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    recorder.save(out_dir)
    (out_dir / "console.log").write_text(report + "\n", encoding="utf-8")

    print(report)
    print(f"\n结果目录：{out_dir}")
    return 0


def cmd_list_scenarios(args: argparse.Namespace) -> int:
    scenarios_dir = DATA_DIR / "scenarios"
    for path in sorted(scenarios_dir.glob("*.json")):
        data = load_json(path)
        print(f"{path.name:<32} {data.get('title', '')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="material-scheduling-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行一个场景")
    run.add_argument("--scenario", required=True, help="场景 JSON 路径")
    run.add_argument("--provider", choices=["qwen", "mock"], default="qwen")
    run.add_argument("--out", help="输出目录，默认 runs/<scenario_id>")
    run.add_argument("--data-dir", help="数据目录，默认 data/")
    run.add_argument("--max-steps", type=int, help="覆盖默认执行步数上限")
    run.add_argument("--max-tool-calls", type=int, help="覆盖默认工具调用上限")
    run.set_defaults(func=cmd_run)

    listing = sub.add_parser("list-scenarios", help="列出所有场景")
    listing.set_defaults(func=cmd_list_scenarios)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
