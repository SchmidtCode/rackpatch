from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from common import auth, config, job_catalog, site


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  label TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  secret_hash TEXT NOT NULL,
  transport TEXT NOT NULL,
  platform TEXT NOT NULL,
  version TEXT NOT NULL,
  capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
  labels JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'online',
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_ref TEXT NOT NULL,
  executor TEXT NOT NULL,
  site_name TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  result JSONB NOT NULL DEFAULT '{}'::jsonb,
  requested_by TEXT NOT NULL,
  requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
  approval_status TEXT NOT NULL DEFAULT 'not_required',
  approved_by TEXT,
  target_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
  artifact_dir TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  queued_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_agent_idx ON jobs(target_agent_id, status);

CREATE TABLE IF NOT EXISTS job_events (
  id BIGSERIAL PRIMARY KEY,
  job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  stream TEXT NOT NULL DEFAULT 'stdout',
  message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS job_events_job_idx ON job_events(job_id, id);

CREATE TABLE IF NOT EXISTS schedules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  cron_expr TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  next_run_at TIMESTAMPTZ,
  last_run_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS backups (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  target_ref TEXT NOT NULL,
  path TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(config.DB_DSN, row_factory=dict_row, autocommit=True)


@contextmanager
def db_cursor() -> Iterator[psycopg.Cursor]:
    with connect() as conn:
        with conn.cursor() as cur:
            yield cur


def init_db() -> None:
    config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    config.JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    config.BACKUPS_ROOT.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(84720113)")
            try:
                cur.execute(SCHEMA)
                cur.execute("SELECT username FROM users WHERE username = %s", (config.ADMIN_USERNAME,))
                if cur.fetchone() is None:
                    cur.execute(
                        "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                        (config.ADMIN_USERNAME, auth.hash_password(config.ADMIN_PASSWORD)),
                    )
                configured_token = config.env("RACKPATCH_AGENT_BOOTSTRAP_TOKEN", "")
                if configured_token and configured_token != "bootstrap-me":
                    bootstrap_token = configured_token
                else:
                    cur.execute("SELECT value FROM settings WHERE key = 'bootstrap_agent_token'")
                    row = cur.fetchone()
                    stored_token = ((row or {}).get("value") or {}).get("token")
                    if stored_token:
                        bootstrap_token = str(stored_token)
                    else:
                        bootstrap_token = auth.random_token("rackpatch-bootstrap-")
                        cur.execute(
                            """
                            INSERT INTO settings (key, value, updated_at)
                            VALUES ('bootstrap_agent_token', %s, NOW())
                            ON CONFLICT (key) DO UPDATE SET
                              value = EXCLUDED.value,
                              updated_at = NOW()
                            """,
                            (json.dumps({"token": bootstrap_token}),),
                        )
                os.environ["RACKPATCH_AGENT_BOOTSTRAP_TOKEN"] = bootstrap_token
                bootstrap_hash = auth.hash_token(bootstrap_token)
                cur.execute(
                    "SELECT id FROM agent_tokens WHERE token_hash = %s AND revoked_at IS NULL",
                    (bootstrap_hash,),
                )
                if cur.fetchone() is None:
                    cur.execute(
                        "INSERT INTO agent_tokens (label, token_hash) VALUES (%s, %s)",
                        ("default-bootstrap", bootstrap_hash),
                    )
                for schedule in site.default_schedules():
                    payload = schedule["payload"]
                    cur.execute(
                        "SELECT id, kind, cron_expr, payload FROM schedules WHERE name = %s",
                        (schedule["name"],),
                    )
                    row = cur.fetchone()
                    if row is None:
                        cur.execute(
                            """
                            INSERT INTO schedules (name, kind, cron_expr, payload, enabled)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (
                                schedule["name"],
                                schedule["kind"],
                                schedule["cron_expr"],
                                json.dumps(payload),
                                False,
                            ),
                        )
                        continue
                    if (
                        row["kind"] != schedule["kind"]
                        or row["cron_expr"] != schedule["cron_expr"]
                        or (row["payload"] or {}) != payload
                    ):
                        cur.execute(
                            """
                            UPDATE schedules
                            SET kind = %s,
                                cron_expr = %s,
                                payload = %s,
                                next_run_at = NULL,
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (
                                schedule["kind"],
                                schedule["cron_expr"],
                                json.dumps(payload),
                                row["id"],
                            ),
                        )
                valid_schedule_kinds = list(job_catalog.known_job_kinds())
                cur.execute(
                    "DELETE FROM schedules WHERE NOT (kind = ANY(%s))",
                    (valid_schedule_kinds,),
                )
            finally:
                cur.execute("SELECT pg_advisory_unlock(84720113)")


def fetch_all(query: str, params: tuple | None = None) -> list[dict]:
    with db_cursor() as cur:
        cur.execute(query, params or ())
        return list(cur.fetchall())


def fetch_one(query: str, params: tuple | None = None) -> dict | None:
    with db_cursor() as cur:
        cur.execute(query, params or ())
        return cur.fetchone()


def ensure_job_dir(job_id: str) -> Path:
    job_dir = config.JOBS_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir
