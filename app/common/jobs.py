from __future__ import annotations

from copy import deepcopy
import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from common import config, db, job_catalog, notify, site


PACKAGE_JOB_KINDS = {"package_check", "package_patch"}
AGENT_JOB_KINDS = PACKAGE_JOB_KINDS
APPROVAL_REQUIRED = {"docker_update", "package_patch", "proxmox_patch", "proxmox_reboot", "rollback"}
VALID_EXECUTORS = {"worker", "agent", "auto"}
CANCELLABLE_STATUSES = {"queued", "pending_approval"}
PACKAGE_JOB_CAPABILITIES = {
    "package_check": {"host-package-check"},
    "package_patch": {"host-package-patch"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_event(job_id: str, message: str, stream: str = "stdout") -> None:
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO job_events (job_id, stream, message) VALUES (%s, %s, %s)",
            (job_id, stream, message.rstrip("\n")),
        )


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


def resolve_agent_id(target_type: str, target_ref: str, payload: dict[str, Any]) -> str | None:
    if payload.get("target_agent_id"):
        return str(payload["target_agent_id"])

    host_name = target_ref
    if target_type == "stack":
        stack = site.find_stack(target_ref)
        host_name = (stack or {}).get("host", target_ref)

    row = db.fetch_one("SELECT id FROM agents WHERE name = %s", (host_name,))
    if row:
        return str(row["id"])
    return None


def _agent_capabilities(agent_id: str | None) -> set[str]:
    if not agent_id:
        return set()
    row = db.fetch_one("SELECT capabilities FROM agents WHERE id = %s", (agent_id,))
    values = (row or {}).get("capabilities") or []
    return {str(value) for value in values if str(value).strip()}


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
        return "Use the Proxmox patch or reboot jobs for Proxmox nodes."
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


def _agent_capability_error(kind: str, target_ref: str, payload: dict[str, Any], agent_id: str | None) -> str | None:
    if kind not in PACKAGE_JOB_KINDS:
        return None
    blocker = _package_job_blocker(kind, target_ref, payload)
    if blocker:
        return blocker
    if not agent_id:
        return f"No enrolled agent found for {target_ref}."
    capabilities = _agent_capabilities(agent_id)
    required = PACKAGE_JOB_CAPABILITIES.get(kind, set())
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
        "required_capabilities": sorted(PACKAGE_JOB_CAPABILITIES.get(kind, set())),
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
