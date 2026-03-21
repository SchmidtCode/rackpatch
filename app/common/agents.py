from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from common import config


def offline_grace_seconds() -> float:
    return max(float(config.AGENT_POLL_SECONDS) * 3, 60.0)


def last_seen_at(agent: Mapping[str, Any]) -> datetime | None:
    value = agent.get("last_seen_at")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    text = str(value or "").strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def effective_status(agent: Mapping[str, Any], now: datetime | None = None) -> str:
    status = str(agent.get("status") or "unknown").strip().lower() or "unknown"
    if status != "online":
        return status

    seen_at = last_seen_at(agent)
    if seen_at is None:
        return status

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if current - seen_at > timedelta(seconds=offline_grace_seconds()):
        return "offline"
    return status


def with_effective_status(agent: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    return {**dict(agent), "status": effective_status(agent, now)}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip()
    if text == "/":
        return text
    return text.rstrip("/")


def _split_paths(value: Any) -> list[str]:
    raw_items = value if isinstance(value, (list, tuple, set)) else str(value or "").split(",")
    items: list[str] = []
    for item in raw_items:
        normalized = _normalize_path(item)
        if normalized and normalized not in items:
            items.append(normalized)
    return items


def identity(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    data = metadata or {}
    return {
        "hostname": _normalize_text(data.get("hostname")),
        "mode": _normalize_text(data.get("mode")),
        "compose_dir": _normalize_path(data.get("compose_dir")),
        "install_dir": _normalize_path(data.get("install_dir")),
    }


def has_install_location(metadata: Mapping[str, Any] | None) -> bool:
    details = identity(metadata)
    return bool(details["compose_dir"] or details["install_dir"])


def same_identity(existing: Mapping[str, Any] | None, incoming: Mapping[str, Any] | None) -> bool:
    current = identity(existing)
    desired = identity(incoming)
    if not desired["hostname"] or current["hostname"] != desired["hostname"]:
        return False
    if desired["mode"] and current["mode"] and current["mode"] != desired["mode"]:
        return False

    desired_paths = [desired["compose_dir"], desired["install_dir"]]
    current_paths = {current["compose_dir"], current["install_dir"]}
    if any(desired_paths):
        return any(path and path in current_paths for path in desired_paths)
    if current["compose_dir"] or current["install_dir"]:
        return False
    return True


def can_reuse_agent_record(
    agent: Mapping[str, Any],
    incoming_metadata: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> bool:
    if not same_identity(agent.get("metadata"), incoming_metadata):
        return False
    if has_install_location(agent.get("metadata")) and has_install_location(incoming_metadata):
        return True
    return effective_status(agent, now) != "online"


def runtime_mode(agent: Mapping[str, Any] | None) -> str:
    data = agent or {}
    metadata = data.get("metadata") if isinstance(data, Mapping) and "metadata" in data else data
    return _normalize_text((metadata or {}).get("mode"))


def stack_roots(agent: Mapping[str, Any] | None) -> list[str]:
    data = agent or {}
    metadata = data.get("metadata") if isinstance(data, Mapping) and "metadata" in data else data
    metadata = metadata or {}
    docker_meta = metadata.get("docker") or {}

    selected: list[str] = []
    for value in _split_paths(docker_meta.get("stack_roots")) + _split_paths(metadata.get("stack_roots")):
        if value not in selected:
            selected.append(value)
    for key in ("compose_dir", "install_dir"):
        path = _normalize_path(metadata.get(key))
        if path and path not in selected:
            selected.append(path)
    return selected


def _path_is_within(root: str, candidate: str) -> bool:
    normalized_root = _normalize_path(root)
    normalized_candidate = _normalize_path(candidate)
    if not normalized_root or not normalized_candidate:
        return False
    if normalized_root == "/":
        return normalized_candidate.startswith("/")
    return normalized_candidate == normalized_root or normalized_candidate.startswith(f"{normalized_root}/")


def project_dir_access_reason(agent: Mapping[str, Any] | None, project_dir: Any) -> str | None:
    normalized_dir = _normalize_path(project_dir)
    if not normalized_dir:
        return "project_dir is not set."

    mode = runtime_mode(agent)
    if mode not in {"compose", "container"}:
        return None

    roots = stack_roots(agent)
    if any(_path_is_within(root, normalized_dir) for root in roots):
        return None

    roots_label = ", ".join(roots) if roots else "none reported"
    return (
        f"Agent cannot access {normalized_dir} because it is outside the mounted stack roots "
        f"({roots_label}). Update or reinstall the agent with a stack-root mount that includes this path."
    )
