import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

psycopg_stub = types.ModuleType("psycopg")
psycopg_stub.Connection = object
psycopg_stub.connect = lambda *args, **kwargs: None
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_rows_stub.dict_row = object()
psycopg_stub.rows = psycopg_rows_stub
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

fastapi_stub = types.ModuleType("fastapi")


class _HTTPException(Exception):
    def __init__(self, status_code=500, detail="error"):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _header(*args, **kwargs):
    del args, kwargs
    return None


fastapi_stub.Header = _header
fastapi_stub.HTTPException = _HTTPException
sys.modules.setdefault("fastapi", fastapi_stub)

from common import jobs


class _FakeCursor:
    def __init__(self, deleted_row):
        self.deleted_row = deleted_row
        self.executed = []

    def execute(self, query, params):
        self.executed.append((query, params))

    def fetchone(self):
        return self.deleted_row


class _FakeCursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, exc_type, exc, tb):
        return False


class DeleteJobTests(unittest.TestCase):
    def test_delete_job_removes_terminal_job_and_reports_deleted_events(self) -> None:
        deleted_row = {"id": "job-1", "status": "completed", "artifact_dir": "/tmp/job-1"}
        cursor = _FakeCursor(deleted_row)
        with (
            patch.object(
                jobs.db,
                "fetch_one",
                side_effect=[
                    {"id": "job-1", "status": "completed", "artifact_dir": "/tmp/job-1"},
                    {"value": 3},
                ],
            ),
            patch.object(jobs.db, "db_cursor", return_value=_FakeCursorContext(cursor)),
            patch.object(jobs, "_delete_job_artifacts") as delete_artifacts,
        ):
            row, reason = jobs.delete_job("job-1", "admin")

        self.assertIsNone(reason)
        self.assertEqual(row["id"], "job-1")
        self.assertEqual(row["deleted_event_count"], 3)
        self.assertIn("DELETE FROM jobs", cursor.executed[0][0])
        delete_artifacts.assert_called_once_with("job-1", "/tmp/job-1")

    def test_delete_job_rejects_non_terminal_jobs(self) -> None:
        with (
            patch.object(jobs.db, "fetch_one", return_value={"id": "job-1", "status": "running", "artifact_dir": None}),
            patch.object(jobs.db, "db_cursor") as db_cursor,
        ):
            row, reason = jobs.delete_job("job-1", "admin")

        self.assertIsNone(row)
        self.assertEqual(reason, "not_deletable")
        db_cursor.assert_not_called()

    def test_delete_job_artifacts_only_removes_paths_inside_jobs_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            jobs_root = Path(tmpdir) / "jobs"
            jobs_root.mkdir()
            managed_dir = jobs_root / "job-1"
            managed_dir.mkdir()
            outside_dir = Path(tmpdir) / "outside"
            outside_dir.mkdir()

            with patch.object(jobs.config, "JOBS_ROOT", jobs_root):
                jobs._delete_job_artifacts("job-1", str(outside_dir))

            self.assertFalse(managed_dir.exists())
            self.assertTrue(outside_dir.exists())


class RackpatchStackUpdateTests(unittest.TestCase):
    def test_rackpatch_stack_update_fields_use_latest_release_for_control_plane_stack(self) -> None:
        public_settings = {
            "repo_url": "https://github.com/SchmidtCode/rackpatch.git",
            "repo_ref": "main",
            "rackpatch_compose_dir": "/srv/compose/rackpatch",
        }
        stack = {
            "name": "rackpatch",
            "project_dir": "/srv/compose/rackpatch",
        }

        with patch.object(jobs.releases, "fetch_latest_release", return_value={"version": "v0.3.8"}):
            fields = jobs._rackpatch_stack_update_fields(stack, public_settings)

        self.assertEqual(
            fields,
            {
                "rackpatch_managed": True,
                "repo_url": "https://github.com/SchmidtCode/rackpatch.git",
                "release_ref": "v0.3.8",
                "target_version": "v0.3.8",
            },
        )

    def test_rackpatch_stack_update_fields_ignore_other_stacks(self) -> None:
        public_settings = {
            "repo_url": "https://github.com/SchmidtCode/rackpatch.git",
            "repo_ref": "main",
            "rackpatch_compose_dir": "/srv/compose/rackpatch",
        }
        stack = {
            "name": "dashboard",
            "project_dir": "/srv/compose/dashboard",
        }

        with patch.object(jobs.releases, "fetch_latest_release") as fetch_latest_release:
            fields = jobs._rackpatch_stack_update_fields(stack, public_settings)

        self.assertEqual(fields, {})
        fetch_latest_release.assert_not_called()


if __name__ == "__main__":
    unittest.main()
