"""CLI for regenerating and verifying the Step 11 benchmark fixtures.

Commands:
  generate   Regenerate ``evaluation/datasets/`` from the current generators.
  verify     Confirm the committed fixtures match the generators exactly.
  show       Print the category/split summary for the committed manifest.

All commands refuse unknown arguments and exit non-zero on failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.learning.benchmarks.generators import generate_all_tasks
from backend.learning.benchmarks.store import (
    DATASETS_DIR,
    BenchmarkCorruptionError,
    build_manifest,
    load_manifest,
    verify_splits,
    write_fixtures,
)


def _cmd_generate(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve() if args.output_dir else DATASETS_DIR
    tasks = generate_all_tasks()
    manifest = write_fixtures(tasks, output_dir=output_dir)
    rel = output_dir.resolve()
    print(f"wrote {manifest.task_count} tasks to {rel}")
    print(f"  dev_train:      {manifest.train_count}")
    print(f"  dev_validation: {manifest.validation_count}")
    for category, count in manifest.category_counts.items():
        print(f"  {category.value:11s} {count}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).resolve() if args.source_dir else DATASETS_DIR
    try:
        manifest = verify_splits(source_dir=source_dir)
    except BenchmarkCorruptionError as exc:
        print(f"verify failed: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"verify failed: missing file {exc.filename}", file=sys.stderr)
        return 2
    # Separately confirm regeneration yields the same manifest hash, which guards against
    # drift that write_fixtures would absorb on its next run.
    regenerated = build_manifest(generate_all_tasks())
    if regenerated.tasks_hash != manifest.tasks_hash:
        print("verify failed: regenerated tasks_hash disagrees", file=sys.stderr)
        return 2
    print(f"ok: {manifest.task_count} tasks ({manifest.family_count} families)")
    print(f"    train_hash={manifest.train_hash}")
    print(f"    validation_hash={manifest.validation_hash}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).resolve() if args.source_dir else DATASETS_DIR
    manifest = load_manifest(source_dir=source_dir)
    print(f"schema_version:    {manifest.schema_version}")
    print(f"generator_version: {manifest.generator_version}")
    print(f"task_count:        {manifest.task_count}")
    print(f"family_count:      {manifest.family_count}")
    print(f"train_count:       {manifest.train_count}")
    print(f"validation_count:  {manifest.validation_count}")
    print(f"tasks_hash:        {manifest.tasks_hash}")
    print(f"train_hash:        {manifest.train_hash}")
    print(f"validation_hash:   {manifest.validation_hash}")
    print("category_counts:")
    for category, count in manifest.category_counts.items():
        print(f"  {category.value:11s} {count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fixtures", description="Step 11 benchmark fixture utilities"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="regenerate committed fixtures")
    gen.add_argument("--output-dir", default=None)
    gen.set_defaults(func=_cmd_generate)

    verify = sub.add_parser("verify", help="confirm fixtures match the generators")
    verify.add_argument("--source-dir", default=None)
    verify.set_defaults(func=_cmd_verify)

    show = sub.add_parser("show", help="print the committed manifest summary")
    show.add_argument("--source-dir", default=None)
    show.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
