#!/usr/bin/env python3

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from croniter import croniter
from datetime import datetime

OPS_ROOT = Path("/workspace")
STATE_FILE = OPS_ROOT / "state" / "scheduler-state.json"
SETTINGS_FILE = OPS_ROOT / "inventory" / "group_vars" / "all.yml"
OPS_API_BASE = os.environ.get("OPS_API_BASE", "http://ops-controller:9080").rstrip("/")
OPS_TELEGRAM_BASE = os.environ.get("OPS_TELEGRAM_BASE", "http://ops-telegram:9091").rstrip("/")
OPS_API_TOKEN = os.environ.get("OPS_API_TOKEN", "")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ops-scheduler/1.0"})


@dataclass(frozen=True)
class Job:
    name: str
    cron: str


def load_settings() -> dict:
    return yaml.safe_load(SETTINGS_FILE.read_text(encoding="utf-8")) or {}


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def api_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if OPS_API_TOKEN:
        headers["X-Ops-Token"] = OPS_API_TOKEN
    return headers


def ops_post(path: str, payload: dict) -> dict:
    response = SESSION.post(f"{OPS_API_BASE}{path}", headers=api_headers(), json=payload, timeout=900)
    response.raise_for_status()
    return response.json()


def notify(message: str) -> None:
    response = SESSION.post(
        f"{OPS_TELEGRAM_BASE}/notify",
        headers=api_headers(),
        json={"message": message},
        timeout=60,
    )
    response.raise_for_status()


def safe_notify(message: str) -> None:
    try:
        notify(message)
    except Exception as exc:  # noqa: BLE001
        print(f"telegram notify failed: {exc}", flush=True)
        print(message, flush=True)


def short_package_summary(report: dict) -> list[str]:
    lines = [
        f"packages: hosts={report.get('host_count', 0)} outdated_hosts={report.get('hosts_outdated', 0)} reboot_hosts={report.get('reboot_hosts', 0)} total_packages={report.get('total_packages', 0)}"
    ]
    for host in report.get("hosts", []):
        status = host.get("status", "unknown")
        if status == "up-to-date":
            lines.append(f"- {host['name']}: up-to-date")
            continue
        if status == "reboot-required":
            lines.append(f"- {host['name']}: reboot-required")
            continue
        if status == "outdated":
            reboot = "yes" if host.get("reboot_required") else "no"
            lines.append(f"- {host['name']}: {host.get('package_count', 0)} packages reboot={reboot}")
            continue
        detail = host.get("error", status)
        lines.append(f"- {host['name']}: error {detail}")
    return lines


def short_docker_summary(report: dict) -> list[str]:
    lines = [
        f"docker: stacks={report.get('stack_count', 0)} outdated_stacks={report.get('outdated_stacks', 0)} outdated_images={report.get('outdated_images', 0)}"
    ]
    for stack in report.get("stacks", []):
        status = stack.get("status", "unknown")
        if status == "up-to-date":
            lines.append(f"- {stack['name']}: up-to-date")
            continue
        if status == "outdated":
            lines.append(f"- {stack['name']}: {stack.get('outdated_count', 0)}/{stack.get('image_count', 0)} outdated")
            continue
        lines.append(f"- {stack['name']}: {status}")
    return lines


def short_execute_summary(label: str, payload: dict, response: dict) -> str:
    lines = [
        label,
        f"status={response.get('status')}",
        f"exit_code={response.get('exit_code')}",
        f"mode={'dry-run' if payload.get('dry_run') else 'live'}",
    ]
    update_report = response.get("update_report")
    if update_report:
        lines.append("")
        lines.extend(short_docker_summary(update_report))
    artifacts = response.get("artifacts") or {}
    artifact_lines: list[str] = []
    for kind in ("snapshot", "backup", "rollback"):
        for item in artifacts.get(kind, []):
            if item.get("value"):
                artifact_lines.append(f"- {kind} {item.get('stack')}: {item.get('value')}")
    if artifact_lines:
        lines.append("")
        lines.append("artifacts:")
        lines.extend(artifact_lines[:10])
    return "\n".join(lines)


def run_daily_status() -> None:
    docker_report = ops_post("/check-updates", {"window": "all"})
    package_report = ops_post("/check-packages", {"scope": "all"})
    pending = ops_post("/render-payload", {"window": "approve"})
    lines = ["Daily homelab update status"]
    lines.extend(short_docker_summary(docker_report))
    lines.append("")
    lines.extend(short_package_summary(package_report))
    if pending.get("approved_services"):
        lines.append("")
        lines.append(f"approved window pending: {','.join(pending['approved_services'])}")
        lines.append("run /approve_dry all or /approve_live all")
    safe_notify("\n".join(lines))


def run_low_risk_auto() -> None:
    payload = {"target": "auto-windowed", "window": "auto-windowed", "dry_run": False}
    response = ops_post("/execute", payload)
    safe_notify(short_execute_summary("Scheduled low-risk Docker window", payload, response))


def run_approved_reminder() -> None:
    pending = ops_post("/render-payload", {"window": "approve"})
    docker_report = ops_post("/check-updates", {"window": "approve"})
    package_report = ops_post("/check-packages", {"scope": "all"})
    lines = ["Approved maintenance window ready"]
    services = pending.get("approved_services") or []
    lines.append(f"approved_stacks={','.join(services) if services else 'none'}")
    lines.append("")
    lines.extend(short_docker_summary(docker_report))
    lines.append("")
    lines.extend(short_package_summary(package_report))
    lines.append("")
    lines.append("commands:")
    lines.append("/approve_dry all")
    lines.append("/approve_live all")
    lines.append("/maint_dry all")
    safe_notify("\n".join(lines))


def run_proxmox_reminder() -> None:
    package_report = ops_post("/check-packages", {"scope": "proxmox"})
    lines = ["Proxmox maintenance window ready"]
    lines.extend(short_package_summary(package_report))
    lines.append("")
    lines.append("commands:")
    lines.append("/reboots proxmox")
    lines.append("/proxmox_dry proxmox-node-a")
    lines.append("/proxmox_live proxmox-node-a")
    lines.append("/proxmox_soft_reboot proxmox-node-a")
    safe_notify("\n".join(lines))


def job_handlers() -> dict[str, callable]:
    return {
        "daily_status": run_daily_status,
        "low_risk_auto": run_low_risk_auto,
        "approved_reminder": run_approved_reminder,
        "proxmox_reminder": run_proxmox_reminder,
    }


def load_jobs() -> tuple[list[Job], ZoneInfo]:
    settings = load_settings()
    timezone_name = os.environ.get("TZ") or settings.get("maintenance_timezone", "America/New_York")
    windows = settings.get("default_windows", {})
    jobs = [
        Job("daily_status", windows.get("discovery", "0 5 * * *")),
        Job("low_risk_auto", windows.get("docker_auto", "30 5 * * *")),
        Job("approved_reminder", windows.get("approved_guest_container", "0 4 * * 6")),
        Job("proxmox_reminder", windows.get("proxmox_nodes", "30 4 * * 0")),
    ]
    return jobs, ZoneInfo(timezone_name)


def main() -> int:
    handlers = job_handlers()
    jobs, timezone = load_jobs()
    state = load_state()
    print(f"ops-scheduler running with timezone={timezone.key}", flush=True)
    for job in jobs:
        print(f"schedule {job.name}={job.cron}", flush=True)

    while True:
        now = datetime.now(timezone)
        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        for job in jobs:
            if croniter.match(job.cron, now):
                if state.get(job.name) == minute_key:
                    continue
                try:
                    handlers[job.name]()
                    state[job.name] = minute_key
                    save_state(state)
                    print(f"ran {job.name} at {minute_key}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    safe_notify(f"Scheduled job failed\njob={job.name}\nerror={exc}")
                    state[job.name] = minute_key
                    save_state(state)
        time.sleep(20)


if __name__ == "__main__":
    raise SystemExit(main())
