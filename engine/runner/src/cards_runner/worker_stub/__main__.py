"""`python -m cards_runner.worker_stub` entry."""
from __future__ import annotations

from .worker import main_from_env


def main() -> int:
    return main_from_env()


if __name__ == "__main__":
    raise SystemExit(main())
