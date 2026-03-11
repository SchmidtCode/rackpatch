from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from common import config


SERVER_URL = os.environ.get("OPS_SERVER_URL", "http://localhost:9080").rstrip("/")
STATE_DIR = Path(os.environ.get("OPS_AGENT_STATE_DIR", "/var/lib/ops-agent"))
STATE_FILE = STATE_DIR / "agent.json"
BOOTSTRAP_TOKEN = os.environ.get("OPS_AGENT_BOOTSTRAP_TOKEN", "")
AGENT_NAME = os.environ.get("OPS_AGENT_NAME", socket.gethostname())
DISPLAY_NAME = os.environ.get("OPS_AGENT_DISPLAY_NAME", AGENT_NAME)
AGENT_MODE = os.environ.get("OPS_AGENT_MODE", "systemd")
AGENT_LABELS = [item.strip() for item in os.environ.get("OPS_AGENT_LABELS", "").split(",") if item.strip()]
AGENT_VERSION = os.environ.get("OPS_AGENT_VERSION", config.APP_VERSION)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"rackpatch-agent/{AGENT_VERSION}"})


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def capabilities() -> list[str]:
    caps = {"package_check"}
    if shutil.which("docker"):
        caps.add("docker")
        caps.add("docker-exec")
    if shutil.which("sudo"):
        caps.add("sudo-packages")
    if Path("/etc/pve").exists():
        caps.add("proxmox")
    return sorted(caps)


def register() -> dict[str, Any]:
    response = SESSION.post(
        f"{SERVER_URL}/api/v1/agents/register",
        headers={"X-Ops-Agent-Token": BOOTSTRAP_TOKEN},
        json={
            "name": AGENT_NAME,
            "display_name": DISPLAY_NAME,
            "transport": "poll",
            "platform": platform.platform(),
            "version": AGENT_VERSION,
            "capabilities": capabilities(),
            "labels": AGENT_LABELS,
            "metadata": {
                "python": sys.version.split()[0],
                "mode": AGENT_MODE,
                "hostname": socket.gethostname(),
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    save_state(
        {
            "agent_id": payload["id"],
            "agent_secret": payload["agent_secret"],
            "poll_seconds": payload.get("poll_seconds", config.AGENT_POLL_SECONDS),
        }
    )
    return load_state()


def ensure_registered() -> dict[str, Any]:
    state = load_state()
    if not state.get("agent_id") or not state.get("agent_secret"):
        return register()
    return state


def agent_headers(state: dict[str, Any]) -> dict[str, str]:
    return {"X-Ops-Agent-Secret": state["agent_secret"]}


def heartbeat(state: dict[str, Any]) -> None:
    SESSION.post(
        f"{SERVER_URL}/api/v1/agents/heartbeat",
        headers=agent_headers(state),
        json={
            "agent_id": state["agent_id"],
            "metadata": {
                "capabilities": capabilities(),
                "mode": AGENT_MODE,
            },
        },
        timeout=30,
    ).raise_for_status()


def post_event(state: dict[str, Any], job_id: str, message: str, stream: str = "stdout") -> None:
    SESSION.post(
        f"{SERVER_URL}/api/v1/jobs/{job_id}/events",
        headers=agent_headers(state),
        json={
            "agent_id": state["agent_id"],
            "stream": stream,
            "message": message,
        },
        timeout=30,
    ).raise_for_status()


def complete(state: dict[str, Any], job_id: str, status: str, result: dict[str, Any]) -> None:
    SESSION.post(
        f"{SERVER_URL}/api/v1/jobs/{job_id}/complete",
        headers=agent_headers(state),
        json={
            "agent_id": state["agent_id"],
            "status": status,
            "result": result,
        },
        timeout=60,
    ).raise_for_status()


def claim(state: dict[str, Any]) -> dict[str, Any] | None:
    response = SESSION.post(
        f"{SERVER_URL}/api/v1/agents/claim",
        headers=agent_headers(state),
        json={"agent_id": state["agent_id"]},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("job") is None:
        return None
    return payload["job"]


def run_command(command: list[str], cwd: str | None = None) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        output.append(raw.rstrip("\n"))
    return process.wait(), "\n".join(output)


def check_packages() -> dict[str, Any]:
    rc, stdout = run_command(
        [
            "sh",
            "-lc",
            "apt list --upgradable 2>/dev/null | tail -n +2; "
            "printf '__OPS_REBOOT__=%s\\n' \"$(test -f /var/run/reboot-required && echo yes || echo no)\"",
        ]
    )
    packages = []
    reboot_required = False
    for line in stdout.splitlines():
        if line.startswith("__OPS_REBOOT__="):
            reboot_required = line.split("=", 1)[1] == "yes"
        elif line.strip():
            packages.append(line.strip())
    return {"exit_code": rc, "packages": packages, "reboot_required": reboot_required, "stdout": stdout}


def patch_packages() -> dict[str, Any]:
    rc, stdout = run_command(
        [
            "sh",
            "-lc",
            "sudo apt-get update && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y && "
            "sudo apt-get autoremove -y",
        ]
    )
    return {"exit_code": rc, "stdout": stdout}


def docker_update(payload: dict[str, Any]) -> dict[str, Any]:
    project_dir = payload["project_dir"]
    compose_env_files = payload.get("compose_env_files", [])
    command = ["docker", "compose"]
    for env_file in compose_env_files:
        command.extend(["--env-file", env_file])
    rc_config, out_config = run_command(command + ["config"], cwd=project_dir)
    if rc_config != 0:
        return {"exit_code": rc_config, "stdout": out_config}
    rc_pull, out_pull = run_command(command + ["pull"], cwd=project_dir)
    if rc_pull != 0:
        return {"exit_code": rc_pull, "stdout": out_pull}
    rc_up, out_up = run_command(command + ["up", "-d"], cwd=project_dir)
    return {"exit_code": rc_up, "stdout": "\n".join([out_config, out_pull, out_up]).strip()}


def execute_job(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    payload = dict(job.get("payload") or {})
    kind = job["kind"]
    if kind == "package_check":
        result = check_packages()
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    if kind == "package_patch":
        result = patch_packages()
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    if kind == "docker_update":
        result = docker_update(payload)
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    return "failed", {"error": f"unsupported agent job kind: {kind}"}


def main() -> int:
    state = ensure_registered()
    print(f"rackpatch-agent registered name={AGENT_NAME} id={state['agent_id']}", flush=True)
    while True:
        try:
            heartbeat(state)
            job = claim(state)
            if job:
                job_id = str(job["id"])
                post_event(state, job_id, f"agent {AGENT_NAME} executing {job['kind']}")
                status, result = execute_job(job)
                for line in str(result.get("stdout", "")).splitlines():
                    if line.strip():
                        post_event(state, job_id, line)
                complete(state, job_id, status, result)
            time.sleep(float(state.get("poll_seconds", config.AGENT_POLL_SECONDS)))
        except Exception as exc:  # noqa: BLE001
            print(f"rackpatch-agent loop error: {exc}", flush=True)
            time.sleep(config.AGENT_POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
