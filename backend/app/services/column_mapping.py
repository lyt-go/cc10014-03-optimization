"""字段映射业务规则：支持从入库批次提取源字段，映射到目标字段，并驱动ODS表结构创建。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "column_mapping"
LIST_FIELDS = ["规则编号", "源字段名", "目标字段名", "数据类型", "默认值", "转换函数", "规则版本", "生效日期"]
REQUIRED_FIELDS = ["规则编号", "源字段名", "目标字段名"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []


class ColumnMappingService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 关联来源批次
        if values.get("来源批次"):
            entry["来源批次"] = values["来源批次"]
        rows.append(entry)
        # 解析源字段映射规则并生成字段映射版本表，替代旧的重复全表扫描校验。
        # 非法值按功能优化失败处理：规则保留登记，但标记异常并记录失败原因，不产生半截版本。
        from app.services.column_mapping_version import (
            ColumnMappingVersionService,
            MappingVersionError,
        )
        try:
            ColumnMappingVersionService().parse_and_register(entry)
            entry["版本解析状态"] = "已解析"
        except MappingVersionError as exc:
            entry["abnormal"] = True
            entry["版本解析状态"] = f"解析失败：{exc}"
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"映射规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于字段映射可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.save_version(MODULE, entry_id, entry)
        return entry, f"映射规则已{action}"

    def get_mappings_by_batch(self, batch_no: str) -> list[dict[str, Any]]:
        return store.find_all_by_field(MODULE, "来源批次", batch_no)
