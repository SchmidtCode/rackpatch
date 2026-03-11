from __future__ import annotations

import os
from pathlib import Path


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


APP_NAME = "Custom Ops UI"
APP_VERSION = "0.1.0"

API_HOST = env("OPS_API_HOST", "0.0.0.0")
API_PORT = int(env("OPS_API_PORT", "9080"))
NOTIFY_PORT = int(env("OPS_NOTIFY_PORT", "9091"))

DB_DSN = env("OPS_DB_DSN", "postgresql://ops:ops@ops-db:5432/ops")
DATA_ROOT = Path(env("OPS_DATA_ROOT", "/data"))
JOBS_ROOT = DATA_ROOT / "jobs"
BACKUPS_ROOT = DATA_ROOT / "backups"
SITE_ROOT = Path(env("OPS_SITE_ROOT", "/workspace/sites/example"))
SITE_NAME = env("OPS_SITE_NAME", SITE_ROOT.name)

ADMIN_USERNAME = env("OPS_ADMIN_USERNAME", "opsadmin")
ADMIN_PASSWORD = env("OPS_ADMIN_PASSWORD", "changeme")
AUTH_SECRET = env("OPS_AUTH_SECRET", "change-me-in-production")
DEFAULT_AGENT_BOOTSTRAP_TOKEN = env("OPS_AGENT_BOOTSTRAP_TOKEN", "bootstrap-me")
PUBLIC_BASE_URL = env("OPS_PUBLIC_BASE_URL", "http://YOUR-OPS-HOST:3011").rstrip("/")
PUBLIC_REPO_URL = env("OPS_PUBLIC_REPO_URL", "https://github.com/YOUR-ORG/custom-ops-ui.git").rstrip("/")
PUBLIC_REPO_REF = env("OPS_PUBLIC_REPO_REF", "v0.1.0")
PUBLIC_INSTALL_SCRIPT_URL = env("OPS_PUBLIC_INSTALL_SCRIPT_URL", "").rstrip("/")

WORKER_POLL_SECONDS = float(env("OPS_WORKER_POLL_SECONDS", "5"))
SCHEDULE_POLL_SECONDS = float(env("OPS_SCHEDULE_POLL_SECONDS", "20"))
AGENT_POLL_SECONDS = float(env("OPS_AGENT_POLL_SECONDS", "10"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = [
    item.strip()
    for item in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
    if item.strip()
]


def public_install_script_url() -> str:
    if PUBLIC_INSTALL_SCRIPT_URL:
        return PUBLIC_INSTALL_SCRIPT_URL

    repo_url = PUBLIC_REPO_URL[:-4] if PUBLIC_REPO_URL.endswith(".git") else PUBLIC_REPO_URL
    prefix = "https://github.com/"
    if repo_url.startswith(prefix):
        repo_path = repo_url[len(prefix):].strip("/")
        return f"https://raw.githubusercontent.com/{repo_path}/{PUBLIC_REPO_REF}/scripts/install-agent.sh"
    return "https://example.invalid/install-agent.sh"
