from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from common import config, db, notify, site


# Route only lightweight read-only checks through agents. Update execution stays on the
# worker path so stack backups, snapshots, and ordered playbooks are not bypassed.
AGENT_JOB_KINDS = {"package_check"}
APPROVAL_REQUIRED = {"docker_update", "package_patch", "proxmox_patch", "proxmox_reboot", "rollback"}


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


def create_job(
    kind: str,
    target_type: str,
    target_ref: str,
    payload: dict[str, Any],
    requested_by: str,
    source: str = "ui",
) -> dict[str, Any]:
    requires_approval = bool(payload.get("requires_approval", kind in APPROVAL_REQUIRED))
    executor = payload.get("executor", "worker")
    target_agent_id = None
    if executor == "auto":
        target_agent_id = resolve_agent_id(target_type, target_ref, payload)
        executor = "agent" if target_agent_id and kind in AGENT_JOB_KINDS else "worker"
    elif executor == "agent":
        target_agent_id = resolve_agent_id(target_type, target_ref, payload)
        if not target_agent_id:
            raise ValueError(f"no registered agent found for {target_ref}")

    status = "pending_approval" if requires_approval else "queued"
    approval_status = "pending" if requires_approval else "not_required"

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
