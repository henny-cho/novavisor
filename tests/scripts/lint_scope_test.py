"""The clang-tidy scope is spelled twice; keep both real and identical."""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLANG_TIDY = REPO / ".clang-tidy"
sys.path.insert(0, str(REPO / "scripts"))

from nova_cli.checks import LINT_TREES  # noqa: E402


def lint_trees() -> list[str]:
    return list(LINT_TREES)


class LintScopeTests(unittest.TestCase):
    def test_every_linted_tree_exists(self):
        # A tree named here but absent on disk matches nothing, so lint
        # passes while diagnosing none of the sources it claims to cover.
        for tree in lint_trees():
            with self.subTest(tree=tree):
                self.assertTrue((REPO / "src" / tree).is_dir())

    def test_clang_tidy_header_filter_matches_cli(self):
        match = re.search(
            r"^HeaderFilterRegex:\s*'([^']+)'",
            CLANG_TIDY.read_text(),
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match, ".clang-tidy no longer sets HeaderFilterRegex")
        self.assertEqual(match.group(1), f"/src/({'|'.join(lint_trees())})/")


if __name__ == "__main__":
    unittest.main()
