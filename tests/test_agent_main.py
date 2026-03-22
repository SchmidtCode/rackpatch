import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


if __name__ == "__main__":
    unittest.main()
