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


SERVER_URL = config.env("RACKPATCH_SERVER_URL", "http://localhost:9080").rstrip("/")
STATE_DIR = Path(config.env("RACKPATCH_AGENT_STATE_DIR", "/var/lib/rackpatch-agent"))
STATE_FILE = STATE_DIR / "agent.json"
BOOTSTRAP_TOKEN = config.env("RACKPATCH_AGENT_BOOTSTRAP_TOKEN", "")
AGENT_NAME = config.env("RACKPATCH_AGENT_NAME", socket.gethostname())
DISPLAY_NAME = config.env("RACKPATCH_AGENT_DISPLAY_NAME", AGENT_NAME)
AGENT_MODE = config.env("RACKPATCH_AGENT_MODE", "systemd")
AGENT_LABELS = [
    item.strip()
    for item in config.env("RACKPATCH_AGENT_LABELS", "").split(",")
    if item.strip()
]
AGENT_VERSION = config.env("RACKPATCH_AGENT_VERSION", config.APP_VERSION)
AGENT_INSTALL_DIR = config.env("RACKPATCH_AGENT_INSTALL_DIR", "")
AGENT_COMPOSE_DIR = config.env("RACKPATCH_AGENT_COMPOSE_DIR", "")
COMPOSE_DISCOVERY_TTL_SECONDS = int(config.env("RACKPATCH_AGENT_COMPOSE_DISCOVERY_TTL", "300"))
HOST_HELPER_SOCKET = config.env("RACKPATCH_HOST_HELPER_SOCKET", "/run/rackpatch-host-helper.sock")

HOST_MAINTENANCE_CAPABILITIES = {
    "package_check": "host-package-check",
    "package_patch": "host-package-patch",
    "proxmox_patch": "host-proxmox-patch",
    "proxmox_reboot": "host-proxmox-reboot",
}

_compose_discovery_cache: dict[str, Any] = {
    "captured_at": 0.0,
    "projects": [],
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"rackpatch-agent/{AGENT_VERSION}"})


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def clear_state() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def _helper_request(payload: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    socket_path = Path(HOST_HELPER_SOCKET)
    if not socket_path.exists():
        raise RuntimeError(f"host maintenance helper socket not found: {socket_path}")

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(socket_path))
        client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        client.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        client.close()

    raw = b"".join(chunks).decode("utf-8").strip()
    if not raw:
        raise RuntimeError("host maintenance helper returned an empty response")
    response = json.loads(raw)
    if not isinstance(response, dict):
        raise RuntimeError("host maintenance helper returned a non-object response")
    return response


def describe_host_helper() -> dict[str, Any] | None:
    try:
        response = _helper_request({"action": "describe"})
    except Exception:  # noqa: BLE001
        return None
    if not response.get("ok"):
        return None
    return response


def host_maintenance_actions() -> set[str]:
    payload = describe_host_helper() or {}
    return {str(item) for item in payload.get("actions", []) if str(item).strip()}


def host_maintenance_metadata() -> dict[str, Any]:
    payload = describe_host_helper() or {}
    actions = sorted({str(item) for item in payload.get("actions", []) if str(item).strip()})
    enabled = bool(actions)
    detail = str(payload.get("detail") or "")
    if not detail:
        detail = (
            "Limited to approved maintenance actions via the host helper."
            if enabled
            else "Host maintenance helper not enabled."
        )
    return {
        "enabled": enabled,
        "actions": actions,
        "detail": detail,
        "transport": "unix-socket" if enabled else "unavailable",
        "socket_path": HOST_HELPER_SOCKET,
    }


def capabilities() -> list[str]:
    caps: set[str] = set()
    if shutil.which("docker"):
        caps.add("docker")
        caps.add("docker-exec")
        caps.add("docker-compose-discovery")
    helper_actions = host_maintenance_actions()
    for action, capability in HOST_MAINTENANCE_CAPABILITIES.items():
        if action not in helper_actions:
            continue
        caps.add(capability)
    return sorted(caps)


def register() -> dict[str, Any]:
    response = SESSION.post(
        f"{SERVER_URL}/api/v1/agents/register",
        headers={"X-Rackpatch-Agent-Token": BOOTSTRAP_TOKEN},
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
                "install_dir": AGENT_INSTALL_DIR,
                "compose_dir": AGENT_COMPOSE_DIR,
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
    return {"X-Rackpatch-Agent-Secret": state["agent_secret"]}


def _run_json_command(command: list[str]) -> Any:
    rc, stdout = run_command(command)
    if rc != 0:
        raise RuntimeError(stdout or f"command failed: {' '.join(command)}")
    return json.loads(stdout)


def discover_compose_projects() -> list[dict[str, Any]]:
    if not shutil.which("docker"):
        return []

    rc, container_ids_raw = run_command(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project"]
    )
    if rc != 0:
        return []

    container_ids = [item.strip() for item in container_ids_raw.splitlines() if item.strip()]
    if not container_ids:
        return []

    try:
        payload = _run_json_command(["docker", "inspect", *container_ids])
    except Exception:  # noqa: BLE001
        return []

    projects: dict[tuple[str, str, str], dict[str, Any]] = {}
    for container in payload:
        labels = ((container.get("Config") or {}).get("Labels") or {})
        project_name = str(labels.get("com.docker.compose.project") or "").strip()
        project_dir = str(labels.get("com.docker.compose.project.working_dir") or "").strip()
        config_files_raw = str(labels.get("com.docker.compose.project.config_files") or "").strip()
        if not project_name or not project_dir:
            continue

        config_files = [item.strip() for item in config_files_raw.split(",") if item.strip()]
        key = (project_name, project_dir, ",".join(config_files))
        project = projects.setdefault(
            key,
            {
                "project_name": project_name,
                "project_dir": project_dir,
                "config_files": config_files,
                "compose_env_files": [".env"] if Path(project_dir, ".env").exists() else [],
                "services": [],
            },
        )
        project["services"].append(
            {
                "service": str(labels.get("com.docker.compose.service") or "").strip(),
                "container_name": str(container.get("Name") or "").lstrip("/"),
                "image": str((container.get("Config") or {}).get("Image") or "").strip(),
                "state": str((container.get("State") or {}).get("Status") or "").strip(),
            }
        )

    return sorted(
        (
            {
                **project,
                "services": sorted(
                    project["services"],
                    key=lambda service: (service.get("service") or service.get("container_name") or ""),
                ),
            }
            for project in projects.values()
        ),
        key=lambda project: (project["project_name"], project["project_dir"]),
    )


def compose_projects_metadata() -> list[dict[str, Any]]:
    now = time.time()
    cached_at = float(_compose_discovery_cache.get("captured_at") or 0.0)
    if now - cached_at < COMPOSE_DISCOVERY_TTL_SECONDS:
        return list(_compose_discovery_cache.get("projects") or [])

    projects = discover_compose_projects()
    _compose_discovery_cache["captured_at"] = now
    _compose_discovery_cache["projects"] = projects
    return list(projects)


def heartbeat_metadata(current_capabilities: list[str] | None = None) -> dict[str, Any]:
    current_capabilities = current_capabilities if current_capabilities is not None else capabilities()
    metadata = {
        "capabilities": current_capabilities,
        "mode": AGENT_MODE,
        "host_maintenance": host_maintenance_metadata(),
    }
    if AGENT_INSTALL_DIR:
        metadata["install_dir"] = AGENT_INSTALL_DIR
    if AGENT_COMPOSE_DIR:
        metadata["compose_dir"] = AGENT_COMPOSE_DIR
    if shutil.which("docker"):
        metadata["docker"] = {
            "compose_projects": compose_projects_metadata(),
        }
    return metadata


def heartbeat(state: dict[str, Any]) -> None:
    current_capabilities = capabilities()
    SESSION.post(
        f"{SERVER_URL}/api/v1/agents/heartbeat",
        headers=agent_headers(state),
        json={
            "agent_id": state["agent_id"],
            "version": AGENT_VERSION,
            "capabilities": current_capabilities,
            "metadata": heartbeat_metadata(current_capabilities),
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


def _result_from_helper(action: str, *, timeout: float = 120.0, **payload: Any) -> dict[str, Any]:
    response = _helper_request({"action": action, **payload}, timeout=timeout)
    if not response.get("ok"):
        message = str(response.get("error") or "host maintenance helper request failed")
        stdout = str(response.get("stdout") or message)
        return {"exit_code": 1, "error": message, "stdout": stdout}
    result = response.get("result")
    if not isinstance(result, dict):
        return {"exit_code": 1, "error": "host maintenance helper returned an invalid result", "stdout": ""}
    return result


def check_packages() -> dict[str, Any]:
    return _result_from_helper("package_check")


def patch_packages(payload: dict[str, Any]) -> dict[str, Any]:
    return _result_from_helper("package_patch", timeout=3600.0, dry_run=bool(payload.get("dry_run", False)))


def patch_proxmox(payload: dict[str, Any]) -> dict[str, Any]:
    return _result_from_helper("proxmox_patch", timeout=7200.0, dry_run=bool(payload.get("dry_run", False)))


def reboot_proxmox(payload: dict[str, Any]) -> dict[str, Any]:
    guest_order = payload.get("guest_order")
    return _result_from_helper(
        "proxmox_reboot",
        timeout=120.0,
        dry_run=bool(payload.get("dry_run", False)),
        reboot_mode=str(payload.get("reboot_mode") or "soft"),
        guest_order=list(guest_order) if isinstance(guest_order, list) else [],
    )


def docker_update(payload: dict[str, Any]) -> dict[str, Any]:
    project_dir = str(payload.get("project_dir") or "").strip()
    if not project_dir:
        return {"exit_code": 1, "error": "project_dir is required", "stdout": ""}
    compose_env_files = payload.get("compose_env_files", [])
    command = ["docker", "compose"]
    for env_file in compose_env_files:
        command.extend(["--env-file", env_file])
    rc_config, out_config = run_command(command + ["config"], cwd=project_dir)
    if rc_config != 0:
        return {"exit_code": rc_config, "stdout": out_config}
    if bool(payload.get("dry_run", False)):
        return {
            "exit_code": 0,
            "stdout": "\n".join(
                [
                    out_config,
                    "dry-run mode validated docker compose config only; no images were pulled and no services were restarted.",
                ]
            ).strip(),
        }
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
        result = patch_packages(payload)
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    if kind == "proxmox_patch":
        result = patch_proxmox(payload)
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    if kind == "proxmox_reboot":
        result = reboot_proxmox(payload)
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
        except requests.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code in {401, 404}:
                print(
                    f"rackpatch-agent state rejected by server (status={status_code}); re-registering",
                    flush=True,
                )
                clear_state()
                state = register()
                continue
            print(f"rackpatch-agent loop error: {exc}", flush=True)
            time.sleep(config.AGENT_POLL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            print(f"rackpatch-agent loop error: {exc}", flush=True)
            time.sleep(config.AGENT_POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
