"""字段映射版本表专项测试。

覆盖：
- 解析源字段映射规则生成版本表（保留源字段、目标字段、生效状态）
- 非法值按功能优化失败处理，无半截写入
- 状态机按顺序推进、不能跳级、不能回退
- 标记生效与 DWD 明细刷新标记原子落库，失败整体回滚
- 同目标字段冲突检测，两条排序边界均不裁决优先级
- 列表筛选、排序、翻页、检索
- 查询映射后只出现一份当前生效版本，刷新原因可定位规则编号
"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.store import store
from app.services.column_mapping import ColumnMappingService
from app.services.column_mapping_version import (
    ColumnMappingVersionService,
    MappingVersionError,
)


def reset_store() -> None:
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    store._table_data.clear()
    store._versions.clear()
    store._lineage_edges.clear()
    store._quality_results.clear()
    store._snapshot_data.clear()
    store._archive_records.clear()
    store._backfill_partitions.clear()
    store._dwd_refresh_marks.clear()


class TestMappingVersion:
    def setup_method(self):
        reset_store()
        self.svc = ColumnMappingVersionService()

    def test_seed_versions_keep_source_target_and_status(self):
        rows = store.rows("column_mapping_version")
        # 3 条规则 × 每规则 3 个字段 = 9 条版本行
        assert len(rows) == 9
        first = rows[0]
        assert first["源字段"] == "order_id"
        assert first["目标字段"] == "order_id"
        assert first["status"] == "已生效"

    def test_parse_rule_generates_versions(self):
        created = self.svc.parse_and_register({
            "规则编号": "MAP-T-001",
            "源字段名": "a,b",
            "目标字段名": "ta,tb",
            "数据类型": "STRING,INT",
            "生效日期": "2026-11-10",
        })
        assert len(created) == 2
        assert [r["源字段"] for r in created] == ["a", "b"]
        assert [r["目标字段"] for r in created] == ["ta", "tb"]
        assert all(r["status"] == "草稿" for r in created)
        # 登记顺序沿已有版本连续推进，不跳号
        assert [r["登记顺序"] for r in created] == [10, 11]

    def test_illegal_value_fails_without_partial_write(self):
        before = len(store.rows("column_mapping_version"))
        with pytest.raises(MappingVersionError):
            self.svc.parse_and_register({
                "规则编号": "MAP-BAD", "源字段名": "a,b", "目标字段名": "only_one",
            })
        with pytest.raises(MappingVersionError):
            self.svc.parse_and_register({
                "规则编号": "MAP-BAD2", "源字段名": "a,,b", "目标字段名": "ta,,tb",
            })
        assert len(store.rows("column_mapping_version")) == before

    def test_duplicate_parse_rejected(self):
        with pytest.raises(MappingVersionError):
            # 种子里 MAP-ORD-001 已解析过
            self.svc.parse_and_register({
                "规则编号": "MAP-ORD-001",
                "源字段名": "order_id",
                "目标字段名": "order_id",
            })

    def test_state_machine_no_skip_no_rollback(self):
        created = self.svc.parse_and_register({
            "规则编号": "MAP-S", "源字段名": "a", "目标字段名": "ta", "数据类型": "STRING",
        })
        vid = created[0]["id"]
        # 草稿不能直接跳到已生效
        entry, msg = self.svc.run_action(vid, "标记生效")
        assert entry is None and "跳级" in msg
        # 正常推进
        entry, _ = self.svc.run_action(vid, "提交校验")
        assert entry["status"] == "待校验"
        entry, _ = self.svc.run_action(vid, "标记生效")
        assert entry["status"] == "已生效"
        # 不能回退
        entry, msg = self.svc.run_action(vid, "提交校验")
        assert entry is None and "顺序推进" in msg
        # 已生效可废弃
        entry, _ = self.svc.run_action(vid, "标记废弃")
        assert entry["status"] == "已废弃"

    def test_activation_atomic_with_refresh_marks_and_rollback(self):
        created = self.svc.parse_and_register({
            "规则编号": "MAP-LINK", "源字段名": "oid", "目标字段名": "order_id",
            "数据类型": "STRING", "来源批次": "RAW-20261101",
        })
        vid = created[0]["id"]
        self.svc.run_action(vid, "提交校验")
        entry, msg = self.svc.run_action(vid, "标记生效")
        assert entry["status"] == "已生效"
        # 真实下游：DWD_ORD_001 的关联键列含 order_id
        marks = store.get_refresh_marks(rule_no="MAP-LINK")
        assert len(marks) == 1
        assert marks[0]["明细表名称"] == "dwd_order_detail"
        assert "MAP-LINK" in marks[0]["刷新原因"]

        # 注入故障：刷新标记已写、版本保存失败时整体回滚
        fail_rows = self.svc.parse_and_register({
            "规则编号": "MAP-FAIL", "源字段名": "z", "目标字段名": "zzz", "数据类型": "STRING",
        })
        fid = fail_rows[0]["id"]
        self.svc.run_action(fid, "提交校验")
        original = store.save_version
        calls = {"n": 0}

        def boom(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("disk full")
            return original(*args, **kwargs)

        store.save_version = boom
        try:
            with pytest.raises(MappingVersionError):
                self.svc.run_action(fid, "标记生效")
        finally:
            store.save_version = original
        assert store.find("column_mapping_version", fid)["status"] == "待校验"
        assert store.get_refresh_marks(rule_no="MAP-FAIL") == []

    def test_conflict_marked_with_two_sort_boundaries(self):
        first = self.svc.parse_and_register({
            "规则编号": "MAP-C1", "源字段名": "a", "目标字段名": "shared",
            "数据类型": "STRING", "生效日期": "2026-11-05",
        })[0]
        second = self.svc.parse_and_register({
            "规则编号": "MAP-C2", "源字段名": "b", "目标字段名": "shared",
            "数据类型": "STRING", "生效日期": "2026-11-06",
        })[0]
        for vid in (first["id"], second["id"]):
            self.svc.run_action(vid, "提交校验")
            self.svc.run_action(vid, "标记生效")
        conflicts = self.svc.list_conflicts()
        # 两条版本都指向同一目标字段，均被标记，且不裁决谁优先
        assert {row["版本编号"] for row in conflicts} == {first["版本编号"], second["版本编号"]}
        assert all("待定" in row["冲突说明"] for row in conflicts)
        # 两条排序边界都保留冲突双方
        by_date = self.svc.current_field_list("生效日期")
        by_seq = self.svc.current_field_list("登记顺序")
        shared_date = [r for r in by_date if r["目标字段"] == "shared"]
        shared_seq = [r for r in by_seq if r["目标字段"] == "shared"]
        assert len(shared_date) == 2 and len(shared_seq) == 2
        assert shared_date[0]["生效日期"] <= shared_date[1]["生效日期"]
        assert shared_seq[0]["登记顺序"] < shared_seq[1]["登记顺序"]

    def test_list_filter_sort_pagination_search(self):
        items, total = self.svc.list_versions(filters={"规则编号": "MAP-ORD-001"})
        assert total == 3 and all(r["规则编号"] == "MAP-ORD-001" for r in items)
        items, total = self.svc.list_versions(keyword="order_amount")
        assert total == 1 and items[0]["源字段"] == "amount"
        items, total = self.svc.list_versions(status="已生效", page=1, size=5)
        assert total == 9 and len(items) == 5
        items, _ = self.svc.list_versions(page=2, size=5, sort_by="登记顺序", sort_order="desc")
        assert items[0]["登记顺序"] > items[-1]["登记顺序"]
        with pytest.raises(MappingVersionError):
            self.svc.list_versions(sort_by="非法字段")
        with pytest.raises(MappingVersionError):
            self.svc.list_versions(status="非法状态")

    def test_current_list_has_single_effective_version_per_field(self):
        # 种子数据全部已生效，当前字段列表只含已生效版本
        current = self.svc.current_field_list()
        assert all(r["status"] == "已生效" for r in current)
        draft = self.svc.parse_and_register({
            "规则编号": "MAP-DRAFT", "源字段名": "a", "目标字段名": "draft_field",
            "数据类型": "STRING",
        })[0]
        current_after = self.svc.current_field_list()
        assert all(r["版本编号"] != draft["版本编号"] for r in current_after)

    def test_mapping_create_auto_parses_versions(self):
        cms = ColumnMappingService()
        entry, missing = cms.create_entry({
            "规则编号": "MAP-AUTO", "源字段名": "x,y", "目标字段名": "tx,ty",
            "数据类型": "STRING,STRING",
        })
        assert not missing
        versions = self.svc.get_versions_by_rule("MAP-AUTO")
        assert len(versions) == 2
        # 非法规则：登记保留但标记异常，版本表不写入
        bad, _ = cms.create_entry({
            "规则编号": "MAP-AUTO-BAD", "源字段名": "x", "目标字段名": "tx,ty",
        })
        assert bad["abnormal"]
        assert self.svc.get_versions_by_rule("MAP-AUTO-BAD") == []

    def test_dwd_build_refresh_reason_locates_rule(self):
        from app.services.dwd_layer import DwdLayerService
        result = DwdLayerService().build_dwd_from_ods(1)
        assert result["ok"]
        assert "MAP-ORD-001" in result["刷新原因"]
        assert result["关联映射规则"] == ["MAP-ORD-001"]
