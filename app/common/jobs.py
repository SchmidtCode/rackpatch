from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from common import agents as agent_records, config, control_plane, db, job_catalog, notify, releases, runtime_settings, site, stack_catalog


PACKAGE_JOB_KINDS = {"package_check", "package_patch"}
PROXMOX_JOB_KINDS = {"proxmox_patch", "proxmox_reboot"}
AGENT_UPDATE_JOB_KINDS = {"agent_update"}
DOCKER_STACK_JOB_KINDS = {"docker_check", "docker_update"}
HOST_HELPER_ACTION_CAPABILITIES = {
    "package_check": "host-package-check",
    "package_patch": "host-package-patch",
    "proxmox_patch": "host-proxmox-patch",
    "proxmox_reboot": "host-proxmox-reboot",
}
AGENT_JOB_KINDS = PACKAGE_JOB_KINDS | PROXMOX_JOB_KINDS | AGENT_UPDATE_JOB_KINDS | DOCKER_STACK_JOB_KINDS
APPROVAL_REQUIRED = {"docker_update", "package_patch", "proxmox_patch", "proxmox_reboot", "rollback"}
VALID_EXECUTORS = {"worker", "agent", "auto"}
CANCELLABLE_STATUSES = {"queued", "pending_approval"}
DELETABLE_STATUSES = {"completed", "failed", "cancelled"}
AGENT_JOB_CAPABILITIES = {
    "package_check": {"host-package-check"},
    "package_patch": {"host-package-patch"},
    "proxmox_patch": {"host-proxmox-patch"},
    "proxmox_reboot": {"host-proxmox-reboot"},
    "docker_check": {"docker-stack-inspect"},
    "docker_update": {"docker"},
    "agent_update": {"agent-self-update"},
}
RETIRED_WORKER_CONTROL_JOB_KINDS = {
    "docker_discover",
    "docker_update",
    "snapshot",
    "proxmox_patch",
    "proxmox_reboot",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_event(job_id: str, message: str, stream: str = "stdout") -> None:
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO job_events (job_id, stream, message) VALUES (%s, %s, %s)",
            (job_id, stream, message.rstrip("\n")),
        )


def job_is_deletable(status: str | None) -> bool:
    return str(status or "").strip().lower() in DELETABLE_STATUSES


def _delete_job_artifacts(job_id: str, artifact_dir: str | None = None) -> None:
    jobs_root = config.JOBS_ROOT.resolve(strict=False)
    candidates = [config.JOBS_ROOT / str(job_id)]
    if artifact_dir:
        candidates.append(Path(str(artifact_dir)))

    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved == jobs_root:
            continue
        try:
            resolved.relative_to(jobs_root)
        except ValueError:
            continue
        if resolved.exists():
            if resolved.is_dir():
                shutil.rmtree(resolved, ignore_errors=True)
            else:
                resolved.unlink(missing_ok=True)


def set_job_status(job_id: str, status: str, result: dict[str, Any] | None = None) -> None:
    result = result or {}
    timestamps = {
        "queued": "queued_at",
        "running": "started_at",
        "completed": "finished_at",
        "failed": "finished_at",
        "cancelled": "finished_at",
    }
    field = timestamps.get(status)
    with db.db_cursor() as cur:
        if field:
            cur.execute(
                f"UPDATE jobs SET status = %s, result = %s, {field} = NOW() WHERE id = %s",
                (status, json.dumps(result), job_id),
            )
        else:
            cur.execute(
                "UPDATE jobs SET status = %s, result = %s WHERE id = %s",
                (status, json.dumps(result), job_id),
            )


def record_backup(job_id: str | None, kind: str, target_ref: str, path: str, metadata: dict[str, Any]) -> None:
    with db.db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO backups (job_id, kind, target_ref, path, metadata)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (job_id, kind, target_ref, path, json.dumps(metadata)),
        )


def remove_recorded_backups(artifacts: list[dict[str, Any]]) -> None:
    if not artifacts:
        return
    with db.db_cursor() as cur:
        for artifact in artifacts:
            path = str(artifact.get("path") or "").strip()
            if not path:
                continue
            cur.execute(
                "DELETE FROM backups WHERE kind = %s AND target_ref = %s AND path = %s",
                (
                    str(artifact.get("kind") or "artifact"),
                    str(artifact.get("target_ref") or ""),
                    path,
                ),
            )


def resolve_agent_id(target_type: str, target_ref: str, payload: dict[str, Any]) -> str | None:
    if payload.get("target_agent_id"):
        return str(payload["target_agent_id"])

    if target_type == "agent":
        row = db.fetch_one("SELECT id FROM agents WHERE id::text = %s OR name = %s", (target_ref, target_ref))
        if row:
            return str(row["id"])
        return None

    host_name = target_ref
    if target_type == "stack":
        stack = site.find_stack(target_ref)
        host_name = stack_catalog.stack_runtime_host(stack) if stack else target_ref

    row = db.fetch_one("SELECT id FROM agents WHERE name = %s", (host_name,))
    if row:
        return str(row["id"])
    return None


def _agent_row(agent_id: str | None) -> dict[str, Any] | None:
    if not agent_id:
        return None
    row = db.fetch_one("SELECT capabilities, metadata, status, last_seen_at FROM agents WHERE id = %s", (agent_id,))
    if not row:
        return None
    return agent_records.with_effective_status(row)


def _agent_capabilities_from_row(row: dict[str, Any] | None) -> set[str]:
    values = (row or {}).get("capabilities") or []
    selected = {str(value) for value in values if str(value).strip()}
    metadata = (row or {}).get("metadata") or {}
    metadata_capabilities = metadata.get("capabilities") or []
    selected.update(str(value) for value in metadata_capabilities if str(value).strip())
    host_maintenance = metadata.get("host_maintenance") or {}
    actions = {str(value) for value in host_maintenance.get("actions", []) if str(value).strip()}
    for action, capability in HOST_HELPER_ACTION_CAPABILITIES.items():
        if action in actions:
            selected.add(capability)
    return selected


def _agent_capabilities(agent_id: str | None) -> set[str]:
    return _agent_capabilities_from_row(_agent_row(agent_id))


def _site_host(target_ref: str) -> dict[str, Any] | None:
    for host in site.load_hosts():
        if str(host.get("name")) == target_ref:
            return host
    return None


def _dedupe(values: list[str]) -> list[str]:
    selected: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in selected:
            selected.append(item)
    return selected


def _normalize_dir(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _split_targets(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values: list[str] = []
        for item in raw:
            values.extend(_split_targets(item))
        return values
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _package_scope_hosts(selector: str) -> list[str]:
    value = str(selector).strip()
    if not value:
        return []
    if value == "all":
        hosts = _dedupe(site.group_hosts("guests") + site.group_hosts("docker_hosts"))
        if hosts:
            return hosts
        return [host["name"] for host in site.load_hosts() if str(host.get("group")) != "proxmox_nodes"]
    if value in {"guests", "docker_hosts"}:
        return site.group_hosts(value)
    return [value]


def _resolve_package_targets(kind: str, target_ref: str, payload: dict[str, Any]) -> list[str]:
    selectors: list[str] = []
    if kind == "package_check":
        selectors.extend(_split_targets(payload.get("hosts")))
        if not selectors:
            selectors.extend(_split_targets(payload.get("scope")))
    if kind == "package_patch":
        selectors.extend(_split_targets(payload.get("limit")))
    if not selectors:
        selectors.extend(_split_targets(target_ref))

    selected: list[str] = []
    for selector in selectors:
        for host_name in _package_scope_hosts(selector):
            if host_name not in selected:
                selected.append(host_name)
    return selected


def _proxmox_scope_hosts(selector: str) -> list[str]:
    value = str(selector).strip()
    if not value:
        return []
    if value in {"all", "proxmox_nodes"}:
        return site.group_hosts("proxmox_nodes")
    return [value]


def _resolve_proxmox_targets(target_ref: str, payload: dict[str, Any]) -> list[str]:
    selectors = _split_targets(payload.get("limit"))
    if not selectors:
        selectors = _split_targets(target_ref)

    selected: list[str] = []
    for selector in selectors:
        for host_name in _proxmox_scope_hosts(selector):
            if host_name not in selected:
                selected.append(host_name)
    return selected


def _resolve_docker_update_targets(target_ref: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    requested = _dedupe(_split_targets(payload.get("selected_stacks")))
    stacks = site.load_stacks()

    if not requested and target_ref not in {"", "all", "full-stack-catalog"}:
        requested = _dedupe(_split_targets(target_ref))

    if requested:
        index = {str(stack.get("name")): stack for stack in stacks if str(stack.get("name") or "").strip()}
        return [index[name] for name in requested if name in index]

    window = str(payload.get("window") or "all").strip() or "all"
    if window == "all":
        return stacks
    return [stack for stack in stacks if str(stack.get("update_mode") or "") == window]


def _docker_update_release_target(public_settings: dict[str, Any]) -> tuple[str, str]:
    latest = releases.fetch_latest_release(str(public_settings.get("repo_url") or config.PUBLIC_REPO_URL))
    latest_version = str(latest.get("version") or "").strip()
    target_ref = latest_version or str(public_settings.get("repo_ref") or config.PUBLIC_REPO_REF)
    target_version = latest_version or target_ref
    return target_ref, target_version


def _rackpatch_stack_update_fields(stack: dict[str, Any], public_settings: dict[str, Any]) -> dict[str, Any]:
    project_dir = _normalize_dir(stack_catalog.stack_project_dir(stack))
    rackpatch_compose_dir = _normalize_dir(
        public_settings.get("rackpatch_compose_dir") or config.PUBLIC_RACKPATCH_COMPOSE_DIR
    )
    if not project_dir or not rackpatch_compose_dir or project_dir != rackpatch_compose_dir:
        return {}

    release_ref, target_version = _docker_update_release_target(public_settings)
    return {
        "rackpatch_managed": True,
        "repo_url": str(public_settings.get("repo_url") or config.PUBLIC_REPO_URL).strip(),
        "release_ref": release_ref,
        "target_version": target_version,
    }


def _resolve_agent_update_targets(target_ref: str, payload: dict[str, Any]) -> list[str]:
    requested = _dedupe(_split_targets(payload.get("selected_agents")))
    if not requested and target_ref not in {"", "all"}:
        requested = _dedupe(_split_targets(target_ref))
    return requested


def _current_maintenance_hour() -> int:
    timezone_name = str(site.load_group_vars().get("maintenance_timezone") or "UTC")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return datetime.now(zone).hour


def _package_job_blocker(kind: str, target_ref: str, payload: dict[str, Any]) -> str | None:
    host = _site_host(target_ref)
    if not host:
        return f"{target_ref} is not present in inventory."
    if str(host.get("group")) == "proxmox_nodes":
        return "Use the helper-backed Proxmox patch or Proxmox reboot actions for proxmox_nodes."
    if kind != "package_patch":
        return None
    allow_manual = bool(payload.get("allow_manual_guests", False))
    if str(host.get("guest_patch_policy", "managed")) == "manual" and not allow_manual:
        return "Host patch policy is manual, so helper-driven package patching is disabled."
    if not bool(payload.get("dry_run", False)) and str(host.get("snapshot_class", "none")) != "none":
        return "Host requires a pre-patch snapshot, which is not available through the limited host-maintenance helper."
    allow_dns_anytime = bool(payload.get("force_dns_critical") or payload.get("allow_dns_critical_anytime"))
    if bool(host.get("dns_critical")) and not bool(payload.get("dry_run", False)) and not allow_dns_anytime:
        if _current_maintenance_hour() >= 7:
            return "DNS-critical host patching is restricted to the early-morning maintenance window."
    return None


def _proxmox_job_blocker(kind: str, target_ref: str, payload: dict[str, Any]) -> str | None:
    host = _site_host(target_ref)
    if not host:
        return f"{target_ref} is not present in inventory."
    if str(host.get("group")) != "proxmox_nodes":
        return f"{kind} is only available for proxmox_nodes."
    if kind == "proxmox_reboot":
        reboot_mode = str(payload.get("reboot_mode") or "soft").strip() or "soft"
        if reboot_mode not in {"soft", "hard"}:
            return "reboot_mode must be soft or hard."
    return None


def _docker_stack_blocker(kind: str, target_ref: str, payload: dict[str, Any]) -> str | None:
    stack = site.find_stack(target_ref)
    if not stack:
        return f"{target_ref} is not present in the stack catalog."

    project_dir = stack_catalog.stack_project_dir(stack)
    if not project_dir:
        return f"{target_ref} is missing path or project_dir."

    if kind == "docker_check" or bool(payload.get("dry_run", False)):
        return None
    return None


def _rollback_blocker(target_ref: str) -> str | None:
    stack = site.find_stack(target_ref)
    if not stack:
        return f"{target_ref} is not present in the stack catalog."
    host_name = str(stack.get("host") or "localhost").strip()
    if host_name in {"", "localhost", "127.0.0.1"}:
        return None
    host = _site_host(host_name)
    if host and (bool(host.get("rackpatch_control_plane")) or bool(host.get("control_plane"))):
        return None
    return "Remote rollback is not supported in the agent-first runtime."


def _job_blocker(kind: str, target_ref: str, payload: dict[str, Any]) -> str | None:
    if kind in PACKAGE_JOB_KINDS:
        return _package_job_blocker(kind, target_ref, payload)
    if kind in PROXMOX_JOB_KINDS:
        return _proxmox_job_blocker(kind, target_ref, payload)
    if kind in DOCKER_STACK_JOB_KINDS:
        return _docker_stack_blocker(kind, target_ref, payload)
    return None


def _agent_capability_error(kind: str, target_ref: str, payload: dict[str, Any], agent_id: str | None) -> str | None:
    if kind not in AGENT_JOB_KINDS:
        return None
    blocker = _job_blocker(kind, target_ref, payload)
    if blocker:
        return blocker
    if not agent_id:
        return f"No enrolled agent found for {target_ref}."
    row = _agent_row(agent_id)
    if not row:
        return f"No enrolled agent found for {target_ref}."
    if str(row.get("status") or "").lower() != "online":
        return f"Agent for {target_ref} is offline."
    if kind in DOCKER_STACK_JOB_KINDS:
        project_dir = str(payload.get("project_dir") or "").strip()
        if not project_dir:
            stack = site.find_stack(target_ref)
            project_dir = stack_catalog.stack_project_dir(stack)
        access_error = agent_records.project_dir_access_reason(row, project_dir)
        if access_error:
            return access_error
    capabilities = _agent_capabilities_from_row(row)
    required = AGENT_JOB_CAPABILITIES.get(kind, set())
    if required.issubset(capabilities):
        return None
    capability_label = ", ".join(sorted(required))
    return f"Agent for {target_ref} does not advertise {capability_label}."


def _agent_can_run_job(kind: str, target_ref: str, payload: dict[str, Any], agent_id: str | None) -> bool:
    if not agent_id or kind not in AGENT_JOB_KINDS:
        return False
    if "," in target_ref:
        return False
    if _agent_capability_error(kind, target_ref, payload, agent_id):
        return False
    return True


def host_job_access(kind: str, target_ref: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    effective_payload = dict(payload or {})
    agent_id = resolve_agent_id("host", target_ref, effective_payload)
    error = _agent_capability_error(kind, target_ref, effective_payload, agent_id)
    return {
        "eligible": error is None,
        "reason": "" if error is None else error,
        "required_capabilities": sorted(AGENT_JOB_CAPABILITIES.get(kind, set())),
        "target_agent_id": agent_id,
    }


def stack_job_access(kind: str, target_ref: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    effective_payload = dict(payload or {})
    agent_id = resolve_agent_id("stack", target_ref, effective_payload)
    error = _agent_capability_error(kind, target_ref, effective_payload, agent_id)
    return {
        "eligible": error is None,
        "reason": "" if error is None else error,
        "required_capabilities": sorted(AGENT_JOB_CAPABILITIES.get(kind, set())),
        "target_agent_id": agent_id,
    }


def _insert_job(
    *,
    kind: str,
    status: str,
    source: str,
    target_type: str,
    target_ref: str,
    executor: str,
    payload: dict[str, Any],
    requested_by: str,
    requires_approval: bool,
    approval_status: str,
    target_agent_id: str | None,
) -> dict[str, Any]:
    with db.db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs (
              kind, status, source, target_type, target_ref, executor, site_name, payload,
              requested_by, requires_approval, approval_status, target_agent_id, queued_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'queued' THEN NOW() ELSE NULL END)
            RETURNING *
            """,
            (
                kind,
                status,
                source,
                target_type,
                target_ref,
                executor,
                config.SITE_NAME,
                json.dumps(payload),
                requested_by,
                requires_approval,
                approval_status,
                target_agent_id,
                status,
            ),
        )
        job = cur.fetchone()
    append_event(str(job["id"]), f"[{now_iso()}] job created kind={kind} executor={executor} target={target_ref}")
    if requires_approval:
        notify.send_job_event(job, "pending")
    return job


def _fanout_summary(
    kind: str,
    target_type: str,
    target_ref: str,
    queued_jobs: list[dict[str, Any]],
    skipped: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "target_type": target_type,
        "target_ref": target_ref,
        "executor": "agent",
        "fanout": True,
        "queued_count": len(queued_jobs),
        "job_ids": [str(job["id"]) for job in queued_jobs],
        "jobs": [
            {
                "id": str(job["id"]),
                "target_ref": str(job["target_ref"]),
                "status": str(job["status"]),
                "approval_status": str(job["approval_status"]),
            }
            for job in queued_jobs
        ],
        "skipped": skipped,
    }


def _create_package_jobs(
    kind: str,
    target_type: str,
    target_ref: str,
    payload: dict[str, Any],
    requested_by: str,
    source: str,
) -> dict[str, Any]:
    executor = str(payload.get("executor", "agent")).strip() or "agent"
    if executor == "worker":
        raise ValueError(f"{kind} no longer supports the worker executor; enable the limited host-maintenance helper instead")
    if executor not in VALID_EXECUTORS:
        allowed = ", ".join(sorted(VALID_EXECUTORS))
        raise ValueError(f"invalid executor {executor!r}; expected one of: {allowed}")

    targets = _resolve_package_targets(kind, target_ref, payload)
    if not targets:
        raise ValueError("no package-maintenance hosts were selected")

    requires_approval = bool(payload.get("requires_approval", kind in APPROVAL_REQUIRED))
    status = "pending_approval" if requires_approval else "queued"
    approval_status = "pending" if requires_approval else "not_required"
    queued_jobs: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for host_name in targets:
        child_payload = deepcopy(payload)
        child_payload.pop("target_agent_id", None)
        child_payload.pop("scope", None)
        if kind == "package_check":
            child_payload["hosts"] = [host_name]
        else:
            child_payload["limit"] = host_name
        agent_id = resolve_agent_id(target_type, host_name, child_payload)
        error = _agent_capability_error(kind, host_name, child_payload, agent_id)
        if error:
            skipped.append({"target_ref": host_name, "reason": error})
            continue
        queued_jobs.append(
            _insert_job(
                kind=kind,
                status=status,
                source=source,
                target_type=target_type,
                target_ref=host_name,
                executor="agent",
                payload=child_payload,
                requested_by=requested_by,
                requires_approval=requires_approval,
                approval_status=approval_status,
                target_agent_id=agent_id,
            )
        )

    if not queued_jobs:
        reasons = "; ".join(f"{item['target_ref']}: {item['reason']}" for item in skipped[:4])
        raise ValueError(reasons or f"no hosts were eligible for {kind}")
    if len(queued_jobs) == 1 and not skipped:
        return queued_jobs[0]
    return _fanout_summary(kind, target_type, target_ref, queued_jobs, skipped)


def _create_proxmox_jobs(
    kind: str,
    target_type: str,
    target_ref: str,
    payload: dict[str, Any],
    requested_by: str,
    source: str,
) -> dict[str, Any]:
    executor = str(payload.get("executor", "agent")).strip() or "agent"
    if executor == "worker":
        raise ValueError(f"{kind} no longer supports the worker executor; enable the matching Proxmox helper actions instead")
    if executor not in VALID_EXECUTORS:
        allowed = ", ".join(sorted(VALID_EXECUTORS))
        raise ValueError(f"invalid executor {executor!r}; expected one of: {allowed}")

    targets = _resolve_proxmox_targets(target_ref, payload)
    if not targets:
        raise ValueError("no Proxmox nodes were selected")

    requires_approval = bool(payload.get("requires_approval", kind in APPROVAL_REQUIRED))
    if not bool(payload.get("dry_run", False)) and len(targets) > 1 and not requires_approval:
        raise ValueError(
            "Live Proxmox patch and reboot across multiple nodes must stay approval-gated or be queued one node at a time."
        )
    status = "pending_approval" if requires_approval else "queued"
    approval_status = "pending" if requires_approval else "not_required"
    queued_jobs: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for host_name in targets:
        host = _site_host(host_name) or {}
        child_payload = deepcopy(payload)
        child_payload.pop("target_agent_id", None)
        child_payload["limit"] = host_name
        child_payload["proxmox_node_name"] = str(host.get("proxmox_node_name") or host_name)
        child_payload["guest_order"] = [
            str(item)
            for item in (host.get("soft_reboot_guest_order") or host.get("guest_ids") or [])
            if str(item).strip()
        ]
        if kind == "proxmox_reboot":
            child_payload["reboot_mode"] = str(payload.get("reboot_mode") or "soft").strip() or "soft"
        agent_id = resolve_agent_id(target_type, host_name, child_payload)
        error = _agent_capability_error(kind, host_name, child_payload, agent_id)
        if error:
            skipped.append({"target_ref": host_name, "reason": error})
            continue
        queued_jobs.append(
            _insert_job(
                kind=kind,
                status=status,
                source=source,
                target_type=target_type,
                target_ref=host_name,
                executor="agent",
                payload=child_payload,
                requested_by=requested_by,
                requires_approval=requires_approval,
                approval_status=approval_status,
                target_agent_id=agent_id,
            )
        )

    if not queued_jobs:
        reasons = "; ".join(f"{item['target_ref']}: {item['reason']}" for item in skipped[:4])
        raise ValueError(reasons or f"no Proxmox nodes were eligible for {kind}")
    if len(queued_jobs) == 1 and not skipped:
        return queued_jobs[0]
    return _fanout_summary(kind, target_type, target_ref, queued_jobs, skipped)


def _create_docker_stack_jobs(
    kind: str,
    target_type: str,
    target_ref: str,
    payload: dict[str, Any],
    requested_by: str,
    source: str,
) -> dict[str, Any]:
    executor = str(payload.get("executor", "agent")).strip() or "agent"
    if executor == "worker":
        raise ValueError(f"{kind} no longer supports the worker executor; use an enrolled Docker-capable agent")
    if executor not in VALID_EXECUTORS:
        allowed = ", ".join(sorted(VALID_EXECUTORS))
        raise ValueError(f"invalid executor {executor!r}; expected one of: {allowed}")

    targets = _resolve_docker_update_targets(target_ref, payload)
    if not targets:
        raise ValueError(f"no stacks were selected for {kind}")

    requires_approval = bool(payload.get("requires_approval", kind in APPROVAL_REQUIRED))
    status = "pending_approval" if requires_approval else "queued"
    approval_status = "pending" if requires_approval else "not_required"
    docker_update_settings = runtime_settings.get_docker_update_settings()
    public_settings = runtime_settings.get_public_settings()
    queued_jobs: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for stack in targets:
        stack_name = str(stack.get("name") or "").strip()
        if not stack_name:
            continue
        child_payload = deepcopy(payload)
        child_payload.pop("target_agent_id", None)
        child_payload["selected_stacks"] = [stack_name]
        child_payload["stack_name"] = stack_name
        child_payload["project_dir"] = stack_catalog.stack_project_dir(stack)
        child_payload["compose_env_files"] = list(stack.get("compose_env_files") or [])
        child_payload["image_strategy"] = str(stack.get("image_strategy") or "")
        child_payload["docker_update_policy"] = docker_update_settings
        child_payload["host"] = stack_catalog.stack_runtime_host(stack)
        child_payload["backup_before"] = bool(stack.get("backup_before"))
        child_payload["snapshot_before"] = bool(stack.get("snapshot_before"))
        child_payload["backup_commands"] = list(stack.get("backup_commands") or [])
        child_payload["backup_retention"] = int(docker_update_settings.get("backup_retention") or 3)
        child_payload["run_backup_commands"] = bool(docker_update_settings.get("run_backup_commands"))
        child_payload["risk"] = str(stack.get("risk") or "")
        child_payload["catalog_source"] = str(stack.get("catalog_source") or "")
        if kind == "docker_update":
            child_payload.update(_rackpatch_stack_update_fields(stack, public_settings))
        agent_id = resolve_agent_id(target_type, stack_name, child_payload)
        error = _agent_capability_error(kind, stack_name, child_payload, agent_id)
        if error:
            skipped.append({"target_ref": stack_name, "reason": error})
            continue
        queued_jobs.append(
            _insert_job(
                kind=kind,
                status=status,
                source=source,
                target_type=target_type,
                target_ref=stack_name,
                executor="agent",
                payload=child_payload,
                requested_by=requested_by,
                requires_approval=requires_approval,
                approval_status=approval_status,
                target_agent_id=agent_id,
            )
        )

    if not queued_jobs:
        reasons = "; ".join(f"{item['target_ref']}: {item['reason']}" for item in skipped[:4])
        raise ValueError(reasons or f"no stacks were eligible for {kind}")
    if len(queued_jobs) == 1 and not skipped:
        return queued_jobs[0]
    return _fanout_summary(kind, target_type, target_ref, queued_jobs, skipped)


def _create_docker_check_jobs(
    kind: str,
    target_type: str,
    target_ref: str,
    payload: dict[str, Any],
    requested_by: str,
    source: str,
) -> dict[str, Any]:
    return _create_docker_stack_jobs(kind, target_type, target_ref, payload, requested_by, source)


def _create_docker_update_jobs(
    kind: str,
    target_type: str,
    target_ref: str,
    payload: dict[str, Any],
    requested_by: str,
    source: str,
) -> dict[str, Any]:
    return _create_docker_stack_jobs(kind, target_type, target_ref, payload, requested_by, source)


def _agent_update_release_target(public_settings: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str]:
    requested_ref = str(payload.get("ref") or "").strip()
    requested_version = str(payload.get("target_version") or "").strip()
    if requested_ref:
        return requested_ref, requested_version or requested_ref

    latest = releases.fetch_latest_release(str(public_settings.get("repo_url") or config.PUBLIC_REPO_URL))
    latest_version = str(latest.get("version") or "").strip()
    target_ref = latest_version or str(public_settings.get("repo_ref") or config.PUBLIC_REPO_REF)
    target_version = requested_version or latest_version or target_ref
    return target_ref, target_version


def _agent_update_skip_reason(item: dict[str, Any], target_version: str) -> str | None:
    reason = str(item.get("reason") or "").strip()
    if reason:
        return reason
    capabilities = {str(value) for value in (item.get("capabilities") or []) if str(value).strip()}
    if "agent-self-update" not in capabilities:
        return "agent does not yet support queued self-updates; update it manually once to bootstrap this feature"
    if str(item.get("status") or "").lower() != "online":
        return "agent is offline"
    current_version = str(item.get("version") or "").strip()
    if target_version:
        release_state = releases.compare_versions(current_version, target_version)
        if release_state == "current":
            return f"already running {target_version}"
        if release_state == "ahead":
            return f"already ahead of {target_version}"
    if not str(item.get("command") or "").strip():
        return "update command is unavailable"
    if not str(item.get("id") or "").strip():
        return "agent id is unavailable"
    return None


def _create_agent_update_jobs(
    kind: str,
    target_type: str,
    target_ref: str,
    payload: dict[str, Any],
    requested_by: str,
    source: str,
) -> dict[str, Any]:
    executor = str(payload.get("executor", "agent")).strip() or "agent"
    if executor == "worker":
        raise ValueError("agent_update must run through enrolled agents")
    if executor not in VALID_EXECUTORS:
        allowed = ", ".join(sorted(VALID_EXECUTORS))
        raise ValueError(f"invalid executor {executor!r}; expected one of: {allowed}")

    public_settings = runtime_settings.get_public_settings()
    target_ref_value, target_version = _agent_update_release_target(public_settings, payload)
    agent_rows = db.fetch_all(
        """
        SELECT id, name, display_name, version, capabilities, labels, metadata, status
        FROM agents
        ORDER BY name
        """
    )
    plan = control_plane.build_agent_update_plan(public_settings, target_ref_value, agent_rows)
    requested_agents = _resolve_agent_update_targets(target_ref, payload)
    requested_set = {value for value in requested_agents if value != "all"}
    items = plan["items"]
    if requested_set:
        items = [item for item in items if str(item.get("agent_name") or "") in requested_set]
    if not items:
        raise ValueError("no enrolled agents matched the requested selection")

    requires_approval = bool(payload.get("requires_approval", kind in APPROVAL_REQUIRED))
    status = "pending_approval" if requires_approval else "queued"
    approval_status = "pending" if requires_approval else "not_required"
    queued_jobs: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for item in items:
        label = str(item.get("name") or item.get("agent_name") or "unknown")
        reason = _agent_update_skip_reason(item, target_version)
        if reason:
            skipped.append({"target_ref": label, "reason": reason})
            continue

        child_payload = deepcopy(payload)
        child_payload.pop("target_agent_id", None)
        child_payload["update_command"] = str(item.get("command") or "")
        child_payload["release_ref"] = target_ref_value
        child_payload["target_version"] = target_version
        child_payload["update_mode"] = str(item.get("mode") or "")
        child_payload["target_agent_name"] = str(item.get("agent_name") or "")
        if str(item.get("mode") or "") == "compose":
            child_payload["update_target_dir"] = str(item.get("compose_dir") or "")
        elif str(item.get("mode") or "") == "container":
            child_payload["update_target_dir"] = str(item.get("install_dir") or "")
        queued_jobs.append(
            _insert_job(
                kind=kind,
                status=status,
                source=source,
                target_type=target_type,
                target_ref=str(item.get("agent_name") or label),
                executor="agent",
                payload=child_payload,
                requested_by=requested_by,
                requires_approval=requires_approval,
                approval_status=approval_status,
                target_agent_id=str(item.get("id") or ""),
            )
        )

    if not queued_jobs:
        reasons = "; ".join(f"{item['target_ref']}: {item['reason']}" for item in skipped[:4])
        raise ValueError(reasons or "no agents were eligible for agent_update")
    if len(queued_jobs) == 1 and not skipped:
        return queued_jobs[0]
    return _fanout_summary(kind, target_type, target_ref, queued_jobs, skipped)


def create_job(
    kind: str,
    target_type: str,
    target_ref: str,
    payload: dict[str, Any],
    requested_by: str,
    source: str = "ui",
) -> dict[str, Any]:
    kind_metadata = job_catalog.get_job_kind(kind)
    if not kind_metadata:
        raise ValueError(f"unsupported job kind: {kind}")
    expected_target_type = str(kind_metadata["target_type"])
    if target_type != expected_target_type:
        raise ValueError(f"{kind} jobs must target {expected_target_type}, not {target_type}")
    if kind in PACKAGE_JOB_KINDS:
        return _create_package_jobs(kind, target_type, target_ref, payload, requested_by, source)
    if kind in PROXMOX_JOB_KINDS:
        return _create_proxmox_jobs(kind, target_type, target_ref, payload, requested_by, source)
    if kind == "docker_check":
        return _create_docker_check_jobs(kind, target_type, target_ref, payload, requested_by, source)
    if kind == "docker_update":
        return _create_docker_update_jobs(kind, target_type, target_ref, payload, requested_by, source)
    if kind == "agent_update":
        return _create_agent_update_jobs(kind, target_type, target_ref, payload, requested_by, source)
    if kind == "rollback":
        blocker = _rollback_blocker(target_ref)
        if blocker:
            raise ValueError(blocker)

    requires_approval = bool(payload.get("requires_approval", kind in APPROVAL_REQUIRED))
    executor = str(payload.get("executor", "worker")).strip() or "worker"
    if executor not in VALID_EXECUTORS:
        allowed = ", ".join(sorted(VALID_EXECUTORS))
        raise ValueError(f"invalid executor {executor!r}; expected one of: {allowed}")
    target_agent_id = None
    if executor == "auto":
        target_agent_id = resolve_agent_id(target_type, target_ref, payload)
        executor = "agent" if _agent_can_run_job(kind, target_ref, payload, target_agent_id) else "worker"
    elif executor == "agent":
        target_agent_id = resolve_agent_id(target_type, target_ref, payload)
        if not target_agent_id:
            raise ValueError(f"no registered agent found for {target_ref}")
        if not _agent_can_run_job(kind, target_ref, payload, target_agent_id):
            raise ValueError(f"agent for {target_ref} does not support {kind}")

    status = "pending_approval" if requires_approval else "queued"
    approval_status = "pending" if requires_approval else "not_required"
    return _insert_job(
        kind=kind,
        status=status,
        source=source,
        target_type=target_type,
        target_ref=target_ref,
        executor=executor,
        payload=payload,
        requested_by=requested_by,
        requires_approval=requires_approval,
        approval_status=approval_status,
        target_agent_id=target_agent_id,
    )


def cancel_job(job_id: str, username: str) -> dict[str, Any] | None:
    result = {"cancelled_by": username, "reason": "cancelled from control plane"}
    with db.db_cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = 'cancelled',
                approval_status = CASE
                    WHEN approval_status = 'pending' THEN 'cancelled'
                    ELSE approval_status
                END,
                result = %s,
                finished_at = NOW()
            WHERE id = %s
              AND status = ANY(%s)
            RETURNING *
            """,
            (json.dumps(result), job_id, list(CANCELLABLE_STATUSES)),
        )
        job = cur.fetchone()
    if job:
        append_event(job_id, f"[{now_iso()}] job cancelled by {username}")
        notify.send_job_event(job, "cancelled", result)
    return job


def delete_job(job_id: str, username: str) -> tuple[dict[str, Any] | None, str | None]:
    del username
    existing = db.fetch_one("SELECT id, status, artifact_dir FROM jobs WHERE id = %s", (job_id,))
    if not existing:
        return None, "not_found"
    if not job_is_deletable(existing.get("status")):
        return None, "not_deletable"

    event_count_row = db.fetch_one(
        "SELECT COUNT(*) AS value FROM job_events WHERE job_id = %s",
        (job_id,),
    )
    event_count = int((event_count_row or {}).get("value") or 0)
    with db.db_cursor() as cur:
        cur.execute(
            "DELETE FROM jobs WHERE id = %s AND status = ANY(%s) RETURNING *",
            (job_id, list(DELETABLE_STATUSES)),
        )
        deleted = cur.fetchone()
    if not deleted:
        return None, "not_deletable"

    _delete_job_artifacts(str(deleted["id"]), deleted.get("artifact_dir"))
    return {
        **deleted,
        "deleted_event_count": event_count,
    }, None


def approve_job(job_id: str, username: str) -> dict[str, Any] | None:
    with db.db_cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = 'queued',
                approval_status = 'approved',
                approved_by = %s,
                queued_at = NOW()
            WHERE id = %s AND approval_status = 'pending'
            RETURNING *
            """,
            (username, job_id),
        )
        job = cur.fetchone()
    if job:
        append_event(job_id, f"[{now_iso()}] job approved by {username}")
        notify.send_job_event(job, "approved")
    return job


def recover_stale_worker_jobs() -> list[dict[str, Any]]:
    recovery_result = {
        "error": "worker restarted before job completion",
        "recovered_by": "worker_startup",
        "recovered_at": now_iso(),
    }
    with db.db_cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                result = COALESCE(result, '{}'::jsonb) || %s::jsonb,
                finished_at = NOW()
            WHERE executor = 'worker'
              AND status = 'running'
            RETURNING *
            """,
            (json.dumps(recovery_result),),
        )
        recovered = list(cur.fetchall())

    for job in recovered:
        append_event(str(job["id"]), f"[{now_iso()}] worker startup marked interrupted job as failed")
        notify.send_job_event(job, "failed", job.get("result") or recovery_result)
    return recovered


def retire_legacy_package_jobs() -> list[dict[str, Any]]:
    retirement_result = {
        "error": "legacy worker package path removed",
        "recovered_by": "host_maintenance_helper_rollout",
        "recovered_at": now_iso(),
    }
    with db.db_cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                approval_status = CASE
                    WHEN approval_status = 'pending' THEN 'cancelled'
                    ELSE approval_status
                END,
                result = COALESCE(result, '{}'::jsonb) || %s::jsonb,
                finished_at = NOW()
            WHERE kind = ANY(%s)
              AND executor = 'worker'
              AND status = ANY(%s)
            RETURNING *
            """,
            (
                json.dumps(retirement_result),
                list(PACKAGE_JOB_KINDS),
                ["queued", "pending_approval", "running"],
            ),
        )
        retired = list(cur.fetchall())
    for job in retired:
        append_event(
            str(job["id"]),
            f"[{now_iso()}] worker package path retired; requeue with a helper-enabled agent",
            stream="stderr",
        )
    return retired


def retire_legacy_worker_control_jobs() -> list[dict[str, Any]]:
    retirement_result = {
        "error": "legacy worker host-control path removed",
        "recovered_by": "agent_first_runtime",
        "recovered_at": now_iso(),
    }
    with db.db_cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                approval_status = CASE
                    WHEN approval_status = 'pending' THEN 'cancelled'
                    ELSE approval_status
                END,
                result = COALESCE(result, '{}'::jsonb) || %s::jsonb,
                finished_at = NOW()
            WHERE kind = ANY(%s)
              AND executor = 'worker'
              AND status = ANY(%s)
            RETURNING *
            """,
            (
                json.dumps(retirement_result),
                list(RETIRED_WORKER_CONTROL_JOB_KINDS),
                ["queued", "pending_approval", "running"],
            ),
        )
        retired = list(cur.fetchall())
    for job in retired:
        append_event(
            str(job["id"]),
            f"[{now_iso()}] legacy worker host-control path retired; requeue with an enrolled agent if the workflow is still supported",
            stream="stderr",
        )
    return retired
