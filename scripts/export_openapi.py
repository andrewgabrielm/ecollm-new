"""Export the actual Step 07 FastAPI schema without starting a server."""

import argparse
import hashlib
import json
import secrets
from pathlib import Path

from backend.api.main import create_app


def export_schema() -> Path:
    app = create_app(environment={"MOCK_MODE": "1", "LOCAL_WRITE_TOKEN": secrets.token_urlsafe(32)})
    schema = app.openapi()
    root = Path(__file__).resolve().parents[1]
    destination = root / "evaluation" / "results" / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("OpenAPI output must not be a symbolic link")
    destination.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    destination = export_schema()
    print(f"OpenAPI: {destination}")
    print(f"SHA-256: {hashlib.sha256(destination.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
