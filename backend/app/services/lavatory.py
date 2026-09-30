"""清水排污业务规则：明细是唯一数据源，班次汇总实时从已完成明细推导。"""
from __future__ import annotations

import math
import re
import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "lavatory"
VEHICLE_MODULE = "vehicle"

ID_FIELD = "排污编号"
FLIGHT_FIELD = "对应航班"
WATER_FIELD = "清水加注量"
WASTE_FIELD = "排污量"
VEHICLE_FIELD = "服务车辆"
OPERATOR_FIELD = "操作人员"
COMPLETED_AT_FIELD = "完成时间"
SHIFT_FIELD = "班次"
DISPLAY_STATUS_FIELD = "服务状态"

REQUIRED_FIELDS = [ID_FIELD, FLIGHT_FIELD, WATER_FIELD]
EDITABLE_FIELDS = [
    FLIGHT_FIELD,
    WATER_FIELD,
    WASTE_FIELD,
    VEHICLE_FIELD,
    OPERATOR_FIELD,
    COMPLETED_AT_FIELD,
    SHIFT_FIELD,
]
INPUT_CAPACITY_FIELDS = {"车辆额定容量", "额定容量", "清水罐容量", "清水箱容量", "capacity"}
PROTECTED_FIELDS = {"id", "status", "pending", "abnormal", DISPLAY_STATUS_FIELD}

STATUS_PENDING = "待服务"
STATUS_IN_SERVICE = "服务中"
STATUS_COMPLETED = "已完成"
STATUS_CANCELLED = "已取消"
STATUS_ORDER = [STATUS_PENDING, STATUS_IN_SERVICE, STATUS_COMPLETED, STATUS_CANCELLED]

ACTION_RULES = {
    "安排服务": STATUS_IN_SERVICE,
    "开始服务": STATUS_COMPLETED,
    "完成服务": STATUS_COMPLETED,
    "完成加注": STATUS_COMPLETED,
    "取消服务": STATUS_CANCELLED,
    "撤销服务": STATUS_CANCELLED,
}

MORNING = "上午"
AFTERNOON = "下午"
NIGHT = "夜间"
UNKNOWN_SHIFT = "未知班次"
UNKNOWN_DATE = "未知日期"
SHIFT_ORDER = {MORNING: 0, AFTERNOON: 1, NIGHT: 2, UNKNOWN_SHIFT: 3}
SHIFT_ALIASES = {
    "上午": MORNING,
    "早上": MORNING,
    "早班": MORNING,
    "morning": MORNING,
    "下午": AFTERNOON,
    "午班": AFTERNOON,
    "afternoon": AFTERNOON,
    "晚上": NIGHT,
    "夜间": NIGHT,
    "夜班": NIGHT,
    "night": NIGHT,
}

CAPACITY_FIELDS = ["车辆额定容量", "额定容量", "清水罐容量", "清水箱容量", "capacity"]
DEFAULT_VEHICLE_CAPACITY = 1000.0
VEHICLE_CAPACITIES = {
    "VEHI-0001": 500.0,
    "VEHI-0002": 750.0,
    "VEHI-0003": 1000.0,
    "LAVA-CART-001": 500.0,
    "LAVA-CART-002": 750.0,
    "LAVA-CART-003": 1000.0,
}
CAPACITY_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:L|升|公升)\b", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"^[+-]?\d+(?:\.\d+)?$")

LEGACY_SUMMARY_FIELDS = {
    "班次汇总",
    "加注趟数",
    "排污趟数",
    "清水加注总量",
    "排污总量",
    "上午加注趟数",
    "上午排污量",
    "下午加注趟数",
    "下午排污量",
}


class LavatoryError(Exception):
    """清水排污业务错误；路由层保留这里给出的原始说明和状态码。"""

    def __init__(self, message: str, status_code: int = 400, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail if detail is not None else message


def _number(value: Any, field: str, *, default: float | int | None = None) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is not None:
            return default
        raise LavatoryError(f"{field}必须是数字")
    if isinstance(value, bool):
        raise LavatoryError(f"{field}必须是数字，不能使用布尔值")
    if isinstance(value, int):
        number = float(value)
    elif isinstance(value, float):
        number = value
    elif isinstance(value, str):
        text = value.strip().replace(",", "").replace("，", "")
        match = NUMBER_PATTERN.fullmatch(text)
        if match is None:
            if default is not None:
                return default
            raise LavatoryError(f"{field}必须是数字，当前值为「{value}」")
        number = float(match.group())
    else:
        if default is not None:
            return default
        raise LavatoryError(f"{field}必须是数字，当前值为「{value}」")

    if not math.isfinite(number):
        raise LavatoryError(f"{field}必须是有限数字")
    if number < 0:
        raise LavatoryError(f"{field}不能为负数，当前值为 {_display_number(number)}")
    return number


def _display_number(number: float | int) -> int | float:
    numeric = float(number)
    return int(numeric) if numeric.is_integer() else round(numeric, 3)


def _amount(value: Any, field: str, *, lenient: bool = False) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    try:
        return _number(value, field)
    except LavatoryError:
        if lenient:
            return 0
        raise


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_shift(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return SHIFT_ALIASES.get(text.lower(), text)


def _parse_completion(value: Any) -> tuple[str, str]:
    text = _text(value)
    if not text:
        return "", ""

    iso_text = text.replace("/", "-").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        date_match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", text)
        if date_match:
            return text, ""
        return text, ""

    date = parsed.date().isoformat()
    if parsed.hour < 12:
        shift = MORNING
    elif parsed.hour < 18:
        shift = AFTERNOON
    else:
        shift = NIGHT
    return text, shift


def _capacity_number(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        capacity = _number(value, "车辆额定容量")
    except LavatoryError:
        match = CAPACITY_PATTERN.search(_text(value))
        if match is None:
            return None
        capacity = float(match.group(1))
    if capacity <= 0:
        raise LavatoryError("车辆额定容量必须大于 0")
    return capacity


class LavatoryService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._request_keys: dict[str, tuple[int, Any]] = {}

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            source_rows = store.rows(MODULE)
            rows = [self._canonicalize(row) for row in source_rows]
            if keyword:
                rows = [row for row in rows if keyword in str(row.get(ID_FIELD, ""))]
            if status:
                rows = [row for row in rows if row.get("status") == status]
            total = len(rows)
            start = max(page - 1, 0) * size
            return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with self._lock:
            entry = store.find(MODULE, entry_id)
            return self._canonicalize(dict(entry)) if entry is not None else None

    def create_entry(
        self,
        values: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """登记明细；同一编号或同一幂等键的重复提交直接返回首次结果。"""
        with self._lock:
            payload = self._sanitize_values(values)
            missing = [field for field in REQUIRED_FIELDS if not _text(payload.get(field))]
            if missing:
                raise LavatoryError(f"缺少必填字段：{'、'.join(missing)}")

            fingerprint = self._payload_fingerprint(payload)
            if idempotency_key:
                existing = self._existing_for_key(idempotency_key, fingerprint)
                if existing is not None:
                    return existing, False

            entry_id = max((int(row.get("id", 0)) for row in store.rows(MODULE)), default=0) + 1
            normalized = self._normalize_values(payload)
            existing = self._find_by_business_id(normalized[ID_FIELD])
            if existing is not None:
                canonical_existing = self._canonicalize(dict(existing))
                if not self._same_payload(canonical_existing, payload):
                    raise LavatoryError(
                        f"{ID_FIELD}「{normalized[ID_FIELD]}」已存在，且本次提交内容与首次记录不一致",
                        409,
                    )
                if idempotency_key:
                    self._request_keys[idempotency_key] = (int(canonical_existing["id"]), fingerprint)
                return canonical_existing, False

            self._validate_capacity(normalized)
            entry: dict[str, Any] = {"id": entry_id}
            entry.update({field: normalized[field] for field in [ID_FIELD, *EDITABLE_FIELDS]})
            entry["status"] = STATUS_PENDING
            entry = self._canonicalize(entry)
            store.rows(MODULE).append(entry)
            if idempotency_key:
                self._request_keys[idempotency_key] = (entry_id, fingerprint)
            return entry, True

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                raise LavatoryError(f"排污任务 {entry_id} 不存在或已归档", 404)

            payload = self._sanitize_values(values)
            current = self._canonicalize(dict(entry))
            if ID_FIELD in payload and _text(payload[ID_FIELD]) != _text(current.get(ID_FIELD)):
                raise LavatoryError(f"{ID_FIELD}是记录主键，不能通过编辑修改", 409)
            merged = {ID_FIELD: current.get(ID_FIELD, "")}
            merged.update({field: current.get(field, "") for field in EDITABLE_FIELDS})
            merged.update(payload)
            normalized = self._normalize_values(merged)
            self._validate_capacity(normalized)

            for field in EDITABLE_FIELDS:
                entry[field] = normalized[field]
            return self._canonicalize(entry)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any], str]:
        action_name = _text(action)
        with self._lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                raise LavatoryError(f"排污任务 {entry_id} 不存在或已归档", 404)
            if action_name not in ACTION_RULES:
                raise LavatoryError(f"动作「{action_name}」不属于清水排污可执行范围")

            entry = self._canonicalize(entry)
            current = str(entry["status"])
            target = ACTION_RULES[action_name]
            if current == target:
                return entry, f"排污任务已处于{target}，重复操作未重复生效"
            self._ensure_transition(current, target, action_name)

            entry["status"] = target
            if target == STATUS_COMPLETED and not entry.get(COMPLETED_AT_FIELD):
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                entry[COMPLETED_AT_FIELD] = now
            updated = self._canonicalize(entry)
            return updated, f"排污任务已{action_name}"

    def shift_summary(self, shift_date: str | None = None) -> dict[str, Any]:
        with self._lock:
            rows = [self._canonicalize(row) for row in store.rows(MODULE)]
            completed = [row for row in rows if row["status"] == STATUS_COMPLETED]
            if shift_date:
                expected = _text(shift_date)
                completed = [row for row in completed if self._shift_date(row) == expected]

            groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for row in completed:
                groups.setdefault((self._shift_date(row), self._shift_name(row)), []).append(row)

            shifts = [self._summarize_group(date, shift, group_rows) for (date, shift), group_rows in groups.items()]
            shifts.sort(key=lambda item: (item["shift_date"] == UNKNOWN_DATE, item["shift_date"], item["shift_order"]))
            totals = self._totals(completed)
            cancelled_count = sum(1 for row in rows if row["status"] == STATUS_CANCELLED)
            return {
                "caliber": "只统计已完成服务；待服务、服务中与已取消记录均不计入",
                "summary_date": shift_date or "全部",
                "completed_count": totals["service_count"],
                "cancelled_count": cancelled_count,
                **totals,
                "加注趟数": totals["water_fill_count"],
                "排污趟数": totals["waste_discharge_count"],
                WATER_FIELD: totals["water_amount"],
                WASTE_FIELD: totals["waste_amount"],
                "shifts": shifts,
            }

    def recalculate_shifts(self, shift_date: str | None = None) -> dict[str, Any]:
        """按当前明细重新规范化并推导历史班次；不保留独立汇总表。"""
        with self._lock:
            for row in store.rows(MODULE):
                self._canonicalize(row)
            return self.shift_summary(shift_date)

    def _existing_for_key(
        self,
        idempotency_key: str,
        fingerprint: Any,
    ) -> dict[str, Any] | None:
        remembered = self._request_keys.get(idempotency_key)
        if remembered is None:
            return None
        entry_id, first_fingerprint = remembered
        entry = store.find(MODULE, entry_id)
        if entry is None:
            self._request_keys.pop(idempotency_key, None)
            return None
        if first_fingerprint != fingerprint:
            raise LavatoryError("幂等键已用于另一条排污记录，不能重复提交不同内容", 409)
        return self._canonicalize(dict(entry))

    def _find_by_business_id(self, business_id: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if _text(row.get(ID_FIELD)) == business_id:
                return row
        return None

    @staticmethod
    def _sanitize_values(values: dict[str, Any]) -> dict[str, Any]:
        protected = sorted(field for field in values if field in PROTECTED_FIELDS)
        if protected:
            raise LavatoryError(f"字段不能通过登记或编辑直接修改：{'、'.join(protected)}；请使用状态动作")
        allowed_fields = set(EDITABLE_FIELDS) | INPUT_CAPACITY_FIELDS
        allowed_fields.add(ID_FIELD)
        return {key: value for key, value in values.items() if key in allowed_fields}

    @staticmethod
    def _payload_fingerprint(payload: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(key), _text(value)) for key, value in payload.items()))

    def _same_payload(self, entry: dict[str, Any], payload: dict[str, Any]) -> bool:
        for field, value in payload.items():
            if field in {WATER_FIELD, WASTE_FIELD, "车辆额定容量"}:
                if _amount(entry.get(field, 0), field, lenient=True) != _amount(value, field):
                    return False
            elif _text(entry.get(field)) != _text(value):
                return False
        return True

    def _normalize_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            ID_FIELD: _text(payload.get(ID_FIELD)),
            FLIGHT_FIELD: _text(payload.get(FLIGHT_FIELD)),
            WATER_FIELD: _display_number(_amount(payload.get(WATER_FIELD, 0), WATER_FIELD)),
            WASTE_FIELD: _display_number(_amount(payload.get(WASTE_FIELD, 0), WASTE_FIELD)),
            VEHICLE_FIELD: _text(payload.get(VEHICLE_FIELD)),
            OPERATOR_FIELD: _text(payload.get(OPERATOR_FIELD)),
            COMPLETED_AT_FIELD: "",
            SHIFT_FIELD: "",
            "车辆额定容量": None,
        }
        completion, inferred_shift = _parse_completion(payload.get(COMPLETED_AT_FIELD))
        normalized[COMPLETED_AT_FIELD] = completion
        normalized[SHIFT_FIELD] = _normalize_shift(payload.get(SHIFT_FIELD)) or inferred_shift
        capacity = self._extract_capacity(payload.get(VEHICLE_FIELD), payload)
        if capacity is not None:
            normalized["车辆额定容量"] = _display_number(capacity)
        return normalized

    def _canonicalize(self, row: dict[str, Any]) -> dict[str, Any]:
        for legacy_field in LEGACY_SUMMARY_FIELDS:
            row.pop(legacy_field, None)

        status = row.get("status")
        if status not in STATUS_ORDER:
            status = row.get(DISPLAY_STATUS_FIELD) if row.get(DISPLAY_STATUS_FIELD) in STATUS_ORDER else STATUS_PENDING
        row["status"] = status
        row[DISPLAY_STATUS_FIELD] = status
        row["pending"] = status in {STATUS_PENDING, STATUS_IN_SERVICE}
        row["abnormal"] = status == STATUS_CANCELLED

        row[ID_FIELD] = _text(row.get(ID_FIELD))
        row[FLIGHT_FIELD] = _text(row.get(FLIGHT_FIELD))
        row[WATER_FIELD] = _display_number(_amount(row.get(WATER_FIELD, 0), WATER_FIELD, lenient=True))
        row[WASTE_FIELD] = _display_number(_amount(row.get(WASTE_FIELD, 0), WASTE_FIELD, lenient=True))
        row[VEHICLE_FIELD] = _text(row.get(VEHICLE_FIELD))
        row[OPERATOR_FIELD] = _text(row.get(OPERATOR_FIELD))
        completion, inferred_shift = _parse_completion(row.get(COMPLETED_AT_FIELD))
        row[COMPLETED_AT_FIELD] = completion
        row[SHIFT_FIELD] = _normalize_shift(row.get(SHIFT_FIELD)) or inferred_shift
        return row

    def _extract_capacity(self, vehicle: Any, payload: dict[str, Any]) -> float | None:
        for field in CAPACITY_FIELDS:
            if field in payload:
                capacity = _capacity_number(payload.get(field))
                if capacity is not None:
                    return capacity

        vehicle_text = _text(vehicle)
        for vehicle_row in store.rows(VEHICLE_MODULE):
            vehicle_id = _text(vehicle_row.get("车辆编号"))
            vehicle_type = _text(vehicle_row.get("车辆类型"))
            id_matches = bool(vehicle_text and vehicle_id and (vehicle_text == vehicle_id or vehicle_id in vehicle_text))
            if id_matches:
                capacity = _capacity_number(vehicle_row.get("额定容量"))
                if capacity is not None:
                    return capacity
            type_matches = bool(vehicle_text and vehicle_type and (vehicle_text == vehicle_type or vehicle_type in vehicle_text) and "清水" in vehicle_type)
            if type_matches:
                capacity = _capacity_number(vehicle_row.get("额定容量"))
                if capacity is not None:
                    return capacity

        for code, capacity in VEHICLE_CAPACITIES.items():
            if code in vehicle_text:
                return capacity
        match = CAPACITY_PATTERN.search(vehicle_text)
        if match:
            return float(match.group(1))
        return DEFAULT_VEHICLE_CAPACITY

    def _validate_capacity(self, normalized: dict[str, Any]) -> None:
        vehicle = normalized.get(VEHICLE_FIELD, "")
        capacity = self._extract_capacity(vehicle, normalized)
        water = float(normalized.get(WATER_FIELD, 0))
        if capacity is None or water <= capacity:
            return
        message = (
            f"{WATER_FIELD} {_display_number(water)} 升超出服务车辆「{vehicle or '未指定'}」"
            f"的额定容量 {_display_number(capacity)} 升，超出 {_display_number(water - capacity)} 升"
        )
        raise LavatoryError(
            message,
            422,
            {
                "message": message,
                "field": WATER_FIELD,
                "value": _display_number(water),
                "vehicle": vehicle,
                "capacity": _display_number(capacity),
                "limit": _display_number(capacity),
                "overage": _display_number(water - capacity),
                "exceeded_by": _display_number(water - capacity),
            },
        )

    @staticmethod
    def _ensure_transition(current: str, target: str, action_name: str) -> None:
        allowed: dict[tuple[str, str], str] = {
            (STATUS_PENDING, STATUS_IN_SERVICE): action_name,
            (STATUS_PENDING, STATUS_COMPLETED): action_name,
            (STATUS_PENDING, STATUS_CANCELLED): action_name,
            (STATUS_IN_SERVICE, STATUS_COMPLETED): action_name,
            (STATUS_IN_SERVICE, STATUS_CANCELLED): action_name,
            (STATUS_COMPLETED, STATUS_CANCELLED): action_name,
        }
        if (current, target) not in allowed:
            raise LavatoryError(f"排污任务当前为{current}，不能执行{action_name}", 409)

    @staticmethod
    def _shift_date(row: dict[str, Any]) -> str:
        completion = _text(row.get(COMPLETED_AT_FIELD))
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", completion)
        return date_match.group(0) if date_match else UNKNOWN_DATE

    @staticmethod
    def _shift_name(row: dict[str, Any]) -> str:
        return _normalize_shift(row.get(SHIFT_FIELD)) or UNKNOWN_SHIFT

    def _summarize_group(
        self,
        shift_date: str,
        shift: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        totals = self._totals(rows)
        return {
            "shift_date": shift_date,
            "shift": shift,
            "shift_order": SHIFT_ORDER.get(shift, 99),
            **totals,
            "日期": shift_date,
            "班次": shift,
            "服务趟数": totals["service_count"],
            "加注趟数": totals["water_fill_count"],
            "排污趟数": totals["waste_discharge_count"],
            WATER_FIELD: totals["water_amount"],
            WASTE_FIELD: totals["waste_amount"],
        }

    @staticmethod
    def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
        water_values = [float(row.get(WATER_FIELD, 0)) for row in rows]
        waste_values = [float(row.get(WASTE_FIELD, 0)) for row in rows]
        water_total = _display_number(math.fsum(water_values))
        waste_total = _display_number(math.fsum(waste_values))
        count = len(rows)
        return {
            "service_count": count,
            "completed_count": count,
            "water_fill_count": sum(1 for value in water_values if value > 0),
            "waste_discharge_count": sum(1 for value in waste_values if value > 0),
            "water_amount": water_total,
            "waste_amount": waste_total,
            "total_service_count": count,
            "total_water_fill_count": sum(1 for value in water_values if value > 0),
            "total_waste_discharge_count": sum(1 for value in waste_values if value > 0),
            "total_water_amount": water_total,
            "total_waste_amount": waste_total,
        }
