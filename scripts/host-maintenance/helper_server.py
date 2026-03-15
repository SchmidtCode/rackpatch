#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socketserver
import subprocess
from pathlib import Path
from typing import Any


SOCKET_PATH = Path(os.environ.get("RACKPATCH_HOST_HELPER_SOCKET", "/run/rackpatch-host-helper.sock"))
PACKAGE_CHECK_CMD = Path(
    os.environ.get("RACKPATCH_HOST_PACKAGE_CHECK_CMD", "/usr/local/libexec/rackpatch-package-check")
)
PACKAGE_PATCH_CMD = Path(
    os.environ.get("RACKPATCH_HOST_PACKAGE_PATCH_CMD", "/usr/local/libexec/rackpatch-package-patch")
)
SOCKET_MODE = int(os.environ.get("RACKPATCH_HOST_HELPER_SOCKET_MODE", "660"), 8)


def _available_actions() -> dict[str, Path]:
    actions: dict[str, Path] = {}
    if PACKAGE_CHECK_CMD.is_file() and os.access(PACKAGE_CHECK_CMD, os.X_OK):
        actions["package_check"] = PACKAGE_CHECK_CMD
    if PACKAGE_PATCH_CMD.is_file() and os.access(PACKAGE_PATCH_CMD, os.X_OK):
        actions["package_patch"] = PACKAGE_PATCH_CMD
    return actions


def _response(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")


def _execute(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = result.stdout.strip()
    if not stdout:
        return {
            "ok": False,
            "error": "helper command returned no output",
            "stdout": result.stderr.strip(),
        }
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "helper command returned invalid JSON",
            "stdout": stdout,
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "helper command returned a non-object payload",
            "stdout": stdout,
        }
    return {"ok": True, "result": payload}


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.read().decode("utf-8").strip()
        if not raw:
            self.wfile.write(_response({"ok": False, "error": "empty request"}))
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.wfile.write(_response({"ok": False, "error": "invalid JSON request"}))
            return
        if not isinstance(payload, dict):
            self.wfile.write(_response({"ok": False, "error": "request must be a JSON object"}))
            return

        actions = _available_actions()
        action = str(payload.get("action") or "").strip()
        if action == "describe":
            self.wfile.write(
                _response(
                    {
                        "ok": True,
                        "actions": sorted(actions),
                        "detail": (
                            "Limited to approved maintenance actions only."
                            if actions
                            else "Host maintenance helper installed but no actions are enabled."
                        ),
                    }
                )
            )
            return
        if action not in actions:
            self.wfile.write(_response({"ok": False, "error": f"unsupported action: {action or 'unknown'}"}))
            return

        command = ["sudo", "-n", str(actions[action])]
        if action == "package_patch":
            dry_run = payload.get("dry_run", False)
            if not isinstance(dry_run, bool):
                self.wfile.write(_response({"ok": False, "error": "dry_run must be a boolean"}))
                return
            if dry_run:
                command.append("--dry-run")

        self.wfile.write(_response(_execute(command)))


class Server(socketserver.UnixStreamServer):
    allow_reuse_address = True


def main() -> int:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    with Server(str(SOCKET_PATH), Handler) as server:
        os.chmod(SOCKET_PATH, SOCKET_MODE)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
