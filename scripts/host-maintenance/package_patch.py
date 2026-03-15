#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys


def _run(command: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    stdout = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()
    return result.returncode, stdout


def _check_only() -> dict[str, object]:
    rc, stdout = _run(["sh", "-lc", "apt list --upgradable 2>/dev/null | tail -n +2"])
    packages = [line.strip() for line in stdout.splitlines() if line.strip()]
    return {
        "exit_code": rc,
        "dry_run": True,
        "packages": packages,
        "package_count": len(packages),
        "reboot_required": os.path.exists("/var/run/reboot-required"),
        "stdout": "\n".join(
            line
            for line in [
                "Dry run requested. Reporting pending package state without applying changes.",
                stdout,
            ]
            if line
        ).strip(),
    }


def main() -> int:
    if "--dry-run" in sys.argv[1:]:
        payload = _check_only()
        print(json.dumps(payload))
        return int(payload["exit_code"])

    output_lines: list[str] = []
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    commands = [
        ["apt-get", "update"],
        ["apt-get", "dist-upgrade", "-y"],
        ["apt-get", "autoremove", "-y"],
    ]
    exit_code = 0
    for command in commands:
        rc, stdout = _run(command, env=env)
        if stdout:
            output_lines.append(stdout)
        if rc != 0:
            exit_code = rc
            break

    payload = {
        "exit_code": exit_code,
        "dry_run": False,
        "reboot_required": os.path.exists("/var/run/reboot-required"),
        "stdout": "\n".join(part for part in output_lines if part).strip(),
    }
    print(json.dumps(payload))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
