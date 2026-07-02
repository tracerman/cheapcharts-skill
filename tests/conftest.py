"""Make the skill's scripts/ importable as plain modules for the unit tests."""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "cheapcharts" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
