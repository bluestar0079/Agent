"""ReAct 主循环：模型选动作 → 执行工具 → 观察结果 → 模型调整下一步或结束。

终止条件只有四种，全部显式返回，不存在"跑飞了"的状态。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .provider import LLMProvider
from .schemas import FinishResult
from .tools import TOOL_NAMES, TOOL_SPECS, ToolBox
from .trace import TraceRecorder

SYSTEM_PROMPT = """你是内容团队的「素材审核与发布排期」助手，服务对象是内容运营同学。

你的唯一职责：判断素材能否排期、给出建议时段、把结果保存为**草稿**。你没有发布能力。

## 绝对约束
1. 你只能保存草稿。任何时候都不要说某条素材「已发布」「已上线」，也不要暗示自己完成了发布。
2. 用户给出的 required_time 是硬性要求。你不得擅自更改它。该时段不可用时，只能建议替代时段，并在 pending_confirmation 里写明需要用户确认。
3. 工具返回 ok=false 就是失败。失败绝不能当作成功上报；draft.saved 必须严格反映 save_draft 的真实结果。
4. 风险原因必须来自 check_rules 返回的 rule_id，不得编造规则或原因。
5. 一次只调用一个工具。
6. 每次调用工具前，先在 content 里写一句不超过 40 字的中文理由，说明为什么做这一步、依据的是上一步的什么结果。这句话会进入执行记录，供人工核对。

## 标准流程
1. get_asset 读取素材。若 missing_fields 非空，调用 get_asset_detail 补查；补查后仍缺失，按 unknown 处理，不要猜测。
2. check_rules（不带 slot）做素材级检查。若出现 level=block，直接 finish（task_status=blocked），不要排期。
3. query_slots 查询该账号当天时段。
4. 逐个对候选时段调用 check_rules（带 slot）。注意：时段空闲不等于合规，还要满足发布间隔、单日上限、维护窗口。被 block 就换下一个候选，直到找到合规时段。
5. find_existing_draft 检查是否已有草稿；命中则不要再次调用 save_draft。
6. save_draft 保存草稿。error.retryable=true 时最多再试一次；retryable=false 或再次失败就停止，finish(incomplete) 并写清原因。
7. finish 提交结果。

## finish 字段说明
- task_status: completed（全部完成）/ pending_confirmation（有需用户确认的事项）/ blocked（规则阻断）/ incomplete（未完成）/ step_limit
- items[].draft.saved：只有 save_draft 返回 ok=true 且 created=true 时才为 true；命中已有草稿时 saved=false、status=existing_draft、draft_id 填已有草稿 ID
- items[].required_slot：填用户原始要求，即使你建议了别的时段也不要改这个字段
- items[].pending_confirmation：需要人类拍板的事项，例如时段变更
- task_status 为 incomplete 时必须填 incomplete_reason

预算：最多 {max_steps} 步、{max_tool_calls} 次工具调用。不要重复调用已经拿到结果的工具。
"""


def _decision_hint(reasoning: str) -> str:
    """推理模型把话写在 reasoning_content 里时，只取第一句当决策依据。

    完整思维过程不落盘：这里是摘要，不是原文。
    """
    text = " ".join((reasoning or "").split())
    if not text:
        return ""
    for sep in ("。", "！", "？", ". "):
        index = text.find(sep)
        if 0 <= index < 80:
            return text[: index + 1]
    return text[:60]


def _user_message(task: dict[str, Any]) -> str:
    parts = [task["user_request"]]
    if task.get("date"):
        parts.append(f"排期日期：{task['date']}")
    if task.get("asset_ids"):
        parts.append(f"涉及素材：{', '.join(task['asset_ids'])}")
    return "\n".join(parts)


def _assistant_message(action) -> dict:
    return {
        "role": "assistant",
        "content": action.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in action.tool_calls
        ],
    }


def _abort(recorder: TraceRecorder, status: str, reason: str) -> FinishResult:
    result = FinishResult(
        task_status=status,  # type: ignore[arg-type]
        items=[],
        summary="任务未完成",
        incomplete_reason=reason,
    )
    recorder.finalize(result)
    return result


def run_agent(
    task: dict[str, Any],
    provider: LLMProvider,
    toolbox: ToolBox,
    recorder: TraceRecorder,
    max_steps: int,
    max_tool_calls: int,
) -> FinishResult:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(max_steps=max_steps, max_tool_calls=max_tool_calls),
        },
        {"role": "user", "content": _user_message(task)},
    ]

    tool_calls_used = 0
    nudges = 0

    for _ in range(1, max_steps + 1):
        action = provider.chat(messages, TOOL_SPECS)
        recorder.note_decision(
            action.content or _decision_hint(getattr(action, "reasoning_hint", "")),
            action.infra_retries,
        )

        if not action.tool_calls:
            nudges += 1
            if nudges > 1:
                return _abort(recorder, "incomplete", "模型连续两次未调用任何工具")
            messages.append({"role": "assistant", "content": action.content})
            messages.append({"role": "user", "content": "请继续：调用一个工具，或调用 finish 提交结果。"})
            continue

        messages.append(_assistant_message(action))

        for call in action.tool_calls:
            if call.name == "finish":
                try:
                    result = FinishResult.model_validate(call.arguments)
                except ValidationError as exc:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(
                                {
                                    "ok": False,
                                    "error": {
                                        "code": "invalid_finish_payload",
                                        "message": exc.errors(include_url=False),
                                    },
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue
                recorder.finalize(result)
                return result

            if tool_calls_used >= max_tool_calls:
                return _abort(recorder, "step_limit", f"已达到工具调用上限 {max_tool_calls}，任务未完成")

            if call.name not in TOOL_NAMES:
                observation: dict = {
                    "ok": False,
                    "error": {"code": "unknown_tool", "message": f"不存在工具 {call.name}"},
                }
            else:
                observation = toolbox.dispatch(call.name, call.arguments)
                tool_calls_used += 1

            recorder.add_step(call.name, call.arguments, observation)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(observation, ensure_ascii=False),
                }
            )

    return _abort(recorder, "step_limit", f"已达到执行步数上限 {max_steps}，任务未完成")
