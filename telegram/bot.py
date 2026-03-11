#!/usr/bin/env python3

import json
import os
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests


BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_IDS = {item.strip() for item in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if item.strip()}
OPS_API_BASE = os.environ.get("OPS_API_BASE", "http://ops-controller:9080").rstrip("/")
OPS_API_TOKEN = os.environ.get("OPS_API_TOKEN", "")
BOT_PORT = int(os.environ.get("TELEGRAM_BOT_PORT", "9091"))
STATE_ROOT = Path("/workspace/state")
OFFSET_FILE = STATE_ROOT / "telegram-offset.txt"
HARD_REBOOT_FILE = STATE_ROOT / "hard-reboot-approvals.json"
HARD_REBOOT_TTL_SECONDS = 600


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "ops-telegram/1.0"})
    return session


SESSION = new_session()


def telegram_api(method: str, payload: dict) -> dict:
    response = SESSION.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram api error: {data}")
    return data


def send_message(message: str, chat_id: str | None = None) -> None:
    targets = [chat_id] if chat_id else sorted(CHAT_IDS)
    if not targets:
        return
    clipped = message if len(message) <= 3900 else message[:3890] + "\n...[truncated]"
    for target in targets:
        telegram_api(
            "sendMessage",
            {
                "chat_id": target,
                "text": clipped,
                "disable_web_page_preview": True,
            },
        )


def load_offset() -> int | None:
    if not OFFSET_FILE.exists():
        return None
    try:
        return int(OFFSET_FILE.read_text().strip())
    except ValueError:
        return None


def save_offset(offset: int) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset), encoding="utf-8")


def load_hard_reboot_requests() -> dict:
    if not HARD_REBOOT_FILE.exists():
        return {}
    try:
        return json.loads(HARD_REBOOT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_hard_reboot_requests(data: dict) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    HARD_REBOOT_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ops_post(path: str, payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if OPS_API_TOKEN:
        headers["X-Ops-Token"] = OPS_API_TOKEN
    response = SESSION.post(f"{OPS_API_BASE}{path}", headers=headers, json=payload, timeout=600)
    response.raise_for_status()
    return response.json()


def summarize_pending(payload: dict) -> str:
    stacks = payload.get("stacks", [])
    if not stacks:
        return "No pending approved stacks."
    lines = [f"Pending window: {payload.get('window', 'approve')}"]
    for stack in stacks:
        protections = []
        if stack.get("backup_before"):
            protections.append("backup")
        if stack.get("snapshot_before"):
            protections.append("snapshot")
        protection_text = ",".join(protections) if protections else "none"
        lines.append(f"- {stack['name']} risk={stack['risk']} protections={protection_text}")
    lines.append("")
    lines.append("Commands:")
    lines.append("/approve_dry all  # approved Docker stacks only")
    lines.append("/approve_live all # approved Docker stacks only")
    lines.append("/maint_dry all    # includes guest maintenance")
    lines.append("/maint_live all   # includes guest maintenance")
    lines.append("/updates all|approve|auto|stack1,stack2")
    return "\n".join(lines)


def parse_selected_stacks(stdout: str) -> list[str]:
    match = re.search(r'"stacks"\s*:\s*\[(.*?)\]', stdout, re.S)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def parse_stack_actions(stdout: str) -> list[tuple[str, str]]:
    matches = re.findall(r'"action"\s*:\s*"([^"]+)".*?"stack"\s*:\s*"([^"]+)"', stdout, re.S)
    return [(stack, action) for action, stack in matches]


def parse_play_recap(stdout: str) -> list[str]:
    recap_lines: list[str] = []
    for line in stdout.splitlines():
        if re.search(r"\bok=\d+\s+changed=\d+\s+unreachable=\d+\s+failed=\d+", line):
            recap_lines.append(" ".join(line.split()))
    return recap_lines


def parse_failure_context(stdout: str, stderr: str) -> list[str]:
    tail = stderr.strip() or stdout.strip()
    if not tail:
        return []
    lines = tail.splitlines()
    failed_task = ""
    for index, line in enumerate(lines):
        if "FAILED!" in line:
            for reverse_index in range(index, -1, -1):
                if lines[reverse_index].startswith("TASK ["):
                    failed_task = lines[reverse_index]
                    break
            break
    summary: list[str] = []
    if failed_task:
        summary.append(failed_task)
    summary.extend(lines[-10:])
    return summary


def summarize_update_report(report: dict | None) -> list[str]:
    if not report:
        return []
    lines = [
        f"update summary: stacks={report.get('stack_count', 0)} outdated_stacks={report.get('outdated_stacks', 0)} outdated_images={report.get('outdated_images', 0)}"
    ]
    for stack in report.get("stacks", []):
        name = stack.get("name", "unknown")
        status = stack.get("status", "unknown")
        if status == "up-to-date":
            lines.append(f"- {name}: up-to-date")
            continue
        if status == "outdated":
            lines.append(f"- {name}: {stack.get('outdated_count', 0)}/{stack.get('image_count', 0)} outdated")
            for image in stack.get("images", []):
                if image.get("status") == "outdated":
                    lines.append(
                        f"  {image['ref']} {image.get('local_short', 'unknown')} -> {image.get('remote_short', 'unknown')}"
                    )
            continue
        if status == "warning":
            lines.append(f"- {name}: warning")
            for image in stack.get("images", []):
                if image.get("status") != "up-to-date":
                    detail = image.get("error") or image.get("status")
                    lines.append(f"  {image['ref']} {detail}")
            continue
        if status == "unsupported":
            lines.append(f"- {name}: unsupported")
            continue
        lines.append(f"- {name}: {status}")
    return lines


def summarize_artifacts(artifacts: dict | None) -> list[str]:
    if not artifacts:
        return []
    lines: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for kind in ("snapshot", "backup", "rollback"):
        for item in artifacts.get(kind, []):
            key = (kind, item.get("stack") or "", item.get("value") or "")
            if key in seen or not key[2]:
                continue
            seen.add(key)
            if not lines:
                lines.append("artifacts:")
            lines.append(f"- {kind} {item.get('stack')}: {item.get('value')}")
    return lines


def summarize_update_check(report: dict) -> str:
    lines = ["Docker update check"]
    lines.extend(summarize_update_report(report))
    return "\n".join(lines)


def summarize_package_report(report: dict) -> str:
    lines = [
        "Package update check",
        f"package summary: hosts={report.get('host_count', 0)} outdated_hosts={report.get('hosts_outdated', 0)} reboot_hosts={report.get('reboot_hosts', 0)} total_packages={report.get('total_packages', 0)}",
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
            for package in host.get("packages", [])[:3]:
                lines.append(f"  {package}")
            if host.get("package_count", 0) > 3:
                lines.append(f"  ... +{host['package_count'] - 3} more")
            continue
        lines.append(f"- {host['name']}: error {host.get('error', status)}")
    return "\n".join(lines)


def summarize_status(docker_report: dict, package_report: dict) -> str:
    lines = ["Homelab status"]
    lines.extend(summarize_update_report(docker_report))
    lines.append("")
    lines.append(
        f"package summary: hosts={package_report.get('host_count', 0)} outdated_hosts={package_report.get('hosts_outdated', 0)} reboot_hosts={package_report.get('reboot_hosts', 0)} total_packages={package_report.get('total_packages', 0)}"
    )
    for host in package_report.get("hosts", []):
        if host.get("status") == "up-to-date":
            lines.append(f"- {host['name']}: up-to-date")
        elif host.get("status") == "reboot-required":
            lines.append(f"- {host['name']}: reboot-required")
        elif host.get("status") == "outdated":
            reboot = "yes" if host.get("reboot_required") else "no"
            lines.append(f"- {host['name']}: {host.get('package_count', 0)} packages reboot={reboot}")
        else:
            lines.append(f"- {host['name']}: error {host.get('error', host.get('status'))}")
    return "\n".join(lines)


def summarize_reboots(report: dict) -> str:
    lines = ["Reboot status", f"reboot_hosts={report.get('reboot_hosts', 0)}"]
    reboot_hosts = [host for host in report.get("hosts", []) if host.get("reboot_required")]
    if not reboot_hosts:
        lines.append("No managed hosts currently report reboot-required.")
        return "\n".join(lines)
    for host in reboot_hosts:
        lines.append(f"- {host['name']}: reboot-required")
    return "\n".join(lines)


def live_risk_banner(kind: str, targets: str) -> str:
    lines = [f"Safety warning: {kind}"]
    lines.append(f"targets={targets}")
    if kind == "guest patching":
        lines.append("This live run may reboot the selected guest(s) if reboot-required is present.")
    elif kind == "proxmox patching":
        lines.append("This live run applies Proxmox package updates only and will not reboot the selected node(s).")
        lines.append("If reboot-required is reported afterward, use /reboots proxmox and then /proxmox_soft_reboot host1,host2.")
    elif kind == "maintenance orchestrator":
        lines.append("This live run may reboot guest(s) if reboot-required is present.")
        lines.append("DNS-critical services on your primary apps guest may be interrupted during the maintenance window.")
    return "\n".join(lines)


def make_hard_reboot_key(chat_id: str, targets: str) -> str:
    return f"{chat_id}:{targets}"


def request_hard_reboot_approval(chat_id: str, targets: str) -> None:
    approvals = load_hard_reboot_requests()
    approvals[make_hard_reboot_key(chat_id, targets)] = {"requested_at": int(time.time())}
    save_hard_reboot_requests(approvals)


def consume_hard_reboot_approval(chat_id: str, targets: str) -> bool:
    approvals = load_hard_reboot_requests()
    key = make_hard_reboot_key(chat_id, targets)
    payload = approvals.get(key)
    now = int(time.time())
    changed = False
    for stale_key, stale_value in list(approvals.items()):
        if now - int(stale_value.get("requested_at", 0)) > HARD_REBOOT_TTL_SECONDS:
            approvals.pop(stale_key, None)
            changed = True
    if not payload or now - int(payload.get("requested_at", 0)) > HARD_REBOOT_TTL_SECONDS:
        if changed:
            save_hard_reboot_requests(approvals)
        return False
    approvals.pop(key, None)
    save_hard_reboot_requests(approvals)
    return True


def summarize_result(label: str, response: dict, payload: dict) -> str:
    stdout = (response.get("stdout") or "").strip()
    stderr = (response.get("stderr") or "").strip()
    selected_stacks = response.get("approved_services") or parse_selected_stacks(stdout)
    stack_actions = parse_stack_actions(stdout)
    recaps = parse_play_recap(stdout)

    lines = [
        f"{label}",
        f"status={response.get('status')}",
        f"exit_code={response.get('exit_code')}",
        f"mode={'dry-run' if payload.get('dry_run') else 'live'}",
    ]
    if response.get("window"):
        lines.append(f"window={response.get('window')}")
    if selected_stacks:
        lines.append(f"stacks={','.join(selected_stacks)}")
    if stack_actions:
        lines.append("")
        lines.append("stack summary:")
        for stack, action in stack_actions:
            lines.append(f"- {stack}: {action}")
    update_lines = summarize_update_report(response.get("update_report"))
    if update_lines:
        lines.append("")
        lines.extend(update_lines)
    if recaps:
        lines.append("")
        lines.append("recap:")
        lines.extend(f"- {line}" for line in recaps[-3:])
    artifact_lines = summarize_artifacts(response.get("artifacts"))
    if artifact_lines:
        lines.append("")
        lines.extend(artifact_lines)
    if response.get("exit_code", 1) != 0:
        failure_context = parse_failure_context(stdout, stderr)
        if failure_context:
            lines.append("")
            lines.append("failure:")
            lines.extend(failure_context)
    return "\n".join(lines)


def parse_services(parts: list[str]) -> list[str]:
    services: list[str] = []
    for part in parts:
        for item in part.split(","):
            item = item.strip()
            if item:
                services.append(item)
    return services


def resolve_pending_services() -> list[str]:
    payload = ops_post("/render-payload", {"window": "approve"})
    return payload.get("approved_services", [])


def resolve_update_query(args: list[str]) -> dict:
    if not args or args == ["all"]:
        return {"window": "all"}
    first = args[0].lower()
    if first in {"approve", "approved"}:
        return {"window": "approve"}
    if first in {"auto", "auto-windowed"}:
        return {"window": "auto-windowed"}
    return {"stacks": parse_services(args)}


def resolve_package_query(args: list[str]) -> dict:
    if not args or args == ["all"]:
        return {"scope": "all"}
    first = args[0].lower()
    if first in {"guests", "docker_hosts", "docker-hosts"}:
        return {"scope": "guests" if first == "guests" else "docker_hosts"}
    if first in {"proxmox", "proxmox_nodes", "nodes"}:
        return {"scope": "proxmox"}
    return {"hosts": parse_services(args)}


def execute_async(chat_id: str, label: str, payload: dict) -> None:
    def worker() -> None:
        try:
            response = ops_post("/execute", payload)
            send_message(summarize_result(label, response, payload), chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001
            send_message(f"{label}\nstatus=failed\nerror={exc}", chat_id=chat_id)

    threading.Thread(target=worker, daemon=True).start()


def check_updates_async(chat_id: str, payload: dict) -> None:
    def worker() -> None:
        try:
            response = ops_post("/check-updates", payload)
            send_message(summarize_update_check(response), chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001
            send_message(f"Docker update check\nstatus=failed\nerror={exc}", chat_id=chat_id)

    threading.Thread(target=worker, daemon=True).start()


def check_packages_async(chat_id: str, payload: dict) -> None:
    def worker() -> None:
        try:
            response = ops_post("/check-packages", payload)
            send_message(summarize_package_report(response), chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001
            send_message(f"Package update check\nstatus=failed\nerror={exc}", chat_id=chat_id)

    threading.Thread(target=worker, daemon=True).start()


def check_reboots_async(chat_id: str, payload: dict) -> None:
    def worker() -> None:
        try:
            response = ops_post("/check-packages", payload)
            send_message(summarize_reboots(response), chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001
            send_message(f"Reboot status\nstatus=failed\nerror={exc}", chat_id=chat_id)

    threading.Thread(target=worker, daemon=True).start()


def status_async(chat_id: str) -> None:
    def worker() -> None:
        try:
            docker_report = ops_post("/check-updates", {"window": "all"})
            package_report = ops_post("/check-packages", {"scope": "all"})
            send_message(summarize_status(docker_report, package_report), chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001
            send_message(f"Homelab status\nstatus=failed\nerror={exc}", chat_id=chat_id)

    threading.Thread(target=worker, daemon=True).start()


def handle_command(chat_id: str, text: str) -> None:
    parts = text.strip().split()
    if not parts:
        return

    command = parts[0].lower()
    args = parts[1:]

    if command == "/help":
        send_message(
            "\n".join(
                [
                    "Ops Telegram commands",
                    "/health",
                    "/status",
                    "/pending",
                    "/packages all|guests|proxmox|host1,host2",
                    "/reboots all|guests|proxmox|host1,host2",
                    "/auto_dry",
                    "/auto_live",
                    "/approve_dry all|stack1,stack2  approved Docker stacks only",
                    "/approve_live all|stack1,stack2 approved Docker stacks only",
                    "/guest_dry host1,host2         guest package patch dry-run",
                    "/guest_live host1,host2        guest package patch live",
                    "/proxmox_dry host1,host2       proxmox node patch dry-run",
                    "/proxmox_live host1,host2      proxmox node patch live",
                    "/proxmox_soft_reboot host1,host2",
                    "/proxmox_hard_request host1,host2",
                    "/proxmox_hard_confirm host1,host2",
                    "/maint_dry all|stack1,stack2    guest/container maintenance",
                    "/maint_live all|stack1,stack2   guest/container maintenance",
                    "/updates all|approve|auto|stack1,stack2",
                ]
            ),
            chat_id=chat_id,
        )
        return

    if command == "/health":
        try:
            controller = SESSION.get(f"{OPS_API_BASE}/health", timeout=15).json()
            send_message(f"ops-controller status={controller.get('status', 'unknown')}", chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001
            send_message(f"ops-controller status=failed error={exc}", chat_id=chat_id)
        return

    if command == "/status":
        send_message("Starting homelab status check", chat_id=chat_id)
        status_async(chat_id)
        return

    if command == "/pending":
        try:
            payload = ops_post("/render-payload", {"window": "approve"})
            send_message(summarize_pending(payload), chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001
            send_message(f"pending lookup failed: {exc}", chat_id=chat_id)
        return

    if command in {"/auto_dry", "/auto_live"}:
        dry_run = command.endswith("dry")
        send_message(f"Starting low-risk window dry_run={str(dry_run).lower()}", chat_id=chat_id)
        execute_async(
            chat_id,
            "Low-risk Docker window",
            {
                "target": "auto-windowed",
                "window": "auto-windowed",
                "dry_run": dry_run,
            },
        )
        return

    if command in {"/updates", "/check_updates"}:
        update_payload = resolve_update_query(args)
        send_message(f"Starting Docker update check query={json.dumps(update_payload, separators=(',', ':'))}", chat_id=chat_id)
        check_updates_async(chat_id, update_payload)
        return

    if command in {"/packages", "/package_updates"}:
        package_payload = resolve_package_query(args)
        send_message(f"Starting package update check query={json.dumps(package_payload, separators=(',', ':'))}", chat_id=chat_id)
        check_packages_async(chat_id, package_payload)
        return

    if command in {"/reboots", "/reboot_status"}:
        package_payload = resolve_package_query(args)
        send_message(f"Starting reboot status check query={json.dumps(package_payload, separators=(',', ':'))}", chat_id=chat_id)
        check_reboots_async(chat_id, package_payload)
        return

    if command in {"/approve", "/approve_dry", "/approve_live"}:
        dry_run = command in {"/approve", "/approve_dry"}
        services = resolve_pending_services() if not args or args == ["all"] else parse_services(args)
        if not services:
            send_message("No approved services were resolved.", chat_id=chat_id)
            return
        send_message(
            f"Starting approved Docker updates dry_run={str(dry_run).lower()} services={','.join(services)}",
            chat_id=chat_id,
        )
        execute_async(
            chat_id,
            "Approved Docker updates",
            {
                "target": "docker-approved",
                "window": "approve",
                "approved_services": services,
                "dry_run": dry_run,
            },
        )
        return

    if command in {"/maint_dry", "/maint_live"}:
        dry_run = command == "/maint_dry"
        services = resolve_pending_services() if not args or args == ["all"] else parse_services(args)
        if not services:
            send_message("No approved services were resolved.", chat_id=chat_id)
            return
        if not dry_run:
            send_message(live_risk_banner("maintenance orchestrator", ",".join(services)), chat_id=chat_id)
        send_message(
            f"Starting maintenance orchestrator dry_run={str(dry_run).lower()} services={','.join(services)}",
            chat_id=chat_id,
        )
        execute_async(
            chat_id,
            "Maintenance orchestrator",
            {
                "target": "maintenance",
                "window": "approved_guest_container",
                "approved_services": services,
                "dry_run": dry_run,
            },
        )
        return

    if command in {"/guest_dry", "/guest_live"}:
        dry_run = command == "/guest_dry"
        hosts = parse_services(args)
        if not hosts:
            send_message("Specify at least one guest host, for example /guest_dry apps-vm", chat_id=chat_id)
            return
        host_limit = ",".join(hosts)
        if not dry_run:
            send_message(live_risk_banner("guest patching", host_limit), chat_id=chat_id)
        send_message(f"Starting guest patch run dry_run={str(dry_run).lower()} hosts={host_limit}", chat_id=chat_id)
        execute_async(
            chat_id,
            "Guest patching",
            {
                "target": "patch-guests",
                "limit": host_limit,
                "dry_run": dry_run,
                "allow_manual_guests": True,
            },
        )
        return

    if command in {"/proxmox_dry", "/proxmox_live"}:
        dry_run = command == "/proxmox_dry"
        hosts = parse_services(args)
        if not hosts:
            send_message("Specify at least one Proxmox host, for example /proxmox_dry proxmox-node-a", chat_id=chat_id)
            return
        host_limit = ",".join(hosts)
        if not dry_run:
            send_message(live_risk_banner("proxmox patching", host_limit), chat_id=chat_id)
        send_message(f"Starting Proxmox patch run dry_run={str(dry_run).lower()} hosts={host_limit}", chat_id=chat_id)
        execute_async(
            chat_id,
            "Proxmox patching",
            {
                "target": "patch-proxmox",
                "limit": host_limit,
                "dry_run": dry_run,
            },
        )
        return

    if command == "/proxmox_soft_reboot":
        hosts = parse_services(args)
        if not hosts:
            send_message("Specify at least one Proxmox host, for example /proxmox_soft_reboot proxmox-node-a", chat_id=chat_id)
            return
        host_limit = ",".join(hosts)
        send_message(
            "\n".join(
                [
                    "Safety warning: proxmox soft reboot",
                    f"targets={host_limit}",
                    "This will gracefully shut down guests on the target node in the configured order, then reboot the node.",
                    "Guests with onboot enabled will auto-start after the node comes back.",
                    "A follow-up Telegram message will be sent after the node reboot completes and the configured guests are checked.",
                ]
            ),
            chat_id=chat_id,
        )
        send_message(f"Starting Proxmox soft reboot hosts={host_limit}", chat_id=chat_id)
        execute_async(
            chat_id,
            "Proxmox soft reboot",
            {
                "target": "reboot-proxmox",
                "limit": host_limit,
                "dry_run": False,
                "reboot_mode": "soft",
            },
        )
        return

    if command == "/proxmox_hard_request":
        hosts = parse_services(args)
        if not hosts:
            send_message("Specify at least one Proxmox host, for example /proxmox_hard_request proxmox-node-a", chat_id=chat_id)
            return
        host_limit = ",".join(hosts)
        request_hard_reboot_approval(chat_id, host_limit)
        send_message(
            "\n".join(
                [
                    "Hard reboot approval requested",
                    f"targets={host_limit}",
                    "This will hard-reboot the Proxmox node and can hard-interrupt guests on that node.",
                    f"Use /proxmox_hard_confirm {host_limit} within 10 minutes to proceed.",
                    f"Safer alternative: /proxmox_soft_reboot {host_limit}",
                ]
            ),
            chat_id=chat_id,
        )
        return

    if command == "/proxmox_hard_confirm":
        hosts = parse_services(args)
        if not hosts:
            send_message("Specify at least one Proxmox host, for example /proxmox_hard_confirm proxmox-node-a", chat_id=chat_id)
            return
        host_limit = ",".join(hosts)
        if not consume_hard_reboot_approval(chat_id, host_limit):
            send_message(
                "\n".join(
                    [
                        "No active hard reboot approval was found for that target.",
                        f"Run /proxmox_hard_request {host_limit} first.",
                        f"Safer alternative: /proxmox_soft_reboot {host_limit}",
                    ]
                ),
                chat_id=chat_id,
            )
            return
        send_message(
            "\n".join(
                [
                    "Safety warning: proxmox hard reboot",
                    f"targets={host_limit}",
                    "This will hard-reboot the Proxmox node and may hard-interrupt guests on that node.",
                    f"Safer alternative remains: /proxmox_soft_reboot {host_limit}",
                    "A follow-up Telegram message will be sent after the node reboot completes and the configured guests are checked.",
                ]
            ),
            chat_id=chat_id,
        )
        send_message(f"Starting Proxmox hard reboot hosts={host_limit}", chat_id=chat_id)
        execute_async(
            chat_id,
            "Proxmox hard reboot",
            {
                "target": "reboot-proxmox",
                "limit": host_limit,
                "dry_run": False,
                "reboot_mode": "hard",
            },
        )
        return

    send_message("Unknown command. Use /help.", chat_id=chat_id)


def poll_loop() -> None:
    global SESSION
    offset = load_offset()
    send_message("Ops Telegram bridge is online. Use /help for commands.")
    while True:
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            response = SESSION.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params=params,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                time.sleep(5)
                continue
            for update in data.get("result", []):
                offset = int(update["update_id"]) + 1
                save_offset(offset)
                message = update.get("message", {})
                chat_id = str((message.get("chat") or {}).get("id", ""))
                text = message.get("text", "")
                if chat_id not in CHAT_IDS:
                    continue
                handle_command(chat_id, text)
        except Exception as exc:  # noqa: BLE001
            try:
                SESSION.close()
            except Exception:  # noqa: BLE001
                pass
            SESSION = new_session()
            send_message(f"Ops Telegram bridge polling error: {exc}")
            time.sleep(10)


class Handler(BaseHTTPRequestHandler):
    server_version = "ops-telegram/1.0"

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _authorized(self) -> bool:
        if not OPS_API_TOKEN:
            return True
        return self.headers.get("X-Ops-Token", "") == OPS_API_TOKEN

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(HTTPStatus.OK, {"status": "ok"})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not-found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/notify", "/simulate-command"}:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not-found"})
            return
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        payload = self._read_json()
        if self.path == "/simulate-command":
            text = payload.get("text", "").strip()
            chat_id = str(payload.get("chat_id") or (sorted(CHAT_IDS)[0] if CHAT_IDS else ""))
            if not text or not chat_id:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "text and chat_id are required"})
                return
            handle_command(chat_id, text)
            self._send(HTTPStatus.OK, {"status": "accepted", "chat_id": chat_id, "text": text})
            return
        message = payload.get("message", "").strip()
        if not message:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "message is required"})
            return
        send_message(message)
        self._send(HTTPStatus.OK, {"status": "sent"})


if __name__ == "__main__":
    threading.Thread(target=poll_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", BOT_PORT), Handler)
    print(f"ops-telegram listening on 0.0.0.0:{BOT_PORT}", flush=True)
    httpd.serve_forever()
