#!/usr/bin/env python3

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

RACKPATCH_ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture current stack image state for rollback.")
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--stack-path", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--env-file", action="append", default=[])
    args = parser.parse_args()

    stack_path = Path(args.stack_path)
    output_path = Path(args.output)
    compose_cmd = [str(RACKPATCH_ROOT / "scripts" / "compose-wrapper.sh")]
    for env_file in args.env_file:
        compose_cmd.extend(["--env-file", env_file])

    config_cmd = compose_cmd + ["config", "--format", "json"]
    config_result = run(config_cmd, cwd=stack_path)
    if config_result.returncode != 0:
        raise SystemExit(config_result.stderr or config_result.stdout)
    config = json.loads(config_result.stdout)

    services = []
    for service_name, service_def in config.get("services", {}).items():
        image_ref = service_def.get("image", "")
        container_result = run(compose_cmd + ["ps", "-q", service_name], cwd=stack_path)
        container_id = container_result.stdout.strip()
        image_id = ""
        image_result = run(compose_cmd + ["images", "-q", service_name], cwd=stack_path)
        if image_result.returncode == 0:
            image_id = image_result.stdout.strip()
        services.append(
            {
                "service": service_name,
                "configured_image_ref": image_ref,
                "container_id": container_id,
                "image_id": image_id,
            }
        )

    git_meta = {}
    if (stack_path / ".git").exists():
        head_result = run(["git", "rev-parse", "HEAD"], cwd=stack_path)
        status_result = run(["git", "status", "--short"], cwd=stack_path)
        git_meta = {
            "head": head_result.stdout.strip() if head_result.returncode == 0 else "",
            "status": status_result.stdout.splitlines() if status_result.returncode == 0 else [],
        }

    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stack_name": args.stack_name,
        "stack_path": str(stack_path),
        "env_files": args.env_file,
        "git": git_meta,
        "services": services,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
