"""finish 载荷的 schema 校验。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas import FinishResult


def test_minimal_completed_is_valid():
    result = FinishResult(task_status="completed", items=[], summary="ok")
    assert result.task_status == "completed"
    assert result.incomplete_reason is None


@pytest.mark.parametrize("status", ["incomplete", "step_limit"])
def test_incomplete_status_requires_reason(status):
    with pytest.raises(ValidationError):
        FinishResult(task_status=status, items=[], summary="x")


def test_unknown_status_is_rejected():
    with pytest.raises(ValidationError):
        FinishResult(task_status="published", items=[], summary="x")


def test_invalid_review_status_is_rejected():
    with pytest.raises(ValidationError):
        FinishResult(
            task_status="completed",
            items=[{"asset_id": "M01", "review_status": "looks_fine"}],
            summary="x",
        )


def test_item_keeps_required_slot_separate_from_suggested_slot():
    result = FinishResult(
        task_status="pending_confirmation",
        items=[
            {
                "asset_id": "M04",
                "review_status": "risk_needs_review",
                "required_slot": "19:00",
                "suggested_slot": "20:00",
                "slot_changed": True,
                "pending_confirmation": ["时段变更需确认"],
                "draft": {"saved": True, "draft_id": "D-1", "status": "saved_pending_confirmation"},
            }
        ],
        summary="x",
    )
    item = result.items[0]
    assert item.required_slot == "19:00"
    assert item.suggested_slot == "20:00"
    assert item.slot_changed is True
