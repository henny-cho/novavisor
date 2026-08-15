import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from novakit.services import boundaries  # noqa: E402


class BoundariesTest(unittest.TestCase):
    def test_package_layer_violations_are_clean(self):
        violations = boundaries.find_layer_violations(REPO_ROOT)
        self.assertEqual(violations, [], f"layer violations found: {violations}")

    def test_single_owner_violations_are_clean(self):
        violations = boundaries.find_ownership_violations(REPO_ROOT)
        self.assertEqual(violations, [], f"ownership violations found: {violations}")

    def test_seam_violations_are_clean(self):
        violations = boundaries.find_seam_violations(REPO_ROOT)
        self.assertEqual(violations, [], f"seam violations found: {violations}")

    def test_all_violations_are_clean(self):
        violations = boundaries.find_violations(REPO_ROOT)
        self.assertEqual(violations, [], f"violations found: {violations}")

    def test_full_check_passes(self):
        self.assertEqual(boundaries.check(REPO_ROOT), 0)


if __name__ == "__main__":
    unittest.main()
