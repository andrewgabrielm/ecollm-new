"""Step 13 CLI: atomically point the active router pointer at a passing artifact.

The target artifact must exist under ``--router-root`` and its validation
report must carry ``promotion_passed = True``. The previous pointer is backed
up under ``active.json.bak`` so an owner-driven rollback stays trivial.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from backend.learning.training import PromotionError, promote_artifact

DEFAULT_ROUTER_ROOT = Path("var/routers")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="promote_router",
        description="Promote a previously-trained LightGBM router artifact",
    )
    parser.add_argument(
        "--version",
        default=os.environ.get("VERSION"),
        required=os.environ.get("VERSION") is None,
        help="artifact id to promote",
    )
    parser.add_argument(
        "--router-root",
        default=os.environ.get("ROUTER_ROOT", str(DEFAULT_ROUTER_ROOT)),
        help="directory containing router artifacts",
    )
    args = parser.parse_args(argv)
    router_root = Path(args.router_root)
    resolved = router_root if router_root.is_absolute() else (Path.cwd() / router_root).resolve()
    if not resolved.is_dir():
        raise SystemExit(f"router root not found: {resolved}")
    try:
        result = promote_artifact(root=resolved, artifact_id=args.version)
    except PromotionError as error:
        raise SystemExit(f"promotion failed: {error}") from error
    print(f"promoted:  {result.artifact_id}")
    print(f"previous:  {result.previous_artifact_id or '<none>'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
