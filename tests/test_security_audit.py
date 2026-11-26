"""Step 24 security audit tests — confirms the scanner fires on seeded content."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import security_audit

pytestmark = pytest.mark.privacy


def test_audit_scans_project_without_critical_findings(tmp_path: Path) -> None:
    report = security_audit._audit(tmp_path)
    assert report.scanned_files > 0
    # All critical checks should be present and 'ok' in a clean tree.
    for name in (
        "dependencies_pinned",
        "host_guard_present",
        "origin_guard_present",
        "write_token_required",
    ):
        assert report.checks.get(name) == "ok", report.checks
    # No secret or placeholder leaks from source.
    for finding in report.findings:
        assert finding.kind != "secret_marker"
        assert finding.kind != "placeholder_leak"


def test_audit_report_is_persisted(tmp_path: Path) -> None:
    security_audit._audit(tmp_path)
    reports = list(tmp_path.glob("security-report-*.json"))
    assert reports, "expected a security-report JSON file"
    payload = json.loads(reports[0].read_text())
    assert payload["schema_version"] == "security-report-v1"
    assert "scanned_files" in payload
    assert "checks" in payload


def test_audit_scans_seeded_sqlite_for_placeholder(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "seeded.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute(
            "INSERT INTO sample VALUES (?)",
            ("[[PII_" + "0" * 32 + "_SYNTHETIC_1]]",),
        )
    report = security_audit.AuditReport(started_at=0.0, finished_at=0.0)
    security_audit._scan_sqlite(database, report)
    kinds = {finding.kind for finding in report.findings}
    assert "placeholder_leak" in kinds


def test_audit_recognizes_seeded_secret() -> None:
    report = security_audit.AuditReport(started_at=0.0, finished_at=0.0)
    seeded = "sk_live_" + "A" * 24
    # Ad-hoc inline scan so we don't need to write a file under ROOT.
    for pattern in security_audit._SECRET_MARKERS:
        if pattern.search(seeded):
            report.findings.append(
                security_audit.AuditFinding(
                    kind="secret_marker", location="synthetic", detail="seed"
                )
            )
    assert any(f.kind == "secret_marker" for f in report.findings)
