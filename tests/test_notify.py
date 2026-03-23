import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from support import install_croniter_stub

install_croniter_stub()

psycopg_stub = types.ModuleType("psycopg")
psycopg_stub.Connection = object
psycopg_stub.Cursor = object
psycopg_stub.connect = lambda *args, **kwargs: None
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_rows_stub.dict_row = object()
psycopg_stub.rows = psycopg_rows_stub
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

fastapi_stub = sys.modules.get("fastapi", types.ModuleType("fastapi"))


class _FastAPI:
    def __init__(self, *args, **kwargs):
        del args, kwargs

    def on_event(self, *args, **kwargs):
        del args, kwargs

        def decorator(fn):
            return fn

        return decorator

    def get(self, *args, **kwargs):
        del args, kwargs

        def decorator(fn):
            return fn

        return decorator

    def post(self, *args, **kwargs):
        del args, kwargs

        def decorator(fn):
            return fn

        return decorator


class _HTTPException(Exception):
    def __init__(self, status_code=500, detail="error"):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _header(*args, **kwargs):
    del args, kwargs
    return None


fastapi_stub.FastAPI = _FastAPI
fastapi_stub.HTTPException = _HTTPException
fastapi_stub.Header = _header
sys.modules["fastapi"] = fastapi_stub

from common import notify as common_notify
from notify import main as notify_main


class SharedNotifyTests(unittest.TestCase):
    def test_send_message_falls_back_to_log_delivery_when_telegram_is_unconfigured(self) -> None:
        with (
            patch.object(
                common_notify.runtime_settings,
                "get_telegram_settings",
                return_value={"bot_token": "", "chat_ids": []},
            ),
            patch("builtins.print") as mock_print,
            patch.object(common_notify.SESSION, "post") as post,
        ):
            result = common_notify.send_message("hello from logs")

        self.assertEqual(
            result,
            {
                "status": "ok",
                "configured": False,
                "mode": "log",
                "reason": "telegram_not_configured:bot_token,chat_ids",
                "bot_token_configured": False,
                "chat_ids_configured": False,
                "chat_count": 0,
                "chat_ids": [],
                "bot_token": "",
            },
        )
        mock_print.assert_called_once_with("hello from logs", flush=True)
        post.assert_not_called()

    def test_send_message_uses_telegram_when_token_and_chat_ids_are_configured(self) -> None:
        response = MagicMock()
        with (
            patch.object(
                common_notify.runtime_settings,
                "get_telegram_settings",
                return_value={"bot_token": "secret-token", "chat_ids": ["123", "456"]},
            ),
            patch.object(common_notify.SESSION, "post", return_value=response) as post,
        ):
            result = common_notify.send_message("hello telegram")

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["configured"])
        self.assertEqual(result["mode"], "telegram")
        self.assertEqual(post.call_count, 2)
        response.raise_for_status.assert_called()


class NotifyServiceTests(unittest.TestCase):
    def test_health_and_ready_report_log_delivery_state(self) -> None:
        state = {
            "configured": False,
            "mode": "log",
            "reason": "telegram_not_configured:chat_ids",
            "bot_token_configured": True,
            "chat_ids_configured": False,
            "chat_count": 0,
        }
        with patch.object(notify_main.common_notify, "delivery_state", return_value={**state, "bot_token": "secret", "chat_ids": []}):
            health = notify_main.health()
            ready = notify_main.ready()

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["delivery"], state)
        self.assertEqual(ready, {"status": "ok", "ready": True, "service": "notify", "delivery": state})

    def test_notify_returns_explicit_log_delivery_state_when_unconfigured(self) -> None:
        state = {
            "configured": False,
            "mode": "log",
            "reason": "telegram_not_configured:bot_token,chat_ids",
            "bot_token_configured": False,
            "chat_ids_configured": False,
            "chat_count": 0,
        }
        with (
            patch.object(notify_main.common_notify, "delivery_state", return_value={**state, "bot_token": "", "chat_ids": []}),
            patch.object(notify_main.common_notify, "send_message") as send_message,
        ):
            response = notify_main.notify({"message": "notify me"})

        self.assertEqual(response, {"status": "ok", "delivery": state})
        send_message.assert_called_once_with("notify me")

    def test_notify_returns_explicit_telegram_delivery_state_when_configured(self) -> None:
        state = {
            "configured": True,
            "mode": "telegram",
            "reason": "",
            "bot_token_configured": True,
            "chat_ids_configured": True,
            "chat_count": 2,
        }
        with (
            patch.object(
                notify_main.common_notify,
                "delivery_state",
                return_value={**state, "bot_token": "secret-token", "chat_ids": ["123", "456"]},
            ),
            patch.object(notify_main.common_notify, "send_message") as send_message,
        ):
            response = notify_main.notify({"message": "notify me"})

        self.assertEqual(response, {"status": "ok", "delivery": state})
        send_message.assert_called_once_with("notify me")


if __name__ == "__main__":
    unittest.main()
