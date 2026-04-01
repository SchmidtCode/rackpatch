from __future__ import annotations

from typing import Any, Callable
import re
import time

import requests

from common import config


DEFAULT_VERSION_STRATEGY = "stable"
DEFAULT_SEMVER_POLICY = "patch"
DEFAULT_ALLOW_PRERELEASE = False
DEFAULT_ALLOW_MAJOR_UPGRADES = False
DEFAULT_RESOLVE_TO_DIGEST = True
SUPPORTED_VERSION_STRATEGIES = {"latest", "stable", "previous_stable"}
SUPPORTED_SEMVER_POLICIES = {"patch", "minor", "major"}
TAG_CACHE_TTL_SECONDS = 300

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"rackpatch/{config.APP_VERSION}"})
_TAG_CACHE: dict[str, dict[str, Any]] = {}

_IMAGE_REF_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9._/-]+)(?::(?P<tag>[A-Za-z0-9._-]+))?(?:@(?P<digest>sha256:[a-fA-F0-9]{64}))?$"
)
_SEMVER_RE = re.compile(
    r"^[vV]?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?(?P<suffix>[-+._]?[A-Za-z][0-9A-Za-z.+_-]*)?$"
)
_AUTH_CHALLENGE_RE = re.compile(r'([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"')


def normalize_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    stored = dict(policy or {})
    strategy = str(stored.get("version_strategy") or stored.get("strategy") or DEFAULT_VERSION_STRATEGY).strip().lower()
    if strategy not in SUPPORTED_VERSION_STRATEGIES:
        strategy = DEFAULT_VERSION_STRATEGY

    semver_policy = str(stored.get("semver_policy") or DEFAULT_SEMVER_POLICY).strip().lower()
    if semver_policy not in SUPPORTED_SEMVER_POLICIES:
        semver_policy = DEFAULT_SEMVER_POLICY

    return {
        "version_strategy": strategy,
        "semver_policy": semver_policy,
        "allow_prerelease": _normalize_bool(stored.get("allow_prerelease"), DEFAULT_ALLOW_PRERELEASE),
        "allow_major_upgrades": _normalize_bool(stored.get("allow_major_upgrades"), DEFAULT_ALLOW_MAJOR_UPGRADES),
        "resolve_to_digest": _normalize_bool(stored.get("resolve_to_digest"), DEFAULT_RESOLVE_TO_DIGEST),
    }


def _normalize_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def parse_image_ref(ref: str) -> dict[str, str] | None:
    value = str(ref or "").strip()
    if not value:
        return None
    match = _IMAGE_REF_RE.fullmatch(value)
    if not match:
        return None

    image_name = match.group("name") or ""
    digest = match.group("digest") or ""
    tag = match.group("tag") or ""

    registry = "docker.io"
    repository = image_name
    if "/" in image_name:
        first, remainder = image_name.split("/", 1)
        if "." in first or ":" in first or first == "localhost":
            registry = first
            repository = remainder

    api_repository = repository
    if registry == "docker.io" and "/" not in repository:
        api_repository = f"library/{repository}"

    display_repository = image_name
    effective_tag = tag or "latest"
    return {
        "ref": value,
        "registry": registry,
        "repository": repository,
        "api_repository": api_repository,
        "display_repository": display_repository,
        "tag": effective_tag,
        "digest": digest,
    }


def is_image_ref(value: str) -> bool:
    return parse_image_ref(value) is not None


def build_image_ref(display_repository: str, tag: str, digest: str = "") -> str:
    ref = f"{display_repository}:{tag}"
    if digest:
        ref = f"{ref}@{digest}"
    return ref


def parse_semverish(tag: str) -> dict[str, Any] | None:
    value = str(tag or "").strip()
    if not value:
        return None
    match = _SEMVER_RE.fullmatch(value)
    if not match:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor") or 0)
    patch = int(match.group("patch") or 0)
    suffix = str(match.group("suffix") or "")
    prerelease = bool(suffix and "-" in suffix or re.search(r"[A-Za-z]", suffix))
    return {
        "tag": value,
        "major": major,
        "minor": minor,
        "patch": patch,
        "prerelease": prerelease,
        "sort_key": (major, minor, patch, 0 if prerelease else 1),
    }


def version_is_newer(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    return tuple(candidate["sort_key"]) > tuple(current["sort_key"])


def _candidate_allowed(
    candidate: dict[str, Any],
    current: dict[str, Any] | None,
    policy: dict[str, Any],
) -> bool:
    if candidate.get("prerelease") and not policy["allow_prerelease"]:
        return False
    if current is None:
        return True

    if not policy["allow_major_upgrades"] and candidate["major"] != current["major"]:
        return False

    semver_policy = policy["semver_policy"]
    if semver_policy == "patch":
        return candidate["major"] == current["major"] and candidate["minor"] == current["minor"]
    if semver_policy == "minor":
        return candidate["major"] == current["major"]
    return True


def _cache_get(key: str) -> list[str] | None:
    item = _TAG_CACHE.get(key)
    if not item:
        return None
    if time.time() - float(item.get("fetched_at", 0.0)) > TAG_CACHE_TTL_SECONDS:
        return None
    value = item.get("value")
    return list(value) if isinstance(value, list) else None


def _cache_set(key: str, value: list[str]) -> list[str]:
    _TAG_CACHE[key] = {"fetched_at": time.time(), "value": list(value)}
    return list(value)


def _parse_authenticate_header(header: str) -> tuple[str, dict[str, str]]:
    scheme, _, remainder = str(header or "").partition(" ")
    params = {key: value for key, value in _AUTH_CHALLENGE_RE.findall(remainder)}
    return scheme.lower(), params


def _authorized_get(url: str, *, repository: str) -> requests.Response:
    response = SESSION.get(url, timeout=15)
    if response.status_code != 401:
        response.raise_for_status()
        return response

    scheme, params = _parse_authenticate_header(response.headers.get("WWW-Authenticate", ""))
    if scheme != "bearer":
        response.raise_for_status()

    token_params = {key: value for key, value in params.items() if key in {"service", "scope"} and value}
    scope = token_params.get("scope")
    if not scope:
        token_params["scope"] = f"repository:{repository}:pull"
    token_response = SESSION.get(str(params.get("realm") or ""), params=token_params, timeout=15)
    token_response.raise_for_status()
    token_payload = token_response.json()
    token = str(token_payload.get("token") or token_payload.get("access_token") or "")
    if not token:
        raise RuntimeError("registry token response did not contain a bearer token")

    authorized = SESSION.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    authorized.raise_for_status()
    return authorized


def list_registry_tags(ref: str) -> list[str]:
    parsed = parse_image_ref(ref)
    if not parsed:
        return []
    cache_key = f"{parsed['registry']}/{parsed['api_repository']}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if parsed["registry"] == "docker.io":
        registry_base = "https://registry-1.docker.io"
    else:
        registry_base = f"https://{parsed['registry']}"

    url = f"{registry_base}/v2/{parsed['api_repository']}/tags/list?n=100"
    response = _authorized_get(url, repository=parsed["api_repository"])
    payload = response.json()
    tags = [str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()]
    return _cache_set(cache_key, tags)


def choose_target_ref(
    current_ref: str,
    policy: dict[str, Any] | None,
    *,
    list_tags: Callable[[str], list[str]] | None = None,
    resolve_digest: Callable[[str], tuple[str | None, str | None]] | None = None,
) -> dict[str, Any]:
    normalized_policy = normalize_policy(policy)
    parsed = parse_image_ref(current_ref)
    if not parsed:
        return {
            "current_ref": current_ref,
            "target_ref": current_ref,
            "changed": False,
            "strategy": normalized_policy["version_strategy"],
            "reason": "current image reference could not be parsed",
            "error": "invalid image reference",
        }

    tags_provider = list_tags or list_registry_tags
    strategy = normalized_policy["version_strategy"]
    current_version = parse_semverish(parsed["tag"])
    target_tag = parsed["tag"]
    version_reason = "current tag retained"

    if strategy == "latest":
        target_tag = "latest"
        version_reason = "following mutable latest tag"
    else:
        available_tags = [tag for tag in tags_provider(current_ref) if tag]
        semver_candidates = [item for item in (parse_semverish(tag) for tag in available_tags) if item is not None]
        semver_candidates = [item for item in semver_candidates if _candidate_allowed(item, current_version, normalized_policy)]
        semver_candidates.sort(key=lambda item: item["sort_key"], reverse=True)

        if not semver_candidates:
            return {
                "current_ref": current_ref,
                "target_ref": current_ref,
                "changed": False,
                "strategy": strategy,
                "reason": "no matching release tags were found",
                "error": "no matching release tags were found",
            }

        if strategy == "previous_stable" and len(semver_candidates) > 1:
            target_tag = semver_candidates[1]["tag"]
            version_reason = "tracking one stable version behind newest"
        else:
            target_tag = semver_candidates[0]["tag"]
            version_reason = "newer stable release detected"

        if current_version is not None:
            matching = next((item for item in semver_candidates if item["tag"] == target_tag), None)
            if matching is not None and not version_is_newer(matching, current_version):
                target_tag = parsed["tag"]
                version_reason = "current version already satisfies the selected policy"

    target_ref = build_image_ref(parsed["display_repository"], target_tag)
    target_digest = ""
    digest_error = ""
    if normalized_policy["resolve_to_digest"] and resolve_digest is not None:
        digest, error = resolve_digest(target_ref)
        target_digest = str(digest or "")
        digest_error = str(error or "")
        if target_digest:
            target_ref = build_image_ref(parsed["display_repository"], target_tag, target_digest)

    return {
        "current_ref": current_ref,
        "current_tag": parsed["tag"],
        "current_digest": parsed["digest"],
        "target_ref": target_ref,
        "target_tag": target_tag,
        "target_digest": target_digest,
        "repository": parsed["display_repository"],
        "strategy": strategy,
        "semver_policy": normalized_policy["semver_policy"],
        "allow_prerelease": normalized_policy["allow_prerelease"],
        "allow_major_upgrades": normalized_policy["allow_major_upgrades"],
        "resolve_to_digest": normalized_policy["resolve_to_digest"],
        "reason": version_reason,
        "changed": target_ref != current_ref,
        "error": digest_error,
    }
