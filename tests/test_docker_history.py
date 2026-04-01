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


if __name__ == "__main__":
    unittest.main()
