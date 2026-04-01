import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from support import install_croniter_stub

install_croniter_stub()

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

from common import runtime_settings


class DockerUpdateSettingsTests(unittest.TestCase):
    def test_get_docker_update_settings_includes_policy_defaults(self) -> None:
        with patch.object(runtime_settings, "_load_json_setting", return_value={}):
            result = runtime_settings.get_docker_update_settings()

        self.assertEqual(result["version_strategy"], "stable")
        self.assertEqual(result["semver_policy"], "patch")
        self.assertFalse(result["allow_prerelease"])
        self.assertFalse(result["allow_major_upgrades"])
        self.assertTrue(result["resolve_to_digest"])
        self.assertEqual(result["backup_retention"], 3)
        self.assertFalse(result["run_backup_commands"])

    def test_set_docker_update_settings_normalizes_policy_fields(self) -> None:
        saved: dict[str, object] = {}

        def _capture_save(key: str, value: dict[str, object]) -> None:
            saved["key"] = key
            saved["value"] = value

        with (
            patch.object(runtime_settings, "_load_json_setting", return_value={}),
            patch.object(runtime_settings, "_save_json_setting", side_effect=_capture_save),
            patch.object(runtime_settings, "get_docker_update_settings", return_value={"ok": True}),
        ):
            result = runtime_settings.set_docker_update_settings(
                {
                    "version_strategy": "PREVIOUS_STABLE",
                    "semver_policy": "minor",
                    "allow_prerelease": "yes",
                    "allow_major_upgrades": "false",
                    "resolve_to_digest": "1",
                    "backup_retention": "5",
                    "run_backup_commands": "on",
                }
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(saved["key"], runtime_settings.DOCKER_UPDATE_SETTINGS_KEY)
        self.assertEqual(
            saved["value"],
            {
                "version_strategy": "previous_stable",
                "semver_policy": "minor",
                "allow_prerelease": True,
                "allow_major_upgrades": False,
                "resolve_to_digest": True,
                "backup_retention": 5,
                "run_backup_commands": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
