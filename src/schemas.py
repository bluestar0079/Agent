"""交付结果的强类型定义。finish 工具的参数与 FinishResult 同构，由 pydantic 兜底校验。

注意：这里不存在任何 "published" 字段——越权发布在数据结构上就不可能表达。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

TaskStatus = Literal["completed", "pending_confirmation", "blocked", "incomplete", "step_limit"]
ReviewStatus = Literal["no_risk", "risk_blocked", "risk_needs_review", "unknown"]
DraftStatus = Literal["saved_pending_confirmation", "existing_draft", "failed"]


class RiskItem(BaseModel):
    rule_id: str = Field(description="规则编号，必须来自 check_rules 的返回")
    level: str = Field(description="block / confirm / warn / unknown")
    reason: str = Field(description="面向运营的中文说明")


class DraftInfo(BaseModel):
    saved: bool = Field(description="是否本次真实写入了草稿")
    draft_id: Optional[str] = None
    status: DraftStatus


class FinishItem(BaseModel):
    asset_id: str
    review_status: ReviewStatus
    risk_reasons: list[RiskItem] = Field(default_factory=list)
    required_slot: Optional[str] = Field(default=None, description="用户原始硬性要求，不得被改写")
    suggested_slot: Optional[str] = Field(
        default=None, description="有可排期结论时必填：能通过 check_rules 的那个时段"
    )
    slot_changed: bool = False
    pending_confirmation: list[str] = Field(default_factory=list)
    draft: Optional[DraftInfo] = None

    @model_validator(mode="after")
    def _require_slot_when_schedulable(self) -> "FinishItem":
        """给了「可排期」的结论，就必须说清楚排在几点。

        被规则阻断或信息不足的素材本来就没有时段，这里不强制。
        """
        if self.review_status in ("no_risk", "risk_needs_review") and not self.suggested_slot:
            raise ValueError(
                f"素材 {self.asset_id} 的 review_status 为 {self.review_status}，"
                "必须提供 suggested_slot（填 check_rules 通过的那个时段）；"
                "若确实无法排期，请把 review_status 改为 risk_blocked 或 unknown 并说明原因"
            )
        return self


class FinishResult(BaseModel):
    task_status: TaskStatus
    items: list[FinishItem] = Field(default_factory=list)
    summary: str = ""
    incomplete_reason: Optional[str] = None

    @model_validator(mode="after")
    def _require_reason_when_incomplete(self) -> "FinishResult":
        if self.task_status in ("incomplete", "step_limit") and not self.incomplete_reason:
            raise ValueError("task_status 为 incomplete / step_limit 时必须提供 incomplete_reason")
        return self
