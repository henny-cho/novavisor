"""The suite's one path fact.

`novakit` is not installed; it is imported from the repository root, which
is also the discovery top level, so the runner puts it on the path and no
module here has to.

Discovery has to be given the repository as its top level so this runs:

    python -m unittest discover -s tests -t . -p '*_test.py'

which is what `nova test` does. One module is `python -m unittest
tests.workbench.session_test`; a subdirectory without an `__init__.py` is
skipped silently, so every group has one.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
