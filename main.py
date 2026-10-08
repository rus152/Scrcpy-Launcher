from pathlib import Path

from launcher.app import run_application


if __name__ == "__main__":
    raise SystemExit(run_application(Path(__file__).resolve().parent))
