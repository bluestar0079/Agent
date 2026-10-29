"""工具层单测：时段查询、规则判定、草稿幂等、越权守门、参数错误可自愈。"""

from __future__ import annotations

from src.store import DataStore, FaultInjector
from src.tools import ToolBox
from helpers import DATA


def make_toolbox(bookings=None, drafts=None, faults=None):
    # 默认空排期，避免测试之间被 data/bookings.json 的默认数据干扰
    store = DataStore(DATA, seed_bookings=bookings or [], seed_drafts=drafts or [])
    return store, ToolBox(store, FaultInjector(faults))


def test_query_slots_marks_occupied():
    _, tb = make_toolbox(bookings=[{"account_id": "A07", "slot": "18:00", "asset_id": "X02"}])
    obs = tb.dispatch("query_slots", {"account_id": "A07", "date": "2026-11-02"})
    slots = {s["time"]: s for s in obs["slots"]}
    assert slots["18:00"]["available"] is False
    assert slots["18:00"]["occupied_by"] == "X02"
    assert slots["19:00"]["available"] is True


def test_check_rules_blocks_interval_violation():
    """19:00 空闲，但距 18:00 排期只有 60 分钟，必须被 R2 阻断。"""
    _, tb = make_toolbox(
        bookings=[{"account_id": "A07", "slot": "18:00", "asset_id": "X02"}]
    )
    obs = tb.dispatch("check_rules", {"asset_id": "M01", "account_id": "A07", "slot": "19:00"})
    assert any(r["rule_id"] == "R2" and r["level"] == "block" for r in obs["risks"])


def test_check_rules_passes_at_two_hour_gap():
    _, tb = make_toolbox(
        bookings=[{"account_id": "A07", "slot": "18:00", "asset_id": "X02"}]
    )
    obs = tb.dispatch("check_rules", {"asset_id": "M01", "account_id": "A07", "slot": "20:00"})
    assert not [r for r in obs["risks"] if r["level"] == "block"]
    assert any(r["rule_id"] == "R5" and r["level"] == "confirm" for r in obs["risks"])


def test_save_draft_is_idempotent():
    store, tb = make_toolbox()
    first = tb.dispatch("save_draft", {"asset_id": "M01", "slot": "18:00", "deviation_from_required": False})
    second = tb.dispatch("save_draft", {"asset_id": "M01", "slot": "18:00", "deviation_from_required": False})
    assert first["created"] is True
    assert second["created"] is False and second["deduplicated"] is True
    assert first["draft_id"] == second["draft_id"]
    assert len(store.drafts_snapshot()) == 1


def test_save_draft_guard_rejects_blocked_slot():
    """模型跳过 check_rules 直接保存非法时段，工具层必须拦住。"""
    _, tb = make_toolbox(
        bookings=[{"account_id": "A07", "slot": "18:00", "asset_id": "X02"}]
    )
    obs = tb.dispatch("save_draft", {"asset_id": "M01", "slot": "19:00", "deviation_from_required": True})
    assert obs["ok"] is False
    assert obs["error"]["code"] == "rule_violation"


def test_missing_args_error_is_actionable():
    _, tb = make_toolbox()
    obs = tb.dispatch("save_draft", {"asset_id": "M01"})
    assert obs["ok"] is False
    assert obs["error"]["code"] == "bad_arguments"
    assert "slot" in obs["error"]["message"]
    assert obs["error"]["retryable"] is True
    assert "slot" in obs["error"]["required"]


def test_get_asset_detail_fills_missing_category():
    _, tb = make_toolbox()
    before = tb.dispatch("get_asset", {"asset_id": "M02"})
    assert before["missing_fields"] == ["category"]
    after = tb.dispatch("get_asset_detail", {"asset_id": "M02"})
    assert after["ok"] is True
    assert after["asset"]["category"] == "医疗健康"


def test_fault_injection_can_fail_save_once():
    _, tb = make_toolbox(faults={"save_draft": {"fail_times": 1, "code": "storage_unavailable"}})
    first = tb.dispatch("save_draft", {"asset_id": "M01", "slot": "18:00", "deviation_from_required": False})
    second = tb.dispatch("save_draft", {"asset_id": "M01", "slot": "18:00", "deviation_from_required": False})
    assert first["ok"] is False
    assert second["ok"] is True
