"""清水污班班次汇总和明细规则的服务层回归测试。"""
from __future__ import annotations

import unittest

from app.services.lavatory import (
    AFTERNOON,
    MORNING,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    LavatoryError,
    LavatoryService,
)
from app.store import store


class LavatoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        store.__init__()
        self.service = LavatoryService()

    def test_shift_summary_is_derived_only_from_completed_rows(self) -> None:
        summary = self.service.shift_summary()

        self.assertEqual(summary["completed_count"], 2)
        self.assertEqual(summary["cancelled_count"], 1)
        self.assertEqual(summary["water_amount"], 640)
        self.assertEqual(summary["waste_amount"], 510)
        morning = next(item for item in summary["shifts"] if item["shift"] == MORNING)
        afternoon = next(item for item in summary["shifts"] if item["shift"] == AFTERNOON)
        self.assertEqual(morning["water_amount"], 220)
        self.assertEqual(afternoon["waste_amount"], 350)

    def test_cancelling_completed_row_removes_it_from_summary(self) -> None:
        entry, created = self.service.create_entry(
            {
                "排污编号": "LAVA-TEST-1",
                "对应航班": "CA8888",
                "清水加注量": 100,
                "排污量": 90,
                "服务车辆": "VEHI-0001",
                "完成时间": "2026-09-10 09:00",
            }
        )
        self.assertTrue(created)
        self.service.run_action(entry["id"], "完成服务")
        self.assertEqual(self.service.shift_summary("2026-09-10")["service_count"], 1)

        cancelled, _ = self.service.run_action(entry["id"], "取消服务")

        self.assertEqual(cancelled["status"], STATUS_CANCELLED)
        summary = self.service.shift_summary("2026-09-10")
        self.assertEqual(summary["service_count"], 0)
        self.assertEqual(summary["water_amount"], 0)
        self.assertEqual(summary["cancelled_count"], 2)

    def test_update_is_read_back_identically_in_list_and_detail(self) -> None:
        updated = self.service.update_entry(3, {"清水加注量": 230.5, "排污量": 170})
        listed = self.service.list_entries(keyword="LAVA-0003", page=1, size=1)[0][0]
        detail = self.service.get_entry(3)

        self.assertEqual(updated["清水加注量"], 230.5)
        self.assertEqual(listed["清水加注量"], 230.5)
        self.assertEqual(detail["清水加注量"], 230.5)
        self.assertEqual(listed["排污量"], detail["排污量"])
        self.assertEqual(STATUS_COMPLETED, detail["status"])

    def test_duplicate_submission_only_first_submission_takes_effect(self) -> None:
        values = {
            "排污编号": "LAVA-DUP-1",
            "对应航班": "CA9001",
            "清水加注量": 120,
            "服务车辆": "VEHI-0001",
        }
        first, created = self.service.create_entry(values, "request-key-1")
        second, second_created = self.service.create_entry(values, "request-key-1")
        no_key_duplicate, no_key_created = self.service.create_entry(values)

        self.assertTrue(created)
        self.assertFalse(second_created)
        self.assertFalse(no_key_created)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(no_key_duplicate["id"], first["id"])
        self.assertIsNone(next(row for row in store.rows("lavatory") if row["排污编号"] == "LAVA-DUP-1").get("车辆额定容量"))

        with self.assertRaises(LavatoryError) as context:
            self.service.create_entry({**values, "对应航班": "CA9999"}, "request-key-1")
        self.assertEqual(context.exception.status_code, 409)

    def test_over_capacity_water_amount_is_rejected_before_persisting(self) -> None:
        before = len(store.rows("lavatory"))

        with self.assertRaises(LavatoryError) as context:
            self.service.create_entry(
                {
                    "排污编号": "LAVA-OVER-1",
                    "对应航班": "CA9002",
                    "清水加注量": 501,
                    "服务车辆": "VEHI-0001",
                }
            )

        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.detail["field"], "清水加注量")
        self.assertEqual(context.exception.detail["limit"], 500)
        self.assertEqual(context.exception.detail["value"], 501)
        self.assertEqual(context.exception.detail["overage"], 1)
        self.assertEqual(len(store.rows("lavatory")), before)


if __name__ == "__main__":
    unittest.main()
