import sys
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from support import install_croniter_stub

install_croniter_stub()

psycopg_stub = sys.modules.get("psycopg", types.ModuleType("psycopg"))
psycopg_stub.Connection = object
psycopg_stub.connect = lambda *args, **kwargs: None
psycopg_rows_stub = sys.modules.get("psycopg.rows", types.ModuleType("psycopg.rows"))
psycopg_rows_stub.dict_row = object()
psycopg_stub.rows = psycopg_rows_stub
sys.modules["psycopg"] = psycopg_stub
sys.modules["psycopg.rows"] = psycopg_rows_stub

fastapi_stub = sys.modules.get("fastapi", types.ModuleType("fastapi"))


class _HTTPException(Exception):
    def __init__(self, status_code=500, detail="error"):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _FastAPIStub:
    def __init__(self, *args, **kwargs):
        del args, kwargs

    def add_middleware(self, *args, **kwargs):
        del args, kwargs

    def on_event(self, *args, **kwargs):
        del args, kwargs

        def decorator(func):
            return func

        return decorator

    def get(self, *args, **kwargs):
        del args, kwargs

        def decorator(func):
            return func

        return decorator

    def post(self, *args, **kwargs):
        del args, kwargs

        def decorator(func):
            return func

        return decorator

    def delete(self, *args, **kwargs):
        del args, kwargs

        def decorator(func):
            return func

        return decorator


def _depends(value):
    return value


def _header(*args, **kwargs):
    del args, kwargs
    return None


fastapi_stub.Depends = getattr(fastapi_stub, "Depends", _depends)
fastapi_class = getattr(fastapi_stub, "FastAPI", _FastAPIStub)
for method_name in ("add_middleware", "on_event", "get", "post", "delete"):
    if not hasattr(fastapi_class, method_name):
        setattr(fastapi_class, method_name, getattr(_FastAPIStub, method_name))
fastapi_stub.FastAPI = fastapi_class
fastapi_stub.Header = getattr(fastapi_stub, "Header", _header)
fastapi_stub.HTTPException = getattr(fastapi_stub, "HTTPException", _HTTPException)
sys.modules["fastapi"] = fastapi_stub

fastapi_cors_stub = sys.modules.get("fastapi.middleware.cors", types.ModuleType("fastapi.middleware.cors"))
fastapi_cors_stub.CORSMiddleware = object
sys.modules["fastapi.middleware.cors"] = fastapi_cors_stub

docker_stub = sys.modules.get("docker", types.ModuleType("docker"))
docker_stub.DockerClient = object
docker_stub.from_env = lambda: None
sys.modules["docker"] = docker_stub

from api import main as api_main
from common import site
from worker import main as worker_main


class ScheduleTimezoneTests(unittest.TestCase):
    def test_default_schedules_use_maintenance_timezone(self) -> None:
        with patch.object(site, "load_group_vars", return_value={"maintenance_timezone": "America/Los_Angeles"}):
            schedules = site.default_schedules()

        self.assertTrue(schedules)
        self.assertTrue(all(item["timezone"] == "America/Los_Angeles" for item in schedules))

    def test_schedule_next_run_respects_spring_forward_timezone(self) -> None:
        base = datetime(2026, 3, 7, 8, 0, tzinfo=timezone.utc)

        next_run = site.schedule_next_run("30 2 * * *", timezone_name="America/New_York", base=base)

        self.assertEqual(next_run, datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc))

    def test_schedule_next_run_respects_fall_back_timezone(self) -> None:
        base = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)

        next_run = site.schedule_next_run("30 1 * * *", timezone_name="America/New_York", base=base)

        self.assertEqual(next_run, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))

    def test_next_cron_defaults_invalid_timezone_to_maintenance_timezone(self) -> None:
        base = datetime(2026, 3, 22, 0, 0, tzinfo=timezone.utc)
        with patch.object(site, "load_group_vars", return_value={"maintenance_timezone": "America/Chicago"}):
            next_run = worker_main.next_cron("0 5 * * *", timezone_name="Invalid/Zone", base=base)

        self.assertEqual(next_run, datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc))


class _FakeCursor:
    def __init__(self, fetchone_results=None):
        self.fetchone_results = list(fetchone_results or [])
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        if self.fetchone_results:
            return self.fetchone_results.pop(0)
        return None


@contextmanager
def _cursor_context(cursor):
    yield cursor


class ScheduleApiTests(unittest.TestCase):
    def test_upsert_schedule_defaults_timezone_and_recomputes_next_run_for_definition_changes(self) -> None:
        cursor = _FakeCursor(
            fetchone_results=[
                {
                    "id": "sched-1",
                    "kind": "docker_check",
                    "cron_expr": "0 5 * * *",
                    "timezone": "UTC",
                    "payload": {"executor": "agent"},
                    "next_run_at": datetime(2026, 3, 22, 5, 0, tzinfo=timezone.utc),
                },
                {"id": "sched-1", "timezone": "America/Chicago"},
            ]
        )
        recomputed = datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc)

        with (
            patch.object(api_main.db, "db_cursor", return_value=_cursor_context(cursor)),
            patch.object(site, "load_group_vars", return_value={"maintenance_timezone": "America/Chicago"}),
            patch.object(site, "schedule_next_run", return_value=recomputed) as schedule_next_run,
        ):
            row = api_main.upsert_schedule(
                {
                    "name": "Daily Docker Stack Check",
                    "kind": "docker_check",
                    "cron_expr": "0 5 * * *",
                    "payload": {"executor": "agent"},
                    "enabled": True,
                },
                username="admin",
            )

        self.assertEqual(row["timezone"], "America/Chicago")
        schedule_next_run.assert_called_once_with("0 5 * * *", timezone_name="America/Chicago")
        insert_query, insert_params = cursor.executed[-1]
        self.assertIn("timezone", insert_query)
        self.assertEqual(insert_params[3], "America/Chicago")
        self.assertEqual(insert_params[6], recomputed)

    def test_toggle_schedule_enabling_backfills_missing_next_run(self) -> None:
        cursor = _FakeCursor(
            fetchone_results=[
                {
                    "cron_expr": "15 5 * * *",
                    "timezone": "America/New_York",
                    "next_run_at": None,
                },
                {"id": "sched-1", "enabled": True},
            ]
        )
        recomputed = datetime(2026, 3, 22, 9, 15, tzinfo=timezone.utc)

        with (
            patch.object(api_main.db, "db_cursor", return_value=_cursor_context(cursor)),
            patch.object(site, "schedule_next_run", return_value=recomputed) as schedule_next_run,
        ):
            row = api_main.toggle_schedule("sched-1", {"enabled": True}, username="admin")

        self.assertTrue(row["enabled"])
        schedule_next_run.assert_called_once_with("15 5 * * *", timezone_name="America/New_York")
        update_query, update_params = cursor.executed[-1]
        self.assertIn("next_run_at", update_query)
        self.assertEqual(update_params[1], recomputed)


if __name__ == "__main__":
    unittest.main()
