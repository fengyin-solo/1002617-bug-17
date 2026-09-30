"""清水排污接口：维护排污任务，班次汇总只从已完成明细实时推导。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.lavatory import LavatoryError, LavatoryService

router = APIRouter(prefix="/api/lavatory", tags=["清水排污"])

service = LavatoryService()

LIST_FIELDS = ["排污编号", "对应航班", "清水加注量", "排污量", "服务车辆", "操作人员", "完成时间", "服务状态"]
STATUSES = ["待服务", "服务中", "已完成", "已取消"]


def _raise_lavatory_error(error: LavatoryError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail)


def _idempotency_key(request: Request, payload: EntryPayload) -> str | None:
    return (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Idempotency-Key")
        or request.headers.get("X-Request-Id")
        or str(payload.values.get("idempotency_key") or payload.values.get("request_id") or "").strip()
        or None
    )


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
@router.get("/shift-summary")
@router.get("/shifts")
@router.get("/shifts/summary")
def get_shift_summary(
    shift_date: str | None = Query(default=None, description="按完成日期筛选班次，例如 2026-09-01"),
) -> dict[str, Any]:
    """读取班次汇总；汇总每次都由已完成明细重新推导，不维护可漂移缓存。"""
    try:
        return service.shift_summary(shift_date)
    except LavatoryError as error:
        _raise_lavatory_error(error)


@router.post("/summary/rebuild")
@router.post("/shifts/rebuild")
@router.post("/shifts/recalculate")
@router.post("/recalculate-shifts")
def rebuild_shift_summary(
    shift_date: str | None = Query(default=None, description="只重算指定日期；不传则重算全部历史班次"),
) -> dict[str, Any]:
    """按新口径用历史明细重算班次汇总。"""
    try:
        summary = service.recalculate_shifts(shift_date)
        summary["ok"] = True
        summary["message"] = "历史班次已按当前明细重算"
        return summary
    except LavatoryError as error:
        _raise_lavatory_error(error)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出清水排污清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "lavatory", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
@router.get("/entries/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条排污任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"排污任务 {entry_id} 不存在或已归档")
    return entry


@router.post("")
@router.post("/entries")
def create_entry(request: Request, payload: EntryPayload) -> ActionResult:
    """登记排污任务；同一编号或幂等键重复提交，只保留并返回第一次结果。"""
    try:
        key = _idempotency_key(request, payload)
        values = payload.values if payload.values else {key: value for key, value in payload.model_dump().items() if key != "values" and value is not None}
        entry, created = service.create_entry(values, key)
        if not created:
            return ActionResult(ok=True, message="该排污任务已提交过，本次返回首次结果", entry=entry, duplicate=True)
        return ActionResult(ok=True, message="排污任务已登记", entry=entry)
    except LavatoryError as error:
        _raise_lavatory_error(error)


@router.put("/{entry_id}")
@router.patch("/{entry_id}")
@router.put("/entries/{entry_id}")
@router.patch("/entries/{entry_id}")
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改单条明细；列表和详情共用这份规范化后的记录。"""
    try:
        entry = service.update_entry(entry_id, payload.values)
        return ActionResult(ok=True, message="排污任务明细已更新", entry=entry)
    except LavatoryError as error:
        _raise_lavatory_error(error)


@router.post("/{entry_id}/actions", response_model=ActionResult)
@router.post("/entries/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条排污任务执行安排、开始、完成、取消；非法流转不改动明细。"""
    action = str(payload.values.get("action") or payload.values.get("动作") or payload.action or "").strip()
    try:
        entry, message = service.run_action(entry_id, action)
        return ActionResult(ok=True, message=message, entry=entry)
    except LavatoryError as error:
        _raise_lavatory_error(error)
