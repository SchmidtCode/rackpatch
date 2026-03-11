from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from common import auth, config, db, jobs, runtime_settings, site


app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.get("/api/v1/stacks")
def stacks(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return {"items": site.load_stacks()}


@app.get("/api/v1/hosts")
def hosts(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    known_agents = {
        row["name"]: row
        for row in db.fetch_all(
            "SELECT id, name, display_name, status, capabilities, last_seen_at FROM agents ORDER BY name"
        )
    }
    items = []
    for host in site.load_hosts():
        agent = known_agents.get(host["name"])
        items.append(
            {
                **host,
                "agent": agent,
            }
        )
    return {"items": items}


@app.get("/api/v1/agents")
def agents(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return {
        "items": db.fetch_all(
            """
            SELECT id, name, display_name, transport, platform, version, capabilities, labels,
                   metadata, status, last_seen_at, created_at, updated_at
            FROM agents
            ORDER BY name
            """
        )
    }


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


@app.get("/api/v1/backups")
def list_backups(username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    return {
        "items": db.fetch_all(
            "SELECT id, job_id, kind, target_ref, path, metadata, created_at FROM backups ORDER BY created_at DESC"
        )
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
    bootstrap_token = auth.ensure_bootstrap_token()
    public_settings = runtime_settings.get_public_settings()
    telegram_settings = runtime_settings.get_telegram_settings()
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
        "agent_install": {
            "container": (
                f"curl -fsSL {public_settings['install_script_url']} | sh -s -- "
                f"--server-url {public_settings['base_url']} --bootstrap-token {bootstrap_token} "
                f"--mode container --install-source {public_settings['repo_url']}"
            ),
            "systemd": (
                f"curl -fsSL {public_settings['install_script_url']} | sh -s -- "
                f"--server-url {public_settings['base_url']} --bootstrap-token {bootstrap_token} "
                f"--mode systemd --install-source {public_settings['repo_url']}"
            ),
        },
    }


@app.post("/api/v1/settings/public")
def update_public_settings(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    runtime_settings.set_public_settings(payload)
    return settings("_internal")


@app.post("/api/v1/settings/telegram")
def update_telegram_settings(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    runtime_settings.set_telegram_settings(payload)
    return settings("_internal")


@app.post("/api/v1/settings/agent-tokens")
def create_agent_token(payload: dict[str, Any], username: str = Depends(auth.require_user)) -> dict[str, Any]:
    del username
    label = str(payload.get("label", "manual-token")).strip()
    token = auth.random_token("ops-agent-")
    with db.db_cursor() as cur:
        cur.execute(
            "INSERT INTO agent_tokens (label, token_hash) VALUES (%s, %s) RETURNING id, label, created_at",
            (label, auth.hash_token(token)),
        )
        row = cur.fetchone()
    row["token"] = token
    return row


@app.post("/api/v1/agents/register")
def register_agent(
    payload: dict[str, Any],
    x_ops_agent_token: str | None = Header(default=None),
) -> dict[str, Any]:
    token = x_ops_agent_token or str(payload.get("bootstrap_token", ""))
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

    secret = auth.random_token("ops-secret-")
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
    x_ops_agent_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", ""))
    _validate_agent_secret(agent_id, x_ops_agent_secret)
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
    x_ops_agent_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", ""))
    _validate_agent_secret(agent_id, x_ops_agent_secret)
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
    x_ops_agent_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", ""))
    _validate_agent_secret(agent_id, x_ops_agent_secret)
    row = db.fetch_one("SELECT target_agent_id FROM jobs WHERE id = %s", (job_id,))
    if not row or str(row["target_agent_id"]) != agent_id:
        raise HTTPException(status_code=403, detail="job is not owned by this agent")
    jobs.append_event(job_id, str(payload.get("message", "")), stream=str(payload.get("stream", "stdout")))
    return {"status": "ok"}


@app.post("/api/v1/jobs/{job_id}/complete")
def complete_job(
    job_id: str,
    payload: dict[str, Any],
    x_ops_agent_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", ""))
    _validate_agent_secret(agent_id, x_ops_agent_secret)
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
    return {"status": "ok"}
