import sys
import types
import unittest
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

    def put(self, *args, **kwargs):
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
for method_name in ("add_middleware", "on_event", "get", "post", "put", "delete"):
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


class DockerHistoryTests(unittest.TestCase):
    def test_history_rows_flatten_component_updates_and_skip_dry_runs(self) -> None:
        finished_at = datetime(2026, 4, 1, 12, 30, tzinfo=timezone.utc)
        jobs = [
            {
                "id": "job-1",
                "source": "ui",
                "target_ref": "media",
                "executor": "agent",
                "requested_by": "admin",
                "approval_status": "not_required",
                "payload": {"dry_run": False, "host": "docker-a"},
                "result": {
                    "update_summary": {
                        "stacks": [
                            {
                                "stack": "media",
                                "host": "docker-a",
                                "services": [
                                    {
                                        "service": "plex",
                                        "from_ref": "linuxserver/plex:latest",
                                        "to_ref": "linuxserver/plex:latest",
                                        "from_short": "sha-old",
                                        "to_short": "sha-new",
                                    }
                                ],
                            }
                        ]
                    }
                },
                "created_at": finished_at,
                "started_at": finished_at,
                "finished_at": finished_at,
            },
            {
                "id": "job-2",
                "source": "ui",
                "target_ref": "media",
                "executor": "agent",
                "requested_by": "admin",
                "approval_status": "not_required",
                "payload": {"dry_run": True, "host": "docker-a"},
                "result": {
                    "update_summary": {
                        "stacks": [
                            {
                                "stack": "media",
                                "host": "docker-a",
                                "services": [
                                    {
                                        "service": "ignored",
                                        "from_short": "old",
                                        "to_short": "new",
                                    }
                                ],
                            }
                        ]
                    }
                },
                "created_at": finished_at,
                "started_at": finished_at,
                "finished_at": finished_at,
            },
        ]

        rows = api_main._docker_history_rows(jobs)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stack"], "media")
        self.assertEqual(rows[0]["component"], "plex")
        self.assertEqual(rows[0]["from_version"], "sha-old")
        self.assertEqual(rows[0]["to_version"], "sha-new")
        self.assertEqual(rows[0]["mode"], "Manual")
        self.assertEqual(rows[0]["source"], "Control Plane")

    def test_history_payload_summarizes_manual_and_automation_rows(self) -> None:
        finished_at = datetime(2026, 4, 1, 16, 45, tzinfo=timezone.utc)
        fetch_rows = [
            {
                "id": "job-1",
                "source": "schedule",
                "target_ref": "edge",
                "executor": "agent",
                "requested_by": "system",
                "approval_status": "not_required",
                "payload": {"host": "docker-b"},
                "result": {
                    "update_summary": {
                        "stacks": [
                            {
                                "stack": "edge",
                                "host": "docker-b",
                                "services": [
                                    {
                                        "service": "traefik",
                                        "from_ref": "traefik:v3.3.0",
                                        "to_ref": "traefik:v3.3.1",
                                        "from_short": "old-traefik",
                                        "to_short": "new-traefik",
                                    }
                                ],
                            }
                        ]
                    }
                },
                "created_at": finished_at,
                "started_at": finished_at,
                "finished_at": finished_at,
            },
            {
                "id": "job-2",
                "source": "ui",
                "target_ref": "media",
                "executor": "agent",
                "requested_by": "admin",
                "approval_status": "not_required",
                "payload": {"host": "docker-a"},
                "result": {
                    "update_summary": {
                        "stacks": [
                            {
                                "stack": "media",
                                "host": "docker-a",
                                "services": [
                                    {
                                        "service": "radarr",
                                        "from_ref": "lscr.io/linuxserver/radarr:5.19.3",
                                        "to_ref": "lscr.io/linuxserver/radarr:5.19.4",
                                        "from_short": "old-radarr",
                                        "to_short": "new-radarr",
                                    }
                                ],
                            }
                        ]
                    }
                },
                "created_at": finished_at,
                "started_at": finished_at,
                "finished_at": finished_at,
            },
        ]

        with patch.object(api_main.db, "fetch_all", return_value=fetch_rows):
            payload = api_main._docker_history_payload()

        self.assertTrue(payload["loaded"])
        self.assertEqual(payload["summary"]["total_rows"], 2)
        self.assertEqual(payload["summary"]["total_jobs"], 2)
        self.assertEqual(payload["summary"]["total_stacks"], 2)
        self.assertEqual(payload["summary"]["manual_rows"], 1)
        self.assertEqual(payload["summary"]["automation_rows"], 1)
        self.assertEqual(payload["summary"]["last_updated_at"], finished_at)

    def test_docker_updates_payload_marks_outdated_stack_current_after_successful_live_update(self) -> None:
        checked_at = "2026-04-01T09:00:00+00:00"
        finished_at = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
        latest_check = {
            "id": "check-1",
            "status": "completed",
            "result": {
                "report": {
                    "checked_at": checked_at,
                    "status": "outdated",
                    "image_count": 2,
                    "outdated_count": 1,
                    "services": [
                        {
                            "service": "beszel",
                            "status": "outdated",
                            "ref": "henrygd/beszel:latest",
                        },
                        {
                            "service": "proxy",
                            "status": "up-to-date",
                            "ref": "nginx:latest",
                        },
                    ],
                }
            },
            "created_at": checked_at,
            "started_at": checked_at,
            "finished_at": checked_at,
        }
        latest_update = {
            "id": "update-1",
            "status": "completed",
            "payload": {"dry_run": False},
            "result": {
                "update_summary": {
                    "changed_services": 1,
                    "stacks": [
                        {
                            "stack": "beszel",
                            "host": "docker-a",
                            "changed_services": 1,
                            "services": [
                                {
                                    "service": "beszel",
                                    "from_short": "old-beszel",
                                    "to_short": "new-beszel",
                                }
                            ],
                        }
                    ],
                }
            },
            "created_at": finished_at,
            "started_at": finished_at,
            "finished_at": finished_at,
        }

        with (
            patch.object(api_main.site, "load_stacks", return_value=[{"name": "beszel", "host": "docker-a", "path": "/srv/compose/beszel"}]),
            patch.object(api_main, "_latest_stack_job_map", side_effect=[{"beszel": latest_check}, {"beszel": latest_update}]),
            patch.object(api_main.db, "fetch_all", return_value=[]),
            patch.object(
                api_main,
                "_docker_stack_access",
                side_effect=[
                    {"eligible": True, "reason": "", "required_capabilities": [], "target_agent_id": "agent-1"},
                    {"eligible": True, "reason": "", "required_capabilities": [], "target_agent_id": "agent-1"},
                    {"eligible": True, "reason": "", "required_capabilities": [], "target_agent_id": "agent-1"},
                ],
            ),
        ):
            payload = api_main._docker_updates_payload()

        item = payload["items"][0]
        self.assertEqual(item["inspection"]["state"], "up-to-date")
        self.assertEqual(item["inspection"]["report"]["status"], "up-to-date")
        self.assertEqual(item["inspection"]["report"]["outdated_count"], 0)
        self.assertTrue(item["inspection"]["derived_from_update"])
        self.assertEqual(item["inspection"]["report"]["services"][0]["status"], "up-to-date")
        self.assertFalse(item["selection_eligible"])
        self.assertEqual(payload["summary"]["outdated_stacks"], 0)
        self.assertEqual(payload["summary"]["outdated_images"], 0)

    def test_pending_docker_update_rows_flatten_latest_check_report(self) -> None:
        requested_at = datetime(2026, 4, 1, 18, 15, tzinfo=timezone.utc)
        jobs = [
            {
                "id": "job-approve-1",
                "target_ref": "beszel",
                "requested_by": "admin",
                "payload": {"host": "docker-a"},
                "created_at": requested_at,
                "queued_at": requested_at,
            }
        ]
        check_jobs = {
            "beszel": {
                "id": "check-1",
                "result": {
                    "report": {
                        "host": "docker-a",
                        "checked_at": "2026-04-01T18:00:00+00:00",
                        "services": [
                            {
                                "service": "beszel",
                                "status": "outdated",
                                "ref": "henrygd/beszel:latest",
                                "target_ref": f"henrygd/beszel:v0.18.7@sha256:{'2' * 64}",
                                "local_digest": f"sha256:{'1' * 64}",
                                "remote_digest": f"sha256:{'2' * 64}",
                                "update_reason": "newer stable release detected",
                            }
                        ],
                    }
                },
            }
        }

        rows = api_main._pending_docker_update_rows(jobs, check_jobs, {"beszel": {"name": "beszel", "host": "docker-a"}})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stack"], "beszel")
        self.assertEqual(rows[0]["component"], "beszel")
        self.assertEqual(rows[0]["host"], "docker-a")
        self.assertEqual(rows[0]["from_version"], f"latest ({'1' * 12})")
        self.assertEqual(rows[0]["to_version"], f"v0.18.7 ({'2' * 12})")
        self.assertTrue(rows[0]["preview_available"])
        self.assertEqual(rows[0]["reason"], "newer stable release detected")

    def test_pending_docker_update_rows_include_placeholder_when_preview_missing(self) -> None:
        jobs = [
            {
                "id": "job-approve-2",
                "target_ref": "media",
                "requested_by": "admin",
                "payload": {"host": "docker-b"},
                "created_at": datetime(2026, 4, 1, 19, 0, tzinfo=timezone.utc),
            }
        ]

        rows = api_main._pending_docker_update_rows(jobs, {}, {"media": {"name": "media", "host": "docker-b"}})

        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["preview_available"])
        self.assertEqual(rows[0]["stack"], "media")
        self.assertIn("Run a fresh Docker check", rows[0]["reason"])

    def test_overview_includes_pending_docker_update_summary(self) -> None:
        pending_payload = {
            "summary": {"total_jobs": 2, "total_rows": 3},
            "items": [{"id": "one"}, {"id": "two"}, {"id": "three"}],
        }

        with (
            patch.object(api_main, "_pending_docker_update_payload", return_value=pending_payload),
            patch.object(
                api_main.db,
                "fetch_one",
                side_effect=[
                    {"value": 4},
                    {"value": 20},
                    {"value": 1},
                    {"value": 3},
                    {"value": 2},
                    {"value": 5},
                ],
            ),
            patch.object(api_main.site, "load_stacks", return_value=[{"name": "a"}, {"name": "b"}]),
            patch.object(api_main.site, "load_hosts", return_value=[{"name": "h1"}]),
            patch.object(api_main.site, "site_root", return_value=Path("/srv/compose/rackpatch/sites/local")),
        ):
            payload = api_main.overview(username="admin")

        self.assertEqual(payload["counts"]["pending_docker_jobs"], 2)
        self.assertEqual(payload["pending_docker_updates"]["summary"]["total_rows"], 3)
        self.assertEqual(payload["stacks"], 2)
        self.assertEqual(payload["hosts"], 1)


if __name__ == "__main__":
    unittest.main()
