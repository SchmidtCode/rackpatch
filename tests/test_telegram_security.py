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
from telegrambot import main


class TelegramSettingsTests(unittest.TestCase):
    def test_get_telegram_settings_includes_sender_allowlists_and_blockers(self) -> None:
        stored = {
            "bot_token": "123456:ABCDEF-token",
            "chat_ids": "1001 1002",
            "allowed_user_ids": "2001,2002",
            "allowed_usernames": "@Alice bob",
        }
        with (
            patch.object(runtime_settings, "_load_json_setting", return_value=stored),
            patch.object(
                runtime_settings.config,
                "insecure_secret_warnings_for_telegram_bot",
                return_value=["RACKPATCH_ADMIN_PASSWORD is still set to the default insecure value"],
            ),
        ):
            result = runtime_settings.get_telegram_settings(include_secret=True)

        self.assertEqual(result["chat_ids"], ["1001", "1002"])
        self.assertEqual(result["allowed_user_ids"], ["2001", "2002"])
        self.assertEqual(result["allowed_usernames"], ["alice", "bob"])
        self.assertEqual(result["allowed_usernames_csv"], "@alice, @bob")
        self.assertTrue(result["sender_allowlist_configured"])
        self.assertFalse(result["bot_runtime_ready"])
        self.assertEqual(len(result["security_blockers"]), 1)
        self.assertEqual(result["bot_token"], "123456:ABCDEF-token")

    def test_set_telegram_settings_normalizes_sender_allowlists(self) -> None:
        saved: dict[str, object] = {}

        def _capture_save(key: str, value: dict[str, object]) -> None:
            saved["key"] = key
            saved["value"] = value

        with (
            patch.object(runtime_settings, "_load_json_setting", return_value={}),
            patch.object(runtime_settings, "_save_json_setting", side_effect=_capture_save),
            patch.object(runtime_settings, "get_telegram_settings", return_value={"ok": True}),
        ):
            result = runtime_settings.set_telegram_settings(
                {
                    "allowed_user_ids": "2001, 2002",
                    "allowed_usernames": "@Alice Bob @alice",
                }
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(saved["key"], runtime_settings.TELEGRAM_SETTINGS_KEY)
        self.assertEqual(
            saved["value"],
            {
                "allowed_user_ids": ["2001", "2002"],
                "allowed_usernames": ["alice", "bob"],
            },
        )


class TelegramProcessUpdateTests(unittest.TestCase):
    def test_process_update_keeps_chat_only_flow_when_sender_allowlist_missing(self) -> None:
        update = {
            "message": {
                "text": "/status",
                "chat": {"id": 1001},
                "from": {"id": 2001, "username": "alice"},
            }
        }
        with (
            patch.object(
                main.runtime_settings,
                "get_telegram_settings",
                return_value={
                    "chat_ids": ["1001"],
                    "allowed_user_ids": [],
                    "allowed_usernames": [],
                },
            ),
            patch.object(main, "handle_command", return_value="ok") as handle_command,
            patch.object(main, "send_message") as send_message,
        ):
            main.process_update(update)

        handle_command.assert_called_once_with("/status")
        send_message.assert_called_once_with("1001", "ok")

    def test_process_update_rejects_sender_not_in_allowlist(self) -> None:
        update = {
            "message": {
                "text": "/status",
                "chat": {"id": 1001},
                "from": {"id": 2009, "username": "mallory"},
            }
        }
        with (
            patch.object(
                main.runtime_settings,
                "get_telegram_settings",
                return_value={
                    "chat_ids": ["1001"],
                    "allowed_user_ids": ["2001"],
                    "allowed_usernames": ["alice"],
                },
            ),
            patch.object(main, "handle_command") as handle_command,
            patch.object(main, "send_message") as send_message,
        ):
            main.process_update(update)

        handle_command.assert_not_called()
        send_message.assert_called_once_with("1001", "This Telegram user is not authorized for rackpatch.")

    def test_process_update_allows_sender_username_in_allowlist(self) -> None:
        update = {
            "message": {
                "text": "/status",
                "chat": {"id": 1001},
                "from": {"id": 2009, "username": "Alice"},
            }
        }
        with (
            patch.object(
                main.runtime_settings,
                "get_telegram_settings",
                return_value={
                    "chat_ids": ["1001"],
                    "allowed_user_ids": [],
                    "allowed_usernames": ["alice"],
                },
            ),
            patch.object(main, "handle_command", return_value="ok") as handle_command,
            patch.object(main, "send_message") as send_message,
        ):
            main.process_update(update)

        handle_command.assert_called_once_with("/status")
        send_message.assert_called_once_with("1001", "ok")


class TelegramRuntimeGuardTests(unittest.TestCase):
    def test_ensure_api_token_rejects_default_insecure_secrets(self) -> None:
        with (
            patch.object(
                main.runtime_settings,
                "get_telegram_settings",
                return_value={
                    "bot_token": "123456:ABCDEF-token",
                    "security_blockers": ["RACKPATCH_AUTH_SECRET is still set to the default insecure value"],
                },
            ),
            patch.object(main.API_SESSION, "post") as post,
        ):
            with self.assertRaisesRegex(RuntimeError, "telegram bot disabled until insecure defaults are replaced"):
                main.ensure_api_token(force=True)

        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
