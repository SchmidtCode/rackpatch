#!/usr/bin/env python3

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

RACKPATCH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACKPATCH_ROOT / "app"))

from common import stack_catalog  # noqa: E402


INVENTORY_FILE = Path(
    os.environ.get("RACKPATCH_INVENTORY_FILE", RACKPATCH_ROOT / "sites" / "example" / "inventory" / "hosts.yml")
)
ROLLBACK_ROOT = Path(os.environ.get("RACKPATCH_ROLLBACK_ROOT", "/data/rollbacks"))
LOCAL_HOSTS = {"", "localhost", "127.0.0.1"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize live Docker update changes using rollback metadata.")
    parser.add_argument("--window", default="approve")
    parser.add_argument("--stack", action="append", dest="stacks", default=[])
    return parser.parse_args()


def load_stack_catalog() -> list[dict]:
    return stack_catalog.load_stack_catalog()


def is_local_host(host: str | None) -> bool:
    return (host or "localhost") in LOCAL_HOSTS


def stack_path(stack: dict) -> str:
    return str(stack.get("path") or stack.get("project_dir") or "")


def short_image_id(value: str | None) -> str:
    if not value:
        return "unknown"
    if value.startswith("sha256:"):
        return value[7:19]
    return value[:12]


def local_compose_command(stack: dict, *args: str) -> list[str]:
    command = [str(RACKPATCH_ROOT / "scripts" / "compose-wrapper.sh")]
    for env_file in stack.get("compose_env_files", []):
        command.extend(["--env-file", env_file])
    command.extend(args)
    return command


def wrap_remote_bash(script: str) -> str:
    return f"bash -lc {shlex.quote(script)}"


def remote_compose_command(stack: dict, *args: str) -> str:
    env_parts = " ".join(
        f"--env-file {shlex.quote(str(env_file))}" for env_file in stack.get("compose_env_files", [])
    )
    arg_parts = " ".join(shlex.quote(part) for part in args)
    project_dir = shlex.quote(stack_path(stack))
    compose_plugin = f"docker compose {env_parts} {arg_parts}".strip()
    compose_legacy = f"docker-compose {env_parts} {arg_parts}".strip()
    script = "\n".join(
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
    return wrap_remote_bash(script)


def strip_ansible_header(output: str) -> str:
    lines = output.splitlines()
    if lines and " | " in lines[0] and lines[0].rstrip().endswith(">>"):
        return "\n".join(lines[1:]).strip()
    return output.strip()


def run_remote_shell(host: str, command: str) -> str:
    result = subprocess.run(
        ["ansible", "-i", str(INVENTORY_FILE), host, "-m", "shell", "-a", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or strip_ansible_header(result.stdout) or f"remote command failed on {host}"
        raise RuntimeError(message)
    return strip_ansible_header(result.stdout)


def capture_state_local(stack: dict) -> dict:
    config_result = subprocess.run(
        local_compose_command(stack, "config", "--format", "json"),
        cwd=stack_path(stack),
        capture_output=True,
        text=True,
        check=False,
    )
    if config_result.returncode != 0:
        raise RuntimeError(config_result.stderr.strip() or config_result.stdout.strip() or "compose config failed")
    config = json.loads(config_result.stdout)
    services = []
    for service_name, service_def in (config.get("services") or {}).items():
        image_result = subprocess.run(
            local_compose_command(stack, "images", "-q", service_name),
            cwd=stack_path(stack),
            capture_output=True,
            text=True,
            check=False,
        )
        services.append(
            {
                "service": service_name,
                "configured_image_ref": str(service_def.get("image") or ""),
                "image_id": image_result.stdout.strip() if image_result.returncode == 0 else "",
            }
        )
    return {"services": services}


def capture_state_remote(stack: dict, host: str) -> dict:
    script = f"""
import json
import subprocess

env_files = {json.dumps(stack.get("compose_env_files", []))}
stack_path = {json.dumps(stack_path(stack))}

def compose_cmd(*args: str) -> list[str]:
    base = ["docker", "compose"]
    if subprocess.run(base + ["version"], capture_output=True, text=True, check=False).returncode != 0:
        base = ["docker-compose"]
    command = base[:]
    for env_file in env_files:
        command.extend(["--env-file", env_file])
    command.extend(args)
    return command

def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=stack_path, capture_output=True, text=True, check=False)

config_result = run(compose_cmd("config", "--format", "json"))
if config_result.returncode != 0:
    raise SystemExit(config_result.stderr or config_result.stdout or "compose config failed")
config = json.loads(config_result.stdout)
services = []
for service_name, service_def in (config.get("services") or {{}}).items():
    image_result = run(compose_cmd("images", "-q", service_name))
    services.append(
        {{
            "service": service_name,
            "configured_image_ref": str(service_def.get("image") or ""),
            "image_id": image_result.stdout.strip() if image_result.returncode == 0 else "",
        }}
    )
print(json.dumps({{"services": services}}, sort_keys=True))
"""
    return json.loads(run_remote_shell(host, wrap_remote_bash(f"python3 - <<'PY'\n{script}\nPY")))


def capture_current_state(stack: dict) -> dict:
    host = str(stack.get("host") or "localhost")
    if is_local_host(host):
        return capture_state_local(stack)
    return capture_state_remote(stack, host)


def load_before_state(stack_name: str) -> dict:
    latest = ROLLBACK_ROOT / stack_name / "latest.json"
    if not latest.exists():
        raise RuntimeError(f"missing rollback metadata for {stack_name}")
    return json.loads(latest.read_text(encoding="utf-8"))


def summarize_stack(stack: dict) -> dict:
    before = load_before_state(str(stack["name"]))
    after = capture_current_state(stack)
    before_services = {item.get("service"): item for item in before.get("services", [])}
    changes = []

    for current in after.get("services", []):
        service_name = current.get("service")
        previous = before_services.get(service_name, {})
        before_ref = str(previous.get("configured_image_ref") or "")
        after_ref = str(current.get("configured_image_ref") or "")
        before_image = str(previous.get("image_id") or "")
        after_image = str(current.get("image_id") or "")
        if before_ref == after_ref and before_image == after_image:
            continue
        changes.append(
            {
                "service": service_name,
                "from_ref": before_ref,
                "to_ref": after_ref,
                "from_image": before_image,
                "to_image": after_image,
                "from_short": short_image_id(before_image),
                "to_short": short_image_id(after_image),
            }
        )

    return {
        "stack": stack["name"],
        "host": str(stack.get("host") or "localhost"),
        "changed_services": len(changes),
        "services": changes,
    }


def main() -> int:
    args = parse_args()
    requested_stacks = []
    for item in args.stacks:
        requested_stacks.extend(part.strip() for part in item.split(",") if part.strip())

    stacks = load_stack_catalog()
    if requested_stacks:
        selected = [stack for stack in stacks if stack.get("name") in requested_stacks]
    elif args.window == "all":
        selected = stacks
    else:
        selected = [stack for stack in stacks if stack.get("update_mode") == args.window]

    summaries = [summarize_stack(stack) for stack in selected]
    payload = {
        "stack_count": len(summaries),
        "changed_stacks": sum(1 for item in summaries if item.get("changed_services", 0) > 0),
        "changed_services": sum(item.get("changed_services", 0) for item in summaries),
        "stacks": summaries,
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
