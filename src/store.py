"""数据访问层：素材、排期、草稿，以及故障注入器。

草稿在存储层就是幂等的（draft_key = asset_id + slot），这样即使模型重复调用
save_draft，也不会产生第二份草稿。模型侧的 find_existing_draft 是行为层保险，
两层分开，测试里可以分别验证。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class FaultInjector:
    """按场景配置让指定工具的前 N 次调用失败，模拟存储/网络故障。"""

    def __init__(self, faults: dict[str, dict[str, Any]] | None = None) -> None:
        self._faults = faults or {}
        self._calls: dict[str, int] = {}

    def maybe_fail(self, tool: str) -> dict[str, Any] | None:
        cfg = self._faults.get(tool)
        if not cfg:
            return None
        n = self._calls.get(tool, 0) + 1
        self._calls[tool] = n
        if n <= int(cfg.get("fail_times", 0)):
            return {
                "ok": False,
                "error": {
                    "code": cfg.get("code", "tool_error"),
                    "message": cfg.get("message", "工具调用失败"),
                    "retryable": bool(cfg.get("retryable", True)),
                },
            }
        return None

    def call_count(self, tool: str) -> int:
        return self._calls.get(tool, 0)


class DataStore:
    def __init__(
        self,
        data_dir: Path | str,
        seed_bookings: list[dict] | None = None,
        seed_drafts: list[dict] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        raw_assets = json.loads((self.data_dir / "assets.json").read_text(encoding="utf-8"))
        self._assets: dict[str, dict] = {
            a["asset_id"]: dict(a) for a in raw_assets["assets"]
        }
        self.rules: dict = json.loads((self.data_dir / "rules.json").read_text(encoding="utf-8"))

        detail_path = self.data_dir / "asset_details.json"
        self._details: dict = (
            json.loads(detail_path.read_text(encoding="utf-8")) if detail_path.exists() else {}
        )

        if seed_bookings is not None:
            self._bookings = [dict(b) for b in seed_bookings]
        else:
            raw = json.loads((self.data_dir / "bookings.json").read_text(encoding="utf-8"))
            self._bookings = [dict(b) for b in raw["bookings"]]

        self._drafts: list[dict] = [dict(d) for d in (seed_drafts or [])]
        self._draft_by_key: dict[str, dict] = {
            self._key(d["asset_id"], d["slot"]): d for d in self._drafts
        }

    # ---- 素材 ----
    def get_asset(self, asset_id: str) -> dict | None:
        return self._assets.get(asset_id)

    def all_assets(self, account_id: str | None = None) -> list[dict]:
        items = [a for a in self._assets.values() if a.get("status") == "pending"]
        if account_id:
            items = [a for a in items if a.get("account_id") == account_id]
        return items

    def apply_detail(self, asset_id: str) -> dict | None:
        """模拟内容库补查：把缺失字段合并进主数据，并记录来源。"""
        asset = self._assets.get(asset_id)
        detail = self._details.get(asset_id)
        if asset is None or not isinstance(detail, dict):
            return None
        for key, value in detail.items():
            if asset.get(key) in (None, ""):
                asset[key] = value
        asset["detail_source"] = detail.get("source", "cms.asset_meta")
        asset["detail_note"] = detail.get("review_note", "")
        return asset

    def has_detail(self, asset_id: str) -> bool:
        return isinstance(self._details.get(asset_id), dict)

    # ---- 排期 ----
    def bookings_for(self, account_id: str) -> list[dict]:
        return [b for b in self._bookings if b.get("account_id") == account_id]

    # ---- 草稿 ----
    @staticmethod
    def _key(asset_id: str, slot: str) -> str:
        return f"{asset_id}|{slot}"

    def find_drafts(self, asset_id: str | None = None, slot: str | None = None) -> list[dict]:
        items = self._drafts
        if asset_id:
            items = [d for d in items if d["asset_id"] == asset_id]
        if slot:
            items = [d for d in items if d["slot"] == slot]
        return [dict(d) for d in items]

    def save_draft(
        self, asset_id: str, slot: str, account_id: str, deviation: bool, note: str = ""
    ) -> tuple[bool, dict]:
        """返回 (是否新建, 草稿)。相同 asset+slot 只会有一份。"""
        key = self._key(asset_id, slot)
        if key in self._draft_by_key:
            return False, dict(self._draft_by_key[key])
        draft_id = "D-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8].upper()
        draft = {
            "draft_id": draft_id,
            "asset_id": asset_id,
            "slot": slot,
            "account_id": account_id,
            "status": "saved_pending_confirmation",
            "deviation_from_required": bool(deviation),
            "note": note,
        }
        self._drafts.append(draft)
        self._draft_by_key[key] = draft
        return True, dict(draft)

    def drafts_snapshot(self) -> list[dict]:
        return [dict(d) for d in self._drafts]
