from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from common import config, db, runtime_settings


API_BASE = config.env("RACKPATCH_API_BASE", "http://api:9080").rstrip("/")
OFFSET_FILE = Path(
    config.env(
        "RACKPATCH_TELEGRAM_OFFSET_FILE",
        str(config.DATA_ROOT / "telegram-offset.txt"),
    )
)
POLL_TIMEOUT = int(config.env("RACKPATCH_TELEGRAM_POLL_TIMEOUT", "25"))
IDLE_SLEEP_SECONDS = float(config.env("RACKPATCH_TELEGRAM_IDLE_SLEEP", "5"))

API_SESSION = requests.Session()
API_SESSION.headers.update({"User-Agent": f"rackpatch-telegram/{config.APP_VERSION}"})
TELEGRAM_SESSION = requests.Session()
STATE = {"api_token": ""}


def load_offset() -> int | None:
    if not OFFSET_FILE.exists():
        return None
    try:
        return int(OFFSET_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def save_offset(offset: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset), encoding="utf-8")


def clip_text(message: str, limit: int = 3900) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 16] + "\n...[truncated]"


def telegram_request(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    telegram_settings = runtime_settings.get_telegram_settings(include_secret=True)
    bot_token = telegram_settings["bot_token"]
    if not bot_token:
        raise RuntimeError("telegram bot token is not configured")
    response = TELEGRAM_SESSION.post(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        json=payload,
        timeout=POLL_TIMEOUT + 5,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok", False):
        raise RuntimeError(f"telegram api error: {data}")
    return data


def send_message(chat_id: str, text: str) -> None:
    telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": clip_text(text),
            "disable_web_page_preview": True,
        },
    )


def ensure_api_token(force: bool = False) -> str:
    if STATE["api_token"] and not force:
        return STATE["api_token"]
    response = API_SESSION.post(
        f"{API_BASE}/api/v1/auth/login",
        json={
            "username": config.ADMIN_USERNAME,
            "password": config.ADMIN_PASSWORD,
        },
        timeout=30,
    )
    response.raise_for_status()
    STATE["api_token"] = response.json()["token"]
    return STATE["api_token"]


def api_request(method: str, path: str, payload: dict[str, Any] | None = None, *, retry: bool = True) -> dict[str, Any]:
    token = ensure_api_token()
    response = API_SESSION.request(
        method,
        f"{API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if response.status_code == 401 and retry:
        token = ensure_api_token(force=True)
        response = API_SESSION.request(
            method,
            f"{API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
    response.raise_for_status()
    return response.json()


def recent_jobs() -> list[dict[str, Any]]:
    return api_request("GET", "/api/v1/jobs").get("items", [])


def recent_schedules() -> list[dict[str, Any]]:
    return api_request("GET", "/api/v1/schedules").get("items", [])


def resolve_job_id(query: str) -> str:
    jobs = recent_jobs()
    if not jobs:
        raise ValueError("no jobs are available")
    exact = [job["id"] for job in jobs if job["id"] == query]
    if exact:
        return exact[0]
    matches = [job["id"] for job in jobs if str(job["id"]).startswith(query)]
    if not matches:
        raise ValueError(f"no job matches {query}")
    if len(matches) > 1:
        raise ValueError(f"multiple jobs match {query}")
    return matches[0]


def resolve_schedule_id(query: str) -> str:
    schedules = recent_schedules()
    if not schedules:
        raise ValueError("no schedules are configured")
    lowered = query.lower()
    exact = [item["id"] for item in schedules if item["id"] == query or item["name"].lower() == lowered]
    if exact:
        return exact[0]
    matches = [
        item["id"]
        for item in schedules
        if item["id"].startswith(query) or lowered in item["name"].lower()
    ]
    if not matches:
        raise ValueError(f"no schedule matches {query}")
    if len(matches) > 1:
        raise ValueError(f"multiple schedules match {query}")
    return matches[0]


def queue_job(kind: str, target_type: str, target_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
    return api_request(
        "POST",
        "/api/v1/jobs",
        {
            "kind": kind,
            "target_type": target_type,
            "target_ref": target_ref,
            "payload": payload,
        },
    )


def format_jobs(items: list[dict[str, Any]], *, limit: int = 8) -> str:
    if not items:
        return "No jobs yet."
    lines = ["Recent jobs"]
    for item in items[:limit]:
        lines.append(
            f"- [{str(item['id'])[:8]}] {item['kind']} {item['target_ref']} "
            f"{item['status']} approval={item['approval_status']}"
        )
    return "\n".join(lines)


def handle_status() -> str:
    overview = api_request("GET", "/api/v1/overview")
    agents = api_request("GET", "/api/v1/agents").get("items", [])
    jobs = recent_jobs()
    approvals = [item for item in jobs if item["approval_status"] == "pending"]
    running = [item for item in jobs if item["status"] == "running"]
    lines = [
        f"rackpatch status for {overview['site']}",
        f"Agents: {len(agents)} total",
        f"Stacks: {overview['stacks']}",
        f"Hosts: {overview['hosts']}",
        f"Jobs: {overview['counts']['jobs']} total / {overview['counts']['running_jobs']} running",
        f"Approvals: {len(approvals)} pending",
    ]
    if running:
        lines.append("")
        lines.append("Running jobs:")
        for item in running[:6]:
            lines.append(f"- [{str(item['id'])[:8]}] {item['kind']} {item['target_ref']}")
    return "\n".join(lines)


def handle_stacks() -> str:
    items = api_request("GET", "/api/v1/stacks").get("items", [])
    if not items:
        return "No stacks configured."
    lines = ["Stacks"]
    for item in items[:30]:
        lines.append(
            f"- {item['name']} host={item.get('host', 'unknown')} "
            f"mode={item.get('update_mode', 'manual')} risk={item.get('risk', 'unknown')}"
        )
    if len(items) > 30:
        lines.append(f"... +{len(items) - 30} more")
    return "\n".join(lines)


def handle_hosts() -> str:
    items = api_request("GET", "/api/v1/hosts").get("items", [])
    if not items:
        return "No hosts configured."
    lines = ["Hosts"]
    for item in items[:30]:
        agent = item.get("agent") or {}
        agent_status = agent.get("status", "no-agent")
        lines.append(
            f"- {item['name']} group={item.get('group', 'all')} "
            f"agent={agent_status} addr={item.get('ansible_host', 'n/a')}"
        )
    if len(items) > 30:
        lines.append(f"... +{len(items) - 30} more")
    return "\n".join(lines)


def handle_jobs(limit_arg: str | None) -> str:
    limit = 8
    if limit_arg:
        try:
            limit = max(1, min(20, int(limit_arg)))
        except ValueError:
            raise ValueError("job limit must be a number") from None
    return format_jobs(recent_jobs(), limit=limit)


def handle_logs(job_ref: str) -> str:
    job_id = resolve_job_id(job_ref)
    events = api_request("GET", f"/api/v1/jobs/{job_id}/events").get("items", [])
    if not events:
        return f"No events for job {job_id}."
    lines = [f"Logs for {job_id}"]
    for item in events[-40:]:
        lines.append(f"[{item['ts']}] {item['message']}")
    return "\n".join(lines)


def handle_approvals() -> str:
    approvals = [item for item in recent_jobs() if item["approval_status"] == "pending"]
    if not approvals:
        return "No pending approvals."
    lines = ["Pending approvals"]
    for item in approvals:
        lines.append(f"- [{str(item['id'])[:8]}] {item['kind']} {item['target_ref']}")
    return "\n".join(lines)


def handle_approve(job_ref: str) -> str:
    job_id = resolve_job_id(job_ref)
    api_request("POST", f"/api/v1/jobs/{job_id}/approve")
    return f"Approved job {job_id}."


def handle_discover(target: str) -> str:
    payload: dict[str, Any] = {
        "executor": "worker",
        "window": "all",
        "requires_approval": False,
    }
    if target != "all":
        payload["stacks"] = [target]
    result = queue_job("docker_discover", "stack", target, payload)
    return f"Queued docker discovery job {result['id']} for {target}."


def handle_update(target: str, mode: str) -> str:
    dry_run = mode != "live"
    payload: dict[str, Any] = {
        "executor": "auto",
        "dry_run": dry_run,
    }
    if dry_run:
        payload["requires_approval"] = False
    if target == "all":
        payload["window"] = "all" if dry_run else "approve"
    else:
        payload["selected_stacks"] = [target]
    result = queue_job("docker_update", "stack", target, payload)
    return f"Queued docker update job {result['id']} for {target} ({'dry-run' if dry_run else 'live'})."


def handle_patch(target: str, mode: str) -> str:
    dry_run = mode != "live"
    payload: dict[str, Any] = {
        "executor": "auto",
        "dry_run": dry_run,
    }
    if dry_run:
        payload["requires_approval"] = False
    result = queue_job("package_patch", "host", target, payload)
    return f"Queued package patch job {result['id']} for {target} ({'dry-run' if dry_run else 'live'})."


def handle_snapshot(target: str) -> str:
    result = queue_job(
        "snapshot",
        "host",
        target,
        {
            "executor": "worker",
            "requires_approval": False,
        },
    )
    return f"Queued snapshot job {result['id']} for {target}."


def handle_proxmox_patch(target: str, mode: str) -> str:
    dry_run = mode != "live"
    payload: dict[str, Any] = {
        "executor": "worker",
        "limit": target,
        "dry_run": dry_run,
    }
    if dry_run:
        payload["requires_approval"] = False
    result = queue_job("proxmox_patch", "host", target, payload)
    return f"Queued Proxmox patch job {result['id']} for {target} ({'dry-run' if dry_run else 'live'})."


def handle_proxmox_reboot(target: str, mode: str) -> str:
    dry_run = mode != "live"
    payload: dict[str, Any] = {
        "executor": "worker",
        "limit": target,
        "dry_run": dry_run,
    }
    if dry_run:
        payload["requires_approval"] = False
    result = queue_job("proxmox_reboot", "host", target, payload)
    return f"Queued Proxmox reboot job {result['id']} for {target} ({'dry-run' if dry_run else 'live'})."


def handle_backup(target: str) -> str:
    result = queue_job(
        "backup",
        "volume",
        target,
        {
            "executor": "worker",
            "volume": target,
            "requires_approval": False,
        },
    )
    return f"Queued backup job {result['id']} for {target}."


def handle_rollback(target: str) -> str:
    result = queue_job("rollback", "stack", target, {"executor": "worker"})
    return f"Queued rollback job {result['id']} for {target}."


def handle_schedules() -> str:
    items = recent_schedules()
    if not items:
        return "No schedules configured."
    lines = ["Schedules"]
    for item in items:
        lines.append(
            f"- [{str(item['id'])[:8]}] {item['name']} {item['cron_expr']} "
            f"{'enabled' if item['enabled'] else 'disabled'}"
        )
    return "\n".join(lines)


def handle_schedule_toggle(query: str, action: str) -> str:
    schedule_id = resolve_schedule_id(query)
    enabled = action.lower() == "on"
    api_request("POST", f"/api/v1/schedules/{schedule_id}/toggle", {"enabled": enabled})
    return f"{'Enabled' if enabled else 'Disabled'} schedule {schedule_id}."


def handle_generic_job(kind: str, target_type: str, target_ref: str, payload_text: str | None) -> str:
    payload = json.loads(payload_text) if payload_text else {}
    if not isinstance(payload, dict):
        raise ValueError("job payload must be a JSON object")
    result = queue_job(kind, target_type, target_ref, payload)
    return f"Queued {kind} job {result['id']} for {target_type}:{target_ref}."


def help_text() -> str:
    return "\n".join(
        [
            "rackpatch Telegram commands",
            "/status",
            "/stacks",
            "/hosts",
            "/jobs [limit]",
            "/logs <job-id>",
            "/approvals",
            "/approve <job-id>",
            "/discover <stack|all>",
            "/update <stack|all> [dry|live]",
            "/patch <host|all> [dry|live]",
            "/snapshot <host>",
            "/proxmox-patch <limit> [dry|live]",
            "/proxmox-reboot <limit> [dry|live]",
            "/backup <volume>",
            "/rollback <stack>",
            "/schedules",
            "/schedule <name-or-id> on|off",
            "/job <kind> <target_type> <target_ref> [json-payload]",
        ]
    )


def handle_command(text: str) -> str:
    pieces = text.strip().split(maxsplit=4)
    if not pieces:
        return help_text()

    command = pieces[0].split("@", 1)[0].lower()
    args = pieces[1:]

    if command in {"/start", "/help"}:
        return help_text()
    if command == "/status":
        return handle_status()
    if command == "/stacks":
        return handle_stacks()
    if command == "/hosts":
        return handle_hosts()
    if command == "/jobs":
        return handle_jobs(args[0] if args else None)
    if command == "/logs":
        if len(args) != 1:
            raise ValueError("usage: /logs <job-id>")
        return handle_logs(args[0])
    if command == "/approvals":
        return handle_approvals()
    if command == "/approve":
        if len(args) != 1:
            raise ValueError("usage: /approve <job-id>")
        return handle_approve(args[0])
    if command == "/discover":
        if len(args) != 1:
            raise ValueError("usage: /discover <stack|all>")
        return handle_discover(args[0])
    if command == "/update":
        if len(args) not in {1, 2}:
            raise ValueError("usage: /update <stack|all> [dry|live]")
        return handle_update(args[0], args[1].lower() if len(args) == 2 else "dry")
    if command == "/patch":
        if len(args) not in {1, 2}:
            raise ValueError("usage: /patch <host|all> [dry|live]")
        return handle_patch(args[0], args[1].lower() if len(args) == 2 else "dry")
    if command == "/snapshot":
        if len(args) != 1:
            raise ValueError("usage: /snapshot <host>")
        return handle_snapshot(args[0])
    if command == "/proxmox-patch":
        if len(args) not in {1, 2}:
            raise ValueError("usage: /proxmox-patch <limit> [dry|live]")
        return handle_proxmox_patch(args[0], args[1].lower() if len(args) == 2 else "dry")
    if command == "/proxmox-reboot":
        if len(args) not in {1, 2}:
            raise ValueError("usage: /proxmox-reboot <limit> [dry|live]")
        return handle_proxmox_reboot(args[0], args[1].lower() if len(args) == 2 else "dry")
    if command == "/backup":
        if len(args) != 1:
            raise ValueError("usage: /backup <volume>")
        return handle_backup(args[0])
    if command == "/rollback":
        if len(args) != 1:
            raise ValueError("usage: /rollback <stack>")
        return handle_rollback(args[0])
    if command == "/schedules":
        return handle_schedules()
    if command == "/schedule":
        if len(args) < 2:
            raise ValueError("usage: /schedule <name-or-id> on|off")
        action = args[-1].lower()
        if action not in {"on", "off"}:
            raise ValueError("schedule action must be on or off")
        query = " ".join(args[:-1]).strip()
        return handle_schedule_toggle(query, action)
    if command == "/job":
        if len(args) < 3:
            raise ValueError("usage: /job <kind> <target_type> <target_ref> [json-payload]")
        payload_text = args[3] if len(args) == 4 else None
        return handle_generic_job(args[0], args[1], args[2], payload_text)

    return "Unknown command. Use /help."


def process_update(update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    text = str(message.get("text", "")).strip()
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", "")).strip()
    if not text or not chat_id:
        return

    telegram_settings = runtime_settings.get_telegram_settings()
    allowed_chats = set(telegram_settings["chat_ids"])
    if allowed_chats and chat_id not in allowed_chats:
        send_message(chat_id, "This chat is not authorized for rackpatch.")
        return
    if not allowed_chats:
        send_message(chat_id, "Telegram is configured without allowed chat IDs. Add at least one in Settings.")
        return

    try:
        send_message(chat_id, handle_command(text))
    except Exception as exc:  # noqa: BLE001
        send_message(chat_id, f"Command failed: {exc}")


def poll_updates(offset: int | None) -> tuple[list[dict[str, Any]], int | None]:
    payload: dict[str, Any] = {"timeout": POLL_TIMEOUT}
    if offset is not None:
        payload["offset"] = offset
    response = telegram_request("getUpdates", payload)
    updates = response.get("result", [])
    next_offset = offset
    for item in updates:
        next_offset = int(item["update_id"]) + 1
    return updates, next_offset


def main() -> int:
    db.init_db()
    offset = load_offset()
    print("rackpatch-telegram started", flush=True)
    while True:
        telegram_settings = runtime_settings.get_telegram_settings(include_secret=True)
        if not telegram_settings["bot_token"]:
            print("telegram token is not configured; sleeping", flush=True)
            time.sleep(IDLE_SLEEP_SECONDS)
            continue
        try:
            updates, offset = poll_updates(offset)
            for update in updates:
                process_update(update)
            if offset is not None:
                save_offset(offset)
        except Exception as exc:  # noqa: BLE001
            print(f"rackpatch-telegram error: {exc}", flush=True)
            time.sleep(IDLE_SLEEP_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
