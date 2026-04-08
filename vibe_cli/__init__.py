import os
import sys

# Ensure the project root is on sys.path so local modules (ui, core, etc.) are importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from main import main  # noqa: E402

__all__ = ["main"]
