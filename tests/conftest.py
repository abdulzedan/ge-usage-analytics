"""Put the repository root on sys.path.

The tool is a flat set of modules rather than an installed package, so that it
can be copied onto a host and run without a build step. The tests import those
modules directly.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
