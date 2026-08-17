"""The suite's one path fact.

`novakit` is not installed; it is read out of the tree. Stating that here
rather than in every module is what makes each test file open with what it
is testing instead of with two lines of bookkeeping.

Discovery has to be given the repository as its top level so this runs:

    python -m unittest discover -s tests -t . -p '*_test.py'

which is what `nova test` does. One module is `python -m unittest
tests.workbench.session_test`; a subdirectory without an `__init__.py` is
skipped silently, so every group has one.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))
