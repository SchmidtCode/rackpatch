#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from pathlib import Path

import docker


RACKPATCH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACKPATCH_ROOT / "app"))

from common import stack_catalog  # noqa: E402


LOCAL_HOSTS = {"", "localhost", "127.0.0.1"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check managed Docker stacks for available image updates.")
    parser.add_argument("--window", default="all", help="Update window to inspect: all, approve, or auto-windowed.")
    parser.add_argument("--stack", action="append", dest="stacks", default=[], help="Specific stack name to inspect.")
    return parser.parse_args()


def load_stack_catalog() -> list[dict]:
    return stack_catalog.load_stack_catalog()


def normalize_repo(ref: str) -> str:
    ref = ref.split("@", 1)[0]
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    if last_colon > last_slash:
        return ref[:last_colon]
    return ref


def short_digest(value: str | None) -> str:
    if not value:
        return "unknown"
    if value.startswith("sha256:"):
        return value[7:19]
    return value[:12]


def get_local_digest(image_attrs: dict, ref: str) -> str | None:
    repo = normalize_repo(ref)
    for digest_ref in image_attrs.get("RepoDigests") or []:
        if digest_ref.startswith(f"{repo}@"):
            return digest_ref.split("@", 1)[1]
    if "@sha256:" in ref:
        return ref.split("@", 1)[1]
    return None


def is_local_host(host: str | None) -> bool:
    return (host or "localhost") in LOCAL_HOSTS


def stack_host(stack: dict) -> str:
    return str(stack.get("host") or "localhost")


def local_compose_command(stack: dict) -> list[str]:
    command = ["/workspace/scripts/compose-wrapper.sh"]
    for env_file in stack.get("compose_env_files", []):
        command.extend(["--env-file", env_file])
    return command


def local_compose_config(stack: dict) -> dict:
    command = local_compose_command(stack)
    command.extend(["config", "--format", "json"])
    result = subprocess.run(
        command,
        cwd=stack_path(stack),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "compose config failed")
    return json.loads(result.stdout)


def compose_images(stack: dict, host: str) -> list[str]:
    if not is_local_host(host):
        raise RuntimeError("remote compose inspection was removed; use agent heartbeats for remote stack discovery")
    config = local_compose_config(stack)

    images = []
    for service in (config.get("services") or {}).values():
        ref = str(service.get("image") or "").strip()
        if ref and ref not in images:
            images.append(ref)
    return images


def local_image_attrs(docker_client, ref: str) -> dict:
    return docker_client.images.get(ref).attrs


def registry_digest(docker_client, ref: str, cache: dict[str, dict[str, str | None]]) -> tuple[str | None, str | None]:
    cached = cache.get(ref)
    if cached:
        return cached.get("digest"), cached.get("error")

    try:
        registry_data = docker_client.images.get_registry_data(ref)
        digest = registry_data.id or (registry_data.attrs.get("Descriptor") or {}).get("digest")
        payload = {"digest": digest, "error": None}
    except Exception as exc:  # noqa: BLE001
        payload = {"digest": None, "error": str(exc)}
    cache[ref] = payload
    return payload["digest"], payload["error"]


def evaluate_stack(stack: dict, docker_client, registry_cache: dict[str, dict[str, str | None]]) -> dict:
    host = stack_host(stack)
    report = {
        "name": stack["name"],
        "host": host,
        "risk": stack["risk"],
        "update_mode": stack["update_mode"],
        "image_count": 0,
        "outdated_count": 0,
        "status": "unknown",
        "images": [],
    }
    if not is_local_host(host):
        report["status"] = "agent-heartbeat"
        report["error"] = "Remote stack discovery is agent-reported now; worker-side remote inspection has been removed."
        return report

    image_refs = compose_images(stack, host)
    report["image_count"] = len(image_refs)
    if not image_refs:
        report["status"] = "no-images"
        return report

    for ref in image_refs:
        image_report = {
            "ref": ref,
            "status": "unknown",
            "local_digest": None,
            "remote_digest": None,
        }
        try:
            image_attrs = local_image_attrs(docker_client, ref)
            image_report["local_digest"] = get_local_digest(image_attrs, ref)
        except Exception as exc:  # noqa: BLE001
            image_report["status"] = "missing-local"
            image_report["error"] = str(exc)

        image_report["remote_digest"], registry_error = registry_digest(docker_client, ref, registry_cache)
        if registry_error:
            image_report["status"] = "registry-error"
            image_report["error"] = registry_error

        if image_report["status"] == "unknown":
            if not image_report["local_digest"]:
                image_report["status"] = "unknown-local-digest"
            elif image_report["local_digest"] == image_report["remote_digest"]:
                image_report["status"] = "up-to-date"
            else:
                image_report["status"] = "outdated"
                report["outdated_count"] += 1

        image_report["local_short"] = short_digest(image_report["local_digest"])
        image_report["remote_short"] = short_digest(image_report["remote_digest"])
        report["images"].append(image_report)

    if any(item["status"] == "outdated" for item in report["images"]):
        report["status"] = "outdated"
    elif any(item["status"] in {"registry-error", "missing-local", "unknown-local-digest"} for item in report["images"]):
        report["status"] = "warning"
    else:
        report["status"] = "up-to-date"
    return report


def stack_path(stack: dict) -> str:
    return stack.get("path") or stack.get("project_dir")


def main() -> int:
    args = parse_args()
    requested_stacks = []
    for item in args.stacks:
        requested_stacks.extend(part.strip() for part in item.split(",") if part.strip())

    stacks = load_stack_catalog()
    if requested_stacks:
        selected = [stack for stack in stacks if stack["name"] in requested_stacks]
    elif args.window == "all":
        selected = stacks
    else:
        selected = [stack for stack in stacks if stack["update_mode"] == args.window]

    client = docker.from_env()
    registry_cache: dict[str, dict[str, str | None]] = {}
    reports = [evaluate_stack(stack, client, registry_cache) for stack in selected]
    payload = {
        "window": args.window,
        "requested_stacks": requested_stacks,
        "stack_count": len(reports),
        "outdated_stacks": sum(1 for item in reports if item["status"] == "outdated"),
        "outdated_images": sum(item["outdated_count"] for item in reports),
        "stacks": reports,
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
