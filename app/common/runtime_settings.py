from __future__ import annotations

import json
import re
from typing import Any

from common import config, db


PUBLIC_SETTINGS_KEY = "public_settings"
TELEGRAM_SETTINGS_KEY = "telegram_settings"


def _load_json_setting(key: str) -> dict[str, Any]:
    row = db.fetch_one("SELECT value FROM settings WHERE key = %s", (key,))
    value = (row or {}).get("value")
    return value if isinstance(value, dict) else {}


def _save_json_setting(key: str, value: dict[str, Any]) -> None:
    with db.db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET
              value = EXCLUDED.value,
              updated_at = NOW()
            """,
            (key, json.dumps(value)),
        )


def _normalize_text(value: Any, *, strip_trailing_slash: bool = False) -> str:
    text = str(value or "").strip()
    if strip_trailing_slash:
        text = text.rstrip("/")
    return text


def _parse_chat_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\s,]+", str(value or ""))
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:6]}...{secret[-4:]}"


def get_public_settings() -> dict[str, str]:
    stored = _load_json_setting(PUBLIC_SETTINGS_KEY)
    base_url = _normalize_text(stored.get("base_url", config.PUBLIC_BASE_URL), strip_trailing_slash=True)
    repo_url = _normalize_text(stored.get("repo_url", config.PUBLIC_REPO_URL), strip_trailing_slash=True)
    repo_ref = _normalize_text(stored.get("repo_ref", config.PUBLIC_REPO_REF)) or config.PUBLIC_REPO_REF
    install_script_override = _normalize_text(stored.get("install_script_url", ""), strip_trailing_slash=True)
    return {
        "base_url": base_url or config.PUBLIC_BASE_URL,
        "repo_url": repo_url or config.PUBLIC_REPO_URL,
        "repo_ref": repo_ref or config.PUBLIC_REPO_REF,
        "install_script_url_override": install_script_override,
        "install_script_url": config.derive_public_install_script_url(
            repo_url or config.PUBLIC_REPO_URL,
            repo_ref or config.PUBLIC_REPO_REF,
            install_script_override or config.PUBLIC_INSTALL_SCRIPT_URL,
        ),
    }


def set_public_settings(payload: dict[str, Any]) -> dict[str, str]:
    stored = _load_json_setting(PUBLIC_SETTINGS_KEY)
    for key in ("base_url", "repo_url", "repo_ref", "install_script_url"):
        if key in payload:
            stored[key] = _normalize_text(
                payload.get(key, ""),
                strip_trailing_slash=key in {"base_url", "repo_url", "install_script_url"},
            )
    _save_json_setting(PUBLIC_SETTINGS_KEY, stored)
    return get_public_settings()


def get_telegram_settings(*, include_secret: bool = False) -> dict[str, Any]:
    stored = _load_json_setting(TELEGRAM_SETTINGS_KEY)
    bot_token = _normalize_text(stored["bot_token"]) if "bot_token" in stored else config.TELEGRAM_BOT_TOKEN
    chat_ids = _parse_chat_ids(stored["chat_ids"]) if "chat_ids" in stored else list(config.TELEGRAM_CHAT_IDS)
    result: dict[str, Any] = {
        "enabled": bool(bot_token),
        "chat_ids": chat_ids,
        "chat_ids_csv": ", ".join(chat_ids),
        "masked_bot_token": _mask_secret(bot_token),
        "bot_token_configured": bool(bot_token),
    }
    if include_secret:
        result["bot_token"] = bot_token
    return result


def set_telegram_settings(payload: dict[str, Any]) -> dict[str, Any]:
    stored = _load_json_setting(TELEGRAM_SETTINGS_KEY)
    if "bot_token" in payload:
        stored["bot_token"] = _normalize_text(payload.get("bot_token", ""))
    if "chat_ids" in payload:
        stored["chat_ids"] = _parse_chat_ids(payload.get("chat_ids", ""))
    _save_json_setting(TELEGRAM_SETTINGS_KEY, stored)
    return get_telegram_settings()
