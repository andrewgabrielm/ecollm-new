"""Secret-scanning checks use invented markers assembled at runtime."""

from pathlib import Path

from scripts.scan_secrets import scan


def test_secret_scan_detects_without_returning_values(tmp_path: Path) -> None:
    marker = "sk-" + "invented" * 5
    (tmp_path / "unsafe.txt").write_text(marker, encoding="utf-8")
    findings = scan(tmp_path)
    assert findings == [(Path("unsafe.txt"), 1)]
    assert marker not in repr(findings)


def test_secret_scan_accepts_blank_keys_and_ignores_state(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("CLOUD_API_KEY=\n", encoding="utf-8")
    local_state = tmp_path / "var"
    local_state.mkdir()
    (local_state / "fixture.txt").write_text("sk-" + "invented" * 5, encoding="utf-8")
    assert scan(tmp_path) == []
