"""字段映射版本表接口：解析映射规则生成版本，维护生效状态，并联动 DWD 刷新标记。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, PageResult
from app.services.column_mapping_version import (
    ColumnMappingVersionService,
    MappingVersionError,
    LIST_FIELDS,
    STATUS_ORDER,
)

router = APIRouter(prefix="/api/column_mapping_version", tags=["字段映射版本表"])

service = ColumnMappingVersionService()


def query_filters(request: Request) -> dict[str, str]:
    """只接受版本表声明过的字段，忽略分页、排序等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_versions(
    request: Request,
    keyword: str | None = Query(default=None, description="按规则编号/源字段/目标字段检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已生效、已废弃"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    sort_by: str = Query(default="登记顺序", description="排序字段"),
    sort_order: str = Query(default="asc", description="asc 或 desc"),
) -> PageResult[dict]:
    """版本表列表：筛选、排序、翻页、检索、过滤，非法值按功能优化失败返回。"""
    try:
        items, total = service.list_versions(
            filters=query_filters(request), keyword=keyword, status=status,
            page=page, size=size, sort_by=sort_by, sort_order=sort_order)
    except MappingVersionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/current")
def current_fields(sort_by: str = Query(default="生效日期", description="生效日期 或 登记顺序")) -> dict:
    """查询映射后只出现当前生效版本，支持两条排序边界。"""
    try:
        items = service.current_field_list(sort_by=sort_by)
    except MappingVersionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"items": items, "total": len(items), "sort_by": sort_by}


@router.get("/conflicts")
def list_conflicts() -> dict:
    """冲突清单：两个已生效版本写同一目标字段，优先级尚无结论，仅标记不裁决。"""
    items = service.list_conflicts()
    return {"items": items, "total": len(items)}


@router.get("/refresh_marks")
def list_refresh_marks(
    rule_no: str | None = Query(default=None, description="按规则编号定位刷新原因"),
    table_name: str | None = Query(default=None),
) -> dict:
    """明细刷新标记：刷新原因可定位到对应规则编号。"""
    items = service.list_refresh_marks(rule_no=rule_no, table_name=table_name)
    return {"items": items, "total": len(items)}


@router.get("/export")
def export_versions(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    sort_by: str = Query(default="登记顺序"),
    sort_order: str = Query(default="asc"),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{version_id} 之前。"""
    try:
        items, _ = service.list_versions(
            filters=query_filters(request), keyword=keyword, status=status,
            page=1, size=1000000, sort_by=sort_by, sort_order=sort_order)
    except MappingVersionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status", "冲突标记"])
    for item in items:
        row = []
        values = [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS),
                  item.get("status", ""), item.get("冲突标记", "")]
        for value in values:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="column_mapping_version.csv"'})


@router.get("/{version_id}", response_model=dict)
def get_version(version_id: int) -> dict:
    """读取单条字段映射版本明细。"""
    entry = service.get_version(version_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"字段映射版本 {version_id} 不存在或已归档")
    return entry


@router.post("/parse", response_model=ActionResult)
def parse_rule(payload: dict[str, object]) -> ActionResult:
    """解析一条字段映射规则，生成字段映射版本表；非法值按功能优化失败处理。"""
    try:
        created = service.parse_and_register(payload)
    except MappingVersionError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=True, message=f"已解析生成 {len(created)} 条字段映射版本", entry=created[0])


@router.post("/{version_id}/actions", response_model=ActionResult)
def run_action(version_id: int, payload: ActionPayload) -> ActionResult:
    """提交校验、标记生效、标记废弃；跳级与回退会被拦下，生效与刷新标记原子落库。"""
    try:
        entry, message = service.run_action(version_id, payload.action.strip())
    except MappingVersionError as exc:
        return ActionResult(ok=False, message=str(exc))
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
