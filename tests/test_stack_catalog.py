import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from common import stack_catalog


class StackCatalogDiscoveryTests(unittest.TestCase):
    def test_discovered_stack_with_compose_env_files_defaults_to_env_ref(self) -> None:
        with (
            patch.object(stack_catalog, "_iter_agent_projects", return_value=[
                {
                    "host": "docker-core",
                    "agent_status": "online",
                    "project_name": "beszel",
                    "project_dir": "/srv/compose/beszel",
                    "config_files": ["docker-compose.yml"],
                    "compose_env_files": ["compose-images.envvars"],
                    "services": [{"service": "beszel"}],
                }
            ]),
            patch.object(stack_catalog, "load_defined_stacks", return_value=[]),
        ):
            items = stack_catalog.load_discovered_stacks()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["image_strategy"], "env-ref")

    def test_discovered_stack_without_compose_env_files_stays_compose_default(self) -> None:
        with (
            patch.object(stack_catalog, "_iter_agent_projects", return_value=[
                {
                    "host": "docker-core",
                    "agent_status": "online",
                    "project_name": "promtail",
                    "project_dir": "/srv/compose/promtail",
                    "config_files": ["docker-compose.yml"],
                    "compose_env_files": [],
                    "services": [{"service": "promtail"}],
                }
            ]),
            patch.object(stack_catalog, "load_defined_stacks", return_value=[]),
        ):
            items = stack_catalog.load_discovered_stacks()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["image_strategy"], "compose-default")


if __name__ == "__main__":
    unittest.main()
