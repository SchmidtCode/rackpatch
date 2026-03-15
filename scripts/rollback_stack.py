#!/usr/bin/env python3

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

RACKPATCH_ROOT = Path("/workspace")
sys.path.insert(0, str(RACKPATCH_ROOT / "app"))

from common import stack_catalog  # noqa: E402


INVENTORY_FILE = Path(os.environ.get("RACKPATCH_INVENTORY_FILE", RACKPATCH_ROOT / "inventory" / "hosts.yml"))
ROLLBACK_ROOT = Path(os.environ.get("RACKPATCH_ROLLBACK_ROOT", "/data/rollbacks"))
LOCAL_HOSTS = {"", "localhost", "127.0.0.1"}


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)


def load_stacks() -> list[dict]:
    return stack_catalog.load_stack_catalog()


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


def is_local_host(host: str | None) -> bool:
    return (host or "localhost") in LOCAL_HOSTS


def strip_ansible_header(output: str) -> str:
    lines = output.splitlines()
    if lines and " | " in lines[0] and lines[0].rstrip().endswith(">>"):
        return "\n".join(lines[1:]).strip()
    return output.strip()


def run_remote_shell(host: str, script: str) -> str:
    command = ["ansible", "-i", str(INVENTORY_FILE), host, "-m", "shell", "-a", f"bash -lc {shlex.quote(script)}"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or strip_ansible_header(result.stdout) or f"remote command failed on {host}")
    return strip_ansible_header(result.stdout)


def local_compose_command(stack: dict, *args: str) -> list[str]:
    command = ["/workspace/scripts/compose-wrapper.sh"]
    for env_file in stack.get("compose_env_files", []):
        command.extend(["--env-file", env_file])
    command.extend(args)
    return command


def remote_compose_script(stack: dict, *args: str) -> str:
    env_parts = " ".join(
        f"--env-file {shlex.quote(str(env_file))}" for env_file in stack.get("compose_env_files", [])
    )
    arg_parts = " ".join(shlex.quote(part) for part in args)
    project_dir = shlex.quote(stack.get("path") or stack.get("project_dir"))
    compose_plugin = f"docker compose {env_parts} {arg_parts}".strip()
    compose_legacy = f"docker-compose {env_parts} {arg_parts}".strip()
    return "\n".join(
        [
            "set -euo pipefail",
            f"cd {project_dir}",
            "if docker compose version >/dev/null 2>&1; then",
            f"  {compose_plugin}",
            "else",
            f"  {compose_legacy}",
            "fi",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rollback a stack to the previously captured local image IDs.")
    parser.add_argument("--stack", required=True)
    parser.add_argument("--file")
    args = parser.parse_args()

    stack = resolve_stack(args.stack)
    rollback_file = resolve_rollback_file(args.stack, args.file)
    payload = json.loads(rollback_file.read_text(encoding="utf-8"))
    host = str(stack.get("host") or "localhost")

    for service in payload.get("services", []):
        image_ref = service.get("configured_image_ref")
        image_id = service.get("image_id")
        if image_ref and image_id:
            repository, tag = split_image_ref(image_ref)
            image_tag = f"{repository}:{tag}"
            if is_local_host(host):
                run(["docker", "image", "tag", image_id, image_tag])
            else:
                run_remote_shell(host, f"docker image tag {shlex.quote(image_id)} {shlex.quote(image_tag)}")

    if is_local_host(host):
        run(local_compose_command(stack, "up", "-d"), cwd=Path(stack.get("path") or stack.get("project_dir")))
    else:
        run_remote_shell(host, remote_compose_script(stack, "up", "-d"))

    print(f"Rolled back stack {args.stack} using {rollback_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
