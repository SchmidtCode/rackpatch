from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from common import agents as agent_records, auth, config, control_plane, db, job_catalog, jobs, notify, releases, runtime_settings, site, stack_catalog


app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)
if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    jobs.retire_legacy_package_jobs()
    jobs.retire_legacy_worker_control_jobs()


def _json_body(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _schedule_payload_fields(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    schedule_payload = _json_body(payload.get("payload"))
    timezone_name = site.schedule_timezone_name(payload.get("timezone"))
    return schedule_payload, timezone_name


def _matching_agent_rows(
    cur: Any,
    *,
    name: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, name, status, last_seen_at, metadata
        FROM agents
        WHERE name <> %s
        ORDER BY last_seen_at DESC
        """,
        (name,),
    )
    matches: list[dict[str, Any]] = []
    for row in cur.fetchall():
        if not agent_records.same_identity(row.get("metadata"), metadata):
            continue
        matches.append(agent_records.with_effective_status(row))
    return matches


def _reusable_agent_row(cur: Any, *, name: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
    for row in _matching_agent_rows(cur, name=name, metadata=metadata):
        if agent_records.can_reuse_agent_record(row, metadata):
            return row
    return None


def _prune_stale_agent_duplicates(
    cur: Any,
    *,
    current_id: str,
    name: str,
    metadata: dict[str, Any],
) -> None:
    for row in _matching_agent_rows(cur, name=name, metadata=metadata):
        if str(row.get("id") or "") == current_id:
            continue
        if str(row.get("status") or "").lower() == "online":
            continue
        cur.execute("SELECT 1 FROM jobs WHERE target_agent_id = %s LIMIT 1", (row["id"],))
        if cur.fetchone() is not None:
            continue
        cur.execute("DELETE FROM agents WHERE id = %s", (row["id"],))


def _validate_agent_secret(agent_id: str, provided: str | None) -> None:
    if not provided:
        raise HTTPException(status_code=401, detail="missing agent secret")
    row = db.fetch_one("SELECT secret_hash FROM agents WHERE id = %s", (agent_id,))
    if not row or row["secret_hash"] != auth.hash_token(provided):
        raise HTTPException(status_code=401, detail="invalid agent secret")


def _normalize_host_identity(value: Any) -> str:
    return str(value or "").strip().lower().strip("[]")


def _public_base_host_identities(public_settings: dict[str, Any]) -> set[str]:
    hostname = _normalize_host_identity(urlparse(str(public_settings.get("base_url") or "")).hostname)
    if not hostname:
        return set()
    identities = {hostname}
    try:
        for family, socktype, proto, canonname, sockaddr in socket.getaddrinfo(hostname, None):
            del family, socktype, proto, canonname
            address = _normalize_host_identity(str(sockaddr[0]).split("%", 1)[0])
            if address:
                identities.add(address)
    except OSError:
        pass
    return identities


def _is_control_plane_host(host: dict[str, Any], base_identities: set[str]) -> bool:
    if bool(host.get("rackpatch_control_plane")) or bool(host.get("control_plane")):
        return True
    host_identities = {
        _normalize_host_identity(host.get("name")),
        _normalize_host_identity(host.get("ansible_host")),
        _normalize_host_identity(host.get("inventory_hostname")),
        _normalize_host_identity(host.get("ansible_hostname")),
    }
    host_identities.discard("")
    return bool(base_identities & host_identities)


def _host_runtime(agent: dict[str, Any] | None, host: dict[str, Any], *, control_plane_host: bool) -> dict[str, str]:
    if agent:
        host_maintenance = (agent.get("metadata") or {}).get("host_maintenance") or {}
        actions = [str(item) for item in host_maintenance.get("actions", []) if str(item).strip()]
        if actions:
            action_labels = ", ".join(action.replace("_", " ") for action in actions)
            detail = str(host_maintenance.get("detail") or f"Limited to approved maintenance actions: {action_labels}.")
        else:
            detail = str(
                host_maintenance.get("detail")
                or "Agent enrolled. UI helper-gated host jobs stay greyed out until the host maintenance helper is enabled."
            )
        return {
            "status": str(agent.get("status") or "unknown"),
            "detail": detail,
        }
    if control_plane_host:
        return {
            "status": "Online",
            "detail": "Rackpatch control plane host. Agent optional.",
        }
    return {
        "status": "No agent",
        "detail": "No agent enrolled. Docker updates require an enrolled Docker-capable agent, and helper-gated package or Proxmox jobs require the limited host-maintenance helper.",
    }


HOST_TEXT_FIELDS = (
    "ansible_host",
    "ansible_user",
    "compose_root",
    "maintenance_tier",
    "proxmox_node_name",
    "guest_type",
)
HOST_INT_FIELDS = ("proxmox_guest_id",)
HOST_INT_LIST_FIELDS = ("guest_ids", "soft_reboot_guest_order")
HOST_BOOL_FIELDS = ("rackpatch_control_plane",)


def _normalize_host_int_list(value: Any, field_name: str) -> list[int]:
    raw_items = value if isinstance(value, list) else str(value or "").split(",")
    items: list[int] = []
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        if not text.isdigit():
            raise HTTPException(status_code=400, detail=f"{field_name} entries must be numeric")
        items.append(int(text))
    return items


def _normalize_host_int(value: Any, field_name: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise HTTPException(status_code=400, detail=f"{field_name} must be numeric")
    return int(text)


def _normalize_host_payload(payload: dict[str, Any], *, existing: dict[str, Any] | None = None) -> tuple[str, str, dict[str, Any]]:
    existing_host = dict(existing or {})
    current_name = str(existing_host.pop("name", "") or "").strip()
    current_group = str(existing_host.pop("group", "") or "").strip()

    host_name = str(payload.get("name") or current_name).strip()
    if not host_name:
        raise HTTPException(status_code=400, detail="host name is required")
    group_name = str(payload.get("group") or current_group or "all").strip() or "all"

    host_data = dict(existing_host)
    for field_name in HOST_TEXT_FIELDS:
        if field_name not in payload:
            continue
        text = str(payload.get(field_name) or "").strip()
        if text:
            host_data[field_name] = text
        else:
            host_data.pop(field_name, None)
    for field_name in HOST_INT_FIELDS:
        if field_name not in payload:
            continue
        value = _normalize_host_int(payload.get(field_name), field_name)
        if value is None:
            host_data.pop(field_name, None)
        else:
            host_data[field_name] = value
    for field_name in HOST_INT_LIST_FIELDS:
        if field_name not in payload:
            continue
        values = _normalize_host_int_list(payload.get(field_name), field_name)
        if values:
            host_data[field_name] = values
        else:
            host_data.pop(field_name, None)
    for field_name in HOST_BOOL_FIELDS:
        if field_name not in payload:
            continue
        if bool(payload.get(field_name)):
            host_data[field_name] = True
        else:
            host_data.pop(field_name, None)
    return host_name, group_name, host_data


def _host_item(
    host: dict[str, Any],
    *,
    known_agents: dict[str, dict[str, Any]],
    base_identities: set[str],
) -> dict[str, Any]:
    agent = known_agents.get(host["name"])
    control_plane_host = _is_control_plane_host(host, base_identities)
    return {
        **host,
        "agent": agent,
        "control_plane_host": control_plane_host,
        "runtime": _host_runtime(agent, host, control_plane_host=control_plane_host),
        "job_access": {
            "package_check": jobs.host_job_access("package_check", host["name"]),
            "package_patch_dry_run": jobs.host_job_access("package_patch", host["name"], {"dry_run": True}),
            "package_patch_live": jobs.host_job_access("package_patch", host["name"], {"dry_run": False}),
            "proxmox_patch_dry_run": jobs.host_job_access("proxmox_patch", host["name"], {"dry_run": True}),
            "proxmox_patch_live": jobs.host_job_access("proxmox_patch", host["name"], {"dry_run": False}),
            "proxmox_reboot_dry_run": jobs.host_job_access("proxmox_reboot", host["name"], {"dry_run": True}),
            "proxmox_reboot_live": jobs.host_job_access("proxmox_reboot", host["name"], {"dry_run": False}),
        },
    }


def _hosts_payload() -> dict[str, Any]:
    public_settings = runtime_settings.get_public_settings()
    base_identities = _public_base_host_identities(public_settings)
    known_agents = {
        row["name"]: agent_records.with_effective_status(row)
        for row in db.fetch_all(
            """
            SELECT id, name, display_name, status, capabilities, metadata, last_seen_at
            FROM agents
            ORDER BY name
            """
        )
    }
    return {
        "items": [
            _host_item(host, known_agents=known_agents, base_identities=base_identities)
            for host in site.load_hosts()
        ],
        "groups": site.load_groups(),
        "inventory_path": str(site.inventory_path()),
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _backup_file_candidates(item: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    metadata = item.get("metadata") or {}
    container_path = str(metadata.get("container_path") or "").strip()
    if container_path:
        candidates.append(Path(container_path))

    raw_path = str(item.get("path") or "").strip()
    if raw_path:
        if not raw_path.startswith("agent://"):
            raw_candidate = Path(raw_path)
            if raw_candidate.is_absolute():
                candidates.append(raw_candidate)
            else:
                trimmed = raw_path[5:] if raw_path.startswith("data/") else raw_path
                candidates.append(config.DATA_ROOT / trimmed)

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return deduped


def _backup_details(item: dict[str, Any]) -> dict[str, Any]:
    candidates = _backup_file_candidates(item)
    existing_file = next((path for path in candidates if path.is_file()), None)
    preferred_path = existing_file or (candidates[0] if candidates else None)
    backup_root = config.BACKUPS_ROOT.resolve(strict=False)
    within_backups_root = bool(preferred_path and _is_relative_to(preferred_path, backup_root))
    metadata = item.get("metadata") or {}
    if existing_file:
        size_bytes: int | None = existing_file.stat().st_size
    else:
        raw_size = metadata.get("size_bytes")
        try:
            parsed_size = int(raw_size) if raw_size is not None else None
        except (TypeError, ValueError):
            parsed_size = None
        size_bytes = parsed_size if parsed_size is not None and parsed_size >= 0 else None
    display_name = preferred_path.name if preferred_path else Path(str(item.get("path") or "")).name
    return {
        "resolved_path": str(preferred_path) if preferred_path else "",
        "file_name": display_name or str(item.get("target_ref") or ""),
        "exists": bool(existing_file),
        "size_bytes": size_bytes,
        "delete_supported": bool(existing_file and within_backups_root),
        "artifact_host": str(metadata.get("host") or ""),
        "artifact_source": str(metadata.get("source") or ""),
    }


def _job_summary_rows(where_sql: str = "", params: tuple[Any, ...] = (), limit: int = 20) -> list[dict[str, Any]]:
    return db.fetch_all(
        f"""
        SELECT id, kind, status, source, target_type, target_ref, executor, requested_by,
               approval_status, approved_by, target_agent_id, created_at, queued_at,
               started_at, finished_at
        FROM jobs
        {where_sql}
        ORDER BY created_at DESC
        LIMIT {int(limit)}
        """,
        params,
    )


def _job_details(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "deletable": jobs.job_is_deletable(item.get("status")),
    }


def _latest_stack_job_map(kind: str) -> dict[str, dict[str, Any]]:
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (target_ref)
               id, kind, status, source, target_type, target_ref, payload, result,
               requested_by, approval_status, target_agent_id, created_at, queued_at,
               started_at, finished_at
        FROM jobs
        WHERE kind = %s
          AND target_type = 'stack'
        ORDER BY target_ref, created_at DESC
        """,
        (kind,),
    )
    return {str(row["target_ref"]): row for row in rows}


def _agent_capability_set(agent: dict[str, Any] | None) -> set[str]:
    if not agent:
        return set()
    values = agent.get("capabilities") or []
    selected = {str(value) for value in values if str(value).strip()}
    metadata = agent.get("metadata") or {}
    selected.update(str(value) for value in (metadata.get("capabilities") or []) if str(value).strip())
    return selected


def _docker_stack_access(
    stack: dict[str, Any],
    agents_by_name: dict[str, dict[str, Any]],
    *,
    kind: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    project_dir = stack_catalog.stack_project_dir(stack)
    if not project_dir:
        reason = f"{stack.get('name') or 'stack'} is missing path or project_dir."
        return {"eligible": False, "reason": reason, "required_capabilities": [], "target_agent_id": None}

    host_name = stack_catalog.stack_runtime_host(stack)
    agent = agents_by_name.get(host_name)
    if not agent:
        reason = f"No enrolled agent found for {stack.get('name') or host_name or 'stack'}."
        required = ["docker-stack-inspect"] if kind == "docker_check" else ["docker"]
        return {"eligible": False, "reason": reason, "required_capabilities": required, "target_agent_id": None}
    if str(agent.get("status") or "").lower() != "online":
        reason = f"Agent for {stack.get('name') or host_name or 'stack'} is offline."
        required = ["docker-stack-inspect"] if kind == "docker_check" else ["docker"]
        return {
            "eligible": False,
            "reason": reason,
            "required_capabilities": required,
            "target_agent_id": str(agent.get("id") or ""),
        }

    access_error = agent_records.project_dir_access_reason(agent, project_dir)
    if access_error:
        required = ["docker-stack-inspect"] if kind == "docker_check" else ["docker"]
        return {
            "eligible": False,
            "reason": access_error,
            "required_capabilities": required,
            "target_agent_id": str(agent.get("id") or ""),
        }

    required = {"docker-stack-inspect"} if kind == "docker_check" else {"docker"}
    capabilities = _agent_capability_set(agent)
    if not required.issubset(capabilities):
        label = ", ".join(sorted(required))
        reason = f"Agent for {stack.get('name') or host_name or 'stack'} does not advertise {label}."
        return {
            "eligible": False,
            "reason": reason,
            "required_capabilities": sorted(required),
            "target_agent_id": str(agent.get("id") or ""),
        }

    return {
        "eligible": True,
        "reason": "",
        "required_capabilities": sorted(required),
        "target_agent_id": str(agent.get("id") or ""),
    }


def _docker_updates_payload() -> dict[str, Any]:
    stacks = site.load_stacks()
    check_jobs = _latest_stack_job_map("docker_check")
    update_jobs = _latest_stack_job_map("docker_update")
    agent_rows = db.fetch_all("SELECT id, name, capabilities, metadata, status, last_seen_at FROM agents ORDER BY name")
    agents_by_name = {str(row["name"]): agent_records.with_effective_status(row) for row in agent_rows}
    items: list[dict[str, Any]] = []

    for stack in stacks:
        stack_name = str(stack.get("name") or "").strip()
        if not stack_name:
            continue

        check_access = _docker_stack_access(stack, agents_by_name, kind="docker_check")
        live_access = _docker_stack_access(stack, agents_by_name, kind="docker_update", dry_run=False)
        dry_access = _docker_stack_access(stack, agents_by_name, kind="docker_update", dry_run=True)
        latest_check = check_jobs.get(stack_name)
        latest_update = update_jobs.get(stack_name)

        check_result = (latest_check or {}).get("result") or {}
        update_result = (latest_update or {}).get("result") or {}
        report = check_result.get("report") if isinstance(check_result, dict) else {}
        if not isinstance(report, dict):
            report = {}

        check_job_status = str((latest_check or {}).get("status") or "").strip().lower()
        if latest_check is None:
            inspection_state = "never_checked"
        elif check_job_status == "completed":
            inspection_state = str(report.get("status") or "completed")
        else:
            inspection_state = check_job_status or "unknown"

        update_summary = update_result.get("update_summary") if isinstance(update_result, dict) else {}
        if not isinstance(update_summary, dict):
            update_summary = {}

        item = {
            **stack,
            "project_dir": stack_catalog.stack_project_dir(stack),
            "resolved_host": stack_catalog.stack_runtime_host(stack),
            "job_access": {
                "docker_check": check_access,
                "docker_update_live": live_access,
                "docker_update_dry_run": dry_access,
            },
            "inspection": {
                "state": inspection_state,
                "job": latest_check,
                "report": report,
                "checked_at": report.get("checked_at")
                or (latest_check or {}).get("finished_at")
                or (latest_check or {}).get("started_at")
                or (latest_check or {}).get("created_at"),
                "error": str(check_result.get("error") or ""),
            },
            "latest_update": {
                "job": latest_update,
                "status": str((latest_update or {}).get("status") or "").strip().lower() or "never_run",
                "finished_at": (latest_update or {}).get("finished_at"),
                "error": str(update_result.get("error") or update_result.get("update_summary_error") or ""),
                "summary": update_summary,
                "artifacts": update_result.get("artifacts") if isinstance(update_result, dict) else [],
            },
        }
        item["selection_eligible"] = bool(
            report.get("status") == "outdated" and live_access.get("eligible")
        )
        items.append(item)

    summary = {
        "total_stacks": len(items),
        "checkable_stacks": sum(1 for item in items if item["job_access"]["docker_check"]["eligible"]),
        "checked_stacks": sum(1 for item in items if item["inspection"]["state"] not in {"never_checked", "queued", "pending_approval", "running"}),
        "outdated_stacks": sum(1 for item in items if (item["inspection"]["report"] or {}).get("status") == "outdated"),
        "outdated_images": sum(int((item["inspection"]["report"] or {}).get("outdated_count") or 0) for item in items),
        "selectable_stacks": sum(1 for item in items if item["selection_eligible"]),
        "blocked_live_updates": sum(
            1
            for item in items
            if (item["inspection"]["report"] or {}).get("status") == "outdated"
            and not item["job_access"]["docker_update_live"]["eligible"]
        ),
        "running_checks": sum(1 for item in items if item["inspection"]["state"] in {"queued", "pending_approval", "running"}),
        "failed_checks": sum(1 for item in items if item["inspection"]["state"] == "failed"),
        "running_updates": sum(
            1
            for item in items
            if str((item["latest_update"]["job"] or {}).get("status") or "").strip().lower() in {"queued", "pending_approval", "running"}
        ),
    }

    checkable_items = [item for item in items if item["job_access"]["docker_check"]["eligible"]]
    completed_checkable_items = [
        item
        for item in checkable_items
        if item["inspection"]["state"] not in {"never_checked", "queued", "pending_approval", "running"}
        and item["inspection"].get("checked_at")
    ]
    summary["last_full_check_at"] = None
    if checkable_items and len(completed_checkable_items) == len(checkable_items):
        summary["last_full_check_at"] = max(
            str(item["inspection"]["checked_at"])
            for item in completed_checkable_items
        )

    return {
        "summary": summary,
        "items": items,
    }


def _settings_payload() -> dict[str, Any]:
    bootstrap_token = auth.ensure_bootstrap_token()
    public_settings = runtime_settings.get_public_settings()
    docker_update_settings = runtime_settings.get_docker_update_settings()
    telegram_settings = runtime_settings.get_telegram_settings()
    agents = db.fetch_all(
        """
        SELECT id, name, display_name, transport, platform, version, capabilities, labels,
               metadata, status, last_seen_at, created_at, updated_at
        FROM agents
        ORDER BY name
        """
    )
    return {
        "ui": {
            "app_name": config.APP_NAME,
            "app_version": config.APP_VERSION,
            "app_display_name": config.APP_DISPLAY_NAME,
        },
        "site_name": config.SITE_NAME,
        "site_root": str(site.site_root()),
        "inventory_path": str(site.inventory_path()),
        "stacks_path": str(site.stacks_path()),
        "maintenance_path": str(site.maintenance_path()),
        "paths": {
            "site_root": str(site.site_root()),
            "inventory": str(site.inventory_path()),
            "stacks": str(site.stacks_path()),
            "maintenance": str(site.maintenance_path()),
        },
        "public": public_settings,
        "docker_updates": docker_update_settings,
        "telegram": telegram_settings,
        "default_agent_bootstrap_token": bootstrap_token,
        "agent_install": control_plane.build_agent_install_commands(public_settings, bootstrap_token),
        "agent_host_maintenance": control_plane.build_agent_host_maintenance_commands(
            public_settings,
            str(public_settings.get("repo_ref") or config.PUBLIC_REPO_REF),
        ),
        "release": releases.build_release_status(public_settings, agents),
    }


@app.get("/api/v1/version")
def version() -> dict[str, str]:
    return {
        "app_name": config.APP_NAME,
        "app_version": config.APP_VERSION,
        "app_display_name": config.APP_DISPLAY_NAME,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": config.APP_VERSION,
        "app_name": config.APP_NAME,
        "app_version": config.APP_VERSION,
        "app_display_name": config.APP_DISPLAY_NAME,
        "site": config.SITE_NAME,
    }


@app.post("/api/v1/auth/login")
def login(payload: dict[str, Any]) -> dict[str, Any]:
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    row = db.fetch_one("SELECT username, password_hash FROM users WHERE username = %s", (username,))
    if not row or not auth.verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = auth.issue_session_token(username)
    return {"token": token, "username": username}


@app.get("/api/v1/overview")
def overview(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    counts = {
        "agents": db.fetch_one("SELECT COUNT(*) AS value FROM agents")["value"],
        "jobs": db.fetch_one("SELECT COUNT(*) AS value FROM jobs")["value"],
        "running_jobs": db.fetch_one("SELECT COUNT(*) AS value FROM jobs WHERE status = 'running'")["value"],
        "pending_approvals": db.fetch_one(
            "SELECT COUNT(*) AS value FROM jobs WHERE approval_status = 'pending'"
        )["value"],
        "schedules": db.fetch_one("SELECT COUNT(*) AS value FROM schedules")["value"],
        "backups": db.fetch_one("SELECT COUNT(*) AS value FROM backups")["value"],
    }
    return {
        "counts": counts,
        "site": config.SITE_NAME,
        "site_root": str(site.site_root()),
        "stacks": len(site.load_stacks()),
        "hosts": len(site.load_hosts()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/job-kinds")
def list_job_kinds(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return {"items": job_catalog.list_job_kinds()}


@app.get("/api/v1/stacks")
def stacks(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return {"items": site.load_stacks()}


@app.get("/api/v1/docker/updates")
def docker_updates(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return _docker_updates_payload()


@app.get("/api/v1/hosts")
def hosts(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return _hosts_payload()


@app.post("/api/v1/hosts")
def create_host(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    host_name, group_name, host_data = _normalize_host_payload(payload)
    if site.find_host(host_name) is not None:
        raise HTTPException(status_code=409, detail=f"host {host_name} already exists")
    site.upsert_host("", host_name, group_name, host_data)
    return _hosts_payload()


@app.put("/api/v1/hosts/{host_name}")
def update_host(host_name: str, payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    existing = site.find_host(host_name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"host {host_name} was not found")
    next_name, group_name, host_data = _normalize_host_payload(payload, existing=existing)
    if next_name != host_name and site.find_host(next_name) is not None:
        raise HTTPException(status_code=409, detail=f"host {next_name} already exists")
    try:
        site.upsert_host(host_name, next_name, group_name, host_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _hosts_payload()


@app.delete("/api/v1/hosts/{host_name}")
def delete_host(host_name: str, username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    if not site.delete_host(host_name):
        raise HTTPException(status_code=404, detail=f"host {host_name} was not found")
    return _hosts_payload()


@app.get("/api/v1/agents")
def agents(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    public_settings = runtime_settings.get_public_settings()
    items = [
        agent_records.with_effective_status(item)
        for item in db.fetch_all(
            """
            SELECT id, name, display_name, transport, platform, version, capabilities, labels,
                   metadata, status, last_seen_at, created_at, updated_at
            FROM agents
            ORDER BY name
            """
        )
    ]
    release_payload = releases.build_release_status(public_settings, items)
    latest_version = str((release_payload.get("latest") or {}).get("version") or "")
    latest_ref = latest_version or str(public_settings.get("repo_ref") or "")
    agent_items = []
    for item in items:
        metadata = item.get("metadata") or {}
        mode = str(metadata.get("mode") or "unknown")
        release_state = releases.compare_versions(str(item.get("version") or ""), latest_version)
        update_command = ""
        if mode in {"compose", "container", "systemd"}:
            update_command = control_plane.build_agent_update_command(
                public_settings,
                latest_ref,
                mode,
                compose_dir=str(metadata.get("compose_dir") or ""),
                install_dir=str(metadata.get("install_dir") or ""),
            )
        agent_items.append(
            {
                **item,
                "release_state": release_state,
                "update_mode": mode,
                "update_command": update_command,
            }
        )
    return {"items": agent_items}


@app.get("/api/v1/jobs")
def list_jobs(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    items = db.fetch_all(
        """
        SELECT id, kind, status, source, target_type, target_ref, executor, payload,
               result, requested_by, requires_approval, approval_status, approved_by,
               target_agent_id, created_at, queued_at, started_at, finished_at
        FROM jobs
        ORDER BY created_at DESC
        LIMIT 200
        """
    )
    return {
        "items": [_job_details(item) for item in items]
    }


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    row = db.fetch_one("SELECT * FROM jobs WHERE id = %s", (job_id,))
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_details(row)


@app.get("/api/v1/jobs/{job_id}/events")
def job_events(job_id: str, username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return {
        "items": db.fetch_all(
            "SELECT id, ts, stream, message FROM job_events WHERE job_id = %s ORDER BY id ASC",
            (job_id,),
        )
    }


@app.post("/api/v1/jobs")
def create_job(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    kind = str(payload.get("kind", "")).strip()
    target_type = str(payload.get("target_type", "")).strip()
    target_ref = str(payload.get("target_ref", "")).strip()
    if not all([kind, target_type, target_ref]):
        raise HTTPException(status_code=400, detail="kind, target_type, and target_ref are required")
    try:
        row = jobs.create_job(
            kind=kind,
            target_type=target_type,
            target_ref=target_ref,
            payload=_json_body(payload.get("payload")),
            requested_by=username,
            source="ui",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return row


@app.post("/api/v1/jobs/{job_id}/approve")
def approve(job_id: str, username: str = Depends(auth.require_user)) -> dict[str, Any]:
    row = jobs.approve_job(job_id, username)
    if not row:
        raise HTTPException(status_code=404, detail="approval not found or already resolved")
    return row


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel(job_id: str, username: str = Depends(auth.require_user)) -> dict[str, Any]:
    row = jobs.cancel_job(job_id, username)
    if not row:
        raise HTTPException(status_code=409, detail="job cannot be cancelled in its current state")
    return row


@app.delete("/api/v1/jobs/{job_id}")
def delete_job(job_id: str, username: str = Depends(auth.require_user)) -> dict[str, Any]:
    row, reason = jobs.delete_job(job_id, username)
    if not row:
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="job not found")
        raise HTTPException(
            status_code=409,
            detail="job cannot be deleted unless it is completed, failed, or cancelled",
        )
    return _job_details(row)


@app.get("/api/v1/backups")
def list_backups(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    items = db.fetch_all(
        "SELECT id, job_id, kind, target_ref, path, metadata, created_at FROM backups ORDER BY created_at DESC"
    )
    return {
        "items": [{**item, **_backup_details(item)} for item in items]
    }


@app.delete("/api/v1/backups/{backup_id}")
def delete_backup(backup_id: str, username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    row = db.fetch_one(
        "SELECT id, job_id, kind, target_ref, path, metadata, created_at FROM backups WHERE id = %s",
        (backup_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="backup not found")

    details = _backup_details(row)
    resolved_path = str(details.get("resolved_path") or "")
    file_deleted = False
    delete_reason = ""

    if resolved_path and details.get("delete_supported"):
        other_rows = db.fetch_all(
            "SELECT id, job_id, kind, target_ref, path, metadata, created_at FROM backups WHERE id <> %s",
            (backup_id,),
        )
        shared_reference = any(_backup_details(item).get("resolved_path") == resolved_path for item in other_rows)
        if shared_reference:
            delete_reason = "file is still referenced by another backup record"
        else:
            try:
                Path(resolved_path).unlink()
                file_deleted = True
            except FileNotFoundError:
                delete_reason = "artifact file was already missing"
    elif resolved_path:
        delete_reason = "artifact file is outside the managed backups directory"
    else:
        delete_reason = "artifact file path is unavailable"

    with db.db_cursor() as cur:
        cur.execute("DELETE FROM backups WHERE id = %s RETURNING id", (backup_id,))
        deleted = cur.fetchone()
    if not deleted:
        raise HTTPException(status_code=404, detail="backup not found")

    return {
        "id": backup_id,
        "file_deleted": file_deleted,
        "delete_reason": delete_reason,
        "resolved_path": resolved_path,
    }


@app.get("/api/v1/schedules")
def list_schedules(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return {
        "items": db.fetch_all(
            """
            SELECT id, name, kind, cron_expr, timezone, payload, enabled, next_run_at, last_run_at, created_at, updated_at
            FROM schedules
            ORDER BY name
            """
        )
    }


@app.post("/api/v1/schedules")
def upsert_schedule(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    name = str(payload.get("name", "")).strip()
    kind = str(payload.get("kind", "")).strip()
    cron_expr = str(payload.get("cron_expr", "")).strip()
    if not all([name, kind, cron_expr]):
        raise HTTPException(status_code=400, detail="name, kind, and cron_expr are required")
    enabled = bool(payload.get("enabled", False))
    schedule_payload, timezone_name = _schedule_payload_fields(payload)
    with db.db_cursor() as cur:
        cur.execute(
            """
            SELECT id, kind, cron_expr, timezone, payload, next_run_at
            FROM schedules
            WHERE name = %s
            """,
            (name,),
        )
        existing = cur.fetchone()
        definition_changed = (
            existing is None
            or existing["kind"] != kind
            or existing["cron_expr"] != cron_expr
            or site.schedule_timezone_name(existing.get("timezone")) != timezone_name
            or (existing["payload"] or {}) != schedule_payload
        )
        next_run_at = (
            site.schedule_next_run(cron_expr, timezone_name=timezone_name)
            if definition_changed or existing is None or existing.get("next_run_at") is None
            else existing["next_run_at"]
        )
        cur.execute(
            """
            INSERT INTO schedules (name, kind, cron_expr, timezone, payload, enabled, next_run_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (name) DO UPDATE SET
              kind = EXCLUDED.kind,
              cron_expr = EXCLUDED.cron_expr,
              timezone = EXCLUDED.timezone,
              payload = EXCLUDED.payload,
              enabled = EXCLUDED.enabled,
              next_run_at = EXCLUDED.next_run_at,
              updated_at = NOW()
            RETURNING *
            """,
            (name, kind, cron_expr, timezone_name, json.dumps(schedule_payload), enabled, next_run_at),
        )
        row = cur.fetchone()
    return row


@app.post("/api/v1/schedules/{schedule_id}/toggle")
def toggle_schedule(schedule_id: str, payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    enabled = bool(payload.get("enabled", False))
    with db.db_cursor() as cur:
        if enabled:
            cur.execute("SELECT cron_expr, timezone, next_run_at FROM schedules WHERE id = %s", (schedule_id,))
            current = cur.fetchone()
            if current is None:
                row = None
            else:
                next_run_at = current["next_run_at"]
                if next_run_at is None or next_run_at <= datetime.now(timezone.utc):
                    next_run_at = site.schedule_next_run(
                        current["cron_expr"],
                        timezone_name=current.get("timezone"),
                    )
                cur.execute(
                    """
                    UPDATE schedules
                    SET enabled = %s,
                        next_run_at = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (enabled, next_run_at, schedule_id),
                )
                row = cur.fetchone()
        else:
            cur.execute(
                "UPDATE schedules SET enabled = %s, updated_at = NOW() WHERE id = %s RETURNING *",
                (enabled, schedule_id),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="schedule not found")
    return row


@app.get("/api/v1/settings")
def settings(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return _settings_payload()


@app.get("/api/v1/context")
def context(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    settings_payload = _settings_payload()
    return {
        "settings": settings_payload,
        "release": settings_payload["release"],
        "job_kinds": job_catalog.list_job_kinds(),
        "api": control_plane.build_api_surface(settings_payload["public"]),
        "jobs": {
            "running": _job_summary_rows("WHERE status = 'running'"),
            "pending_approvals": _job_summary_rows("WHERE approval_status = 'pending'"),
            "recent": _job_summary_rows(limit=12),
        },
    }


@app.post("/api/v1/settings/public")
def update_public_settings(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    runtime_settings.set_public_settings(payload)
    return _settings_payload()


@app.post("/api/v1/settings/docker-updates")
def update_docker_update_settings(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    runtime_settings.set_docker_update_settings(payload)
    return _settings_payload()


@app.post("/api/v1/settings/telegram")
def update_telegram_settings(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    runtime_settings.set_telegram_settings(payload)
    return _settings_payload()


@app.post("/api/v1/settings/agent-tokens")
def create_agent_token(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    label = str(payload.get("label", "manual-token")).strip()
    token = auth.random_token("rackpatch-agent-")
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO agent_tokens (label, token_hash) VALUES (%s, %s) RETURNING id, label, created_at",
            (label, auth.hash_token(token)),
        )
        row = cur.fetchone()
    row["token"] = token
    row["agent_install"] = control_plane.build_agent_install_commands(runtime_settings.get_public_settings(), token)
    return row


@app.post("/api/v1/agents/register")
def register_agent(
    payload: dict[str, Any],
    x_rackpatch_agent_token: str | None = Header(default=None),
) -> dict[str, Any]:
    token = x_rackpatch_agent_token or str(payload.get("bootstrap_token", ""))
    token_hash = auth.hash_token(token)
    token_row = db.fetch_one(
        "SELECT id FROM agent_tokens WHERE token_hash = %s AND revoked_at IS NULL",
        (token_hash,),
    )
    if not token_row:
        raise HTTPException(status_code=401, detail="invalid bootstrap token")

    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="agent name is required")

    display_name = str(payload.get("display_name", name))
    transport = str(payload.get("transport", "poll"))
    platform_name = str(payload.get("platform", "linux"))
    version = str(payload.get("version", "unknown"))
    capabilities_json = json.dumps(payload.get("capabilities", []))
    labels_json = json.dumps(payload.get("labels", []))
    metadata = _json_body(payload.get("metadata"))
    metadata_json = json.dumps(metadata)
    secret = auth.random_token("rackpatch-secret-")
    with db.db_cursor() as cur:
        row = None
        cur.execute("SELECT id FROM agents WHERE name = %s", (name,))
        if cur.fetchone() is None:
            reusable = _reusable_agent_row(cur, name=name, metadata=metadata)
            if reusable is not None:
                cur.execute(
                    """
                    UPDATE agents
                    SET name = %s,
                        display_name = %s,
                        secret_hash = %s,
                        transport = %s,
                        platform = %s,
                        version = %s,
                        capabilities = %s,
                        labels = %s,
                        metadata = %s,
                        updated_at = NOW(),
                        last_seen_at = NOW(),
                        status = 'online'
                    WHERE id = %s
                    RETURNING id, name, display_name, transport, platform, version, capabilities, labels, metadata
                    """,
                    (
                        name,
                        display_name,
                        auth.hash_token(secret),
                        transport,
                        platform_name,
                        version,
                        capabilities_json,
                        labels_json,
                        metadata_json,
                        reusable["id"],
                    ),
                )
                row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO agents (name, display_name, secret_hash, transport, platform, version, capabilities, labels, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                  display_name = EXCLUDED.display_name,
                  secret_hash = EXCLUDED.secret_hash,
                  transport = EXCLUDED.transport,
                  platform = EXCLUDED.platform,
                  version = EXCLUDED.version,
                  capabilities = EXCLUDED.capabilities,
                  labels = EXCLUDED.labels,
                  metadata = EXCLUDED.metadata,
                  updated_at = NOW(),
                  last_seen_at = NOW(),
                  status = 'online'
                RETURNING id, name, display_name, transport, platform, version, capabilities, labels, metadata
                """,
                (
                    name,
                    display_name,
                    auth.hash_token(secret),
                    transport,
                    platform_name,
                    version,
                    capabilities_json,
                    labels_json,
                    metadata_json,
                ),
            )
            row = cur.fetchone()
        _prune_stale_agent_duplicates(cur, current_id=str(row["id"]), name=name, metadata=metadata)
        cur.execute(
            "UPDATE agent_tokens SET last_used_at = NOW() WHERE id = %s",
            (token_row["id"],),
        )
    return {
        **row,
        "agent_secret": secret,
        "poll_seconds": config.AGENT_POLL_SECONDS,
    }


@app.post("/api/v1/agents/heartbeat")
def heartbeat(
    payload: dict[str, Any],
    x_rackpatch_agent_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", ""))
    _validate_agent_secret(agent_id, x_rackpatch_agent_secret)
    with db.db_cursor() as cur:
        cur.execute(
            """
            UPDATE agents
            SET last_seen_at = NOW(),
                status = 'online',
                version = COALESCE(%s, version),
                capabilities = COALESCE(%s::jsonb, capabilities),
                metadata = COALESCE(%s::jsonb, metadata),
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, name, last_seen_at, status
            """,
            (
                str(payload.get("version")) if payload.get("version") is not None else None,
                json.dumps(payload.get("capabilities")) if payload.get("capabilities") is not None else None,
                json.dumps(payload.get("metadata")) if payload.get("metadata") is not None else None,
                agent_id,
            ),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="agent not found")
    return row


@app.post("/api/v1/agents/claim")
def claim(
    payload: dict[str, Any],
    x_rackpatch_agent_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", ""))
    _validate_agent_secret(agent_id, x_rackpatch_agent_secret)
    with db.db_cursor() as cur:
        cur.execute(
            """
            WITH candidate AS (
              SELECT id
              FROM jobs
              WHERE executor = 'agent'
                AND target_agent_id = %s
                AND status = 'queued'
                AND approval_status <> 'pending'
              ORDER BY created_at ASC
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            UPDATE jobs
            SET status = 'running', started_at = NOW()
            WHERE id IN (SELECT id FROM candidate)
            RETURNING id, kind, payload, target_type, target_ref, created_at
            """,
            (agent_id,),
        )
        row = cur.fetchone()
    if row:
        jobs.append_event(str(row["id"]), f"[{datetime.now(timezone.utc).isoformat()}] agent claimed job {agent_id}")
        return {"job": row}
    return {"job": None}


@app.post("/api/v1/jobs/{job_id}/events")
def post_job_event(
    job_id: str,
    payload: dict[str, Any],
    x_rackpatch_agent_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", ""))
    _validate_agent_secret(agent_id, x_rackpatch_agent_secret)
    row = db.fetch_one("SELECT target_agent_id FROM jobs WHERE id = %s", (job_id,))
    if not row or str(row["target_agent_id"]) != agent_id:
        raise HTTPException(status_code=403, detail="job is not owned by this agent")
    jobs.append_event(job_id, str(payload.get("message", "")), stream=str(payload.get("stream", "stdout")))
    return {"status": "ok"}


@app.post("/api/v1/jobs/{job_id}/complete")
def complete_job(
    job_id: str,
    payload: dict[str, Any],
    x_rackpatch_agent_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", ""))
    _validate_agent_secret(agent_id, x_rackpatch_agent_secret)
    row = db.fetch_one("SELECT target_agent_id FROM jobs WHERE id = %s", (job_id,))
    if not row or str(row["target_agent_id"]) != agent_id:
        raise HTTPException(status_code=403, detail="job is not owned by this agent")

    status = str(payload.get("status", "completed"))
    result = _json_body(payload.get("result"))
    jobs.set_job_status(job_id, status, result=result)
    for artifact in result.get("artifacts", []):
        jobs.record_backup(
            job_id=job_id,
            kind=artifact.get("kind", "artifact"),
            target_ref=artifact.get("target_ref", ""),
            path=artifact.get("path", ""),
            metadata=artifact,
        )
    pruned_artifacts = result.get("pruned_artifacts") or []
    if isinstance(pruned_artifacts, list):
        jobs.remove_recorded_backups([item for item in pruned_artifacts if isinstance(item, dict)])
    job = db.fetch_one("SELECT * FROM jobs WHERE id = %s", (job_id,))
    if job:
        notify.send_job_event(job, status, result)
    return {"status": "ok"}
