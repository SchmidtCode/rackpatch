from __future__ import annotations

import json
import shlex
from typing import Any

from common import config


def _shell_quote(value: Any) -> str:
    return shlex.quote(str(value or ""))


def _curl_command(url: str, *extra: str) -> str:
    parts = ["curl", "-fsSL", url, *extra]
    return " ".join(_shell_quote(part) for part in parts)


def build_agent_install_commands(public_settings: dict[str, Any], token: str) -> dict[str, str]:
    common_args = [
        "--server-url",
        public_settings["base_url"],
        "--bootstrap-token",
        token,
        "--install-source",
        public_settings["repo_url"],
        "--install-ref",
        public_settings["repo_ref"],
    ]
    script_prefix = [
        "curl",
        "-fsSL",
        public_settings["install_script_url"],
        "|",
        "bash",
        "-s",
        "--",
    ]
    container = script_prefix + common_args + ["--mode", "container"]
    compose = script_prefix + common_args + [
        "--mode",
        "compose",
        "--compose-dir",
        public_settings["agent_compose_dir"],
    ]
    systemd = script_prefix + common_args + ["--mode", "systemd"]
    return {
        "compose": " ".join(_shell_quote(part) if part != "|" else part for part in compose),
        "container": " ".join(_shell_quote(part) if part != "|" else part for part in container),
        "systemd": " ".join(_shell_quote(part) if part != "|" else part for part in systemd),
    }


def build_agent_host_maintenance_command(
    public_settings: dict[str, Any],
    ref: str,
    mode: str,
    *,
    compose_dir: str = "",
    install_dir: str = "",
) -> str:
    script_url = config.derive_public_script_url(
        public_settings["repo_url"],
        ref,
        "scripts/enable-agent-host-maintenance.sh",
    )
    if "example.invalid" in script_url:
        return "# Configure a GitHub repo URL to generate rackpatch host-maintenance enable commands."
    extra: list[str] = []
    if mode == "compose":
        extra = ["--compose-dir", compose_dir or public_settings["agent_compose_dir"]]
    elif mode in {"container", "systemd"}:
        extra = ["--install-dir", install_dir or "/opt/rackpatch-agent"]
    command = [
        "curl",
        "-fsSL",
        script_url,
        "|",
        "bash",
        "-s",
        "--",
        "--mode",
        mode,
        "--install-source",
        public_settings["repo_url"],
        "--install-ref",
        ref,
        *extra,
    ]
    return " ".join(_shell_quote(part) if part != "|" else part for part in command)


def build_agent_host_maintenance_commands(public_settings: dict[str, Any], ref: str) -> dict[str, str]:
    return {
        "compose": build_agent_host_maintenance_command(public_settings, ref, "compose"),
        "container": build_agent_host_maintenance_command(public_settings, ref, "container"),
        "systemd": build_agent_host_maintenance_command(public_settings, ref, "systemd"),
    }


def build_stack_update_command(public_settings: dict[str, Any], ref: str) -> str:
    script_url = config.derive_public_script_url(
        public_settings["repo_url"],
        ref,
        "scripts/update-rackpatch.sh",
    )
    if "example.invalid" in script_url:
        return "# Configure a GitHub repo URL to generate rackpatch stack update commands."
    command = [
        "curl",
        "-fsSL",
        script_url,
        "|",
        "bash",
        "-s",
        "--",
        "--install-dir",
        public_settings["rackpatch_compose_dir"],
        "--repo-url",
        public_settings["repo_url"],
        "--ref",
        ref,
    ]
    return " ".join(_shell_quote(part) if part != "|" else part for part in command)


def build_agent_update_command(
    public_settings: dict[str, Any],
    ref: str,
    mode: str,
    *,
    compose_dir: str = "",
    install_dir: str = "",
) -> str:
    script_url = config.derive_public_script_url(
        public_settings["repo_url"],
        ref,
        "scripts/update-agent.sh",
    )
    if "example.invalid" in script_url:
        return "# Configure a GitHub repo URL to generate rackpatch agent update commands."
    extra: list[str] = []
    if mode == "compose":
        extra = ["--compose-dir", compose_dir or public_settings["agent_compose_dir"]]
    elif mode in {"container", "systemd"} and install_dir:
        extra = ["--install-dir", install_dir]
    command = [
        "curl",
        "-fsSL",
        script_url,
        "|",
        "bash",
        "-s",
        "--",
        "--mode",
        mode,
        "--install-source",
        public_settings["repo_url"],
        "--install-ref",
        ref,
        *extra,
    ]
    return " ".join(_shell_quote(part) if part != "|" else part for part in command)


def build_agent_update_commands(public_settings: dict[str, Any], ref: str) -> dict[str, str]:
    return {
        "compose": build_agent_update_command(public_settings, ref, "compose"),
        "container": build_agent_update_command(public_settings, ref, "container"),
        "systemd": build_agent_update_command(public_settings, ref, "systemd"),
    }


def build_api_surface(public_settings: dict[str, Any]) -> dict[str, Any]:
    base_url = str(public_settings.get("base_url") or config.PUBLIC_BASE_URL).rstrip("/")
    login_payload = json.dumps(
        {"username": config.ADMIN_USERNAME, "password": "REPLACE_ME"},
        separators=(",", ":"),
    )
    resources = {
        "login": "/api/v1/auth/login",
        "overview": "/api/v1/overview",
        "settings": "/api/v1/settings",
        "context": "/api/v1/context",
        "job_kinds": "/api/v1/job-kinds",
        "jobs": "/api/v1/jobs",
        "job_events_template": "/api/v1/jobs/{job_id}/events",
        "stacks": "/api/v1/stacks",
        "hosts": "/api/v1/hosts",
        "agents": "/api/v1/agents",
        "schedules": "/api/v1/schedules",
        "backups": "/api/v1/backups",
    }
    return {
        "auth": {
            "scheme": "Bearer token from /api/v1/auth/login",
            "header": "Authorization: Bearer <token>",
        },
        "resources": resources,
        "examples": {
            "login": _curl_command(
                f"{base_url}{resources['login']}",
                "-X",
                "POST",
                "-H",
                "Content-Type: application/json",
                "-d",
                login_payload,
            ),
            "context": _curl_command(
                f"{base_url}{resources['context']}",
                "-H",
                "Authorization: Bearer REPLACE_ME",
            ),
            "jobs": _curl_command(
                f"{base_url}{resources['jobs']}",
                "-H",
                "Authorization: Bearer REPLACE_ME",
            ),
        },
    }
