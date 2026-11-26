"""Reject Python execution outside the one supported project environment."""

import sys
from pathlib import Path


def main() -> int:
    expected = Path(__file__).resolve().parents[1] / ".venv"
    if sys.version_info[:2] != (3, 11) or Path(sys.prefix).resolve() != expected.resolve():
        print("Environment error: use Python 3.11 from the project root .venv.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
