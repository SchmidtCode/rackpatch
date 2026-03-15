#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> int:
    result = subprocess.run(
        ["sh", "-lc", "apt list --upgradable 2>/dev/null | tail -n +2"],
        capture_output=True,
        text=True,
        check=False,
    )
    packages = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    payload = {
        "exit_code": result.returncode,
        "packages": packages,
        "package_count": len(packages),
        "reboot_required": os.path.exists("/var/run/reboot-required"),
        "stdout": result.stdout.strip(),
    }
    print(json.dumps(payload))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
