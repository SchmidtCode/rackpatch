from __future__ import annotations

import json
import re
from typing import Any

from common import config, db, image_updates


PUBLIC_SETTINGS_KEY = "public_settings"
TELEGRAM_SETTINGS_KEY = "telegram_settings"
DOCKER_UPDATE_SETTINGS_KEY = "docker_update_settings"
DEFAULT_DOCKER_BACKUP_RETENTION = 3
DEFAULT_DOCKER_RUN_BACKUP_COMMANDS = False


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


def _normalize_positive_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, number)


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def _parse_chat_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\s,]+", str(value or ""))
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _parse_allowed_user_ids(value: Any) -> list[str]:
    return _parse_chat_ids(value)


def _parse_allowed_usernames(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\s,]+", str(value or ""))
    usernames: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        username = str(item).strip().lstrip("@").lower()
        if not username or username in seen:
            continue
        seen.add(username)
        usernames.append(username)
    return usernames


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
    agent_compose_dir = _normalize_text(
        stored.get("agent_compose_dir", config.PUBLIC_AGENT_COMPOSE_DIR),
        strip_trailing_slash=True,
    )
    rackpatch_compose_dir = _normalize_text(
        stored.get("rackpatch_compose_dir", config.PUBLIC_RACKPATCH_COMPOSE_DIR),
        strip_trailing_slash=True,
    )
    return {
        "base_url": base_url or config.PUBLIC_BASE_URL,
        "repo_url": repo_url or config.PUBLIC_REPO_URL,
        "repo_ref": repo_ref or config.PUBLIC_REPO_REF,
        "agent_compose_dir": agent_compose_dir or config.PUBLIC_AGENT_COMPOSE_DIR,
        "rackpatch_compose_dir": rackpatch_compose_dir or config.PUBLIC_RACKPATCH_COMPOSE_DIR,
        "install_script_url_override": install_script_override,
        "install_script_url": config.derive_public_install_script_url(
            repo_url or config.PUBLIC_REPO_URL,
            repo_ref or config.PUBLIC_REPO_REF,
            install_script_override or config.PUBLIC_INSTALL_SCRIPT_URL,
        ),
    }


def set_public_settings(payload: dict[str, Any]) -> dict[str, str]:
    stored = _load_json_setting(PUBLIC_SETTINGS_KEY)
    for key in ("base_url", "repo_url", "repo_ref", "install_script_url", "agent_compose_dir", "rackpatch_compose_dir"):
        if key in payload:
            stored[key] = _normalize_text(
                payload.get(key, ""),
                strip_trailing_slash=key in {"base_url", "repo_url", "install_script_url", "agent_compose_dir", "rackpatch_compose_dir"},
            )
    _save_json_setting(PUBLIC_SETTINGS_KEY, stored)
    return get_public_settings()


def get_docker_update_settings() -> dict[str, Any]:
    stored = _load_json_setting(DOCKER_UPDATE_SETTINGS_KEY)
    normalized_policy = image_updates.normalize_policy(stored)
    return {
        "backup_retention": _normalize_positive_int(
            stored.get("backup_retention"),
            DEFAULT_DOCKER_BACKUP_RETENTION,
        ),
        "run_backup_commands": _normalize_bool(
            stored.get("run_backup_commands"),
            DEFAULT_DOCKER_RUN_BACKUP_COMMANDS,
        ),
        **normalized_policy,
    }


def set_docker_update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    stored = _load_json_setting(DOCKER_UPDATE_SETTINGS_KEY)
    if "backup_retention" in payload:
        stored["backup_retention"] = _normalize_positive_int(
            payload.get("backup_retention"),
            DEFAULT_DOCKER_BACKUP_RETENTION,
        )
    if "run_backup_commands" in payload:
        stored["run_backup_commands"] = _normalize_bool(
            payload.get("run_backup_commands"),
            DEFAULT_DOCKER_RUN_BACKUP_COMMANDS,
        )
    if "version_strategy" in payload or "strategy" in payload:
        normalized = image_updates.normalize_policy(
            {
                **stored,
                "version_strategy": payload.get("version_strategy", payload.get("strategy")),
            }
        )
        stored["version_strategy"] = normalized["version_strategy"]
    if "semver_policy" in payload:
        normalized = image_updates.normalize_policy(
            {
                **stored,
                "semver_policy": payload.get("semver_policy"),
            }
        )
        stored["semver_policy"] = normalized["semver_policy"]
    if "allow_prerelease" in payload:
        stored["allow_prerelease"] = _normalize_bool(
            payload.get("allow_prerelease"),
            image_updates.DEFAULT_ALLOW_PRERELEASE,
        )
    if "allow_major_upgrades" in payload:
        stored["allow_major_upgrades"] = _normalize_bool(
            payload.get("allow_major_upgrades"),
            image_updates.DEFAULT_ALLOW_MAJOR_UPGRADES,
        )
    if "resolve_to_digest" in payload:
        stored["resolve_to_digest"] = _normalize_bool(
            payload.get("resolve_to_digest"),
            image_updates.DEFAULT_RESOLVE_TO_DIGEST,
        )
    _save_json_setting(DOCKER_UPDATE_SETTINGS_KEY, stored)
    return get_docker_update_settings()


def get_telegram_settings(*, include_secret: bool = False) -> dict[str, Any]:
    stored = _load_json_setting(TELEGRAM_SETTINGS_KEY)
    bot_token = _normalize_text(stored["bot_token"]) if "bot_token" in stored else config.TELEGRAM_BOT_TOKEN
    chat_ids = _parse_chat_ids(stored["chat_ids"]) if "chat_ids" in stored else list(config.TELEGRAM_CHAT_IDS)
    allowed_user_ids = (
        _parse_allowed_user_ids(stored["allowed_user_ids"])
        if "allowed_user_ids" in stored
        else list(config.TELEGRAM_ALLOWED_USER_IDS)
    )
    allowed_usernames = (
        _parse_allowed_usernames(stored["allowed_usernames"])
        if "allowed_usernames" in stored
        else list(config.TELEGRAM_ALLOWED_USERNAMES)
    )
    security_blockers = config.insecure_secret_warnings_for_telegram_bot()
    result: dict[str, Any] = {
        "enabled": bool(bot_token),
        "chat_ids": chat_ids,
        "chat_ids_csv": ", ".join(chat_ids),
        "allowed_user_ids": allowed_user_ids,
        "allowed_user_ids_csv": ", ".join(allowed_user_ids),
        "allowed_usernames": allowed_usernames,
        "allowed_usernames_csv": ", ".join(f"@{item}" for item in allowed_usernames),
        "sender_allowlist_configured": bool(allowed_user_ids or allowed_usernames),
        "masked_bot_token": _mask_secret(bot_token),
        "bot_token_configured": bool(bot_token),
        "security_blockers": security_blockers,
        "bot_runtime_ready": bool(bot_token) and not security_blockers,
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
    if "allowed_user_ids" in payload:
        stored["allowed_user_ids"] = _parse_allowed_user_ids(payload.get("allowed_user_ids", ""))
    if "allowed_usernames" in payload:
        stored["allowed_usernames"] = _parse_allowed_usernames(payload.get("allowed_usernames", ""))
    _save_json_setting(TELEGRAM_SETTINGS_KEY, stored)
    return get_telegram_settings()
