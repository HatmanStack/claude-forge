"""Make `from lib import registry, trajectory` resolve regardless of CWD."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
