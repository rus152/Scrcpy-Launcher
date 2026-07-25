from __future__ import annotations

import sys
from pathlib import Path

from launcher.ui import run_application


if __name__ == "__main__":
    raise SystemExit(run_application(Path(__file__).resolve().parent))
