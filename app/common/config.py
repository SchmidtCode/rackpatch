from __future__ import annotations

import os
from pathlib import Path
import re


def env(name: str, default: str) -> str:
    if name in os.environ:
        return os.environ[name]
    return default


APP_NAME = "rackpatch"
APP_VERSION = "0.3.4"
GITHUB_REPO_PREFIXES = (
    "https://github.com/",
    "http://github.com/",
    "ssh://git@github.com/",
    "git@github.com:",
)
LEGACY_WORKSPACE_ROOT = Path("/workspace")

API_HOST = env("RACKPATCH_API_HOST", "0.0.0.0")
API_PORT = int(env("RACKPATCH_API_PORT", "9080"))
NOTIFY_PORT = int(env("RACKPATCH_NOTIFY_PORT", "9091"))

DB_DSN = env("RACKPATCH_DB_DSN", "postgresql://rackpatch:change-me@db:5432/rackpatch")
DATA_ROOT = Path(env("RACKPATCH_DATA_ROOT", "/data"))
JOBS_ROOT = DATA_ROOT / "jobs"
BACKUPS_ROOT = DATA_ROOT / "backups"
RUNTIME_ROOT = Path(env("RACKPATCH_RUNTIME_ROOT", str(Path(__file__).resolve().parents[2])))
APP_ROOT = RUNTIME_ROOT / "app"
SCRIPTS_ROOT = RUNTIME_ROOT / "scripts"
ANSIBLE_CONFIG_PATH = Path(env("ANSIBLE_CONFIG", str(RUNTIME_ROOT / "ansible.cfg")))
SITE_ROOT = Path(env("RACKPATCH_SITE_ROOT", str(RUNTIME_ROOT / "sites" / "example")))
SITE_NAME = env("RACKPATCH_SITE_NAME", SITE_ROOT.name)

ADMIN_USERNAME = env("RACKPATCH_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = env("RACKPATCH_ADMIN_PASSWORD", "change-me")
AUTH_SECRET = env("RACKPATCH_AUTH_SECRET", "change-me-in-production")
DEFAULT_AGENT_BOOTSTRAP_TOKEN = env("RACKPATCH_AGENT_BOOTSTRAP_TOKEN", "bootstrap-me")
PUBLIC_BASE_URL = env("RACKPATCH_PUBLIC_BASE_URL", "http://YOUR-RACKPATCH-HOST:3011").rstrip("/")
PUBLIC_REPO_URL = env("RACKPATCH_PUBLIC_REPO_URL", "https://github.com/SchmidtCode/rackpatch.git").rstrip("/")
PUBLIC_REPO_REF = env("RACKPATCH_PUBLIC_REPO_REF", "main")
PUBLIC_INSTALL_SCRIPT_URL = env("RACKPATCH_PUBLIC_INSTALL_SCRIPT_URL", "").rstrip("/")
PUBLIC_AGENT_COMPOSE_DIR = env("RACKPATCH_PUBLIC_AGENT_COMPOSE_DIR", "/srv/compose/rackpatch-agent").rstrip("/")
PUBLIC_RACKPATCH_COMPOSE_DIR = env("RACKPATCH_PUBLIC_RACKPATCH_COMPOSE_DIR", "/srv/compose/rackpatch").rstrip("/")
CORS_ORIGINS = [
    item.strip()
    for item in env("RACKPATCH_CORS_ORIGINS", "").split(",")
    if item.strip()
]

WORKER_POLL_SECONDS = float(env("RACKPATCH_WORKER_POLL_SECONDS", "5"))
SCHEDULE_POLL_SECONDS = float(env("RACKPATCH_SCHEDULE_POLL_SECONDS", "20"))
AGENT_POLL_SECONDS = float(env("RACKPATCH_AGENT_POLL_SECONDS", "10"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = [
    item.strip()
    for item in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
    if item.strip()
]


def derive_public_install_script_url(repo_url: str, repo_ref: str, explicit_url: str = "") -> str:
    return derive_public_script_url(repo_url, repo_ref, "scripts/install-agent.sh", explicit_url)


def github_repo_slug(repo_url: str) -> str | None:
    value = str(repo_url or "").strip().rstrip("/")
    if not value:
        return None
    if value.endswith(".git"):
        value = value[:-4]
    for prefix in GITHUB_REPO_PREFIXES:
        if value.startswith(prefix):
            slug = value[len(prefix):].strip("/")
            return slug or None
    return None


def derive_public_script_url(repo_url: str, repo_ref: str, script_path: str, explicit_url: str = "") -> str:
    if explicit_url:
        return explicit_url.rstrip("/")

    repo_path = github_repo_slug(repo_url)
    if repo_path:
        return f"https://raw.githubusercontent.com/{repo_path}/{repo_ref}/{script_path.lstrip('/')}"
    return f"https://example.invalid/{Path(script_path).name}"


def public_install_script_url() -> str:
    return derive_public_install_script_url(PUBLIC_REPO_URL, PUBLIC_REPO_REF, PUBLIC_INSTALL_SCRIPT_URL)


def resolve_runtime_path(path: Path) -> Path:
    if path.exists():
        return path
    if not path.is_absolute():
        candidate = RUNTIME_ROOT / path
        return candidate if candidate.exists() else path
    try:
        relative = path.relative_to(LEGACY_WORKSPACE_ROOT)
    except ValueError:
        return path
    candidate = RUNTIME_ROOT / relative
    return candidate if candidate.exists() else path


def normalize_image_tag(ref: str) -> str:
    value = str(ref or "").strip()
    if not value or value in {"main", "master"}:
        return "latest"
    value = value.lstrip("vV")
    if not value:
        return "latest"
    if re.fullmatch(r"[A-Za-z0-9._-]+", value):
        return value
    return "latest"


def derive_public_image_ref(repo_url: str, repo_ref: str, image_name: str) -> str:
    slug = github_repo_slug(repo_url)
    tag = normalize_image_tag(repo_ref)
    if not slug:
        return f"ghcr.io/example-invalid/{image_name}:{tag}"
    owner = slug.split("/", 1)[0].lower()
    return f"ghcr.io/{owner}/{image_name}:{tag}"
