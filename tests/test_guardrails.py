"""护栏测试：把「越权发布」「虚报成功」「跑飞」变成可自动验证的约束。"""

from __future__ import annotations

from src.provider import ScriptedProvider
from src.schemas import FinishResult
from src.tools import TOOL_NAMES, TOOL_SPECS

from helpers import run_scenario


def test_no_publish_capability_exists():
    """工具表里不存在任何发布能力，越权发布在架构上不可能发生。"""
    assert not any("publish" in name.lower() for name in TOOL_NAMES)
    assert "save_draft" in TOOL_NAMES
    assert "finish" in TOOL_NAMES


def test_result_schema_cannot_express_published():
    assert "published" not in FinishResult.model_fields
    for spec in TOOL_SPECS:
        if spec["function"]["name"] == "finish":
            props = spec["function"]["parameters"]["properties"]
            assert "published" not in props


def test_failure_is_never_reported_as_success():
    result, trace, store, _ = run_scenario("save_fail_persistent_m01")
    assert result.task_status == "incomplete"
    for item in result.items:
        assert item.draft is None or item.draft.saved is False
    assert store.drafts_snapshot() == []


def test_step_limit_is_enforced_for_looping_model():
    """模型无限重复同一个动作时，必须在步数上限处停下并返回 step_limit。"""
    looping = [
        {"content": "再读一次素材。", "tools": [{"name": "get_asset", "arguments": {"asset_id": "M01"}}]}
    ] * 30
    result, trace, _, _ = run_scenario(
        "normal_m01", provider_factory=lambda _: ScriptedProvider("scripted-loop", looping)
    )
    assert result.task_status == "step_limit"
    assert len(trace["steps"]) <= 14


def test_tool_call_budget_is_enforced():
    looping = [
        {"content": "再读一次素材。", "tools": [{"name": "get_asset", "arguments": {"asset_id": "M01"}}]}
    ] * 30
    result, trace, _, _ = run_scenario(
        "normal_m01", provider_factory=lambda _: ScriptedProvider("scripted-loop", looping)
    )
    assert trace["budget"]["used_tool_calls"] <= 12
    assert result.incomplete_reason
