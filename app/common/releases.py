from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import re
import time
from urllib.parse import quote

import requests

from common import config, control_plane


CACHE_TTL_SECONDS = 300
SESSION = requests.Session()
SESSION.headers.update(
    {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"rackpatch/{config.APP_VERSION}",
    }
)
_CACHE: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _version_tokens(value: str) -> tuple[tuple[int, Any], ...]:
    normalized = str(value or "").strip().lstrip("vV")
    parts = re.findall(r"\d+|[A-Za-z]+", normalized)
    if not parts:
        return tuple()
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower()) for part in parts)


def compare_versions(current: str, latest: str) -> str:
    current_normalized = str(current or "").strip().lstrip("vV")
    latest_normalized = str(latest or "").strip().lstrip("vV")
    if not current_normalized or not latest_normalized:
        return "unknown"
    if current_normalized == latest_normalized:
        return "current"
    current_tokens = _version_tokens(current_normalized)
    latest_tokens = _version_tokens(latest_normalized)
    if not current_tokens or not latest_tokens:
        return "different"
    if current_tokens < latest_tokens:
        return "outdated"
    if current_tokens > latest_tokens:
        return "ahead"
    return "different"


def _cached(key: str) -> dict[str, Any] | None:
    item = _CACHE.get(key)
    if not item:
        return None
    if time.time() - float(item.get("fetched_at", 0.0)) > CACHE_TTL_SECONDS:
        return None
    return item["value"]


def _store_cache(key: str, value: dict[str, Any]) -> dict[str, Any]:
    _CACHE[key] = {
        "fetched_at": time.time(),
        "value": value,
    }
    return value


def fetch_latest_release(repo_url: str) -> dict[str, Any]:
    slug = config.github_repo_slug(repo_url)
    if not slug:
        return {
            "status": "unsupported",
            "checked_at": _now_iso(),
            "error": "latest version checks currently support GitHub repo URLs",
        }

    cached = _cached(slug)
    if cached:
        return cached

    try:
        release_response = SESSION.get(
            f"https://api.github.com/repos/{slug}/releases/latest",
            timeout=15,
        )
        if release_response.status_code == 200:
            payload = release_response.json()
            return _store_cache(
                slug,
                {
                    "status": "ok",
                    "source": "release",
                    "checked_at": _now_iso(),
                    "repo_slug": slug,
                    "version": str(payload.get("tag_name") or "").strip(),
                    "name": str(payload.get("name") or payload.get("tag_name") or "").strip(),
                    "url": str(payload.get("html_url") or "").strip(),
                    "published_at": payload.get("published_at"),
                    "prerelease": bool(payload.get("prerelease", False)),
                },
            )
        if release_response.status_code not in {404, 422}:
            release_response.raise_for_status()

        tags_response = SESSION.get(
            f"https://api.github.com/repos/{slug}/tags?per_page=1",
            timeout=15,
        )
        tags_response.raise_for_status()
        tags = tags_response.json()
        first = tags[0] if tags else {}
        return _store_cache(
            slug,
            {
                "status": "ok",
                "source": "tag",
                "checked_at": _now_iso(),
                "repo_slug": slug,
                "version": str(first.get("name") or "").strip(),
                "name": str(first.get("name") or "").strip(),
                "url": (
                    f"https://github.com/{slug}/tree/{quote(str(first.get('name') or '').strip(), safe='')}"
                    if first.get("name")
                    else ""
                ),
                "published_at": None,
                "prerelease": False,
            },
        )
    except requests.RequestException as exc:
        return _store_cache(
            slug,
            {
                "status": "error",
                "checked_at": _now_iso(),
                "repo_slug": slug,
                "error": str(exc),
            },
        )


def build_release_status(public_settings: dict[str, Any], agents: list[dict[str, Any]]) -> dict[str, Any]:
    latest = fetch_latest_release(str(public_settings.get("repo_url") or config.PUBLIC_REPO_URL))
    latest_version = str(latest.get("version") or "").strip()
    latest_ref = latest_version or str(public_settings.get("repo_ref") or config.PUBLIC_REPO_REF)

    stack_state = compare_versions(config.APP_VERSION, latest_version)
    agent_counts = {
        "total": len(agents),
        "current": 0,
        "outdated": 0,
        "ahead": 0,
        "different": 0,
        "unknown": 0,
    }
    agent_items: list[dict[str, Any]] = []
    for agent in agents:
        metadata = agent.get("metadata") or {}
        mode = str(metadata.get("mode") or "unknown")
        release_state = compare_versions(str(agent.get("version") or ""), latest_version)
        if release_state not in agent_counts:
            release_state = "different"
        agent_counts[release_state] += 1
        agent_items.append(
            {
                "id": agent.get("id"),
                "name": agent.get("name"),
                "display_name": agent.get("display_name"),
                "version": agent.get("version"),
                "mode": mode,
                "release_state": release_state,
            }
        )

    return {
        "current": {
            "stack_version": config.APP_VERSION,
        },
        "latest": latest,
        "stack": {
            "current_version": config.APP_VERSION,
            "latest_version": latest_version or None,
            "release_state": stack_state,
            "update_available": stack_state == "outdated",
        },
        "agents": {
            "summary": agent_counts,
            "items": agent_items,
            "latest_version": latest_version or None,
        },
        "update_commands": {
            "stack": control_plane.build_stack_update_command(public_settings, latest_ref),
            "agents": control_plane.build_agent_update_commands(public_settings, latest_ref),
        },
    }
