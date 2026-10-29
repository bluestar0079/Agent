"""执行记录。只记录「决策依据」这一句人类可读的意图，不落盘模型内部思维过程。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def summarize(tool: str, args: dict, result: dict) -> str:
    if not result.get("ok", True):
        err = result.get("error", {})
        return f"失败 {err.get('code', 'unknown')}：{err.get('message', '')}"

    if tool == "get_asset":
        asset = result.get("asset", {})
        missing = result.get("missing_fields") or []
        text = (
            f"读取 {asset.get('asset_id')}，类目={asset.get('category') or '缺失'}，"
            f"硬性时间={asset.get('required_time') or '无'}"
        )
        return text + (f"，缺失字段={','.join(missing)}" if missing else "")
    if tool == "get_asset_detail":
        asset = result.get("asset", {})
        return f"补查成功（{result.get('source')}），类目={asset.get('category')}"
    if tool == "check_rules":
        scope = args.get("slot") or "素材级"
        risks = result.get("risks", [])
        if not risks:
            return f"{scope} 检查通过，无风险"
        return "；".join(f"{r['rule_id']}({r['level']}) {r['reason']}" for r in risks)
    if tool == "query_slots":
        slots = result.get("slots", [])
        available = [s["time"] for s in slots if s.get("available")]
        occupied = [f"{s['time']}({s['occupied_by']})" for s in slots if not s.get("available")]
        return f"可用 {','.join(available) or '无'}；已占用 {','.join(occupied) or '无'}"
    if tool == "find_existing_draft":
        if result.get("exists"):
            drafts = result.get("drafts", [])
            return f"命中 {result.get('count', 0)} 份已有草稿：{drafts[0]['draft_id']}@{drafts[0]['slot']}"
        return "无既有草稿"
    if tool == "save_draft":
        flag = "新建" if result.get("created") else "已存在"
        return f"草稿 {result.get('draft_id')} 保存成功（{flag}）"
    if tool == "list_pending_assets":
        return f"待处理 {result.get('count', 0)} 条"
    return json.dumps(result, ensure_ascii=False)[:160]


class TraceRecorder:
    def __init__(
        self,
        run_id: str,
        provider: str,
        model: str,
        max_steps: int,
        max_tool_calls: int,
    ) -> None:
        self.run_id = run_id
        self.provider = provider
        self.model = model
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.steps: list[dict[str, Any]] = []
        self.status = "running"
        self.infra_retries = 0
        self._pending_decision = ""

    def note_decision(self, text: str, infra_retries: int = 0) -> None:
        self._pending_decision = (text or "").strip()
        self.infra_retries += infra_retries

    def add_step(self, tool: str, args: dict, result: dict) -> None:
        if self.steps:
            self.steps[-1]["next_action"] = f"调用 {tool}"
        self.steps.append(
            {
                "step": len(self.steps) + 1,
                "decision_basis": self._pending_decision or f"（模型未给出说明）→ {tool}",
                "action": {"tool": tool, "args": args},
                "observation": result,
                "observation_summary": summarize(tool, args, result),
                "next_action": "待定",
            }
        )
        self._pending_decision = ""

    def finalize(self, result: Any) -> None:
        self.status = result.task_status
        self.result = result.model_dump()
        if self.steps:
            self.steps[-1]["next_action"] = "finish（结束并提交结果）"
        self.finished_at = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "provider": self.provider,
            "model": self.model,
            "started_at": self.started_at,
            "finished_at": getattr(self, "finished_at", None),
            "budget": {
                "max_steps": self.max_steps,
                "used_steps": len(self.steps),
                "max_tool_calls": self.max_tool_calls,
                "used_tool_calls": len(self.steps),
            },
            "infra_retries": self.infra_retries,
            "status": self.status,
            "steps": self.steps,
            "result": getattr(self, "result", None),
        }

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "trace.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path
