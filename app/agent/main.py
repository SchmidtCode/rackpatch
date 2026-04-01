from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import docker
import requests

from common import config, image_updates


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
AGENT_STACK_ROOTS_RAW = config.env("RACKPATCH_AGENT_STACK_ROOTS", "")
COMPOSE_DISCOVERY_TTL_SECONDS = int(config.env("RACKPATCH_AGENT_COMPOSE_DISCOVERY_TTL", "300"))
HOST_HELPER_SOCKET = config.env(
    "RACKPATCH_HOST_HELPER_SOCKET",
    "/run/rackpatch-host-helper/rackpatch-host-helper.sock",
)
DOCKER_SOCKET = Path("/var/run/docker.sock")

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


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip()
    if text == "/":
        return text
    return text.rstrip("/")


def _parse_path_list(value: Any) -> list[str]:
    raw_items = value if isinstance(value, (list, tuple, set)) else str(value or "").split(",")
    items: list[str] = []
    for item in raw_items:
        normalized = _normalize_path(item)
        if normalized and normalized not in items:
            items.append(normalized)
    return items


AGENT_STACK_ROOTS = _parse_path_list(AGENT_STACK_ROOTS_RAW)


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        clear_state()
        return {}


def save_state(payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def clear_state() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def docker_socket_available() -> bool:
    return DOCKER_SOCKET.exists()


def docker_client() -> docker.DockerClient | None:
    if not docker_socket_available():
        return None
    client: docker.DockerClient | None = None
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception:  # noqa: BLE001
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        return None


def docker_command() -> str | None:
    return shutil.which("docker")


def compose_base_command() -> list[str] | None:
    docker_cli = docker_command()
    if docker_cli:
        return [docker_cli, "compose"]
    docker_compose = shutil.which("docker-compose")
    if docker_compose:
        return [docker_compose]
    return None


def docker_capabilities_available() -> bool:
    client = docker_client()
    if client is None:
        return False
    client.close()
    return True


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
    if docker_capabilities_available():
        caps.add("docker")
        caps.add("docker-exec")
        caps.add("docker-compose-discovery")
        caps.add("docker-stack-inspect")
    if AGENT_MODE in {"compose", "container", "systemd"}:
        caps.add("agent-self-update")
    helper_actions = host_maintenance_actions()
    for action, capability in HOST_MAINTENANCE_CAPABILITIES.items():
        if action not in helper_actions:
            continue
        caps.add(capability)
    return sorted(caps)


def register() -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "python": sys.version.split()[0],
        "mode": AGENT_MODE,
        "hostname": socket.gethostname(),
        "install_dir": AGENT_INSTALL_DIR,
        "compose_dir": AGENT_COMPOSE_DIR,
    }
    if AGENT_STACK_ROOTS:
        metadata["stack_roots"] = AGENT_STACK_ROOTS
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
            "metadata": metadata,
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
    client = docker_client()
    if client is None:
        return []

    try:
        containers = client.containers.list(all=True, filters={"label": "com.docker.compose.project"})
        projects: dict[tuple[str, str, str], dict[str, Any]] = {}
        for container in containers:
            labels = ((container.attrs.get("Config") or {}).get("Labels") or {})
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
                    "container_name": str(container.name or "").lstrip("/"),
                    "image": str((container.attrs.get("Config") or {}).get("Image") or "").strip(),
                    "state": str((container.attrs.get("State") or {}).get("Status") or "").strip(),
                }
            )
    finally:
        client.close()

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
        "hostname": socket.gethostname(),
        "mode": AGENT_MODE,
        "host_maintenance": host_maintenance_metadata(),
    }
    if AGENT_INSTALL_DIR:
        metadata["install_dir"] = AGENT_INSTALL_DIR
    if AGENT_COMPOSE_DIR:
        metadata["compose_dir"] = AGENT_COMPOSE_DIR
    if AGENT_STACK_ROOTS:
        metadata["stack_roots"] = AGENT_STACK_ROOTS
    if docker_capabilities_available():
        metadata["docker"] = {
            "compose_projects": compose_projects_metadata(),
            "stack_roots": AGENT_STACK_ROOTS,
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


def run_command(command: list[str], cwd: str | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        output.append(raw.rstrip("\n"))
    return process.wait(), "\n".join(output)


def run_command_split(
    command: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout_text, stderr_text = process.communicate()
    return process.returncode, stdout_text.rstrip("\n"), stderr_text.rstrip("\n")


def _join_output(*parts: str) -> str:
    return "\n".join(part for part in parts if part).strip()


def _load_json_output(stdout: str, stderr: str, *, description: str) -> tuple[Any, list[str]]:
    warnings: list[str] = []
    if stderr.strip():
        warnings.append(stderr.strip())

    try:
        return json.loads(stdout), warnings
    except json.JSONDecodeError:
        combined = _join_output(stdout, stderr)
        if not combined:
            raise RuntimeError(f"{description} returned empty output") from None

        decoder = json.JSONDecoder()
        for match in re.finditer(r"(?m)^[\t ]*[\[{]", combined):
            candidate = combined[match.start() :].lstrip()
            try:
                parsed, end = decoder.raw_decode(candidate)
            except json.JSONDecodeError:
                continue

            leading = combined[: match.start()].strip()
            trailing = candidate[end:].strip()
            fallback_warnings = [part for part in [leading, trailing] if part]
            return parsed, fallback_warnings

    raise RuntimeError(f"{description} returned invalid json")


def _path_is_within(root: str, candidate: str) -> bool:
    normalized_root = _normalize_path(root)
    normalized_candidate = _normalize_path(candidate)
    if not normalized_root or not normalized_candidate:
        return False

    root_path = Path(normalized_root).expanduser().resolve(strict=False)
    candidate_path = Path(normalized_candidate).expanduser().resolve(strict=False)
    try:
        candidate_path.relative_to(root_path)
    except ValueError:
        return False
    return True


def _project_dir_access_error(project_dir: str) -> str | None:
    normalized_dir = _normalize_path(project_dir)
    if not normalized_dir:
        return None
    if Path(normalized_dir).exists():
        return None
    if AGENT_MODE not in {"compose", "container"}:
        return None
    if any(_path_is_within(root, normalized_dir) for root in AGENT_STACK_ROOTS):
        return (
            f"{normalized_dir} is configured as a mounted stack root, but the path is not available inside "
            "this agent container."
        )
    roots_label = ", ".join(AGENT_STACK_ROOTS) if AGENT_STACK_ROOTS else "none configured"
    return (
        f"{normalized_dir} is outside this agent container's mounted stack roots ({roots_label}). "
        "Set RACKPATCH_AGENT_STACK_ROOTS and mount the same host path(s) into the agent container."
    )


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


def _compose_command(compose_env_files: list[str]) -> list[str] | None:
    base = compose_base_command()
    if base is None:
        return None
    command = base[:]
    for env_file in compose_env_files:
        command.extend(["--env-file", str(env_file)])
    return command


def _compose_config_json(project_dir: str, compose_env_files: list[str]) -> dict[str, Any]:
    command = _compose_command(compose_env_files)
    if command is None:
        raise RuntimeError("docker compose command is not available")
    rc, stdout, stderr = run_command_split(command + ["config", "--format", "json"], cwd=project_dir)
    if rc != 0:
        raise RuntimeError(_join_output(stdout, stderr) or "compose config failed")
    parsed, warnings = _load_json_output(stdout, stderr, description="compose config")
    if not isinstance(parsed, dict):
        raise RuntimeError("compose config returned a non-object payload")
    return {"payload": parsed, "warnings": warnings}


def _normalize_repo(ref: str) -> str:
    value = str(ref or "").split("@", 1)[0]
    last_slash = value.rfind("/")
    last_colon = value.rfind(":")
    if last_colon > last_slash:
        return value[:last_colon]
    return value


def _short_digest(value: str | None) -> str:
    if not value:
        return "unknown"
    if value.startswith("sha256:"):
        return value[7:19]
    return value[:12]


def _local_digest(image_attrs: dict[str, Any], ref: str) -> str | None:
    repo = _normalize_repo(ref)
    for digest_ref in image_attrs.get("RepoDigests") or []:
        if digest_ref.startswith(f"{repo}@"):
            return digest_ref.split("@", 1)[1]
    if "@sha256:" in ref:
        return ref.split("@", 1)[1]
    return None


def _registry_digest(
    client: docker.DockerClient,
    ref: str,
    cache: dict[str, dict[str, str | None]],
) -> tuple[str | None, str | None]:
    cached = cache.get(ref)
    if cached:
        return cached.get("digest"), cached.get("error")

    try:
        registry_data = client.images.get_registry_data(ref)
        digest = registry_data.id or (registry_data.attrs.get("Descriptor") or {}).get("digest")
        payload = {"digest": digest, "error": None}
    except Exception as exc:  # noqa: BLE001
        payload = {"digest": None, "error": str(exc)}
    cache[ref] = payload
    return payload["digest"], payload["error"]


def _resolve_compose_env_path(project_dir: str, env_file: str) -> Path:
    path = Path(str(env_file).strip())
    if path.is_absolute():
        return path
    return Path(project_dir) / path


def _parse_env_assignment(line: str) -> dict[str, str] | None:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        return None
    indent = line[: len(line) - len(stripped)]
    body = stripped
    export_prefix = ""
    if body.startswith("export "):
        export_prefix = "export "
        body = body[len("export ") :]
    if "=" not in body:
        return None
    key, value = body.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return {
        "indent": indent,
        "export_prefix": export_prefix,
        "key": key,
        "value": value.strip(),
    }


def _env_image_bindings(project_dir: str, compose_env_files: list[str]) -> dict[str, list[dict[str, str]]]:
    bindings_by_ref: dict[str, list[dict[str, str]]] = {}
    for env_file in compose_env_files:
        env_path = _resolve_compose_env_path(project_dir, env_file)
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_assignment(line)
            if not parsed:
                continue
            value = str(parsed["value"] or "").strip()
            if not image_updates.is_image_ref(value):
                continue
            bindings_by_ref.setdefault(value, []).append(
                {
                    "env_file": str(env_file),
                    "path": str(env_path),
                    "key": parsed["key"],
                    "value": value,
                }
            )
    return bindings_by_ref


def _render_env_file_text(env_path: Path, replacements: dict[str, str]) -> str:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    rendered: list[str] = []
    updated_keys: set[str] = set()
    for line in lines:
        parsed = _parse_env_assignment(line)
        if not parsed:
            rendered.append(line)
            continue
        key = parsed["key"]
        if key not in replacements:
            rendered.append(line)
            continue
        rendered.append(f"{parsed['indent']}{parsed['export_prefix']}{key}={replacements[key]}")
        updated_keys.add(key)

    missing_keys = [key for key in replacements if key not in updated_keys]
    if missing_keys and rendered and rendered[-1] != "":
        rendered.append("")
    for key in missing_keys:
        rendered.append(f"{key}={replacements[key]}")
    return "\n".join(rendered).rstrip("\n") + "\n"


def _build_temp_compose_env_files(
    project_dir: str,
    compose_env_files: list[str],
    replacements_by_path: dict[str, dict[str, str]],
) -> tuple[TemporaryDirectory[str], list[str]]:
    temp_dir: TemporaryDirectory[str] = TemporaryDirectory(prefix="rackpatch-compose-env-")
    rendered_files: list[str] = []
    for index, env_file in enumerate(compose_env_files):
        env_path = _resolve_compose_env_path(project_dir, env_file)
        temp_path = Path(temp_dir.name) / f"{index:02d}-{env_path.name or 'env'}"
        temp_path.write_text(
            _render_env_file_text(env_path, replacements_by_path.get(str(env_path), {})),
            encoding="utf-8",
        )
        rendered_files.append(str(temp_path))
    return temp_dir, rendered_files


def _persist_env_replacements(replacements_by_path: dict[str, dict[str, str]]) -> None:
    for path_str, replacements in replacements_by_path.items():
        env_path = Path(path_str)
        env_path.write_text(_render_env_file_text(env_path, replacements), encoding="utf-8")


def _env_replacement_access_error(replacements_by_path: dict[str, dict[str, str]]) -> str | None:
    for path_str, replacements in replacements_by_path.items():
        if not replacements:
            continue
        env_path = Path(path_str)
        target = env_path if env_path.exists() else env_path.parent
        if not os.access(target, os.W_OK):
            return f"cannot write updated image references to {env_path}"
    return None


def _select_env_ref_targets(
    project_dir: str,
    compose_env_files: list[str],
    config_payload: dict[str, Any],
    policy: dict[str, Any] | None,
    client: docker.DockerClient,
    registry_cache: dict[str, dict[str, str | None]],
) -> dict[str, Any]:
    normalized_policy = image_updates.normalize_policy(policy)
    bindings_by_ref = _env_image_bindings(project_dir, compose_env_files)
    selection_cache: dict[str, dict[str, Any]] = {}
    service_targets: dict[str, dict[str, Any]] = {}
    replacements_by_path: dict[str, dict[str, str]] = {}

    for service_name in sorted(config_payload.get("services") or {}):
        service_def = (config_payload.get("services") or {}).get(service_name) or {}
        ref = str(service_def.get("image") or "").strip()
        if not ref:
            continue
        bindings = list(bindings_by_ref.get(ref) or [])
        if bindings and ref not in selection_cache:
            selection_cache[ref] = image_updates.choose_target_ref(
                ref,
                normalized_policy,
                resolve_digest=lambda candidate_ref: _registry_digest(client, candidate_ref, registry_cache),
            )

        if bindings:
            base_choice = dict(selection_cache[ref])
        else:
            base_choice = {
                "current_ref": ref,
                "target_ref": ref,
                "target_tag": "",
                "target_digest": "",
                "strategy": normalized_policy["version_strategy"],
                "semver_policy": normalized_policy["semver_policy"],
                "allow_prerelease": normalized_policy["allow_prerelease"],
                "allow_major_upgrades": normalized_policy["allow_major_upgrades"],
                "resolve_to_digest": normalized_policy["resolve_to_digest"],
                "reason": "image ref is not managed through compose env files",
                "changed": False,
                "error": "",
            }
        base_choice["env_managed"] = bool(bindings)
        base_choice["env_bindings"] = [{"env_file": binding["env_file"], "key": binding["key"]} for binding in bindings]
        service_targets[service_name] = base_choice

        if bindings and base_choice.get("target_ref") and base_choice["target_ref"] != ref:
            for binding in bindings:
                replacements_by_path.setdefault(binding["path"], {})[binding["key"]] = str(base_choice["target_ref"])

    return {
        "policy": normalized_policy,
        "service_targets": service_targets,
        "replacements_by_path": replacements_by_path,
    }


def _inspect_stack_services(
    project_dir: str,
    compose_env_files: list[str],
    *,
    image_strategy: str = "",
    version_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config_result = _compose_config_json(project_dir, compose_env_files)
    config_payload = config_result["payload"]
    client = docker_client()
    if client is None:
        raise RuntimeError("docker engine is not available")

    registry_cache: dict[str, dict[str, str | None]] = {}
    normalized_policy = image_updates.normalize_policy(version_policy)
    managed_targets: dict[str, dict[str, Any]] = {}
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "service_count": 0,
        "image_count": 0,
        "outdated_count": 0,
        "status": "unknown",
        "services": [],
        "compose_warnings": list(config_result.get("warnings") or []),
        "image_strategy": image_strategy or "compose-default",
        "version_policy": normalized_policy,
    }
    try:
        if image_strategy == "env-ref":
            managed_targets = _select_env_ref_targets(
                project_dir,
                compose_env_files,
                config_payload,
                normalized_policy,
                client,
                registry_cache,
            ).get("service_targets") or {}

        services = config_payload.get("services") or {}
        for service_name in sorted(services):
            service_def = services.get(service_name) or {}
            ref = str(service_def.get("image") or "").strip()
            service_report = {
                "service": service_name,
                "ref": ref,
                "status": "unknown",
                "local_digest": None,
                "remote_digest": None,
                "local_short": "unknown",
                "remote_short": "unknown",
            }
            report["service_count"] += 1
            if not ref:
                service_report["status"] = "no-image-ref"
                report["services"].append(service_report)
                continue

            report["image_count"] += 1
            target = managed_targets.get(service_name)
            if target:
                service_report["target_ref"] = target.get("target_ref") or ref
                service_report["strategy"] = target.get("strategy") or normalized_policy["version_strategy"]
                service_report["update_reason"] = target.get("reason") or ""
                service_report["env_managed"] = bool(target.get("env_managed"))
                service_report["env_bindings"] = list(target.get("env_bindings") or [])

            try:
                image_attrs = client.images.get(ref).attrs
                service_report["local_digest"] = _local_digest(image_attrs, ref)
            except Exception as exc:  # noqa: BLE001
                service_report["status"] = "missing-local"
                service_report["error"] = str(exc)

            lookup_ref = str((target or {}).get("target_ref") or ref)
            preset_digest = str((target or {}).get("target_digest") or "")
            registry_error = str((target or {}).get("error") or "")
            if preset_digest:
                service_report["remote_digest"] = preset_digest
            else:
                service_report["remote_digest"], discovered_registry_error = _registry_digest(client, lookup_ref, registry_cache)
                registry_error = registry_error or str(discovered_registry_error or "")

            if registry_error and service_report["status"] == "unknown":
                if target:
                    service_report["status"] = "policy-error"
                else:
                    service_report["status"] = "registry-error"
                service_report["error"] = registry_error
            elif not target and service_report["status"] == "unknown" and service_report["remote_digest"] is None:
                service_report["status"] = "registry-error"
                service_report["error"] = registry_error

            if service_report["status"] == "unknown":
                if not service_report["local_digest"]:
                    service_report["status"] = "unknown-local-digest"
                elif (
                    str((target or {}).get("target_ref") or ref) != ref
                    or (
                        service_report["remote_digest"]
                        and service_report["local_digest"] != service_report["remote_digest"]
                    )
                ):
                    service_report["status"] = "outdated"
                    report["outdated_count"] += 1
                else:
                    service_report["status"] = "up-to-date"

            service_report["local_short"] = _short_digest(str(service_report.get("local_digest") or ""))
            service_report["remote_short"] = _short_digest(str(service_report.get("remote_digest") or ""))
            report["services"].append(service_report)
    finally:
        client.close()

    if any(item["status"] == "outdated" for item in report["services"]):
        report["status"] = "outdated"
    elif any(item["status"] in {"policy-error", "registry-error", "missing-local", "unknown-local-digest"} for item in report["services"]):
        report["status"] = "warning"
    elif report["image_count"] == 0:
        report["status"] = "no-images"
    else:
        report["status"] = "up-to-date"
    return report


def _capture_stack_state(project_dir: str, compose_env_files: list[str]) -> dict[str, Any]:
    config_result = _compose_config_json(project_dir, compose_env_files)
    config_payload = config_result["payload"]
    command = _compose_command(compose_env_files)
    if command is None:
        raise RuntimeError("docker compose command is not available")

    services: list[dict[str, Any]] = []
    for service_name in sorted(config_payload.get("services") or {}):
        service_def = (config_payload.get("services") or {}).get(service_name) or {}
        rc_ps, out_ps = run_command(command + ["ps", "-q", service_name], cwd=project_dir)
        rc_images, out_images = run_command(command + ["images", "-q", service_name], cwd=project_dir)
        services.append(
            {
                "service": service_name,
                "configured_image_ref": str(service_def.get("image") or ""),
                "container_id": out_ps.strip() if rc_ps == 0 else "",
                "image_id": out_images.strip() if rc_images == 0 else "",
            }
        )
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stack_path": project_dir,
        "env_files": compose_env_files,
        "services": services,
    }


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return token or "stack"


def _stack_name_from_payload(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("stack_name") or "").strip()
    if explicit:
        return explicit
    selected = payload.get("selected_stacks") or []
    if isinstance(selected, list):
        for item in selected:
            value = str(item or "").strip()
            if value:
                return value
    return "stack"


def _write_rollback_capture(stack_name: str, state: dict[str, Any]) -> dict[str, str]:
    capture_root = STATE_DIR / "rollbacks" / _safe_token(stack_name)
    capture_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    capture_path = capture_root / f"{stamp}.json"
    latest_path = capture_root / "latest.json"
    encoded = json.dumps(state, indent=2, sort_keys=True) + "\n"
    capture_path.write_text(encoded, encoding="utf-8")
    latest_path.write_text(encoded, encoding="utf-8")
    return {
        "capture_path": str(capture_path),
        "latest_path": str(latest_path),
    }


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


def _stack_backup_root(stack_name: str) -> Path:
    return STATE_DIR / "backups" / _safe_token(stack_name)


def _agent_artifact_path(host: str, artifact_path: Path) -> str:
    value = str(artifact_path)
    if not value.startswith("/"):
        value = f"/{value}"
    return f"agent://{_safe_token(host)}{value}"


def _backup_artifact(
    stack_name: str,
    host: str,
    artifact_path: Path,
    *,
    source: str,
) -> dict[str, Any]:
    size_bytes: int | None = None
    try:
        size_bytes = artifact_path.stat().st_size
    except FileNotFoundError:
        size_bytes = None
    return {
        "kind": "backup",
        "target_ref": stack_name,
        "path": _agent_artifact_path(host, artifact_path),
        "container_path": str(artifact_path),
        "source": source,
        "host": host,
        "agent_managed": True,
        "size_bytes": size_bytes,
    }


def _backup_run_key(path: Path) -> str:
    match = re.search(r"(\d{14})", path.name)
    return match.group(1) if match else path.name


def _collect_backup_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: (path.stat().st_mtime, path.name))


def _create_stack_backup_archive(stack_name: str, host: str, project_dir: str, stamp: str) -> tuple[Path, dict[str, Any]]:
    backup_root = _stack_backup_root(stack_name)
    backup_root.mkdir(parents=True, exist_ok=True)
    archive_path = backup_root / f"{stamp}-stack.tgz"
    project_path = Path(project_dir)
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(project_path, arcname=project_path.name)
    return archive_path, _backup_artifact(stack_name, host, archive_path, source="agent-docker-update")


def _rewrite_backup_command(command: str) -> str:
    bundled_scripts_root = str(config.SCRIPTS_ROOT)
    if "/workspace/scripts" in command and bundled_scripts_root:
        return command.replace("/workspace/scripts", bundled_scripts_root)
    return command


def _container_mount_source(container: Any, destination: str) -> str:
    for mount in container.attrs.get("Mounts", []):
        if str(mount.get("Destination") or "") == destination:
            return str(mount.get("Source") or "")
    return ""


def _schedule_container_agent_update(
    *,
    update_command: str,
    update_mode: str,
    target_dir: str,
    log_path: Path,
    stamp: str,
    delay_seconds: int,
) -> dict[str, Any]:
    if not target_dir:
        raise RuntimeError(f"{update_mode} agent update is missing update_target_dir")

    client = docker_client()
    if client is None:
        raise RuntimeError("docker engine is not available")

    try:
        current_container = client.containers.get(socket.gethostname())
        image_tags = list(current_container.image.tags or [])
        image_ref = image_tags[0] if image_tags else str(current_container.image.id or "")
        if not image_ref:
            raise RuntimeError("could not resolve the current agent image")

        state_source = _container_mount_source(current_container, str(STATE_DIR))
        if not state_source:
            raise RuntimeError(f"could not resolve the host source for {STATE_DIR}")

        helper_name = f"rackpatch-agent-updater-{stamp}"
        helper_command = [
            "bash",
            "-lc",
            f"sleep {delay_seconds}; {update_command} >> {shlex.quote(str(log_path))} 2>&1",
        ]
        helper = client.containers.run(
            image_ref,
            helper_command,
            auto_remove=True,
            detach=True,
            labels={
                "com.rackpatch.role": "agent-updater",
                "com.rackpatch.update_mode": update_mode,
                "com.rackpatch.target_dir": target_dir,
            },
            name=helper_name,
            volumes={
                str(DOCKER_SOCKET): {"bind": str(DOCKER_SOCKET), "mode": "rw"},
                state_source: {"bind": str(STATE_DIR), "mode": "rw"},
                target_dir: {"bind": target_dir, "mode": "rw"},
            },
            working_dir=target_dir,
        )
        helper_id = str(getattr(helper, "id", "") or "").strip()
        return {
            "exit_code": 0,
            "stdout": "\n".join(
                [
                    f"Scheduled agent update for {target_dir} in {update_mode} mode via helper container {helper_name}.",
                    f"Helper container id: {helper_id[:12] if helper_id else 'unknown'}",
                    f"Update log: {log_path}",
                ]
            ),
            "scheduled": True,
            "update_mode": update_mode,
            "target_dir": target_dir,
            "log_path": str(log_path),
            "helper_container_id": helper_id or None,
            "helper_container_name": helper_name,
        }
    finally:
        client.close()


def _run_rackpatch_stack_update(project_dir: str, repo_url: str, release_ref: str) -> tuple[int, str]:
    script_path = config.resolve_runtime_path(config.SCRIPTS_ROOT / "update-rackpatch.sh")
    if not script_path.is_file():
        return 1, f"rackpatch update script is unavailable at {script_path}"
    command = [
        "bash",
        str(script_path),
        "--install-dir",
        project_dir,
        "--repo-url",
        repo_url,
    ]
    if release_ref:
        command.extend(["--ref", release_ref])
    return run_command(command)


def _run_backup_commands(
    stack_name: str,
    host: str,
    project_dir: str,
    commands: list[str],
    stamp: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not commands:
        return [], []
    backup_root = _stack_backup_root(stack_name)
    backup_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["BACKUP_ROOT"] = str(backup_root)
    env["STACK_UPDATE_STAMP"] = stamp
    env["RACKPATCH_SCRIPTS_ROOT"] = str(config.SCRIPTS_ROOT)
    output_lines: list[str] = []
    for command in commands:
        normalized = _rewrite_backup_command(str(command))
        rc_command, out_command = run_command(["bash", "-lc", normalized], cwd=project_dir, env=env)
        if out_command.strip():
            output_lines.append(out_command.strip())
        if rc_command != 0:
            raise RuntimeError(f"backup command failed: {normalized}")

    artifacts: list[dict[str, Any]] = []
    for path in sorted(path for path in backup_root.rglob(f"*{stamp}*") if path.is_file()):
        artifacts.append(_backup_artifact(stack_name, host, path, source="agent-docker-update-command"))
    return artifacts, output_lines


def _prune_backup_runs(stack_name: str, host: str, retention: int) -> list[dict[str, Any]]:
    backup_root = _stack_backup_root(stack_name)
    retention = _normalize_positive_int(retention, 3)
    files = _collect_backup_files(backup_root)
    if not files:
        return []

    grouped: dict[str, list[Path]] = {}
    for path in files:
        grouped.setdefault(_backup_run_key(path), []).append(path)
    ordered_groups = sorted(
        grouped.items(),
        key=lambda item: max(member.stat().st_mtime for member in item[1]),
        reverse=True,
    )

    pruned: list[dict[str, Any]] = []
    for _, members in ordered_groups[retention:]:
        for path in members:
            if not path.exists():
                continue
            path.unlink()
            pruned.append(_backup_artifact(stack_name, host, path, source="agent-docker-update-pruned"))
    return pruned


def _summarize_stack_changes(before: dict[str, Any], after: dict[str, Any], stack_name: str, host: str) -> dict[str, Any]:
    before_services = {str(item.get("service") or ""): item for item in before.get("services", [])}
    changes: list[dict[str, Any]] = []
    for current in after.get("services", []):
        service_name = str(current.get("service") or "")
        previous = before_services.get(service_name, {})
        before_ref = str(previous.get("configured_image_ref") or "")
        after_ref = str(current.get("configured_image_ref") or "")
        before_image = str(previous.get("image_id") or "")
        after_image = str(current.get("image_id") or "")
        if before_ref == after_ref and before_image == after_image:
            continue
        changes.append(
            {
                "service": service_name,
                "from_ref": before_ref,
                "to_ref": after_ref,
                "from_image": before_image,
                "to_image": after_image,
                "from_short": _short_digest(before_image),
                "to_short": _short_digest(after_image),
            }
        )
    stack_summary = {
        "stack": stack_name,
        "host": host,
        "changed_services": len(changes),
        "services": changes,
    }
    return {
        "stack_count": 1,
        "changed_stacks": 1 if changes else 0,
        "changed_services": len(changes),
        "stacks": [stack_summary],
    }


def docker_check(payload: dict[str, Any]) -> dict[str, Any]:
    project_dir = str(payload.get("project_dir") or "").strip()
    stack_name = _stack_name_from_payload(payload)
    host = str(payload.get("host") or AGENT_NAME or "localhost").strip() or "localhost"
    image_strategy = str(payload.get("image_strategy") or "").strip()
    docker_update_policy = payload.get("docker_update_policy")
    if not project_dir:
        return {"exit_code": 1, "error": "project_dir is required", "stdout": ""}
    access_error = _project_dir_access_error(project_dir)
    if access_error:
        return {"exit_code": 1, "error": access_error, "stdout": access_error}

    compose_env_files = [str(item) for item in (payload.get("compose_env_files") or []) if str(item).strip()]
    try:
        report = _inspect_stack_services(
            project_dir,
            compose_env_files,
            image_strategy=image_strategy,
            version_policy=docker_update_policy if isinstance(docker_update_policy, dict) else None,
        )
    except Exception as exc:  # noqa: BLE001
        return {"exit_code": 1, "error": str(exc), "stdout": str(exc)}

    report.update(
        {
            "name": stack_name,
            "host": host,
            "project_dir": project_dir,
            "risk": str(payload.get("risk") or ""),
            "catalog_source": str(payload.get("catalog_source") or ""),
            "backup_before": bool(payload.get("backup_before")),
            "snapshot_before": bool(payload.get("snapshot_before")),
        }
    )
    stdout = (
        f"Checked {stack_name} on {host}: "
        f"{report['outdated_count']} outdated image"
        f"{'' if report['outdated_count'] == 1 else 's'} across {report['image_count']} tracked service"
        f"{'' if report['image_count'] == 1 else 's'}."
    )
    return {
        "exit_code": 0,
        "stdout": stdout,
        "report": report,
    }


def docker_update(payload: dict[str, Any]) -> dict[str, Any]:
    project_dir = str(payload.get("project_dir") or "").strip()
    if not project_dir:
        return {"exit_code": 1, "error": "project_dir is required", "stdout": ""}
    access_error = _project_dir_access_error(project_dir)
    if access_error:
        return {"exit_code": 1, "error": access_error, "stdout": access_error}
    stack_name = _stack_name_from_payload(payload)
    host = str(payload.get("host") or AGENT_NAME or "localhost").strip() or "localhost"
    compose_env_files = [str(item) for item in (payload.get("compose_env_files") or []) if str(item).strip()]
    image_strategy = str(payload.get("image_strategy") or "").strip()
    docker_update_policy = payload.get("docker_update_policy") if isinstance(payload.get("docker_update_policy"), dict) else None
    normalized_policy = image_updates.normalize_policy(docker_update_policy)
    active_compose_env_files = list(compose_env_files)
    pending_replacements: dict[str, dict[str, str]] = {}
    temp_env_dir: TemporaryDirectory[str] | None = None
    policy_output: list[str] = []

    if _compose_command(compose_env_files) is None:
        return {"exit_code": 1, "error": "docker compose command is not available", "stdout": ""}

    if image_strategy == "env-ref":
        client = docker_client()
        if client is None:
            return {"exit_code": 1, "error": "docker engine is not available", "stdout": ""}
        registry_cache: dict[str, dict[str, str | None]] = {}
        try:
            config_result = _compose_config_json(project_dir, compose_env_files)
            target_plan = _select_env_ref_targets(
                project_dir,
                compose_env_files,
                config_result["payload"],
                normalized_policy,
                client,
                registry_cache,
            )
        except Exception as exc:  # noqa: BLE001
            client.close()
            return {"exit_code": 1, "error": str(exc), "stdout": str(exc)}
        finally:
            client.close()

        service_targets = target_plan.get("service_targets") or {}
        pending_replacements = target_plan.get("replacements_by_path") or {}
        blocking = [
            f"{service_name}: {str(choice.get('error') or '').strip()}"
            for service_name, choice in service_targets.items()
            if choice.get("env_managed") and str(choice.get("error") or "").strip()
        ]
        if blocking:
            message = "Unable to resolve a safe target image for this policy.\n" + "\n".join(blocking[:6])
            return {"exit_code": 1, "error": blocking[0], "stdout": message}

        writable_error = _env_replacement_access_error(pending_replacements)
        if writable_error:
            return {"exit_code": 1, "error": writable_error, "stdout": writable_error}

        changed_targets = [
            f"{service_name}: {choice.get('current_ref')} -> {choice.get('target_ref')}"
            for service_name, choice in service_targets.items()
            if choice.get("env_managed") and choice.get("target_ref") and choice.get("target_ref") != choice.get("current_ref")
        ]
        policy_output = [
            f"Image strategy: {normalized_policy['version_strategy']} ({normalized_policy['semver_policy']})",
            *(changed_targets[:12] or ["No env-backed image references needed a tag change before pull/up."]),
        ]
        if pending_replacements:
            temp_env_dir, active_compose_env_files = _build_temp_compose_env_files(
                project_dir,
                compose_env_files,
                pending_replacements,
            )

    compose_command = _compose_command(active_compose_env_files)
    assert compose_command is not None
    try:
        rc_config, out_config = run_command(compose_command + ["config"], cwd=project_dir)
        if rc_config != 0:
            return {"exit_code": rc_config, "stdout": _join_output(out_config, "\n".join(policy_output))}
        if bool(payload.get("dry_run", False)):
            return {
                "exit_code": 0,
                "stdout": "\n".join(
                    [
                        out_config,
                        *policy_output,
                        "dry-run mode validated docker compose config only; no images were pulled and no services were restarted.",
                    ]
                ).strip(),
            }

        artifacts: list[dict[str, Any]] = []
        pruned_artifacts: list[dict[str, Any]] = []
        rollback_capture: dict[str, Any] | None = None
        before_state: dict[str, Any] | None = None
        update_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_output: list[str] = []
        backup_requested = bool(payload.get("backup_before"))
        run_backup_commands = _normalize_bool(payload.get("run_backup_commands"), False)
        backup_commands = [str(item) for item in (payload.get("backup_commands") or []) if str(item).strip()]
        backup_retention = _normalize_positive_int(payload.get("backup_retention"), 3)
        rackpatch_managed = bool(payload.get("rackpatch_managed"))
        rackpatch_repo_url = str(payload.get("repo_url") or "").strip()
        rackpatch_release_ref = str(payload.get("release_ref") or payload.get("target_version") or "").strip()

        if backup_requested:
            try:
                archive_path, archive_artifact = _create_stack_backup_archive(stack_name, host, project_dir, update_stamp)
                del archive_path
                artifacts.append(archive_artifact)
                if run_backup_commands and backup_commands:
                    command_artifacts, command_output = _run_backup_commands(
                        stack_name,
                        host,
                        project_dir,
                        backup_commands,
                        update_stamp,
                    )
                    seen_paths = {str(item.get("container_path") or "") for item in artifacts}
                    for artifact in command_artifacts:
                        container_path = str(artifact.get("container_path") or "")
                        if container_path in seen_paths:
                            continue
                        seen_paths.add(container_path)
                        artifacts.append(artifact)
                    backup_output.extend(command_output)
                pruned_artifacts = _prune_backup_runs(stack_name, host, backup_retention)
            except Exception as exc:  # noqa: BLE001
                stdout_parts = [out_config, *policy_output]
                if backup_output:
                    stdout_parts.extend(backup_output)
                stdout_parts.append(str(exc))
                return {
                    "exit_code": 1,
                    "error": f"failed to capture stack backup: {exc}",
                    "stdout": "\n".join(part for part in stdout_parts if part).strip(),
                    "artifacts": artifacts,
                    "pruned_artifacts": pruned_artifacts,
                }

        try:
            before_state = _capture_stack_state(project_dir, compose_env_files)
            before_state.update({"stack_name": stack_name, "host": host})
            rollback_capture = _write_rollback_capture(stack_name, before_state)
            artifacts.append(
                {
                    "kind": "rollback",
                    "target_ref": stack_name,
                    "path": rollback_capture["capture_path"],
                    "container_path": rollback_capture["capture_path"],
                    "source": "agent-docker-update",
                    "host": host,
                    "latest_path": rollback_capture["latest_path"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            stdout_parts = [out_config, *policy_output]
            if backup_output:
                stdout_parts.extend(backup_output)
            stdout_parts.append(str(exc))
            return {
                "exit_code": 1,
                "error": f"failed to capture rollback metadata: {exc}",
                "stdout": "\n".join(part for part in stdout_parts if part).strip(),
                "artifacts": artifacts,
                "pruned_artifacts": pruned_artifacts,
            }

        if rackpatch_managed:
            if not rackpatch_repo_url:
                stdout_parts = [out_config, *policy_output]
                if backup_output:
                    stdout_parts.extend(backup_output)
                stdout_parts.append("rackpatch-managed stack update is missing repo_url")
                return {
                    "exit_code": 1,
                    "error": "rackpatch-managed stack update is missing repo_url",
                    "stdout": "\n".join(part for part in stdout_parts if part).strip(),
                    "artifacts": artifacts,
                    "pruned_artifacts": pruned_artifacts,
                }
            rc_update, out_update = _run_rackpatch_stack_update(project_dir, rackpatch_repo_url, rackpatch_release_ref)
            stdout_parts = [out_config, *policy_output]
            if backup_output:
                stdout_parts.extend(backup_output)
            stdout_parts.append(out_update)
            result: dict[str, Any] = {
                "exit_code": rc_update,
                "stdout": "\n".join(part for part in stdout_parts if part).strip(),
                "artifacts": artifacts,
                "pruned_artifacts": pruned_artifacts,
                "release_ref": rackpatch_release_ref or None,
                "rackpatch_managed": True,
            }
        else:
            rc_pull, out_pull = run_command(compose_command + ["pull"], cwd=project_dir)
            if rc_pull != 0:
                stdout_parts = [out_config, *policy_output]
                if backup_output:
                    stdout_parts.extend(backup_output)
                stdout_parts.append(out_pull)
                return {
                    "exit_code": rc_pull,
                    "stdout": "\n".join(part for part in stdout_parts if part).strip(),
                    "artifacts": artifacts,
                    "pruned_artifacts": pruned_artifacts,
                }
            rc_up, out_up = run_command(compose_command + ["up", "-d"], cwd=project_dir)
            stdout_parts = [out_config, *policy_output]
            if backup_output:
                stdout_parts.extend(backup_output)
            stdout_parts.extend([out_pull, out_up])
            result = {
                "exit_code": rc_up,
                "stdout": "\n".join(part for part in stdout_parts if part).strip(),
                "artifacts": artifacts,
                "pruned_artifacts": pruned_artifacts,
            }
            if rc_up == 0 and pending_replacements:
                try:
                    _persist_env_replacements(pending_replacements)
                except Exception as exc:  # noqa: BLE001
                    result["exit_code"] = 1
                    result["error"] = f"services updated but failed to persist env image refs: {exc}"
                    result["stdout"] = _join_output(result["stdout"], str(exc))

        if rollback_capture:
            result["rollback_capture"] = rollback_capture
        result["image_policy"] = normalized_policy
        if result["exit_code"] == 0 and before_state is not None:
            try:
                after_state = _capture_stack_state(project_dir, compose_env_files)
                result["update_summary"] = _summarize_stack_changes(before_state, after_state, stack_name, host)
            except Exception as exc:  # noqa: BLE001
                result["update_summary_error"] = str(exc)
        return result
    finally:
        if temp_env_dir is not None:
            temp_env_dir.cleanup()


def agent_update(payload: dict[str, Any]) -> dict[str, Any]:
    update_command = str(payload.get("update_command") or "").strip()
    if not update_command:
        return {"exit_code": 1, "error": "update_command is required", "stdout": ""}

    update_mode = str(payload.get("update_mode") or AGENT_MODE or "unknown").strip() or "unknown"
    target_version = str(payload.get("target_version") or payload.get("release_ref") or "").strip()
    target_dir = str(payload.get("update_target_dir") or "").strip()
    delay_seconds = int(payload.get("delay_seconds") or 2)
    update_root = STATE_DIR / "updates"
    update_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    log_path = update_root / f"agent-update-{stamp}.log"

    if update_mode == "systemd" and shutil.which("systemd-run"):
        unit_name = f"rackpatch-agent-update-{stamp}"
        background_command = f"sleep 2; {update_command} >> {shlex.quote(str(log_path))} 2>&1"
        rc, stdout = run_command(
            [
                "systemd-run",
                "--unit",
                unit_name,
                "--collect",
                "bash",
                "-lc",
                background_command,
            ]
        )
        if rc != 0:
            return {"exit_code": rc, "error": "failed to schedule systemd agent update", "stdout": stdout}
        return {
            "exit_code": 0,
            "stdout": "\n".join(
                [
                    stdout.strip(),
                    f"Scheduled agent update for {target_version or 'configured ref'} via transient unit {unit_name}.",
                    f"Update log: {log_path}",
                ]
            ).strip(),
            "scheduled": True,
            "update_mode": update_mode,
            "target_version": target_version or None,
            "log_path": str(log_path),
            "unit_name": unit_name,
        }

    if update_mode in {"compose", "container"}:
        try:
            result = _schedule_container_agent_update(
                update_command=update_command,
                update_mode=update_mode,
                target_dir=target_dir,
                log_path=log_path,
                stamp=stamp,
                delay_seconds=delay_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "exit_code": 1,
                "error": f"failed to schedule {update_mode} agent update helper: {exc}",
                "stdout": str(exc),
            }
        result["target_version"] = target_version or None
        return result

    wrapper_path = update_root / f"agent-update-{stamp}.sh"
    wrapper_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"sleep {delay_seconds}",
                f"{update_command} >> {shlex.quote(str(log_path))} 2>&1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    wrapper_path.chmod(0o700)

    with log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"[{datetime.now(timezone.utc).isoformat()}] scheduling agent update mode={update_mode}\n")
        process = subprocess.Popen(
            ["bash", str(wrapper_path)],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    return {
        "exit_code": 0,
        "stdout": "\n".join(
            [
                f"Scheduled agent update for {target_version or 'configured ref'} in {update_mode} mode.",
                f"Background process id: {process.pid}",
                f"Update log: {log_path}",
            ]
        ),
        "scheduled": True,
        "update_mode": update_mode,
        "target_version": target_version or None,
        "log_path": str(log_path),
        "pid": process.pid,
    }


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
    if kind == "docker_check":
        result = docker_check(payload)
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    if kind == "docker_update":
        result = docker_update(payload)
        status = "completed" if result["exit_code"] == 0 else "failed"
        return status, result
    if kind == "agent_update":
        result = agent_update(payload)
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
                try:
                    status, result = execute_job(job)
                except Exception as exc:  # noqa: BLE001
                    message = f"unexpected agent job error: {exc}"
                    status = "failed"
                    result = {"error": message, "stdout": message}
                    post_event(state, job_id, message, stream="stderr")
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
