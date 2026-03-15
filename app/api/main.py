from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from common import auth, config, control_plane, db, job_catalog, jobs, notify, releases, runtime_settings, site


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


def _json_body(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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
            detail = str(host_maintenance.get("detail") or "Agent enrolled. Host maintenance helper not enabled.")
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
        "status": "Worker-routed",
        "detail": "Agent optional. Worker and inventory jobs still available.",
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
    size_bytes = existing_file.stat().st_size if existing_file else None
    display_name = preferred_path.name if preferred_path else Path(str(item.get("path") or "")).name
    return {
        "resolved_path": str(preferred_path) if preferred_path else "",
        "file_name": display_name or str(item.get("target_ref") or ""),
        "exists": bool(existing_file),
        "size_bytes": size_bytes,
        "delete_supported": bool(existing_file and within_backups_root),
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


def _settings_payload() -> dict[str, Any]:
    bootstrap_token = auth.ensure_bootstrap_token()
    public_settings = runtime_settings.get_public_settings()
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
        "telegram": telegram_settings,
        "default_agent_bootstrap_token": bootstrap_token,
        "agent_install": control_plane.build_agent_install_commands(public_settings, bootstrap_token),
        "agent_host_maintenance": control_plane.build_agent_host_maintenance_commands(
            public_settings,
            str(public_settings.get("repo_ref") or config.PUBLIC_REPO_REF),
        ),
        "release": releases.build_release_status(public_settings, agents),
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": config.APP_VERSION,
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


@app.get("/api/v1/hosts")
def hosts(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    public_settings = runtime_settings.get_public_settings()
    base_identities = _public_base_host_identities(public_settings)
    known_agents = {
        row["name"]: row
        for row in db.fetch_all(
            """
            SELECT id, name, display_name, status, capabilities, metadata, last_seen_at
            FROM agents
            ORDER BY name
            """
        )
    }
    items = []
    for host in site.load_hosts():
        agent = known_agents.get(host["name"])
        control_plane_host = _is_control_plane_host(host, base_identities)
        items.append(
            {
                **host,
                "agent": agent,
                "control_plane_host": control_plane_host,
                "runtime": _host_runtime(agent, host, control_plane_host=control_plane_host),
            }
        )
    return {"items": items}


@app.get("/api/v1/agents")
def agents(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    public_settings = runtime_settings.get_public_settings()
    items = db.fetch_all(
        """
        SELECT id, name, display_name, transport, platform, version, capabilities, labels,
               metadata, status, last_seen_at, created_at, updated_at
        FROM agents
        ORDER BY name
        """
    )
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
    return {
        "items": db.fetch_all(
            """
            SELECT id, kind, status, source, target_type, target_ref, executor, payload,
                   result, requested_by, requires_approval, approval_status, approved_by,
                   target_agent_id, created_at, queued_at, started_at, finished_at
            FROM jobs
            ORDER BY created_at DESC
            LIMIT 200
            """
        )
    }


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    row = db.fetch_one("SELECT * FROM jobs WHERE id = %s", (job_id,))
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    return row


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
            SELECT id, name, kind, cron_expr, payload, enabled, next_run_at, last_run_at, created_at, updated_at
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
    with db.db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO schedules (name, kind, cron_expr, payload, enabled, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (name) DO UPDATE SET
              kind = EXCLUDED.kind,
              cron_expr = EXCLUDED.cron_expr,
              payload = EXCLUDED.payload,
              enabled = EXCLUDED.enabled,
              updated_at = NOW()
            RETURNING *
            """,
            (name, kind, cron_expr, json.dumps(_json_body(payload.get("payload"))), enabled),
        )
        row = cur.fetchone()
    return row


@app.post("/api/v1/schedules/{schedule_id}/toggle")
def toggle_schedule(schedule_id: str, payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    enabled = bool(payload.get("enabled", False))
    with db.db_cursor() as cur:
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

    secret = auth.random_token("rackpatch-secret-")
    with db.db_cursor() as cur:
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
                str(payload.get("display_name", name)),
                auth.hash_token(secret),
                str(payload.get("transport", "poll")),
                str(payload.get("platform", "linux")),
                str(payload.get("version", "unknown")),
                json.dumps(payload.get("capabilities", [])),
                json.dumps(payload.get("labels", [])),
                json.dumps(payload.get("metadata", {})),
            ),
        )
        row = cur.fetchone()
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
                metadata = COALESCE(%s::jsonb, metadata),
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, name, last_seen_at, status
            """,
            (json.dumps(payload.get("metadata")) if payload.get("metadata") is not None else None, agent_id),
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
    job = db.fetch_one("SELECT * FROM jobs WHERE id = %s", (job_id,))
    if job:
        notify.send_job_event(job, status, result)
    return {"status": "ok"}
