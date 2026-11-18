"""字段映射版本表业务规则。

优化点：旧的字段映射校验每次都要重复扫描全部明细表，生效状态延迟。
本模块在登记/解析映射规则时一次性把逗号分隔的源字段串拆成逐字段版本行，
版本表只保留「源字段、目标字段、生效状态」，后续查询与联动都走版本表，
不再回扫明细全表。

关键约束：
- 版本状态只能按 草稿 → 待校验 → 已生效 → 已废弃 顺序推进，不能跳级；
- 非法值一律按功能优化失败处理，不产生部分写入；
- 「标记生效」与 DWD 明细刷新标记必须同时落库，任一步失败整体回滚；
- 两个已生效版本写同一目标字段属于冲突，只做检测与标记，不裁决谁优先。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "column_mapping_version"
# 版本表保留的核心字段：源字段、目标字段与生效状态（生效状态落在 status 列）
LIST_FIELDS = ["版本编号", "规则编号", "源字段", "目标字段", "数据类型", "登记顺序", "生效日期"]
REQUIRED_FIELDS = ["规则编号", "源字段", "目标字段"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
# 允许的列表排序字段（白名单，防止非法排序参数）
SORTABLE_FIELDS = ["id", "登记顺序", "生效日期", "版本编号", "规则编号", "源字段", "目标字段"]


class MappingVersionError(Exception):
    """功能优化失败：携带可读原因，由路由层转成 ok=False。"""


class ColumnMappingVersionService:
    # ------------------------------------------------------------------
    # 列表：筛选、排序、翻页、检索、定位
    # ------------------------------------------------------------------
    def list_versions(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        sort_by: str = "登记顺序",
        sort_order: str = "asc",
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [dict(row) for row in store.rows(MODULE)]
        for field, expected in (filters or {}).items():
            if field not in LIST_FIELDS:
                # 不在声明范围内的筛选字段属于非法值，按功能优化失败处理
                raise MappingVersionError(f"筛选字段「{field}」不在字段映射版本表范围内")
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            # 检索同时覆盖规则编号、源字段、目标字段，便于沿数据流定位映射结果
            kw = keyword.casefold()
            rows = [
                row for row in rows
                if kw in str(row.get("规则编号", "")).casefold()
                or kw in str(row.get("源字段", "")).casefold()
                or kw in str(row.get("目标字段", "")).casefold()
            ]
        if status:
            if status not in STATUS_ORDER:
                raise MappingVersionError(f"生效状态「{status}」非法，可选：{'、'.join(STATUS_ORDER)}")
            rows = [row for row in rows if row.get("status") == status]
        rows = self._sort_rows(rows, sort_by, sort_order)
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def _sort_rows(self, rows: list[dict[str, Any]], sort_by: str, sort_order: str) -> list[dict[str, Any]]:
        if sort_by not in SORTABLE_FIELDS:
            raise MappingVersionError(f"排序字段「{sort_by}」非法，可选：{'、'.join(SORTABLE_FIELDS)}")
        if sort_order not in ("asc", "desc"):
            raise MappingVersionError("排序方向只支持 asc 或 desc")
        reverse = sort_order == "desc"

        def key(row: dict[str, Any]) -> Any:
            value = row.get(sort_by, "")
            if sort_by == "id" or sort_by == "登记顺序":
                return int(value or 0)
            return str(value or "")

        return sorted(rows, key=key, reverse=reverse)

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, version_id)

    def get_versions_by_rule(self, rule_no: str) -> list[dict[str, Any]]:
        """沿数据流定位：按规则编号取回该规则解析出的全部字段版本。"""
        return store.find_all_by_field(MODULE, "规则编号", rule_no)

    # ------------------------------------------------------------------
    # 解析源字段映射规则 → 生成字段映射版本表
    # ------------------------------------------------------------------
    def parse_and_register(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        """把一条映射规则（逗号分隔的字段串）解析成逐字段版本行并落库。

        非法值（缺必填、字段段数对不齐、空字段）按功能优化失败处理：
        抛出 MappingVersionError，不写入任何半截版本行。
        """
        rule_no = str(rule.get("规则编号") or "").strip()
        src_raw = str(rule.get("源字段名") or "").strip()
        tgt_raw = str(rule.get("目标字段名") or "").strip()
        if not rule_no or not src_raw or not tgt_raw:
            raise MappingVersionError("映射规则缺少规则编号、源字段名或目标字段名，解析失败")
        src_fields = [item.strip() for item in src_raw.split(",")]
        tgt_fields = [item.strip() for item in tgt_raw.split(",")]
        if not src_fields or not tgt_fields or len(src_fields) != len(tgt_fields):
            raise MappingVersionError(
                f"规则 {rule_no} 源字段与目标字段段数不一致（{len(src_fields)}/{len(tgt_fields)}），解析失败")
        if any(not item for item in src_fields + tgt_fields):
            raise MappingVersionError(f"规则 {rule_no} 存在空字段名，解析失败")
        dtype_fields = [item.strip() for item in str(rule.get("数据类型") or "").split(",")]
        if len(dtype_fields) not in (1, len(src_fields)):
            raise MappingVersionError(
                f"规则 {rule_no} 数据类型段数 {len(dtype_fields)} 与字段数 {len(src_fields)} 对不齐，解析失败")

        rows = store.rows(MODULE)
        # 同一规则重复解析视为优化失败，避免版本表出现重复当前版本
        if any(row.get("规则编号") == rule_no for row in rows):
            raise MappingVersionError(f"规则 {rule_no} 已生成字段映射版本，不能重复解析")

        seq_base = max((int(row.get("登记顺序", 0)) for row in rows), default=0)
        effective_date = str(rule.get("生效日期") or "").strip() or date.today().isoformat()
        created: list[dict[str, Any]] = []
        next_id = max((int(row.get("id", 0)) for row in rows), default=0)
        for index, (src, tgt) in enumerate(zip(src_fields, tgt_fields), start=1):
            seq = seq_base + index
            dtype = dtype_fields[0] if len(dtype_fields) == 1 else dtype_fields[index - 1]
            next_id += 1
            entry = {
                "id": next_id,
                "版本编号": f"MV-{seq:04d}",
                "规则编号": rule_no,
                "源字段": src,
                "目标字段": tgt,
                "数据类型": dtype,
                "登记顺序": seq,
                "生效日期": effective_date,
                "来源批次": rule.get("来源批次", ""),
                "status": STATUS_ORDER[0],
                "pending": True,
                "abnormal": False,
                "冲突标记": False,
                "冲突说明": "",
            }
            rows.append(entry)
            created.append(entry)
        return created

    # ------------------------------------------------------------------
    # 状态机：按顺序推进，不能跳级
    # ------------------------------------------------------------------
    def run_action(self, version_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, version_id)
        if entry is None:
            return None, f"字段映射版本 {version_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于字段映射版本表可执行范围"
        current_idx = STATUS_ORDER.index(entry["status"])
        target_idx = STATUS_ORDER.index(ACTION_RULES[action])
        if target_idx <= current_idx:
            return None, f"状态只能按顺序推进：当前{entry['status']}，不能执行{action}"
        if target_idx != current_idx + 1:
            # 围绕字段映射按顺序推进且不能跳级
            return None, f"状态不能跳级：当前{entry['status']}，需先进入{STATUS_ORDER[current_idx + 1]}"

        if action == "标记生效":
            return self._activate(entry)
        # 提交校验 / 标记废弃：单表状态推进
        entry["status"] = ACTION_RULES[action]
        entry["pending"] = entry["status"] != STATUS_ORDER[-1]
        store.save_version(MODULE, version_id, entry)
        return entry, f"字段映射版本已{action}"

    def _activate(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """标记生效：版本生效与 DWD 明细刷新标记原子落库。"""
        snapshot = dict(entry)
        mark_ids: list[int] = []
        try:
            entry["status"] = "已生效"
            entry["pending"] = False
            # 冲突检测：同一目标字段已有其它已生效版本（不裁决优先级，只标记）
            conflicts = [
                row for row in store.rows(MODULE)
                if row["id"] != entry["id"]
                and row.get("目标字段") == entry.get("目标字段")
                and row.get("status") == "已生效"
            ]
            if conflicts:
                entry["冲突标记"] = True
                peer = "、".join(str(row.get("版本编号")) for row in conflicts)
                entry["冲突说明"] = f"目标字段「{entry['目标字段']}」与已生效版本 {peer} 冲突，处理先后待定"
                for row in conflicts:
                    row["冲突标记"] = True
                    row["冲突说明"] = (
                        f"目标字段「{entry['目标字段']}」与已生效版本 {entry['版本编号']} 冲突，处理先后待定"
                    )
            # 同事务写 DWD 明细刷新标记，刷新原因定位到规则编号
            mark_ids = self._write_refresh_marks(entry)
            store.save_version(MODULE, entry["id"], entry)
        except Exception:
            # 任一步不一致：回滚版本状态与本次刷新标记，保证两边对得上
            entry.clear()
            entry.update(snapshot)
            store.rollback_refresh_marks(mark_ids)
            raise MappingVersionError("字段映射版本生效与明细刷新标记落库不一致，已整体回滚")
        return entry, f"字段映射版本已标记生效，并同步写入 {len(mark_ids)} 条明细刷新标记"

    def _write_refresh_marks(self, entry: dict[str, Any]) -> list[int]:
        """找到消费该映射目标字段的 DWD 明细表，逐表写刷新标记。"""
        target = entry.get("目标字段", "")
        rule_no = entry.get("规则编号", "")
        affected = self._find_affected_dwd_tables(target)
        if not affected:
            # 没有下游明细表也要显式落一条空定位标记，证明生效动作确实执行过刷新联动
            affected = [{"表名称": "", "表编号": ""}]
        ids: list[int] = []
        for dwd in affected:
            mark = store.add_refresh_mark({
                "规则编号": rule_no,
                "版本编号": entry.get("版本编号", ""),
                "源字段": entry.get("源字段", ""),
                "目标字段": target,
                "明细表编号": dwd.get("表编号", ""),
                "明细表名称": dwd.get("表名称", ""),
                "刷新原因": f"字段映射规则 {rule_no} 生效，目标字段 {target} 需要刷新",
                "刷新水位": date.today().isoformat(),
                "刷新状态": "待刷新",
            })
            ids.append(int(mark["id"]))
        return ids

    def _find_affected_dwd_tables(self, target_field: str) -> list[dict[str, Any]]:
        """沿数据流定位下游：DWD 明细表的关联键列/ODS 字段列表命中目标字段即受影响。"""
        affected: list[dict[str, Any]] = []
        for dwd in store.rows("dwd_layer"):
            hit = target_field in str(dwd.get("关联键列", "")).split(",")
            if not hit:
                parent = str(dwd.get("父层来源", ""))
                for ods in store.rows("ods_layer"):
                    if parent in (str(ods.get("表编号", "")), str(ods.get("表名称", ""))):
                        fields = str(ods.get("字段列表", "")).split(",")
                        hit = target_field in [f.strip() for f in fields]
                        break
            if hit:
                affected.append({"表编号": dwd.get("表编号", ""), "表名称": dwd.get("表名称", "")})
        return affected

    # ------------------------------------------------------------------
    # 冲突视图：两条边界排序，不裁决谁优先
    # ------------------------------------------------------------------
    def current_field_list(self, sort_by: str = "生效日期") -> list[dict[str, Any]]:
        """当前字段列表：只取已生效版本。

        - sort_by=生效日期：按生效日期排序的当前字段列表；
        - sort_by=登记顺序：按登记顺序排序的当前字段列表。
        """
        if sort_by not in ("生效日期", "登记顺序"):
            raise MappingVersionError("当前字段列表只支持按生效日期或登记顺序排序")
        rows = [row for row in store.rows(MODULE) if row.get("status") == "已生效"]
        return self._sort_rows(rows, sort_by, "asc")

    def list_conflicts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in store.rows(MODULE) if row.get("冲突标记")]

    def list_refresh_marks(self, *, rule_no: str | None = None,
                           table_name: str | None = None) -> list[dict[str, Any]]:
        return store.get_refresh_marks(rule_no=rule_no, table_name=table_name)
