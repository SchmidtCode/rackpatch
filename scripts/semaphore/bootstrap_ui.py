#!/usr/bin/env python3

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


OPS_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = OPS_ROOT / ".env"
DEFAULT_URL = "http://127.0.0.1:3011"
PROJECT_NAME = "Homelab Updates"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


FILE_ENV = load_env_file(ENV_FILE)


def get_env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or FILE_ENV.get(name) or default


class SemaphoreClient:
    def __init__(self, base_url: str, auth: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self._login(auth, password)

    def _request(self, method: str, path: str, payload: dict | None = None, expect: tuple[int, ...] = (200, 201, 204)):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method, headers=headers)
        try:
            with self.opener.open(req) as resp:
                body = resp.read().decode()
                if resp.status not in expect:
                    raise RuntimeError(f"{method} {path} returned {resp.status}: {body}")
                if not body:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            raise RuntimeError(f"{method} {path} returned {exc.code}: {body}") from exc

    def _login(self, auth: str, password: str) -> None:
        self._request("POST", "/api/auth/login", {"auth": auth, "password": password}, expect=(200, 204))

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, payload: dict):
        return self._request("POST", path, payload, expect=(200, 201))

    def put(self, path: str, payload: dict):
        return self._request("PUT", path, payload, expect=(200, 204))


def ensure_item(client: SemaphoreClient, list_path: str, create_path: str, update_path_tmpl: str, name: str, payload: dict):
    items = client.get(list_path)
    for item in items:
        if item.get("name") == name:
            desired = dict(payload)
            desired["id"] = item["id"]
            client.put(update_path_tmpl.format(id=item["id"]), desired)
            return item["id"], "updated"
    created = client.post(create_path, payload)
    return created["id"], "created"


def main() -> int:
    base_url = get_env("SEMAPHORE_URL", DEFAULT_URL)
    auth = get_env("SEMAPHORE_ADMIN_LOGIN", "opsadmin")
    password = get_env("SEMAPHORE_ADMIN_PASSWORD")
    if not password:
        print("SEMAPHORE_ADMIN_PASSWORD is not set in the environment or ops/.env", file=sys.stderr)
        return 1

    client = SemaphoreClient(base_url, auth, password)

    project_id, project_state = ensure_item(
        client,
        "/api/projects",
        "/api/projects",
        "/api/project/{id}",
        PROJECT_NAME,
        {
            "name": PROJECT_NAME,
            "max_parallel_tasks": 1,
            "alert": False,
        },
    )

    keys = client.get(f"/api/project/{project_id}/keys")
    none_key = next((item for item in keys if item.get("name") == "None"), None)
    if none_key is None:
        raise RuntimeError("Semaphore 'None' key store entry was not found")
    none_key_id = none_key["id"]

    inventory_id, inventory_state = ensure_item(
        client,
        f"/api/project/{project_id}/inventory",
        f"/api/project/{project_id}/inventory",
        f"/api/project/{project_id}/inventory/{{id}}",
        "Ops Inventory File",
        {
            "project_id": project_id,
            "name": "Ops Inventory File",
            "inventory": "/workspace/inventory/hosts.yml",
            "ssh_key_id": none_key_id,
            "type": "file",
        },
    )

    repo_id, repo_state = ensure_item(
        client,
        f"/api/project/{project_id}/repositories",
        f"/api/project/{project_id}/repositories",
        f"/api/project/{project_id}/repositories/{{id}}",
        "Ops Workspace",
        {
            "project_id": project_id,
            "name": "Ops Workspace",
            "git_url": "/workspace",
            "git_branch": "main",
            "ssh_key_id": none_key_id,
        },
    )

    dry_env_json = json.dumps(
        {
            "ANSIBLE_CONFIG": "/workspace/ansible.cfg",
            "OPS_DRY_RUN": "true",
            "PYTHONUNBUFFERED": "1",
            "TZ": get_env("TZ", "America/New_York"),
        }
    )
    live_env_json = json.dumps(
        {
            "ANSIBLE_CONFIG": "/workspace/ansible.cfg",
            "OPS_DRY_RUN": "false",
            "PYTHONUNBUFFERED": "1",
            "TZ": get_env("TZ", "America/New_York"),
        }
    )

    dry_env_id, dry_env_state = ensure_item(
        client,
        f"/api/project/{project_id}/environment",
        f"/api/project/{project_id}/environment",
        f"/api/project/{project_id}/environment/{{id}}",
        "Ops Dry Run",
        {
            "project_id": project_id,
            "name": "Ops Dry Run",
            "env": dry_env_json,
            "json": "{}",
        },
    )

    live_env_id, live_env_state = ensure_item(
        client,
        f"/api/project/{project_id}/environment",
        f"/api/project/{project_id}/environment",
        f"/api/project/{project_id}/environment/{{id}}",
        "Ops Live",
        {
            "project_id": project_id,
            "name": "Ops Live",
            "env": live_env_json,
            "json": "{}",
        },
    )

    templates = [
        {
            "name": "Discovery Report",
            "description": "Runs validation and the discovery playbook for image and package updates.",
            "playbook": "scripts/semaphore/run-discovery.sh",
            "environment_id": dry_env_id,
        },
        {
            "name": "Docker Update Check",
            "description": "Checks managed stack images against the registry without pulling or recreating containers.",
            "playbook": "scripts/semaphore/run-check-updates.sh",
            "environment_id": dry_env_id,
        },
        {
            "name": "Package Update Check",
            "description": "Checks managed guests and Proxmox nodes for pending apt package updates.",
            "playbook": "scripts/semaphore/run-check-packages.sh",
            "environment_id": dry_env_id,
        },
        {
            "name": "Low-Risk Docker Dry Run",
            "description": "Validates the low-risk Docker window without applying changes.",
            "playbook": "scripts/semaphore/run-low-risk-updates.sh",
            "environment_id": dry_env_id,
        },
        {
            "name": "Low-Risk Docker Updates",
            "description": "Runs the live low-risk Docker update window against the mounted ops workspace.",
            "playbook": "scripts/semaphore/run-low-risk-updates.sh",
            "environment_id": live_env_id,
        },
        {
            "name": "Approved Docker Review",
            "description": "Dry-run wrapper for approved Docker updates before the live window.",
            "playbook": "scripts/semaphore/run-approved-updates.sh",
            "environment_id": dry_env_id,
        },
        {
            "name": "Maintenance Window Review",
            "description": "Dry-run wrapper for the approved guest and container maintenance sequence.",
            "playbook": "scripts/semaphore/run-maintenance-window.sh",
            "environment_id": dry_env_id,
        },
        {
            "name": "Guest Patching Review",
            "description": "Dry-run wrapper for Docker guest patching.",
            "playbook": "scripts/semaphore/run-guest-patching.sh",
            "environment_id": dry_env_id,
        },
        {
            "name": "Proxmox Patching Review",
            "description": "Dry-run wrapper for serial Proxmox node patching.",
            "playbook": "scripts/semaphore/run-proxmox-patching.sh",
            "environment_id": dry_env_id,
        },
    ]

    template_results: list[tuple[str, str]] = []
    for item in templates:
        _, state = ensure_item(
            client,
            f"/api/project/{project_id}/templates",
            f"/api/project/{project_id}/templates",
            f"/api/project/{project_id}/templates/{{id}}",
            item["name"],
            {
                "project_id": project_id,
                "repository_id": repo_id,
                "inventory_id": inventory_id,
                "environment_id": item["environment_id"],
                "name": item["name"],
                "playbook": item["playbook"],
                "arguments": "[]",
                "description": item["description"],
                "app": "bash",
                "type": "",
            },
        )
        template_results.append((item["name"], state))

    print(f"project {PROJECT_NAME}: {project_state}")
    print(f"repository Ops Workspace: {repo_state}")
    print(f"inventory Ops Inventory File: {inventory_state}")
    print(f"environment Ops Dry Run: {dry_env_state}")
    print(f"environment Ops Live: {live_env_state}")
    for name, state in template_results:
        print(f"template {name}: {state}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
