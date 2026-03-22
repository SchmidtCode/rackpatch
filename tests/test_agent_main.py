import sys
import types
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
