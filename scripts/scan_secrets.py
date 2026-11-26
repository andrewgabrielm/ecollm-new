"""Offline heuristic scan of source text; diagnostics never contain matched values."""

from __future__ import annotations

import re
from pathlib import Path

IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".uv-cache",
        ".npm-cache",
        ".playwright",
        ".sites-runtime",
        "dist",
        "test-results",
        "playwright-report",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "var",
    }
)
PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|password)"
        r"\s*[:=]\s*['\"][A-Za-z0-9_+/=-]{16,}['\"]"
    ),
)


def scan(root: Path) -> list[tuple[Path, int]]:
    """Return paths/line numbers only, including for synthetic test credentials."""
    findings: list[tuple[Path, int]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            not path.is_file()
            or path.is_symlink()
            or any(part in IGNORED_DIRS for part in relative.parts)
            or relative.parts[:2] == ("evaluation", "results")
            or (path.name.startswith(".env") and path.name != ".env.example")
        ):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            if any(pattern.search(line) for pattern in PATTERNS):
                findings.append((relative, line_number))
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = scan(root)
    for path, line in findings:
        print(f"Possible secret: {path}:{line} (value redacted)")
    print(f"Secret scan: {len(findings)} finding(s); source text only, heuristic checks.")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
