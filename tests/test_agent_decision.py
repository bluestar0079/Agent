"""端到端决策测试：每个用例对应一类业务场景。

全部使用 MockProvider（确定性策略）跑真实的主循环与工具层，
因此断言的是「流程与状态正确」，不代表真实模型的决策质量。
"""

from __future__ import annotations

from helpers import run_scenario, tool_sequence


def first(result):
    return result.items[0]


def test_normal_path_saves_draft_at_required_slot():
    result, trace, store, _ = run_scenario("normal_m01")
    item = first(result)
    assert result.task_status == "completed"
    assert item.suggested_slot == "18:00"
    assert item.slot_changed is False
    assert item.draft.saved is True
    assert len(store.drafts_snapshot()) == 1


def test_conflict_path_switches_slot_and_asks_for_confirmation():
    result, trace, store, _ = run_scenario("conflict_m01")
    item = first(result)
    sequence = tool_sequence(trace)

    assert result.task_status == "pending_confirmation"
    assert item.required_slot == "18:00"          # 原始要求不被改写
    assert item.suggested_slot == "20:00"          # 19:00 违规，落到 20:00
    assert item.slot_changed is True
    assert item.pending_confirmation              # 必须留下待确认项
    assert sequence.count("check_rules") >= 2     # 至少要校验两次时段
    assert len(store.drafts_snapshot()) == 1


def test_missing_metadata_triggers_lookup_then_blocks():
    result, trace, store, _ = run_scenario("missing_meta_m02")
    sequence = tool_sequence(trace)

    assert sequence[1] == "get_asset_detail"      # 先补查再下结论
    assert "save_draft" not in sequence
    assert result.task_status == "blocked"
    assert any(r.rule_id == "R1" for r in first(result).risk_reasons)
    assert store.drafts_snapshot() == []


def test_rule_risk_blocks_without_scheduling():
    result, trace, store, _ = run_scenario("rule_risk_m03")
    assert result.task_status == "blocked"
    assert "save_draft" not in tool_sequence(trace)
    assert store.drafts_snapshot() == []


def test_hard_requirement_is_not_changed_silently():
    result, _, store, _ = run_scenario("hard_requirement_m04")
    item = first(result)
    assert item.required_slot == "19:00"
    assert item.suggested_slot == "20:00"
    assert item.slot_changed is True
    assert any("19:00" in text for text in item.pending_confirmation)
    assert store.drafts_snapshot()[0]["deviation_from_required"] is True


def test_duplicate_submission_does_not_save_again():
    result, trace, store, _ = run_scenario("duplicate_m06")
    item = first(result)

    assert "save_draft" not in tool_sequence(trace)
    assert len(store.drafts_snapshot()) == 1
    assert item.draft.saved is False
    assert item.draft.status == "existing_draft"
    assert item.draft.draft_id == "D-SEED-0001"


def test_save_failure_is_retried_then_reported_truthfully():
    result, trace, store, _ = run_scenario("save_fail_m01")
    assert tool_sequence(trace).count("save_draft") >= 2
    assert first(result).draft.saved is True
    assert len(store.drafts_snapshot()) == 1


def test_persistent_save_failure_returns_incomplete():
    result, _, store, _ = run_scenario("save_fail_persistent_m01")
    assert result.task_status == "incomplete"
    assert result.incomplete_reason
    assert first(result).draft.saved is False
    assert store.drafts_snapshot() == []


def test_step_limit_returns_explicit_incomplete_state():
    result, trace, _, _ = run_scenario("step_limit_m01")
    assert result.task_status == "step_limit"
    assert "步数" in (result.incomplete_reason or "")
    assert len(trace["steps"]) <= 2


def test_batch_processes_every_asset_in_one_run():
    """一批素材里同时出现冲突、阻断、正常三种情况，每条都要有结论。"""
    result, trace, store, _ = run_scenario("batch_mixed_m01_m03_m06")
    by_id = {item.asset_id: item for item in result.items}

    assert len(result.items) == 3
    assert set(by_id) == {"M01", "M03", "M06"}

    assert by_id["M01"].suggested_slot == "20:00"
    assert by_id["M01"].draft.saved is True
    assert by_id["M03"].review_status == "risk_blocked"
    assert by_id["M03"].suggested_slot is None
    assert by_id["M03"].draft is None
    assert by_id["M06"].suggested_slot == "18:00"
    assert by_id["M06"].draft.saved is True

    # 有一条待确认 → 整体是 pending_confirmation；两条草稿，不含被阻断的那条
    assert result.task_status == "pending_confirmation"
    assert len(store.drafts_snapshot()) == 2
    assert tool_sequence(trace).count("get_asset") == 3


def test_two_paths_use_different_tool_sequences():
    """运行数据不同，模型的执行路径就应当不同。"""
    _, normal_trace, _, _ = run_scenario("normal_m01")
    _, conflict_trace, _, _ = run_scenario("conflict_m01")

    normal = tool_sequence(normal_trace)
    conflict = tool_sequence(conflict_trace)

    assert normal != conflict
    assert len(conflict) > len(normal)
    assert conflict.count("check_rules") > normal.count("check_rules")
