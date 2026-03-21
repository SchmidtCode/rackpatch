from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from croniter import croniter

from common import config, db, jobs, legacy, notify


def now() -> datetime:
    return datetime.now(timezone.utc)


def next_cron(expr: str, base: datetime | None = None) -> datetime:
    base = base or now()
    return croniter(expr, base).get_next(datetime)


def seed_schedule_next_run() -> None:
    with db.db_cursor() as cur:
        cur.execute("SELECT id, cron_expr, next_run_at FROM schedules")
        for row in cur.fetchall():
            if row["next_run_at"] is None:
                cur.execute(
                    "UPDATE schedules SET next_run_at = %s, updated_at = NOW() WHERE id = %s",
                    (next_cron(row["cron_expr"]), row["id"]),
                )


def enqueue_schedules() -> None:
    seed_schedule_next_run()
    with db.db_cursor() as cur:
        cur.execute(
            """
            SELECT id, name, kind, cron_expr, payload, next_run_at
            FROM schedules
            WHERE enabled = TRUE AND next_run_at <= NOW()
            ORDER BY next_run_at ASC
            """
        )
        due = list(cur.fetchall())

    for row in due:
        payload = dict(row["payload"] or {})
        payload.setdefault("executor", "auto")
        if row["kind"] == "docker_update" and payload.get("window") == "auto-windowed":
            payload.setdefault("requires_approval", False)

        target_type = "stack" if row["kind"] in {"docker_update", "rollback"} else "host"
        target_ref = payload.get("target_ref", "all")

        try:
            jobs.create_job(
                kind=row["kind"],
                target_type=target_type,
                target_ref=target_ref,
                payload=payload,
                requested_by="system",
                source="schedule",
            )
        except ValueError as exc:
            print(f"schedule {row['name']} skipped: {exc}", flush=True)

        with db.db_cursor() as cur:
            cur.execute(
                """
                UPDATE schedules
                SET last_run_at = NOW(), next_run_at = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (next_cron(row["cron_expr"]), row["id"]),
            )


def claim_worker_job() -> dict | None:
    with db.db_cursor() as cur:
        cur.execute(
            """
            WITH candidate AS (
              SELECT id
              FROM jobs
              WHERE executor = 'worker'
                AND kind NOT IN ('package_check', 'package_patch')
                AND status = 'queued'
                AND approval_status <> 'pending'
              ORDER BY created_at ASC
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            UPDATE jobs
            SET status = 'running', started_at = NOW()
            WHERE id IN (SELECT id FROM candidate)
            RETURNING *
            """
        )
        return cur.fetchone()


def execute_job(job: dict) -> None:
    job_id = str(job["id"])
    payload = dict(job["payload"] or {})
    command = legacy.worker_command(job["kind"], payload, job["target_ref"])
    result = legacy.run_logged(job_id, command)
    artifacts = legacy.artifacts_from_output(result.get("stdout", ""))
    status = "completed" if result["exit_code"] == 0 else "failed"
    final_result = {"command": command, "artifacts": artifacts, **result}
    if job["kind"] == "docker_update" and result["exit_code"] == 0 and not payload.get("dry_run", False):
        try:
            final_result["update_summary"] = legacy.summarize_docker_update(payload, job["target_ref"])
        except Exception as exc:  # noqa: BLE001
            jobs.append_event(job_id, f"docker update summary unavailable: {exc}", stream="stderr")
            final_result["update_summary_error"] = str(exc)
    jobs.set_job_status(job_id, status, result=final_result)
    for artifact in artifacts:
        jobs.record_backup(
            job_id=job_id,
            kind=artifact.get("kind", "artifact"),
            target_ref=artifact.get("stack", job["target_ref"]),
            path=artifact.get("value", ""),
            metadata=artifact,
        )
    if job["kind"] == "backup" and result["exit_code"] == 0:
        payload_name = payload.get("output_name", f"{job['target_ref']}.tgz")
        jobs.record_backup(
            job_id=job_id,
            kind="backup",
            target_ref=job["target_ref"],
            path=f"data/backups/{payload_name}",
            metadata={
                "source": "manual",
                "container_path": str(config.BACKUPS_ROOT / payload_name),
            },
        )
    notify.send_job_event(job, status, final_result)


def main() -> int:
    db.init_db()
    retired = jobs.retire_legacy_package_jobs()
    retired_control = jobs.retire_legacy_worker_control_jobs()
    recovered = jobs.recover_stale_worker_jobs()
    print("rackpatch-worker started", flush=True)
    if retired:
        print(f"rackpatch-worker retired {len(retired)} legacy worker package job(s)", flush=True)
    if retired_control:
        print(f"rackpatch-worker retired {len(retired_control)} legacy worker host-control job(s)", flush=True)
    if recovered:
        print(f"rackpatch-worker recovered {len(recovered)} stale worker job(s)", flush=True)
    schedule_tick = 0.0
    while True:
        now_monotonic = time.monotonic()
        if now_monotonic >= schedule_tick:
            enqueue_schedules()
            schedule_tick = now_monotonic + config.SCHEDULE_POLL_SECONDS

        job = claim_worker_job()
        if job:
            jobs.append_event(str(job["id"]), f"[{datetime.now(timezone.utc).isoformat()}] worker claimed job")
            try:
                execute_job(job)
            except Exception as exc:  # noqa: BLE001
                jobs.append_event(str(job["id"]), f"worker error: {exc}", stream="stderr")
                result = {"error": str(exc)}
                jobs.set_job_status(str(job["id"]), "failed", result=result)
                notify.send_job_event(job, "failed", result)
            continue
        time.sleep(config.WORKER_POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
