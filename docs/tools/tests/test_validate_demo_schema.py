"""Unit tests for ``docs/tools/validate_demo_schema.py``.

Each test writes a temporary schema (re-using the project's
``docs/_data/demo_schema.json`` shape) and a temporary demo data file,
then runs the validator as a subprocess and asserts on its exit code and
output. Skipped if ``jsonschema`` is not installed.

Validates: Requirements 6.5, 8.1
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Skip the whole module if jsonschema is unavailable; the script itself
# exits with code 2 in that case, which would make every assertion noisy.
pytest.importorskip("jsonschema")

SCRIPT = Path(__file__).resolve().parents[1] / "validate_demo_schema.py"
PROJECT_SCHEMA = (
    Path(__file__).resolve().parents[2] / "_data" / "demo_schema.json"
)


def _valid_demo_payload() -> dict:
    """Return a demo payload that satisfies ``demo_schema.json``."""
    return {
        "version": "1.0.0",
        "created_at": "2025-01-01T00:00:00Z",
        "tables": {
            "customer": [
                {
                    "name": "Acme GmbH",
                    "vat_number": "DE123456789",
                    "country": "DE",
                    "tax_type": "eu_b2b",
                },
                {
                    "name": "Globex Ltd",
                    "vat_number": None,
                    "country": "US",
                    "tax_type": "non_eu",
                },
                {
                    "name": "Initech",
                    "vat_number": "FR987654321",
                    "country": "FR",
                    "tax_type": "standard",
                },
            ],
            "invoice": [
                {
                    "invoice_number": "INV-001",
                    "client_name": "Acme GmbH",
                    "amount_eur": 1000.0,
                    "currency": "EUR",
                    "status": "paid",
                    "invoice_date": "2025-01-15",
                },
                {
                    "invoice_number": "INV-002",
                    "client_name": "Globex Ltd",
                    "amount_eur": 2500.5,
                    "currency": "USD",
                    "status": "pending",
                    "invoice_date": "2025-02-01",
                },
                {
                    "invoice_number": "INV-003",
                    "client_name": "Initech",
                    "amount_eur": 750.0,
                    "currency": "EUR",
                    "status": "overdue",
                    "invoice_date": "2025-02-10",
                },
            ],
            "expense": [
                {
                    "amount": 200.0,
                    "currency": "EUR",
                    "category": "office",
                    "expense_date": "2025-01-05",
                },
                {
                    "amount": 50.0,
                    "currency": "EUR",
                    "category": "software",
                    "expense_date": "2025-01-20",
                },
                {
                    "amount": 1200.0,
                    "currency": "USD",
                    "category": "travel",
                    "expense_date": "2025-02-03",
                },
            ],
        },
    }


@pytest.fixture
def schema_path(tmp_path: Path) -> Path:
    """Provide a copy of the project's JSON Schema in *tmp_path*."""
    dest = tmp_path / "schema.json"
    if PROJECT_SCHEMA.exists():
        shutil.copy(PROJECT_SCHEMA, dest)
    else:  # pragma: no cover - defensive fallback
        dest.write_text(
            json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema"}),
            encoding="utf-8",
        )
    return dest


def _run(data_path: Path, schema_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(data_path), str(schema_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_valid_demo_data_passes(tmp_path: Path, schema_path: Path) -> None:
    """A payload satisfying every requirement returns exit 0."""
    data_path = tmp_path / "demo.json"
    _write_json(data_path, _valid_demo_payload())

    result = _run(data_path, schema_path)

    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_missing_required_field_fails(tmp_path: Path, schema_path: Path) -> None:
    """An invoice without the required ``status`` field is rejected."""
    payload = _valid_demo_payload()
    del payload["tables"]["invoice"][0]["status"]
    data_path = tmp_path / "demo.json"
    _write_json(data_path, payload)

    result = _run(data_path, schema_path)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "status" in combined


def test_invalid_enum_fails(tmp_path: Path, schema_path: Path) -> None:
    """A customer with a ``tax_type`` outside the enum is rejected."""
    payload = _valid_demo_payload()
    payload["tables"]["customer"][0]["tax_type"] = "invalid_type"
    data_path = tmp_path / "demo.json"
    _write_json(data_path, payload)

    result = _run(data_path, schema_path)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "invalid_type" in combined or "enum" in combined


def test_invalid_min_items_fails(tmp_path: Path, schema_path: Path) -> None:
    """An invoice array shorter than ``minItems`` is rejected."""
    payload = _valid_demo_payload()
    payload["tables"]["invoice"] = [payload["tables"]["invoice"][0]]
    data_path = tmp_path / "demo.json"
    _write_json(data_path, payload)

    result = _run(data_path, schema_path)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    # jsonschema reports a "too short" / "minItems" style message
    assert "minItems" in combined or "too short" in combined or "shortest" in combined


def test_bad_json_fails(tmp_path: Path, schema_path: Path) -> None:
    """A malformed JSON data file produces a parse-error message and exit 1."""
    data_path = tmp_path / "demo.json"
    data_path.write_text("{not valid json", encoding="utf-8")

    result = _run(data_path, schema_path)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "JSON" in combined or "json" in combined
