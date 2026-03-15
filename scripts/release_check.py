#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_SUFFIXES = {".pem", ".key", ".crt", ".p12", ".pfx"}
ALLOWED_SITE_PREFIX = "sites/example/"


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=False,
        check=True,
    )
    return [item for item in result.stdout.decode("utf-8").split("\0") if item]


def release_violations(paths: list[str]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        posix_path = Path(path).as_posix()
        if posix_path == ".env" or (posix_path.startswith(".env.") and posix_path != ".env.example"):
            errors.append(f"tracked env file: {posix_path}")
        if posix_path.startswith("secrets/"):
            errors.append(f"tracked secrets directory content: {posix_path}")
        if Path(posix_path).suffix.lower() in SENSITIVE_SUFFIXES:
            errors.append(f"tracked key or certificate file: {posix_path}")
        if posix_path.startswith("sites/") and not posix_path.startswith(ALLOWED_SITE_PREFIX):
            errors.append(f"tracked private site overlay: {posix_path}")
    return errors


def main() -> int:
    try:
        paths = tracked_files()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr.decode("utf-8") if exc.stderr else str(exc), file=sys.stderr)
        return 1

    errors = release_violations(paths)
    if errors:
        print("release check failed:", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"release check passed: {len(paths)} tracked files scanned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
