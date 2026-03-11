#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
from pathlib import Path

import docker
import yaml


OPS_ROOT = Path("/workspace")
STACKS_FILE = Path(os.environ.get("OPS_STACKS_FILE", OPS_ROOT / "config" / "stacks.yml"))
ROLLBACK_ROOT = OPS_ROOT / "state" / "rollbacks"


def run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def load_stacks() -> list[dict]:
    with STACKS_FILE.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)["stacks"]


def resolve_stack(name: str) -> dict:
    for stack in load_stacks():
        if stack["name"] == name:
            return stack
    raise SystemExit(f"Unknown stack: {name}")


def resolve_rollback_file(stack_name: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    latest = ROLLBACK_ROOT / stack_name / "latest.json"
    if latest.exists():
        return latest
    raise SystemExit(f"No rollback file found for {stack_name}")


def split_image_ref(image_ref: str) -> tuple[str, str]:
    if ":" in image_ref.rsplit("/", 1)[-1]:
        repository, tag = image_ref.rsplit(":", 1)
        return repository, tag
    return image_ref, "latest"


def main() -> int:
    parser = argparse.ArgumentParser(description="Rollback a stack to the previously captured local image IDs.")
    parser.add_argument("--stack", required=True)
    parser.add_argument("--file")
    args = parser.parse_args()

    stack = resolve_stack(args.stack)
    rollback_file = resolve_rollback_file(args.stack, args.file)
    payload = json.loads(rollback_file.read_text(encoding="utf-8"))
    docker_client = docker.from_env()

    for service in payload.get("services", []):
        image_ref = service.get("configured_image_ref")
        image_id = service.get("image_id")
        if image_ref and image_id:
            repository, tag = split_image_ref(image_ref)
            docker_client.images.get(image_id).tag(repository, tag=tag)

    compose_cmd = ["/workspace/scripts/compose-wrapper.sh"]
    for env_file in stack.get("compose_env_files", []):
        compose_cmd.extend(["--env-file", env_file])
    compose_cmd.extend(["up", "-d"])
    run(compose_cmd, cwd=Path(stack.get("path") or stack.get("project_dir")))

    print(f"Rolled back stack {args.stack} using {rollback_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
