"""Code scorer — isolated offline subprocess execution of candidate code.

The sandbox is deliberately conservative:
  * candidate source is written into a disposable temp directory;
  * a trusted driver reads test cases from stdin and prints results to stdout;
  * ``python -E -s`` ignores PYTHON* env vars and the user site packages while
    still allowing the driver to import ``candidate.py`` from its cwd;
  * ``env`` is wiped to a minimal, offline-safe set (no PYTHONPATH);
  * a short timeout bounds runaway loops;
  * on POSIX we additionally cap CPU time, address space and output size
    via ``resource.setrlimit`` in a ``preexec_fn`` (best-effort).

The scorer never imports the candidate into the test process. Step 11 tests
only pass trusted candidate strings; the sandbox exists so Step 12 can score
real model output without opening the host up to arbitrary I/O.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from backend.learning.benchmarks.types import BenchmarkTask
from backend.learning.scorers.base import ScoreResult
from backend.versions import JSONValue

CODE_EXECUTION_TIMEOUT_SECONDS = 3.0
_CPU_SECONDS = 4
_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
_OUTPUT_BYTES = 4 * 1024 * 1024
_FENCE = re.compile(
    r"```(?:python|py)?\s*(?P<body>.*?)```",
    re.DOTALL | re.IGNORECASE,
)


class CodeExecutionError(RuntimeError):
    """Candidate code could not be executed; distinct from a failing test."""


def _strip_code_fences(text: str) -> str:
    match = _FENCE.search(text)
    if match is not None:
        return match.group("body").strip()
    return text.strip()


_DRIVER = textwrap.dedent(
    """
    import json
    import sys

    import candidate

    plan = json.load(sys.stdin)
    function = getattr(candidate, plan["function"], None)
    results = []
    if function is None:
        for _ in plan["tests"]:
            results.append({"error": "missing_function"})
    else:
        for case in plan["tests"]:
            try:
                value = function(*case["input"])
            except Exception as exc:
                results.append({"error": type(exc).__name__})
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                results.append({"error": "unserialisable_output"})
                continue
            results.append({"output": value})
    json.dump(results, sys.stdout)
    """
).strip()


def _preexec() -> Callable[[], None] | None:
    if os.name != "posix":
        return None
    try:
        import resource
    except ImportError:  # pragma: no cover - POSIX guarantees resource
        return None

    # ``RLIMIT_AS`` and ``RLIMIT_FSIZE`` aren't uniformly honoured across platforms
    # (macOS can reject common values), so each limit is best-effort — a failure to
    # set one must not abort the child via a preexec exception.
    def _limit() -> None:
        for resource_name, value in (
            ("RLIMIT_CPU", _CPU_SECONDS),
            ("RLIMIT_AS", _ADDRESS_SPACE_BYTES),
            ("RLIMIT_FSIZE", _OUTPUT_BYTES),
        ):
            limit = getattr(resource, resource_name, None)
            if limit is None:
                continue
            try:
                resource.setrlimit(limit, (value, value))
            except (ValueError, OSError):
                continue

    return _limit


def _execute_candidate(
    source: str,
    function_name: str,
    tests: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run the candidate in an isolated subprocess; return per-test results."""
    workdir = Path(tempfile.mkdtemp(prefix="ecolm-code-score-"))
    try:
        (workdir / "candidate.py").write_text(source, encoding="utf-8")
        (workdir / "_runner.py").write_text(_DRIVER, encoding="utf-8")
        plan = json.dumps({"function": function_name, "tests": list(tests)})
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        proc = subprocess.run(
            [sys.executable, "-E", "-s", "_runner.py"],
            input=plan,
            capture_output=True,
            cwd=workdir,
            env=env,
            text=True,
            timeout=CODE_EXECUTION_TIMEOUT_SECONDS,
            preexec_fn=_preexec(),
            check=False,
        )
        if proc.returncode != 0:
            raise CodeExecutionError("candidate_process_failed")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise CodeExecutionError("candidate_output_unparseable") from exc
        if not isinstance(data, list) or len(data) != len(tests):
            raise CodeExecutionError("candidate_output_shape")
        return [item if isinstance(item, dict) else {"error": "bad_entry"} for item in data]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _normalise_number(value: JSONValue) -> JSONValue:
    """Fold ``3`` and ``3.0`` together for numeric comparisons; leave booleans alone."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def score_code(task: BenchmarkTask, candidate: str) -> ScoreResult:
    reference = task.reference
    function_name_value = reference["function"]
    tests_value = reference["tests"]
    if not isinstance(function_name_value, str) or not isinstance(tests_value, list):
        raise TypeError("code reference must include function name and tests list")
    tests: list[dict[str, Any]] = []
    for entry in tests_value:
        if not isinstance(entry, dict):
            raise TypeError("each code test case must be a mapping")
        tests.append(dict(entry))
    source = _strip_code_fences(candidate)
    if not source.strip():
        return ScoreResult(
            quality=0.0,
            format_valid=False,
            details={"reason": "empty_candidate"},
        )
    try:
        results = _execute_candidate(source, function_name_value, tests)
    except subprocess.TimeoutExpired:
        return ScoreResult(
            quality=0.0,
            format_valid=False,
            details={"reason": "timeout"},
        )
    except CodeExecutionError as exc:
        return ScoreResult(
            quality=0.0,
            format_valid=False,
            details={"reason": str(exc)},
        )
    passed = 0
    for case, actual in zip(tests, results, strict=True):
        expected = _normalise_number(case.get("expected"))
        got = _normalise_number(actual.get("output"))
        if "error" in actual:
            continue
        if got == expected:
            passed += 1
    total = len(tests) if tests else 1
    quality = passed / total
    # Format validity reports whether the candidate parsed and executed enough to produce
    # at least one usable result row — distinct from passing every test.
    format_valid = all("error" not in entry for entry in results)
    return ScoreResult(
        quality=max(0.0, min(1.0, quality)),
        format_valid=format_valid,
        details={"passed": passed, "total": len(tests)},
    )
