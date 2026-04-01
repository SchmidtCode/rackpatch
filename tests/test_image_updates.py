import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from common import image_updates


class ImageUpdatePolicyTests(unittest.TestCase):
    def test_normalize_policy_applies_expected_defaults(self) -> None:
        normalized = image_updates.normalize_policy({})

        self.assertEqual(normalized["version_strategy"], "stable")
        self.assertEqual(normalized["semver_policy"], "patch")
        self.assertFalse(normalized["allow_prerelease"])
        self.assertFalse(normalized["allow_major_upgrades"])
        self.assertTrue(normalized["resolve_to_digest"])

    def test_choose_target_ref_selects_newer_stable_patch_and_resolves_digest(self) -> None:
        result = image_updates.choose_target_ref(
            "henrygd/beszel:v0.18.5",
            {
                "version_strategy": "stable",
                "semver_policy": "patch",
                "allow_prerelease": False,
                "allow_major_upgrades": False,
                "resolve_to_digest": True,
            },
            list_tags=lambda ref: ["v0.18.4", "v0.18.6", "v0.19.0", "v0.18.6-rc1"],
            resolve_digest=lambda ref: ("sha256:" + ("a" * 64), None),
        )

        self.assertTrue(result["changed"])
        self.assertEqual(result["target_tag"], "v0.18.6")
        self.assertEqual(result["target_digest"], "sha256:" + ("a" * 64))
        self.assertEqual(result["target_ref"], f"henrygd/beszel:v0.18.6@{'sha256:' + ('a' * 64)}")

    def test_choose_target_ref_previous_stable_lags_one_release(self) -> None:
        result = image_updates.choose_target_ref(
            "henrygd/beszel:v0.18.5",
            {
                "version_strategy": "previous_stable",
                "semver_policy": "minor",
                "allow_prerelease": False,
                "allow_major_upgrades": False,
                "resolve_to_digest": False,
            },
            list_tags=lambda ref: ["v0.18.5", "v0.18.6", "v0.19.0"],
        )

        self.assertTrue(result["changed"])
        self.assertEqual(result["target_tag"], "v0.18.6")
        self.assertEqual(result["target_ref"], "henrygd/beszel:v0.18.6")


if __name__ == "__main__":
    unittest.main()
