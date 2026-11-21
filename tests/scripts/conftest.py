"""Make the stdlib-only gate scripts in scripts/ importable from these tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
