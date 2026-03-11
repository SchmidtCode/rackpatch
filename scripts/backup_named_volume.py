#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

import docker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive a Docker named volume into a tarball.")
    parser.add_argument("--volume", required=True, help="Docker volume name to archive")
    parser.add_argument("--backup-root", required=True, help="Host path for backup output")
    parser.add_argument("--output-name", required=True, help="Archive filename to create inside backup root")
    parser.add_argument("--image", default="busybox:1.36", help="Container image used for the archive helper")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    backup_root = os.path.abspath(args.backup_root)
    os.makedirs(backup_root, exist_ok=True)

    client = docker.from_env()
    client.images.pull(args.image)
    current_container = None
    if Path("/.dockerenv").exists():
        current_id = os.environ.get("OPS_CONTAINER_NAME")
        if not current_id:
            hostname_file = Path("/etc/hostname")
            if hostname_file.exists():
                current_id = hostname_file.read_text(encoding="utf-8").strip()
        if current_id:
            try:
                current_container = client.containers.get(current_id).name
            except docker.errors.DockerException:
                current_container = current_id

    command = [
        "sh",
        "-c",
        f"tar czf {backup_root.rstrip('/')}/{args.output_name} -C /source .",
    ]

    run_kwargs = {
        "command": command,
        "remove": True,
        "volumes": {
            args.volume: {"bind": "/source", "mode": "ro"},
        },
    }
    if current_container:
        run_kwargs["volumes_from"] = [current_container]
    else:
        run_kwargs["volumes"][backup_root] = {"bind": backup_root, "mode": "rw"}

    client.containers.run(args.image, **run_kwargs)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except docker.errors.DockerException as exc:
        print(f"volume backup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
