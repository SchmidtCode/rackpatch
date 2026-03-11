#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import docker
import yaml


OPS_ROOT = Path(__file__).resolve().parents[1]
STACKS_FILE = Path(os.environ.get("OPS_STACKS_FILE", OPS_ROOT / "config" / "stacks.yml"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check managed Docker stacks for available image updates.")
    parser.add_argument("--window", default="all", help="Update window to inspect: all, approve, or auto-windowed.")
    parser.add_argument("--stack", action="append", dest="stacks", default=[], help="Specific stack name to inspect.")
    return parser.parse_args()


def load_stack_catalog() -> list[dict]:
    payload = yaml.safe_load(STACKS_FILE.read_text(encoding="utf-8")) or {}
    return payload.get("stacks", [])


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


def get_local_digest(image, ref: str) -> str | None:
    repo = normalize_repo(ref)
    for digest_ref in image.attrs.get("RepoDigests") or []:
        if digest_ref.startswith(f"{repo}@"):
            return digest_ref.split("@", 1)[1]
    if "@sha256:" in ref:
        return ref.split("@", 1)[1]
    return None


def compose_images(stack: dict) -> list[str]:
    command = ["/workspace/scripts/compose-wrapper.sh"]
    for env_file in stack.get("compose_env_files", []):
        command.extend(["--env-file", env_file])
    command.extend(["config", "--images"])
    result = subprocess.run(
        command,
        cwd=stack_path(stack),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "compose config --images failed")
    images = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and line not in images:
            images.append(line)
    return images


def evaluate_stack(stack: dict, docker_client) -> dict:
    report = {
        "name": stack["name"],
        "host": stack["host"],
        "risk": stack["risk"],
        "update_mode": stack["update_mode"],
        "image_count": 0,
        "outdated_count": 0,
        "status": "unknown",
        "images": [],
    }
    if stack["host"] != "localhost":
        report["status"] = "unsupported"
        report["error"] = f"remote host checks are not implemented for {stack['host']}"
        return report

    image_refs = compose_images(stack)
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
            local_image = docker_client.images.get(ref)
            image_report["local_digest"] = get_local_digest(local_image, ref)
        except Exception as exc:  # noqa: BLE001
            image_report["status"] = "missing-local"
            image_report["error"] = str(exc)

        try:
            registry_data = docker_client.images.get_registry_data(ref)
            image_report["remote_digest"] = registry_data.id or (registry_data.attrs.get("Descriptor") or {}).get("digest")
        except Exception as exc:  # noqa: BLE001
            image_report["status"] = "registry-error"
            image_report["error"] = str(exc)

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
    reports = [evaluate_stack(stack, client) for stack in selected]
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
