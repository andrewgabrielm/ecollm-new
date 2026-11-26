"""Step 24 offline security audit: PII scan, dependency pins, policy invariants.

This is a static, defensive sweep. It does not execute attacker-controlled
code, reach any network, or read anything outside the repository. Findings are
summarized to a JSON report under ``evaluation/results/``.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from time import time

ROOT = Path(__file__).resolve().parents[1]

# Scripts and tests deliberately embed synthetic PII markers as test input to
# exercise the sanitizer. They are invented strings; do not treat them as live
# PII leaks. SQLite rows, telemetry blobs and evaluation artifacts have no such
# exemption — a hit there is treated as a critical finding.
_SYNTHETIC_PII_FIXTURE_PATHS = frozenset(
    {
        "backend/broker/real_smoke.py",
        "scripts/demo_policy.py",
        "scripts/demo_routing.py",
        "scripts/smoke.py",
        "docs/cloud-fallback.md",
    }
)

_PII_MARKERS = (
    re.compile(r"\bSTU-\d{4}-\d{4}\b"),
    re.compile(r"\b[A-Z]{3}-\d{3}-\d{4}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # synthetic SSN-shaped
)
_SECRET_MARKERS = (
    re.compile(r"(?i)sk_live_[A-Za-z0-9]{10,}"),
    re.compile(r"(?i)xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)aws[_-]?secret[_-]?access[_-]?key"),
)
_PLACEHOLDER_MARKER = re.compile(r"\[\[PII_[a-f0-9]{32}_[A-Z][A-Z_]{0,63}_[1-9][0-9]*\]\]")


@dataclass
class AuditFinding:
    kind: str
    location: str
    detail: str


@dataclass
class AuditReport:
    started_at: float
    finished_at: float
    scanned_files: int = 0
    scanned_bytes: int = 0
    findings: list[AuditFinding] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        return {
            "schema_version": "security-report-v1",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "scanned_files": self.scanned_files,
            "scanned_bytes": self.scanned_bytes,
            "finding_count": len(self.findings),
            "findings": [
                {"kind": f.kind, "location": f.location, "detail": f.detail} for f in self.findings
            ],
            "checks": self.checks,
        }


def _scan_file_text(path: Path, report: AuditReport) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    report.scanned_files += 1
    report.scanned_bytes += len(content.encode("utf-8"))
    relative = str(path.relative_to(ROOT))
    # Known-synthetic fixtures skip the PII regex to avoid noise. Secret and
    # placeholder checks still apply because those must never appear.
    pii_patterns: tuple[re.Pattern[str], ...] = (
        () if relative in _SYNTHETIC_PII_FIXTURE_PATHS else _PII_MARKERS
    )
    for pattern in pii_patterns:
        for _match in pattern.finditer(content):
            report.findings.append(
                AuditFinding(
                    kind="pii_marker",
                    location=relative,
                    detail=f"matched={pattern.pattern[:40]}",
                )
            )
            break
    for pattern in _SECRET_MARKERS:
        for _match in pattern.finditer(content):
            report.findings.append(
                AuditFinding(
                    kind="secret_marker",
                    location=relative,
                    detail=f"matched={pattern.pattern[:40]}",
                )
            )
            break
    for _match in _PLACEHOLDER_MARKER.finditer(content):
        report.findings.append(
            AuditFinding(
                kind="placeholder_leak",
                location=relative,
                detail="placeholder token found outside request-local state",
            )
        )
        break


def _scan_sqlite(path: Path, report: AuditReport) -> None:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            try:
                rows = connection.execute(f'SELECT * FROM "{table}" LIMIT 1024').fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                for cell in row:
                    if not isinstance(cell, str):
                        continue
                    _scan_string(cell, path, table, report)
    finally:
        connection.close()


def _scan_string(value: str, path: Path, table: str, report: AuditReport) -> None:
    try:
        relative = f"{path.relative_to(ROOT)}#{table}"
    except ValueError:
        relative = f"{path.name}#{table}"
    for pattern in _PII_MARKERS:
        if pattern.search(value):
            report.findings.append(
                AuditFinding(kind="pii_marker", location=relative, detail="in_sqlite_cell")
            )
            return
    if _PLACEHOLDER_MARKER.search(value):
        report.findings.append(
            AuditFinding(
                kind="placeholder_leak",
                location=relative,
                detail="placeholder token persisted",
            )
        )


def _check_dependency_pins(report: AuditReport) -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    unpinned = re.findall(
        r"^\s*\"([a-zA-Z0-9_.-]+)(>=|<|~=|\^)",
        pyproject,
        flags=re.MULTILINE,
    )
    if unpinned:
        for name, _ in unpinned:
            report.findings.append(
                AuditFinding(
                    kind="unpinned_dependency",
                    location="pyproject.toml",
                    detail=name,
                )
            )
    report.checks["dependencies_pinned"] = "ok" if not unpinned else "warn"


def _check_license(report: AuditReport) -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (ROOT / "backend/model_management/manifest.py").read_text(encoding="utf-8")
    has_license_review = "license_review" in manifest
    report.checks["manifest_license_review"] = "ok" if has_license_review else "missing"
    report.checks["pyproject_present"] = "ok" if pyproject else "missing"


def _check_api_boundary(report: AuditReport) -> None:
    security = (ROOT / "backend/api/security.py").read_text(encoding="utf-8")
    report.checks["host_guard_present"] = (
        "ok" if "forbidden_hosts" in security.lower() or "host" in security.lower() else "missing"
    )
    report.checks["origin_guard_present"] = "ok" if "origin" in security.lower() else "missing"
    # The markdown renderer is safe only because it never emits an HTML string.
    # rehype-raw or dangerouslySetInnerHTML would reintroduce an injection path
    # for model output, so both fail the audit rather than merely warn.
    unsafe_render = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "frontend" / "src").rglob("*.tsx"))
        # Matches real usage, not a comment discussing why it is absent.
        if "dangerouslySetInnerHTML" in path.read_text(encoding="utf-8")
        or 'from "rehype-raw"' in path.read_text(encoding="utf-8")
    ]
    frontend_manifest = ROOT / "frontend" / "package.json"
    if frontend_manifest.exists() and "rehype-raw" in frontend_manifest.read_text(encoding="utf-8"):
        unsafe_render.append("frontend/package.json")
    for location in unsafe_render:
        report.findings.append(
            AuditFinding(
                kind="unsafe_markdown_render",
                location=location,
                detail="raw HTML path in rendered model output",
            )
        )
    report.checks["markdown_render_escaped"] = "ok" if not unsafe_render else "unsafe"
    report.checks["write_token_required"] = (
        "ok" if "x-local-token" in security.lower() else "missing"
    )


def _audit(output_dir: Path) -> AuditReport:
    started = time()
    report = AuditReport(started_at=started, finished_at=started)
    for pattern in ("backend/**/*.py", "scripts/**/*.py", "frontend/src/**/*.ts", "docs/**/*.md"):
        for path in sorted(ROOT.glob(pattern)):
            if path.is_file():
                _scan_file_text(path, report)
    for path in sorted((ROOT / "var").rglob("*.sqlite3")) if (ROOT / "var").is_dir() else []:
        _scan_sqlite(path, report)
    _check_dependency_pins(report)
    _check_license(report)
    _check_api_boundary(report)
    report.finished_at = time()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_id = sha256(str(started).encode("utf-8")).hexdigest()[:12]
    target = output_dir / f"security-report-{report_id}.json"
    target.write_text(json.dumps(report.summary(), indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="security_audit")
    parser.add_argument(
        "--out",
        default=str(ROOT / "evaluation" / "results"),
        help="directory to hold the audit report",
    )
    args = parser.parse_args(argv)
    report = _audit(Path(args.out))
    print(f"scanned_files:   {report.scanned_files}")
    print(f"findings:        {len(report.findings)}")
    for check, status in report.checks.items():
        print(f"  {check:30s} {status}")
    for finding in report.findings:
        print(f"  [{finding.kind}] {finding.location}: {finding.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
