import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

docker_stub = types.ModuleType("docker")
docker_stub.DockerClient = object
docker_stub.from_env = lambda: None
sys.modules.setdefault("docker", docker_stub)

from agent import main


class ComposeConfigJsonTests(unittest.TestCase):
    def test_compose_config_json_ignores_stderr_warnings(self) -> None:
        with patch.object(main, "_compose_command", return_value=["docker", "compose"]):
            with patch.object(
                main,
                "run_command_split",
                return_value=(0, '{"services":{"web":{"image":"nginx:latest"}}}', "WARN[0000] The \"N8N\" variable is not set."),
            ):
                result = main._compose_config_json("/tmp/project", [".env"])

        self.assertEqual(result["payload"]["services"]["web"]["image"], "nginx:latest")
        self.assertEqual(result["warnings"], ['WARN[0000] The "N8N" variable is not set.'])

    def test_compose_config_json_recovers_from_warning_prefixed_output(self) -> None:
        mixed_output = 'WARN[0000] The "N8N" variable is not set.\n{"services":{"web":{"image":"nginx:latest"}}}'
        with patch.object(main, "_compose_command", return_value=["docker", "compose"]):
            with patch.object(main, "run_command_split", return_value=(0, mixed_output, "")):
                result = main._compose_config_json("/tmp/project", [".env"])

        self.assertEqual(result["payload"]["services"]["web"]["image"], "nginx:latest")
        self.assertEqual(result["warnings"], ['WARN[0000] The "N8N" variable is not set.'])

    def test_compose_config_json_rejects_invalid_json(self) -> None:
        with patch.object(main, "_compose_command", return_value=["docker", "compose"]):
            with patch.object(main, "run_command_split", return_value=(0, "not json", "")):
                with self.assertRaisesRegex(RuntimeError, "compose config returned invalid json"):
                    main._compose_config_json("/tmp/project", [".env"])


class AgentUpdateTests(unittest.TestCase):
    def test_agent_update_uses_helper_container_for_compose_mode(self) -> None:
        state_dir = Path("/tmp/rackpatch-agent-test-state")
        helper = types.SimpleNamespace(id="helper-container-id")
        current_container = types.SimpleNamespace(
            image=types.SimpleNamespace(tags=["ghcr.io/schmidtcode/rackpatch-agent:0.3.7"], id="sha256:current"),
            attrs={"Mounts": [{"Destination": str(state_dir), "Source": "/host/agent-state"}]},
        )
        client = MagicMock()
        client.containers.get.return_value = current_container
        client.containers.run.return_value = helper

        with patch.object(main, "docker_client", return_value=client):
            with patch.object(main.socket, "gethostname", return_value="current-agent-container"):
                with patch.object(main, "STATE_DIR", state_dir):
                    result = main.agent_update(
                        {
                            "update_command": "curl -fsSL https://example.invalid/update.sh | bash -s -- --mode compose",
                            "update_mode": "compose",
                            "update_target_dir": "/srv/compose/rackpatch-agent",
                            "target_version": "v0.3.8",
                            "delay_seconds": 5,
                        }
                    )

        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["scheduled"])
        self.assertTrue(result["helper_container_name"].startswith("rackpatch-agent-updater-"))
        self.assertEqual(result["target_version"], "v0.3.8")
        client.containers.get.assert_called_once_with("current-agent-container")
        client.containers.run.assert_called_once()
        _, kwargs = client.containers.run.call_args
        self.assertEqual(kwargs["name"], result["helper_container_name"])
        self.assertEqual(kwargs["working_dir"], "/srv/compose/rackpatch-agent")
        self.assertEqual(kwargs["volumes"]["/host/agent-state"]["bind"], "/tmp/rackpatch-agent-test-state")
        self.assertEqual(kwargs["volumes"]["/srv/compose/rackpatch-agent"]["bind"], "/srv/compose/rackpatch-agent")

    def test_agent_update_requires_target_dir_for_compose_mode(self) -> None:
        with patch.object(main, "docker_client", return_value=MagicMock()):
            with patch.object(main, "STATE_DIR", Path("/tmp/rackpatch-agent-test-state-missing")):
                result = main.agent_update(
                    {
                        "update_command": "echo update",
                        "update_mode": "compose",
                    }
                )

        self.assertEqual(result["exit_code"], 1)
        self.assertIn("update_target_dir", result["error"])


class PathValidationTests(unittest.TestCase):
    def test_path_is_within_rejects_dotdot_escape_after_resolution(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "stacks"
            other = Path(tmpdir) / "other"
            root.mkdir()
            other.mkdir()

            candidate = root / ".." / "other" / "project"

            self.assertFalse(main._path_is_within(str(root), str(candidate)))

    def test_project_dir_access_error_treats_resolved_escape_as_outside_stack_roots(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "stacks"
            other = Path(tmpdir) / "other"
            root.mkdir()
            other.mkdir()
            project_dir = root / ".." / "other" / "project"

            with patch.object(main, "AGENT_MODE", "container"):
                with patch.object(main, "AGENT_STACK_ROOTS", [str(root)]):
                    error = main._project_dir_access_error(str(project_dir))

            self.assertIsNotNone(error)
            assert error is not None
            self.assertIn("outside this agent container's mounted stack roots", error)


class EnvRefPolicyTests(unittest.TestCase):
    def test_select_env_ref_targets_only_rewrites_env_managed_refs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            env_path = project_dir / "compose-images.envvars"
            env_path.write_text(
                "APP_IMAGE=ghcr.io/example/app:v1.2.3\nUNRELATED=not-an-image\n",
                encoding="utf-8",
            )
            config_payload = {
                "services": {
                    "app": {"image": "ghcr.io/example/app:v1.2.3"},
                    "db": {"image": "postgres:16"},
                }
            }
            client = MagicMock()

            with patch.object(
                main.image_updates,
                "choose_target_ref",
                return_value={
                    "current_ref": "ghcr.io/example/app:v1.2.3",
                    "target_ref": "ghcr.io/example/app:v1.2.4@sha256:" + ("b" * 64),
                    "target_tag": "v1.2.4",
                    "target_digest": "sha256:" + ("b" * 64),
                    "strategy": "stable",
                    "semver_policy": "patch",
                    "allow_prerelease": False,
                    "allow_major_upgrades": False,
                    "resolve_to_digest": True,
                    "reason": "newer stable release detected",
                    "changed": True,
                    "error": "",
                },
            ):
                result = main._select_env_ref_targets(
                    str(project_dir),
                    ["compose-images.envvars"],
                    config_payload,
                    {"version_strategy": "stable"},
                    client,
                    {},
                )

        replacements = result["replacements_by_path"][str(env_path)]
        self.assertEqual(
            replacements,
            {
                "APP_IMAGE": "ghcr.io/example/app:v1.2.4@sha256:" + ("b" * 64),
            },
        )
        self.assertTrue(result["service_targets"]["app"]["env_managed"])
        self.assertFalse(result["service_targets"]["db"]["env_managed"])

    def test_docker_update_blocks_when_policy_resolution_fails(self) -> None:
        payload = {
            "project_dir": "/tmp/example",
            "stack_name": "example",
            "compose_env_files": ["compose-images.envvars"],
            "image_strategy": "env-ref",
            "docker_update_policy": {"version_strategy": "stable"},
        }
        client = MagicMock()

        with (
            patch.object(main, "_project_dir_access_error", return_value=None),
            patch.object(main, "_compose_command", return_value=["docker", "compose"]),
            patch.object(main, "docker_client", return_value=client),
            patch.object(main, "_compose_config_json", return_value={"payload": {"services": {}}}),
            patch.object(
                main,
                "_select_env_ref_targets",
                return_value={
                    "service_targets": {
                        "app": {
                            "env_managed": True,
                            "error": "no matching release tags were found",
                        }
                    },
                    "replacements_by_path": {},
                },
            ),
        ):
            result = main.docker_update(payload)

        self.assertEqual(result["exit_code"], 1)
        self.assertIn("Unable to resolve a safe target image", result["stdout"])
        client.close.assert_called()


class AgentLoopTests(unittest.TestCase):
    def test_main_marks_claimed_job_failed_on_unexpected_execute_error(self) -> None:
        state = {"agent_id": "agent-1", "agent_secret": "secret", "poll_seconds": 1}
        job = {"id": "job-1", "kind": "docker_check", "payload": {}}

        with patch.object(main, "AGENT_NAME", "agent-under-test"):
            with patch.object(main, "ensure_registered", return_value=state):
                with patch.object(main, "heartbeat"):
                    with patch.object(main, "claim", return_value=job):
                        with patch.object(main, "post_event") as post_event:
                            with patch.object(main, "execute_job", side_effect=RuntimeError("boom")):
                                with patch.object(main, "complete") as complete:
                                    with patch.object(main.time, "sleep", side_effect=KeyboardInterrupt):
                                        with self.assertRaises(KeyboardInterrupt):
                                            main.main()

        complete.assert_called_once_with(
            state,
            "job-1",
            "failed",
            {
                "error": "unexpected agent job error: boom",
                "stdout": "unexpected agent job error: boom",
            },
        )
        post_event.assert_any_call(state, "job-1", "agent agent-under-test executing docker_check")
        post_event.assert_any_call(state, "job-1", "unexpected agent job error: boom", stream="stderr")


if __name__ == "__main__":
    unittest.main()
