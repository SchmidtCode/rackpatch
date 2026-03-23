# Automation and control surfaces

## Web UI

rackpatch exposes focused pages for:

- `Overview`
- `Stacks`
- `Hosts`
- `Agents`
- `Jobs`
- `Approvals`
- `Schedules`
- `Backups`
- `Settings`

## Release tracking and API access

When `RACKPATCH_PUBLIC_REPO_URL` points at GitHub, rackpatch compares the latest upstream release or tag to:

- the running control-plane version
- each enrolled agent version

That release data appears in:

- `Overview`
- `Agents`
- `Settings`
- `/api/v1/settings`
- `/api/v1/context`

Machine-friendly endpoints:

- `/api/v1/context`: settings, release status, install commands, API paths, running jobs, pending approvals, and supported job kinds
- `/api/v1/job-kinds`: job form metadata
- `/api/v1/jobs`: recent jobs
- `/api/v1/jobs/<job-id>/events`: job logs
- `/api/v1/settings`: public repo and Telegram settings

If you need browser access from another origin, set `RACKPATCH_CORS_ORIGINS` to a comma-separated allowlist. The default posture is same-origin only.

## Telegram control

Configure the bot token and allowed chat IDs in `Settings`, then use commands such as:

```text
/status
/stacks
/hosts
/jobs
/approvals
/approve <job-id>
/update <stack|all> [dry|live]
/patch <host|all> [dry|live]
/backup <volume>
/rollback <stack>
/schedules
/schedule <name-or-id> on|off
/job <kind> <target_type> <target_ref> {"executor":"agent"}
```

## Operating notes

- Schedules are seeded from the site overlay and remain disabled by default until explicitly enabled in the UI or Telegram.
- Schedule cron expressions are evaluated in each schedule's stored `timezone`. Seeded schedules default to `maintenance_timezone`, and API-created schedules also fall back to that value when `timezone` is omitted.
- Job backups and rollback artifacts are written under `data/backups`.
- Telegram chat IDs remain the primary gate. Optional sender allowlists can further restrict access by Telegram user ID and/or username; when no sender allowlist is configured, the existing chat-ID-based flow continues to work.
- `telegram` idles until a bot token is configured.
- `telegram` also stays idle when `RACKPATCH_ADMIN_PASSWORD` or `RACKPATCH_AUTH_SECRET` still use the shipped default insecure values, because the bot authenticates into the control plane with administrator privileges.
- `notify` is optional and sends Telegram messages when both a bot token and chat IDs are configured.
- When Telegram is not fully configured, `notify` falls back to log-only delivery and reports that state via `/health` and `/ready`.
