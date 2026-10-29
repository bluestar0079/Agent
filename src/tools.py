"""工具层：业务工具的实现 + 给模型看的 OpenAI function schema。

两个刻意设计：
1. 所有工具都返回结构化 dict，失败也是 {"ok": false, ...} 交给模型观察，而不是抛异常中断循环。
2. 没有任何发布类工具。save_draft 内部会再跑一次规则校验，阻断越权写入。
"""

from __future__ import annotations

from typing import Any

from . import rules as rules_engine
from .store import DataStore, FaultInjector

SLOT_HOURS = list(range(17, 24))  # 17:00 - 23:00

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "asset_id": {"type": "string"},
        "review_status": {
            "type": "string",
            "enum": ["no_risk", "risk_blocked", "risk_needs_review", "unknown"],
        },
        "risk_reasons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "level": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["rule_id", "level", "reason"],
            },
        },
        "required_slot": {"type": ["string", "null"]},
        "suggested_slot": {"type": ["string", "null"]},
        "slot_changed": {"type": "boolean"},
        "pending_confirmation": {"type": "array", "items": {"type": "string"}},
        "draft": {
            "type": ["object", "null"],
            "properties": {
                "saved": {"type": "boolean"},
                "draft_id": {"type": ["string", "null"]},
                "status": {
                    "type": "string",
                    "enum": ["saved_pending_confirmation", "existing_draft", "failed"],
                },
            },
            "required": ["saved", "status"],
        },
    },
    "required": ["asset_id", "review_status"],
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_pending_assets",
            "description": "列出状态为 pending 的待处理素材。可选按账号过滤。",
            "parameters": {
                "type": "object",
                "properties": {"account_id": {"type": "string", "description": "账号 ID，如 A07"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_asset",
            "description": "读取素材主数据。返回中 missing_fields 非空表示字段缺失，需要补查。",
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_asset_detail",
            "description": "补查素材详情（模拟从内容库 CMS 取回缺失字段）。仅在 get_asset 返回 missing_fields 时使用。",
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_rules",
            "description": (
                "检查规则。不带 slot 时只查素材级规则（敏感类目 R1）；"
                "带 slot 时额外检查时段规则：R2 同账号间隔≥120分钟、R3 单日≤3条、"
                "R4 维护窗口、R5 偏离用户硬性要求。返回 risks 中 level=block 表示不可排期。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "account_id": {"type": "string"},
                    "slot": {"type": "string", "description": "候选时段 HH:MM，如 20:00。不传则只做素材级检查"},
                },
                "required": ["asset_id", "account_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_slots",
            "description": "查询某账号某天 17:00-23:00 各时段的占用情况。注意：available=true 只代表该时段没有完全重合的排期，不代表符合间隔规则，仍需对候选时段调用 check_rules。",
            "parameters": {
                "type": "object",
                "properties": {"account_id": {"type": "string"}, "date": {"type": "string"}},
                "required": ["account_id", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_existing_draft",
            "description": "查询是否已存在草稿，用于防止重复提交。命中时不要再次调用 save_draft。",
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "slot": {"type": "string", "description": "可选，按具体时段查"},
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_draft",
            "description": "保存排期草稿（只存草稿，不具备发布能力）。若返回 ok=false，请根据 error.retryable 决定是否再试一次，不要把失败当作成功。",
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "slot": {"type": "string"},
                    "deviation_from_required": {
                        "type": "boolean",
                        "description": "该时段是否偏离了用户的硬性要求",
                    },
                    "note": {"type": "string", "description": "给运营看的备注"},
                },
                "required": ["asset_id", "slot", "deviation_from_required"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "提交最终结果并结束任务。这是唯一的结束方式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_status": {
                        "type": "string",
                        "enum": ["completed", "pending_confirmation", "blocked", "incomplete", "step_limit"],
                    },
                    "items": {"type": "array", "items": _ITEM_SCHEMA},
                    "summary": {"type": "string"},
                    "incomplete_reason": {
                        "type": ["string", "null"],
                        "description": "task_status 为 incomplete 或 step_limit 时必填",
                    },
                },
                "required": ["task_status", "items", "summary"],
            },
        },
    },
]

TOOL_NAMES = {spec["function"]["name"] for spec in TOOL_SPECS}
REQUIRED_ARGS = {
    spec["function"]["name"]: list(spec["function"]["parameters"].get("required", []))
    for spec in TOOL_SPECS
}


class ToolBox:
    def __init__(self, store: DataStore, faults: FaultInjector) -> None:
        self.store = store
        self.faults = faults
        self.call_counts: dict[str, int] = {}

    def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return {"ok": False, "error": {"code": "unknown_tool", "message": f"未知工具 {name}"}}

        # 参数校验放在故障注入之前：格式错误不该消耗故障次数，
        # 且错误信息里带上必需参数，模型才有机会据此自我修正。
        required = REQUIRED_ARGS.get(name, [])
        missing = [key for key in required if key not in args or args[key] is None]
        if missing:
            return {
                "ok": False,
                "error": {
                    "code": "bad_arguments",
                    "message": f"缺少必需参数：{', '.join(missing)}。本工具必需参数：{', '.join(required)}，请补全后重试。",
                    "required": required,
                    "retryable": True,
                },
            }

        self.call_counts[name] = self.call_counts.get(name, 0) + 1
        failed = self.faults.maybe_fail(name)
        if failed is not None:
            return failed

        try:
            return handler(**args)
        except TypeError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "bad_arguments",
                    "message": f"参数不合法：{exc}。本工具必需参数：{', '.join(required) or '无'}，请补全后重试。",
                    "required": required,
                    "retryable": True,
                },
            }
        except Exception as exc:  # 工具内部异常也要变成可观察的失败
            return {"ok": False, "error": {"code": "internal_error", "message": str(exc)}}

    # ---- 实现 ----
    def tool_list_pending_assets(self, account_id: str | None = None) -> dict:
        items = [
            {"asset_id": a["asset_id"], "title": a.get("title"), "account_id": a.get("account_id")}
            for a in self.store.all_assets(account_id)
        ]
        return {"ok": True, "count": len(items), "assets": items}

    def tool_get_asset(self, asset_id: str) -> dict:
        asset = self.store.get_asset(asset_id)
        if asset is None:
            return {"ok": False, "error": {"code": "asset_not_found", "message": f"素材 {asset_id} 不存在"}}
        missing = [f for f in ("category",) if asset.get(f) in (None, "")]
        return {"ok": True, "asset": dict(asset), "missing_fields": missing}

    def tool_get_asset_detail(self, asset_id: str) -> dict:
        if not self.store.has_detail(asset_id):
            return {
                "ok": False,
                "error": {
                    "code": "no_detail_source",
                    "message": f"内容库中没有素材 {asset_id} 的补充信息",
                    "retryable": False,
                },
            }
        asset = self.store.apply_detail(asset_id)
        return {
            "ok": True,
            "asset": dict(asset or {}),
            "source": (asset or {}).get("detail_source", "cms.asset_meta"),
            "missing_fields": [f for f in ("category",) if (asset or {}).get(f) in (None, "")],
        }

    def tool_check_rules(self, asset_id: str, account_id: str, slot: str | None = None) -> dict:
        return rules_engine.evaluate(self.store, asset_id, account_id, slot)

    def tool_query_slots(self, account_id: str, date: str) -> dict:
        bookings = self.store.bookings_for(account_id)
        occupied = {b["slot"]: b["asset_id"] for b in bookings}
        slots = []
        for hour in SLOT_HOURS:
            time = f"{hour:02d}:00"
            slots.append(
                {
                    "time": time,
                    "available": time not in occupied,
                    "occupied_by": occupied.get(time),
                }
            )
        return {
            "ok": True,
            "account_id": account_id,
            "date": date,
            "slots": slots,
            "bookings": [{"slot": b["slot"], "asset_id": b["asset_id"]} for b in bookings],
            "note": "available 只表示该整点未被占用，仍需 check_rules 校验间隔与日上限",
        }

    def tool_find_existing_draft(self, asset_id: str, slot: str | None = None) -> dict:
        drafts = self.store.find_drafts(asset_id=asset_id, slot=slot)
        return {"ok": True, "count": len(drafts), "drafts": drafts, "exists": bool(drafts)}

    def tool_save_draft(
        self,
        asset_id: str,
        slot: str,
        deviation_from_required: bool = False,
        note: str = "",
    ) -> dict:
        asset = self.store.get_asset(asset_id)
        if asset is None:
            return {"ok": False, "error": {"code": "asset_not_found", "message": f"素材 {asset_id} 不存在"}}

        # 工具层守门：拦截越权写入（模型跳过 check_rules 也照样拦住）
        check = rules_engine.evaluate(self.store, asset_id, asset["account_id"], slot)
        if not check.get("ok"):
            return check
        blocking = [r for r in check["risks"] if r["level"] == "block"]
        if blocking:
            return {
                "ok": False,
                "error": {
                    "code": "rule_violation",
                    "message": "；".join(f"{r['rule_id']} {r['reason']}" for r in blocking),
                    "retryable": False,
                },
            }

        required = asset.get("required_time")
        deviation = bool(deviation_from_required or (required and slot != required))
        created, draft = self.store.save_draft(
            asset_id, slot, asset["account_id"], deviation, note
        )
        return {
            "ok": True,
            "draft_id": draft["draft_id"],
            "draft_status": draft["status"],
            "created": created,
            "deduplicated": not created,
            "deviation_from_required": draft["deviation_from_required"],
        }
