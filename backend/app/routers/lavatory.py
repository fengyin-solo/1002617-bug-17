"""清水排污接口：维护排污任务，覆盖登记、修改、开始服务、完成服务、取消服务等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.lavatory import LavatoryService

router = APIRouter(prefix="/api/lavatory", tags=["清水排污"])

service = LavatoryService()

LIST_FIELDS = ["排污编号", "对应航班", "清水加注量", "排污量", "服务车辆", "操作人员", "完成时间", "服务状态"]
STATUSES = ["待服务", "服务中", "已完成", "已取消"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按排污编号检索"),
    status: str | None = Query(default=None, description="待服务、服务中、已完成、已取消"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按排污编号与状态过滤清水排污列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/summary")
def shift_summary() -> dict[str, Any]:
    """班次汇总：只由明细推导，每次读取都按最新明细把全部历史班次按新口径重算一遍。"""
    return service.shift_summary()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出清水排污清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "lavatory", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条排污任务明细；与列表页同一份数据，不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"排污任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条排污任务；缺字段、重复登记、加注量越界时把服务端的说明原样返回。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改单条记录的清水加注量/排污量；越界那一项会在返回信息里讲清楚。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条排污任务执行开始服务、完成服务、取消服务；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
