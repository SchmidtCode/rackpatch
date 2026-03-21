from __future__ import annotations

import json
from typing import Any

import requests

from common import runtime_settings


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "rackpatch-notify/1.0"})


def _event_set(payload: dict[str, Any]) -> set[str]:
    raw = payload.get("notify_on")
    if raw is None:
        return {"pending", "approved", "completed", "failed"}
    if isinstance(raw, str):
        return {item.strip().lower() for item in raw.split(",") if item.strip()}
    if isinstance(raw, list):
        return {str(item).strip().lower() for item in raw if str(item).strip()}
    return set()


def should_notify(payload: dict[str, Any], event: str) -> bool:
    if not payload.get("notify"):
        return False
    return event.lower() in _event_set(payload)


def send_message(message: str) -> None:
    text = str(message or "").strip()
    if not text:
        return
    telegram = runtime_settings.get_telegram_settings(include_secret=True)
    if not telegram.get("bot_token") or not telegram.get("chat_ids"):
        print(text, flush=True)
        return
    for chat_id in telegram["chat_ids"]:
        response = SESSION.post(
            f"https://api.telegram.org/bot{telegram['bot_token']}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=30,
        )
        response.raise_for_status()


def _short_job_id(job: dict[str, Any]) -> str:
    return str(job.get("id", ""))[:8]


def _parse_json_stdout(result: dict[str, Any]) -> dict[str, Any] | None:
    stdout = str(result.get("stdout", "")).strip()
    if not stdout:
        return None
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _docker_summary(result: dict[str, Any]) -> list[str]:
    payload = _parse_json_stdout(result)
    if not payload:
        return []
    lines = [
        f"docker stacks={payload.get('stack_count', 0)} outdated_stacks={payload.get('outdated_stacks', 0)} outdated_images={payload.get('outdated_images', 0)}"
    ]
    for stack in payload.get("stacks", [])[:6]:
        status = stack.get("status", "unknown")
        if status == "outdated":
            lines.append(f"- {stack['name']}: {stack.get('outdated_count', 0)}/{stack.get('image_count', 0)} outdated")
        else:
            lines.append(f"- {stack['name']}: {status}")
    return lines


def _package_summary(result: dict[str, Any]) -> list[str]:
    payload = _parse_json_stdout(result)
    if payload:
        lines = [
            f"packages hosts={payload.get('host_count', 0)} outdated_hosts={payload.get('hosts_outdated', 0)} reboot_hosts={payload.get('reboot_hosts', 0)} total_packages={payload.get('total_packages', 0)}"
        ]
        for host in payload.get("hosts", [])[:6]:
            status = host.get("status", "unknown")
            if status == "outdated":
                lines.append(f"- {host['name']}: {host.get('package_count', 0)} packages reboot={'yes' if host.get('reboot_required') else 'no'}")
            else:
                lines.append(f"- {host['name']}: {status}")
        return lines

    packages = result.get("packages")
    if isinstance(packages, list):
        count = len(packages)
        reboot = "yes" if result.get("reboot_required") else "no"
        lines = [f"packages host_count=1 outdated_hosts={1 if count else 0} reboot_hosts={1 if result.get('reboot_required') else 0} total_packages={count}"]
        lines.append(f"- host: {'up-to-date' if count == 0 else f'{count} packages reboot={reboot}'}")
        return lines
    return []


def _artifact_lines(result: dict[str, Any]) -> list[str]:
    artifacts = result.get("artifacts") or []
    if isinstance(artifacts, dict):
        combined: list[dict[str, Any]] = []
        for items in artifacts.values():
            if isinstance(items, list):
                combined.extend(item for item in items if isinstance(item, dict))
        artifacts = combined
    if not isinstance(artifacts, list):
        return []

    lines: list[str] = []
    for item in artifacts[:8]:
        kind = str(item.get("kind", item.get("type", "artifact")))
        target = str(item.get("stack", item.get("target_ref", ""))).strip()
        value = str(item.get("value", item.get("path", ""))).strip()
        if not value:
            continue
        prefix = f"- {kind}"
        if target:
            prefix += f" {target}"
        lines.append(f"{prefix}: {value}")
    return lines


def job_message(job: dict[str, Any], event: str, result: dict[str, Any] | None = None) -> str:
    result = result or {}
    payload = dict(job.get("payload") or {})
    label = f"[{_short_job_id(job)}] {job.get('kind', 'job')} {job.get('target_ref', 'unknown')}"
    lines: list[str] = []

    if event == "pending":
        lines = [
            f"Approval required {label}",
            f"source={job.get('source', 'unknown')}",
            f"requested_by={job.get('requested_by', 'unknown')}",
        ]
        if payload.get("selected_stacks"):
            lines.append(f"stacks={','.join(payload['selected_stacks'])}")
        if payload.get("limit"):
            lines.append(f"limit={payload['limit']}")
        lines.append(f"/approve {job.get('id')}")
        lines.append(f"/logs {job.get('id')}")
        return "\n".join(lines)

    if event == "approved":
        return "\n".join(
            [
                f"Approved {label}",
                f"approved_by={job.get('approved_by', 'unknown')}",
            ]
        )

    status = "completed" if event == "completed" else "failed"
    lines = [
        f"{status.title()} {label}",
        f"source={job.get('source', 'unknown')}",
    ]
    if "exit_code" in result:
        lines.append(f"exit_code={result.get('exit_code')}")
    if result.get("error"):
        lines.append(f"error={result['error']}")

    if job.get("kind") == "package_check":
        lines.extend(_package_summary(result))

    artifact_lines = _artifact_lines(result)
    if artifact_lines:
        lines.append("artifacts:")
        lines.extend(artifact_lines)
    return "\n".join(lines)


def send_job_event(job: dict[str, Any], event: str, result: dict[str, Any] | None = None) -> None:
    payload = dict(job.get("payload") or {})
    if not should_notify(payload, event):
        return
    try:
        send_message(job_message(job, event, result))
    except Exception as exc:  # noqa: BLE001
        print(f"job notification failed for {job.get('id')}: {exc}", flush=True)
