from __future__ import annotations

import json
import shlex
from typing import Any

from common import agents as agent_records, config


def _shell_quote(value: Any) -> str:
    return shlex.quote(str(value or ""))


def _curl_command(url: str, *extra: str) -> str:
    parts = ["curl", "-fsSL", url, *extra]
    return " ".join(_shell_quote(part) for part in parts)


def _normalize_dir(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _agent_image_for_ref(public_settings: dict[str, Any], repo_ref: str) -> str:
    return config.derive_public_image_ref(
        str(public_settings.get("repo_url") or config.PUBLIC_REPO_URL),
        repo_ref,
        "rackpatch-agent",
    )


def build_agent_install_commands(public_settings: dict[str, Any], token: str) -> dict[str, str]:
    repo_ref = str(public_settings.get("repo_ref") or config.PUBLIC_REPO_REF)
    script_prefix = [
        "curl",
        "-fsSL",
        public_settings["install_script_url"],
        "|",
        "bash",
        "-s",
        "--",
    ]
    image = _agent_image_for_ref(public_settings, repo_ref)
    compose = script_prefix + [
        "--server-url",
        public_settings["base_url"],
        "--bootstrap-token",
        token,
        "--mode",
        "compose",
        "--compose-dir",
        public_settings["agent_compose_dir"],
        "--image",
        image,
    ]
    container = script_prefix + [
        "--server-url",
        public_settings["base_url"],
        "--bootstrap-token",
        token,
        "--mode",
        "container",
        "--image",
        image,
    ]
    systemd = script_prefix + [
        "--server-url",
        public_settings["base_url"],
        "--bootstrap-token",
        token,
        "--mode",
        "systemd",
        "--install-source",
        public_settings["repo_url"],
        "--install-ref",
        repo_ref,
    ]
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
    preset: str = "packages",
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
        "sudo",
        "bash",
        "-s",
        "--",
        "--mode",
        mode,
        "--preset",
        preset,
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
        extra = [
            "--compose-dir",
            compose_dir or public_settings["agent_compose_dir"],
            "--image",
            _agent_image_for_ref(public_settings, ref),
        ]
    elif mode == "container":
        extra = ["--image", _agent_image_for_ref(public_settings, ref)]
        if install_dir:
            extra.extend(["--install-dir", install_dir])
    elif mode == "systemd" and install_dir:
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
        *extra,
    ]
    if mode == "systemd":
        command.extend(
            [
                "--install-source",
                public_settings["repo_url"],
                "--install-ref",
                ref,
            ]
        )
    return " ".join(_shell_quote(part) if part != "|" else part for part in command)


def build_agent_update_commands(public_settings: dict[str, Any], ref: str) -> dict[str, str]:
    return {
        "compose": build_agent_update_command(public_settings, ref, "compose"),
        "container": build_agent_update_command(public_settings, ref, "container"),
        "systemd": build_agent_update_command(public_settings, ref, "systemd"),
    }


def _self_agent_update_reason(public_settings: dict[str, Any], agent: dict[str, Any]) -> str | None:
    metadata = agent.get("metadata") or {}
    mode = str(metadata.get("mode") or "").strip()
    compose_dir = _normalize_dir(metadata.get("compose_dir"))
    rackpatch_compose_dir = _normalize_dir(
        public_settings.get("rackpatch_compose_dir") or config.PUBLIC_RACKPATCH_COMPOSE_DIR
    )
    labels = {str(item).strip().lower() for item in (agent.get("labels") or []) if str(item).strip()}
    if mode != "compose":
        return None
    if compose_dir and rackpatch_compose_dir and compose_dir == rackpatch_compose_dir:
        return "managed by the rackpatch stack update command"
    if "self-agent" in labels:
        return "managed by the rackpatch stack update command"
    return None


def build_agent_update_plan(
    public_settings: dict[str, Any],
    ref: str,
    agents: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered_agents = sorted(
        agents,
        key=lambda item: (
            str((item.get("display_name") or item.get("name") or "")).lower(),
            str(item.get("name") or "").lower(),
        ),
    )
    items: list[dict[str, Any]] = []
    for agent in ordered_agents:
        agent = agent_records.with_effective_status(agent)
        metadata = agent.get("metadata") or {}
        mode = str(metadata.get("mode") or "").strip()
        label = str(agent.get("display_name") or agent.get("name") or agent.get("id") or "agent")
        reason = _self_agent_update_reason(public_settings, agent)
        if not reason and mode not in {"compose", "container", "systemd"}:
            reason = "unsupported update mode"
        command = ""
        if not reason:
            command = build_agent_update_command(
                public_settings,
                ref,
                mode,
                compose_dir=str(metadata.get("compose_dir") or ""),
                install_dir=str(metadata.get("install_dir") or ""),
            )
        items.append(
            {
                "id": str(agent.get("id") or ""),
                "name": label,
                "agent_name": str(agent.get("name") or ""),
                "display_name": str(agent.get("display_name") or ""),
                "mode": mode or "unknown",
                "command": command,
                "capabilities": [str(value) for value in (agent.get("capabilities") or []) if str(value).strip()],
                "reason": reason or "",
                "eligible": not bool(reason),
                "status": str(agent.get("status") or ""),
                "version": str(agent.get("version") or ""),
            }
        )
    return {
        "total": len(ordered_agents),
        "items": items,
    }


def build_agent_fleet_update_command(
    public_settings: dict[str, Any],
    ref: str,
    agents: list[dict[str, Any]],
) -> dict[str, Any]:
    plan = build_agent_update_plan(public_settings, ref, agents)
    total = int(plan.get("total") or 0)
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# rackpatch fleet update bundle generated for {total} agent{'' if total == 1 else 's'}",
        f"# release ref: {ref}",
        "",
    ]
    included = 0
    skipped: list[dict[str, str]] = []
    for item in plan["items"]:
        if not item.get("eligible"):
            skipped.append(
                {
                    "name": str(item.get("name") or "unknown"),
                    "mode": str(item.get("mode") or "unknown"),
                    "reason": str(item.get("reason") or "not eligible for fleet updates"),
                }
            )
            continue
        update_label = f"Updating {item['name']} ({item['mode']})"
        lines.extend(
            [
                f"printf '%s\\n' {_shell_quote(update_label)}",
                str(item.get("command") or ""),
                "",
            ]
        )
        included += 1

    if skipped:
        lines.extend(["# Skipped agents:"])
        lines.extend(
            [
                f"# - {item['name']} ({item['mode']}): {item.get('reason') or 'not eligible for fleet updates'}"
                for item in skipped
            ]
        )
        lines.append("")
    if included == 0:
        lines.append("# No eligible enrolled agents were found for a fleet update bundle.")
    summary_bits = [
        f"{included} command{'' if included == 1 else 's'}",
    ]
    if skipped:
        summary_bits.append(f"{len(skipped)} skipped")
    return {
        "command": "\n".join(lines).rstrip() + "\n",
        "summary": f"Fleet update bundle for {total} agent{'' if total == 1 else 's'}"
        + (f" ({', '.join(summary_bits)})" if summary_bits else ""),
        "total": total,
        "included": included,
        "skipped": skipped,
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
        "docker_updates": "/api/v1/docker/updates",
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
