"""清水排污业务规则：状态流转、字段校验与筛选口径都收在这里。

班次汇总不单独落库、不维护计数器，每次读取都由明细现场推导：
只统计已完成的服务，已取消的一律不计入；同一排污编号重复登记按次数去重，
只留一条。这样历史班次在每次读取时都按同一口径重算，汇总与明细不会脱节。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "lavatory"
REQUIRED_FIELDS = ["排污编号", "对应航班", "清水加注量"]
STATUS_ORDER = ["待服务", "服务中", "已完成", "已取消"]
ACTION_RULES = {"开始服务": "服务中", "完成服务": "已完成", "取消服务": "已取消"}
NEGATIVE_ACTIONS = []
DONE_STATUS = "已完成"
EDITABLE_AMOUNT_FIELDS = ("清水加注量", "排污量")
ENTRY_FIELDS = ["排污编号", "对应航班", "清水加注量", "排污量", "服务车辆", "操作人员", "完成时间"]

# 清水车备案的额定容量（升）：清水加注量超过所派车辆容量的一律拒绝写库。
VEHICLE_CAPACITY_LITERS = {"清水车-01": 3000, "清水车-02": 2500, "清水车-03": 1800}


def _normalize_number(value: float) -> int | float:
    """整数口径的量按整数返回，避免 1200.0 这种展示。"""
    return int(value) if float(value).is_integer() else value


def _parse_amount(field: str, raw: Any) -> tuple[float | None, str | None]:
    """把加注量/排污量解析成非负数字；失败时返回指明字段的错误说明。"""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None, f"{field}「{raw}」不是有效数字，已拒绝写库"
    if value < 0:
        return None, f"{field}不能为负数（收到 {_normalize_number(value)}），已拒绝写库"
    return value, None


def _amount_or_zero(raw: Any) -> float:
    """汇总时容忍历史脏数据：解析不了的量按 0 计，不拖垮整班次的合计。"""
    value, error = _parse_amount("量值", raw)
    return value if error is None else 0.0


def _capacity_error(vehicle: Any, refill: float) -> str | None:
    """校验清水加注量是否超出服务车辆额定容量；越界时讲清楚越界的是哪一项。"""
    name = str(vehicle or "").strip()
    if not name:
        return "服务车辆未填写，无法核验额定容量，已拒绝写库"
    capacity = VEHICLE_CAPACITY_LITERS.get(name)
    if capacity is None:
        return f"服务车辆「{name}」不在清水车备案清单中，无法核验额定容量，已拒绝写库"
    if refill > capacity:
        return (
            f"越界项：清水加注量。清水加注量 {_normalize_number(refill)} 升超出服务车辆"
            f"「{name}」的额定容量 {_normalize_number(capacity)} 升，已拒绝写库"
        )
    return None


class LavatoryService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("排污编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        code = str(values.get("排污编号", "")).strip()
        rows = store.rows(MODULE)
        existing = next((row for row in rows if str(row.get("排污编号", "")).strip() == code), None)
        if existing is not None:
            return None, (
                f"排污编号「{code}」已登记过（记录 #{existing.get('id')}），"
                "同一记录重复登记按次数去重后只保留一条，本次未写入"
            )
        refill, error = _parse_amount("清水加注量", values.get("清水加注量"))
        if error:
            return None, error
        assert refill is not None
        sewage_raw = values.get("排污量")
        if sewage_raw is None or str(sewage_raw).strip() == "":
            sewage = 0.0
        else:
            parsed, error = _parse_amount("排污量", sewage_raw)
            if error:
                return None, error
            sewage = parsed if parsed is not None else 0.0
        error = _capacity_error(values.get("服务车辆"), refill)
        if error:
            return None, error
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ENTRY_FIELDS:
            entry[field] = values.get(field)
        entry["排污编号"] = code
        entry["清水加注量"] = _normalize_number(refill)
        entry["排污量"] = _normalize_number(sewage)
        entry["status"] = STATUS_ORDER[0]
        entry["服务状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, "排污任务已登记"

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """修改单条记录的清水加注量/排污量；列表与详情读的是同一条记录，改完即一致。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"排污任务 {entry_id} 不存在或已归档"
        updates = {
            field: values.get(field)
            for field in EDITABLE_AMOUNT_FIELDS
            if field in values and str(values.get(field) or "").strip() != ""
        }
        if not updates:
            return None, "没有需要修改的字段：只支持调整清水加注量、排污量"
        parsed: dict[str, float] = {}
        for field, raw in updates.items():
            value, error = _parse_amount(field, raw)
            if error:
                return None, error
            assert value is not None
            parsed[field] = value
        if "清水加注量" in parsed:
            error = _capacity_error(entry.get("服务车辆"), parsed["清水加注量"])
            if error:
                return None, error
        for field, value in parsed.items():
            entry[field] = _normalize_number(value)
        changed = "、".join(f"{field}={_normalize_number(value)}" for field, value in parsed.items())
        return entry, f"排污任务 {entry_id} 已更新：{changed}"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"排污任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于清水排污可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["服务状态"] = target
        entry["pending"] = target in STATUS_ORDER[:2]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if target == DONE_STATUS and not str(entry.get("完成时间") or "").strip():
            entry["完成时间"] = date.today().isoformat()
        return entry, f"排污任务已{action}"

    def shift_summary(self) -> dict[str, Any]:
        """班次汇总：完全由明细推导，只统计已完成服务，已取消不计入，重复登记只计一次。"""
        rows = store.rows(MODULE)
        status_counts = {status: 0 for status in STATUS_ORDER}
        for row in rows:
            status = str(row.get("status", ""))
            if status in status_counts:
                status_counts[status] += 1
        seen_codes: set[str] = set()
        buckets: dict[str, dict[str, float]] = {}
        for row in sorted(rows, key=lambda item: int(item.get("id", 0))):
            if row.get("status") != DONE_STATUS:
                continue
            code = str(row.get("排污编号", "")).strip()
            if code in seen_codes:
                continue
            seen_codes.add(code)
            shift = str(row.get("完成时间") or "").strip() or "未分班"
            bucket = buckets.setdefault(shift, {"服务趟数": 0, "清水加注总量": 0.0, "排污总量": 0.0})
            bucket["服务趟数"] += 1
            bucket["清水加注总量"] += _amount_or_zero(row.get("清水加注量"))
            bucket["排污总量"] += _amount_or_zero(row.get("排污量"))
        shifts = [
            {"班次": shift, **{key: _normalize_number(value) for key, value in bucket.items()}}
            for shift, bucket in sorted(buckets.items())
        ]
        totals = {
            "服务趟数": sum(int(bucket["服务趟数"]) for bucket in buckets.values()),
            "清水加注总量": _normalize_number(sum(bucket["清水加注总量"] for bucket in buckets.values())),
            "排污总量": _normalize_number(sum(bucket["排污总量"] for bucket in buckets.values())),
        }
        return {
            "module": MODULE,
            "scope": "汇总只由明细推导：仅统计已完成服务，已取消不计入；同一排污编号重复登记只计一次；每次读取按新口径重算全部历史班次",
            "status_counts": status_counts,
            "shifts": shifts,
            "totals": totals,
        }
