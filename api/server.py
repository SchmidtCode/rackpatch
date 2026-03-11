#!/usr/bin/env python3
import fcntl
import json
import os
import subprocess
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKDIR = Path("/workspace")
LOCK_FILE = WORKDIR / "state" / "ops-execution.lock"
API_HOST = os.environ.get("OPS_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("OPS_API_PORT", "9080"))
API_TOKEN = os.environ.get("OPS_API_TOKEN", "")


class ExecutionBusyError(RuntimeError):
    pass


def run_command(command):
    completed = subprocess.run(
        command,
        cwd=WORKDIR,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def run_locked_command(command):
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExecutionBusyError("another ops execution is already running") from exc
        return run_command(command)


def run_update_check(payload):
    command = ["python3", "scripts/check_stack_updates.py"]
    window = payload.get("window")
    if window:
        command.extend(["--window", window])
    for stack in payload.get("approved_services", []) or payload.get("stacks", []) or []:
        command.extend(["--stack", stack])
    result = run_command(command)
    if result["exit_code"] != 0:
        return None, result
    return json.loads(result["stdout"]), None


def run_package_check(payload):
    command = ["python3", "scripts/check_package_updates.py"]
    scope = payload.get("scope")
    if scope:
        command.extend(["--scope", scope])
    for host in payload.get("hosts", []) or []:
        command.extend(["--host", host])
    result = run_command(command)
    if result["exit_code"] != 0:
        return None, result
    return json.loads(result["stdout"]), None


def parse_artifacts(stdout):
    artifacts = {"backup": [], "rollback": [], "snapshot": []}
    for raw_line in stdout.splitlines():
        if "OPS_ARTIFACT " not in raw_line:
            continue
        line = raw_line.split("OPS_ARTIFACT ", 1)[1].strip().strip('"').strip(",")
        parts = {}
        for field in line.split():
            if "=" not in field:
                continue
            key, value = field.split("=", 1)
            parts[key] = value
        kind = parts.get("kind")
        value = parts.get("value")
        stack = parts.get("stack")
        if kind in artifacts and value:
            artifacts[kind].append({"stack": stack, "value": value})
    return artifacts


def build_playbook_command(payload):
    target = payload.get("target", payload.get("window", "approved_guest_container"))
    approved_services = payload.get("approved_services", [])
    dry_run = bool(payload.get("dry_run", False))
    limit = payload.get("limit", "")
    reboot_mode = payload.get("reboot_mode", "soft")
    docker_update_vars = json.dumps(
        {
            "target_window": "auto-windowed",
            "dry_run": dry_run,
        },
        separators=(",", ":"),
    )
    selected_stack_vars = json.dumps(
        {
            "selected_stacks": approved_services,
            "dry_run": dry_run,
        },
        separators=(",", ":"),
    )
    maintenance_vars = json.dumps(
        {
            "approved_services": approved_services,
            "dry_run": dry_run,
        },
        separators=(",", ":"),
    )
    guest_patch_vars = {"dry_run": dry_run}
    if payload.get("allow_manual_guests"):
        guest_patch_vars["allow_manual_guests"] = True
    if payload.get("force_dns_critical"):
        guest_patch_vars["force_dns_critical"] = True
    guest_patch_vars_json = json.dumps(guest_patch_vars, separators=(",", ":"))
    dry_run_vars = json.dumps({"dry_run": dry_run}, separators=(",", ":"))

    if target in {"auto-windowed", "docker-auto"}:
        return ["ansible-playbook", "playbooks/apply_docker_updates.yml", "-e", docker_update_vars]
    if target in {"approve", "docker-approved"}:
        return [
            "ansible-playbook",
            "playbooks/apply_docker_updates.yml",
            "-e",
            selected_stack_vars,
        ]
    if target in {"approved_guest_container", "maintenance"}:
        return [
            "ansible-playbook",
            "playbooks/maintenance_orchestrator.yml",
            "-e",
            maintenance_vars,
        ]
    if target == "discovery":
        return ["ansible-playbook", "playbooks/discover_updates.yml"]
    if target == "patch-guests":
        command = ["ansible-playbook", "playbooks/patch_guests.yml"]
        if limit:
            command.extend(["--limit", limit])
        command.extend(["-e", guest_patch_vars_json])
        return command
    if target == "patch-proxmox":
        command = ["ansible-playbook", "playbooks/patch_proxmox_nodes.yml"]
        if limit:
            command.extend(["--limit", limit])
        command.extend(["-e", dry_run_vars])
        return command
    if target == "reboot-proxmox":
        reboot_vars = json.dumps(
            {
                "dry_run": dry_run,
                "reboot_mode": reboot_mode,
            },
            separators=(",", ":"),
        )
        command = ["ansible-playbook", "playbooks/reboot_proxmox_nodes.yml"]
        if limit:
            command.extend(["--limit", limit])
        command.extend(["-e", reboot_vars])
        return command
    raise ValueError(f"unsupported target: {target}")


class Handler(BaseHTTPRequestHandler):
    server_version = "ops-controller/1.0"

    def _send(self, status, payload):
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_token(self):
        if not API_TOKEN:
            return True
        return self.headers.get("X-Ops-Token", "") == API_TOKEN

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self._send(HTTPStatus.OK, {"status": "ok"})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not-found"})

    def do_POST(self):
        if not self._require_token():
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        if self.path == "/render-payload":
            payload = self._read_json()
            command = ["python3", "scripts/render_approval_payload.py", "--window", payload.get("window", "approve")]
            temp_path = None
            if "source_event" in payload:
                with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
                    json.dump(payload["source_event"], handle)
                    temp_path = handle.name
                command.extend(["--event-file", temp_path])
            result = run_command(command)
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)
            status = HTTPStatus.OK if result["exit_code"] == 0 else HTTPStatus.BAD_REQUEST
            if result["exit_code"] == 0:
                self._send(status, json.loads(result["stdout"]))
            else:
                self._send(status, result)
            return

        if self.path == "/execute":
            payload = self._read_json()
            try:
                command = build_playbook_command(payload)
            except ValueError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            update_report = None
            update_error = None
            if payload.get("target") in {"auto-windowed", "docker-auto", "approve", "docker-approved", "maintenance", "approved_guest_container"}:
                update_report, check_result = run_update_check(payload)
                if check_result is not None:
                    update_error = check_result
            try:
                result = run_locked_command(command)
            except ExecutionBusyError as exc:
                self._send(HTTPStatus.CONFLICT, {"status": "busy", "error": str(exc)})
                return
            status = HTTPStatus.OK if result["exit_code"] == 0 else HTTPStatus.INTERNAL_SERVER_ERROR
            self._send(
                status,
                {
                    "status": "ok" if result["exit_code"] == 0 else "failed",
                    "window": payload.get("window"),
                    "approved_services": payload.get("approved_services", []),
                    "update_report": update_report,
                    "update_report_error": update_error,
                    "artifacts": parse_artifacts(result["stdout"]),
                    **result,
                },
            )
            return

        if self.path == "/check-updates":
            payload = self._read_json()
            report, error = run_update_check(payload)
            if error is not None:
                self._send(HTTPStatus.BAD_REQUEST, error)
                return
            self._send(HTTPStatus.OK, report)
            return

        if self.path == "/check-packages":
            payload = self._read_json()
            report, error = run_package_check(payload)
            if error is not None:
                self._send(HTTPStatus.BAD_REQUEST, error)
                return
            self._send(HTTPStatus.OK, report)
            return

        self._send(HTTPStatus.NOT_FOUND, {"error": "not-found"})


if __name__ == "__main__":
    httpd = ThreadingHTTPServer((API_HOST, API_PORT), Handler)
    print(f"ops-controller listening on {API_HOST}:{API_PORT}", flush=True)
    httpd.serve_forever()
