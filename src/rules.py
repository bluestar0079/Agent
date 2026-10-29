"""规则引擎。规则参数全部来自 data/rules.json，改数据即可改行为。"""

from __future__ import annotations

from .store import DataStore


def to_minutes(hhmm: str) -> int:
    hour, _, minute = hhmm.partition(":")
    return int(hour) * 60 + int(minute)


def evaluate(
    store: DataStore, asset_id: str, account_id: str, slot: str | None = None
) -> dict:
    """返回 {ok, asset_id, slot, risks[], missing_fields[]}。

    slot 为空时只做素材级检查（类目）；带 slot 时额外检查时段类规则。
    """
    asset = store.get_asset(asset_id)
    if asset is None:
        return {
            "ok": False,
            "error": {"code": "asset_not_found", "message": f"素材 {asset_id} 不存在"},
        }

    rules = store.rules
    risks: list[dict] = []
    missing: list[str] = []

    # R1 敏感类目
    category = asset.get("category")
    if category in (None, ""):
        missing.append("category")
        risks.append(
            {
                "rule_id": "R1",
                "level": "unknown",
                "reason": "缺少 category 字段，无法判定类目风险，需要先补查素材详情",
            }
        )
    elif category in rules["R1"]["sensitive_categories"]:
        risks.append(
            {
                "rule_id": "R1",
                "level": "block",
                "reason": f"类目「{category}」属于敏感类目，必须经法务审核后才能排期",
            }
        )

    if slot:
        required = asset.get("required_time")
        start = to_minutes(rules["R4"]["maintenance_start"])
        end = to_minutes(rules["R4"]["maintenance_end"])
        slot_minutes = to_minutes(slot)

        # R4 维护窗口
        if start <= slot_minutes < end:
            risks.append(
                {
                    "rule_id": "R4",
                    "level": "block",
                    "reason": f"{slot} 落在系统维护窗口 "
                    f"{rules['R4']['maintenance_start']}-{rules['R4']['maintenance_end']} 内，不可发布",
                }
            )

        # R2 同账号发布间隔
        min_gap = rules["R2"]["min_gap_minutes"]
        bookings = store.bookings_for(account_id)
        violations = [
            (abs(to_minutes(b["slot"]) - slot_minutes), b["slot"])
            for b in bookings
            if abs(to_minutes(b["slot"]) - slot_minutes) < min_gap
        ]
        if violations:
            gap, busy_slot = min(violations)
            risks.append(
                {
                    "rule_id": "R2",
                    "level": "block",
                    "reason": f"{slot} 距同账号同日 {busy_slot} 的排期仅 {gap} 分钟，"
                    f"要求至少 {min_gap} 分钟",
                }
            )

        # R3 单日条数上限
        max_per_day = rules["R3"]["max_per_day"]
        if len(bookings) >= max_per_day:
            risks.append(
                {
                    "rule_id": "R3",
                    "level": "block",
                    "reason": f"该账号当天已有 {len(bookings)} 条排期，达到上限 {max_per_day} 条",
                }
            )

        # R5 硬性要求偏离
        if required and slot != required:
            risks.append(
                {
                    "rule_id": "R5",
                    "level": "confirm",
                    "reason": f"用户硬性要求 {required}，建议时段 {slot} 与之不同，需用户确认后才能生效",
                }
            )

    return {"ok": True, "asset_id": asset_id, "slot": slot, "risks": risks, "missing_fields": missing}


def has_blocking(risks: list[dict]) -> bool:
    return any(r.get("level") == "block" for r in risks)
