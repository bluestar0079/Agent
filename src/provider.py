"""模型接入层。三个实现共用同一个 chat() 接口：

- QwenProvider     真实模型（百炼 OpenAI 兼容接口）
- ScriptedProvider 测试用，按脚本回放动作，保证断言确定
- MockProvider     离线用，规则化假模型，只证明流程跑得通，不证明模型决策效果
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelAction:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    infra_retries: int = 0
    # 推理模型常把说明写在这里。只截取第一句作为决策依据，完整思维过程不落盘。
    reasoning_hint: str = ""
    reasoning_hint: str = ""  # 推理模型把话写在 reasoning_content 里，用作决策依据的兜底


class LLMProvider(Protocol):
    name: str
    model: str

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelAction: ...


class QwenProvider:
    name = "qwen"

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        from openai import OpenAI

        self.model = model
        # max_retries=0：HTTP 层重试由我们自己控制，且在 trace 里标记为 infra_retry，
        # 避免和「Agent 决定重试」混为一谈。
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=90.0, max_retries=0)

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelAction:
        retries = 0
        response = None
        for attempt in range(2):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=2500,
                )
                break
            except Exception as exc:  # 只在基础设施类错误上重试一次
                status = getattr(exc, "status_code", None)
                text = str(exc).lower()
                transient = status in (429, 500, 502, 503, 504) or "timeout" in text or "connection" in text
                if attempt == 0 and transient:
                    retries = 1
                    time.sleep(2)
                    continue
                raise

        message = response.choices[0].message
        calls: list[ToolCall] = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        reasoning = getattr(message, "reasoning_content", "") or ""
        if not reasoning:
            extra = getattr(message, "model_extra", None) or {}
            reasoning = extra.get("reasoning_content", "") or ""
        return ModelAction(
            content=message.content or "",
            tool_calls=calls,
            infra_retries=retries,
            reasoning_hint=reasoning,
        )


class ScriptedProvider:
    """按预置脚本逐条返回动作。用于测试：不联网、结果确定。"""

    name = "scripted"

    def __init__(self, model: str, actions: list[dict]) -> None:
        self.model = model
        self._actions = list(actions)
        self._index = 0

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelAction:
        if self._index >= len(self._actions):
            return ModelAction(content="（脚本已用尽）", tool_calls=[])
        raw = self._actions[self._index]
        self._index += 1
        calls = [
            ToolCall(
                id=f"call_{self._index}_{i}",
                name=c["name"],
                arguments=c.get("arguments", {}),
            )
            for i, c in enumerate(raw.get("tools", []))
        ]
        return ModelAction(content=raw.get("content", ""), tool_calls=calls)


class MockProvider:
    """离线假模型：靠观察上一步工具结果决定下一步，仅用于验证流程与状态机。

    它本质上是一段写死的策略，因此不能作为「模型能做出正确决策」的证据——
    这一点在 README 与 docs/03-evaluation.md 里都明确标注。
    """

    name = "mock"
    model = "mock-policy"

    def __init__(self, task: dict) -> None:
        self.task = task
        self.queue = list(task.get("asset_ids") or [])
        # 每条素材处理完的结论，最后一次性交给 finish
        self.collected: list[dict[str, Any]] = []
        first = self.queue.pop(0) if self.queue else None
        self.mem: dict[str, Any] = self._fresh_mem(first)

    @staticmethod
    def _fresh_mem(asset_id: str | None) -> dict[str, Any]:
        return {
            "asset_id": asset_id,
            "account_id": None,
            "asset": None,
            "risks": [],
            "candidates": [],
            "slot": None,
            "save_attempts": 0,
            "draft": None,
            "missing": False,
        }

    # ---- 内部工具 ----
    @staticmethod
    def _last_tool(messages: list[dict]) -> tuple[str | None, dict]:
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.get("role") != "tool":
                continue
            name = None
            for j in range(i - 1, -1, -1):
                prev = messages[j]
                if prev.get("role") == "assistant" and prev.get("tool_calls"):
                    for call in prev["tool_calls"]:
                        if call.get("id") == msg.get("tool_call_id"):
                            name = call["function"]["name"]
                            break
                    break
            try:
                obs = json.loads(msg.get("content") or "{}")
            except json.JSONDecodeError:
                obs = {}
            return name, obs
        return None, {}

    def _action(self, text: str, name: str, arguments: dict) -> ModelAction:
        return ModelAction(
            content=text,
            tool_calls=[ToolCall(id=f"mock_{len(text)}_{name}", name=name, arguments=arguments)],
        )

    def _finish(self, text: str, status: str, **extra) -> ModelAction:
        """一条素材有了结论：记账；还有素材就接着处理，全部处理完才真正 finish。"""
        self.collected.append(
            {"item": self._item(), "status": status, "text": text, "extra": extra}
        )
        if self.queue:
            next_id = self.queue.pop(0)
            self.mem = self._fresh_mem(next_id)
            return self._action(
                f"{self.collected[-1]['item']['asset_id']} 处理完毕，接着处理 {next_id}。",
                "get_asset",
                {"asset_id": next_id},
            )
        return self._action(text, "finish", self._finish_payload())

    def _finish_payload(self) -> dict:
        statuses = [c["status"] for c in self.collected]
        if "pending_confirmation" in statuses:
            total = "pending_confirmation"
        elif "incomplete" in statuses:
            total = "incomplete"
        elif statuses and all(s == "blocked" for s in statuses):
            total = "blocked"
        else:
            total = "completed"

        label = {
            "completed": "已完成",
            "pending_confirmation": "待确认",
            "blocked": "已阻断",
            "incomplete": "未完成",
        }
        summary = "；".join(
            f"{c['item']['asset_id']} {label.get(c['status'], c['status'])}"
            for c in self.collected
        )
        payload: dict[str, Any] = {
            "task_status": total,
            "items": [c["item"] for c in self.collected],
            "summary": summary,
        }
        if total == "incomplete":
            reasons = [
                c["extra"].get("incomplete_reason")
                for c in self.collected
                if c["status"] == "incomplete"
            ]
            payload["incomplete_reason"] = "；".join(r for r in reasons if r) or "存在未完成的素材"
        return payload

    def _item(self) -> dict:
        mem = self.mem
        asset = mem["asset"] or {}
        required = asset.get("required_time")
        slot = mem["slot"]
        blocking = [r for r in mem["risks"] if r["level"] == "block"]
        pending: list[str] = []
        if required and slot and slot != required:
            pending.append(f"时段由用户要求的 {required} 调整为 {slot}，需用户确认")

        draft = mem["draft"]
        if blocking:
            review = "risk_blocked"
        elif any(r["level"] == "confirm" for r in mem["risks"]):
            review = "risk_needs_review"
        elif mem.get("missing"):
            review = "unknown"
        else:
            review = "no_risk"
        return {
            "asset_id": mem["asset_id"],
            "review_status": review,
            "risk_reasons": mem["risks"],
            "required_slot": required,
            "suggested_slot": slot,
            "slot_changed": bool(required and slot and slot != required),
            "pending_confirmation": pending,
            "draft": draft,
        }

    # ---- 主决策 ----
    def chat(self, messages: list[dict], tools: list[dict]) -> ModelAction:
        name, obs = self._last_tool(messages)
        mem = self.mem
        asset_id = mem["asset_id"]

        if name is None:
            return self._action("先读取素材主数据。", "get_asset", {"asset_id": asset_id})

        if name == "get_asset":
            if not obs.get("ok"):
                return self._finish("素材不存在，无法处理。", "incomplete", incomplete_reason="素材不存在")
            mem["asset"] = obs["asset"]
            mem["account_id"] = obs["asset"]["account_id"]
            if obs.get("missing_fields"):
                mem["missing"] = True
                return self._action("类目字段缺失，先从内容库补查。", "get_asset_detail", {"asset_id": asset_id})
            return self._action("主数据完整，先做素材级规则检查。", "check_rules", {"asset_id": asset_id, "account_id": mem["account_id"]})

        if name == "get_asset_detail":
            if obs.get("ok"):
                mem["asset"] = obs["asset"]
                mem["missing"] = bool(obs.get("missing_fields"))
            else:
                mem["missing"] = True
            return self._action("补查结束，重新做规则检查。", "check_rules", {"asset_id": asset_id, "account_id": mem["account_id"]})

        if name == "check_rules":
            risks = obs.get("risks", [])
            slot = obs.get("slot")
            if slot is None:
                mem["risks"] = risks
                if any(r["level"] == "block" for r in risks):
                    return self._finish("命中阻断规则，不安排排期。", "blocked")
                return self._action("素材级无阻断风险，查询当天可用时段。", "query_slots", {"account_id": mem["account_id"], "date": self.task["date"]})

            blocking = [r for r in risks if r["level"] == "block"]
            if not blocking:
                mem["slot"] = slot
                mem["risks"] = [r for r in mem["risks"] if r["level"] != "unknown"] + [r for r in risks if r["level"] != "block"]
                return self._action(f"{slot} 通过规则校验，先检查是否已有草稿。", "find_existing_draft", {"asset_id": asset_id, "slot": slot})
            return self._next_candidate(f"{slot} 被规则阻断（{blocking[0]['rule_id']}），换下一个候选时段。")

        if name == "query_slots":
            available = [s["time"] for s in obs.get("slots", []) if s["available"]]
            required = (mem["asset"] or {}).get("required_time")
            candidates = ([required] if required else []) + [t for t in available if t != required]
            mem["candidates"] = candidates
            if not candidates:
                return self._finish("当天没有可用时段。", "incomplete", incomplete_reason="没有可用时段")
            return self._check_next("先验证用户要求的时段是否合规。")

        if name == "find_existing_draft":
            if obs.get("exists"):
                drafts = obs.get("drafts", [])
                mem["draft"] = {"saved": False, "draft_id": drafts[0]["draft_id"], "status": "existing_draft"}
                return self._finish("已存在相同草稿，未重复保存。", "completed")
            return self._save()

        if name == "save_draft":
            if obs.get("ok"):
                mem["draft"] = {"saved": True, "draft_id": obs["draft_id"], "status": "saved_pending_confirmation"}
                required = (mem["asset"] or {}).get("required_time")
                status = "pending_confirmation" if required and mem["slot"] != required else "completed"
                return self._finish(f"草稿 {obs['draft_id']} 已保存（仅草稿，未发布）。", status)
            error = obs.get("error", {})
            mem["save_attempts"] += 1
            if error.get("retryable") and mem["save_attempts"] < 2:
                return self._action(f"保存失败（{error.get('code')}），该错误可重试，再试一次。", "save_draft", {
                    "asset_id": asset_id,
                    "slot": mem["slot"],
                    "deviation_from_required": bool((mem["asset"] or {}).get("required_time") and mem["slot"] != (mem["asset"] or {}).get("required_time")),
                    "note": "重试保存",
                })
            mem["draft"] = {"saved": False, "draft_id": None, "status": "failed"}
            return self._finish("草稿保存失败，任务未完成。", "incomplete", incomplete_reason=f"草稿保存失败：{error.get('message', '')}")

        return self._finish("流程异常，未能完成。", "incomplete", incomplete_reason=f"未知工具返回：{name}")

    def _next_candidate(self, text: str) -> ModelAction:
        mem = self.mem
        if not mem["candidates"]:
            return self._finish("没有通过规则校验的时段。", "incomplete", incomplete_reason="没有通过规则校验的候选时段")
        if mem["candidates"][0] == mem["slot"]:
            mem["candidates"].pop(0)
        return self._check_next(text)

    def _check_next(self, text: str) -> ModelAction:
        mem = self.mem
        slot = mem["candidates"].pop(0)
        mem["slot"] = slot
        return self._action(text, "check_rules", {"asset_id": mem["asset_id"], "account_id": mem["account_id"], "slot": slot})

    def _save(self) -> ModelAction:
        mem = self.mem
        required = (mem["asset"] or {}).get("required_time")
        return self._action(
            f"{mem['slot']} 无既有草稿，保存草稿。",
            "save_draft",
            {
                "asset_id": mem["asset_id"],
                "slot": mem["slot"],
                "deviation_from_required": bool(required and mem["slot"] != required),
                "note": "按建议时段保存草稿，等待用户确认",
            },
        )
