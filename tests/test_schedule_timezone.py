import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from support import install_croniter_stub

install_croniter_stub()

from common import site


class ScheduleTimezoneTests(unittest.TestCase):
    def test_schedule_next_run_respects_timezone_across_dst(self) -> None:
        winter = site.schedule_next_run(
            "15 5 * * *",
            timezone_name="America/New_York",
            base=datetime(2026, 1, 15, 4, 30, tzinfo=timezone.utc),
        )
        summer = site.schedule_next_run(
            "15 5 * * *",
            timezone_name="America/New_York",
            base=datetime(2026, 7, 15, 3, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(winter, datetime(2026, 1, 15, 10, 15, tzinfo=timezone.utc))
        self.assertEqual(summer, datetime(2026, 7, 15, 9, 15, tzinfo=timezone.utc))

    def test_default_schedules_use_maintenance_timezone(self) -> None:
        with (
            patch.object(
                site,
                "load_group_vars",
                return_value={"maintenance_timezone": "America/New_York", "default_windows": {}},
            ),
            patch.object(site, "load_defined_stacks", return_value=[{"name": "rackpatch"}]),
        ):
            schedules = site.default_schedules()

        self.assertTrue(schedules)
        self.assertTrue(all(item["timezone"] == "America/New_York" for item in schedules))

    def test_invalid_timezone_falls_back_to_maintenance_timezone(self) -> None:
        with patch.object(site, "load_group_vars", return_value={"maintenance_timezone": "America/New_York"}):
            timezone_name = site.schedule_timezone_name("Invalid/Timezone")

        self.assertEqual(timezone_name, "America/New_York")


if __name__ == "__main__":
    unittest.main()
