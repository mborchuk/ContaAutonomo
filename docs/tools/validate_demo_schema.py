#!/usr/bin/env python3
"""Validate demo data against a JSON Schema (draft 2020-12).

Usage:
    validate_demo_schema.py <demo_data.json> <schema.json>

Exit codes:
    0  data conforms to schema
    1  data does NOT conform, or input files cannot be read/parsed
    2  required dependency (``jsonschema``) is not installed

Used in CI by ``.github/workflows/pages.yml`` to guard the demo data file
that powers the ``/demo/`` page (requirements 6.1, 6.2, 6.3, 6.5, 6.6, 7.3).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _eprint(*args: Any) -> None:
    """Print to stderr."""
    print(*args, file=sys.stderr)


def _load_json(path: Path, label: str) -> Any:
    """Load JSON from ``path`` with friendly errors.

    Exits with code 1 on any read or parse failure.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        _eprint(f"ERROR: {label} not found: {path}")
        sys.exit(1)
    except PermissionError:
        _eprint(f"ERROR: cannot read {label} (permission denied): {path}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        _eprint(
            f"ERROR: {label} is not valid JSON: {path}\n"
            f"       line {exc.lineno}, column {exc.colno}: {exc.msg}"
        )
        sys.exit(1)
    except OSError as exc:
        _eprint(f"ERROR: cannot read {label}: {path}: {exc}")
        sys.exit(1)


def _format_path(parts: Any) -> str:
    """Format a jsonschema error ``absolute_path`` as a JSON Pointer-ish string.

    Example: ``deque(['tables', 'invoice', 0, 'status'])`` -> ``/tables/invoice/0/status``.
    Empty path becomes ``/`` to denote the document root.
    """
    segments = [str(part) for part in parts]
    if not segments:
        return "/"
    return "/" + "/".join(segments)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_demo_schema.py",
        description=(
            "Validate a demo data JSON file against a JSON Schema "
            "(draft 2020-12)."
        ),
    )
    parser.add_argument(
        "data_file",
        metavar="<demo_data.json>",
        help="Path to the demo data JSON file to validate.",
    )
    parser.add_argument(
        "schema_file",
        metavar="<schema.json>",
        help="Path to the JSON Schema file (draft 2020-12).",
    )
    args = parser.parse_args(argv)

    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError:
        _eprint(
            "ERROR: the 'jsonschema' package is required but not installed.\n"
            "       install it with:  pip install jsonschema"
        )
        return 2

    data_path = Path(args.data_file)
    schema_path = Path(args.schema_file)

    data = _load_json(data_path, "demo data")
    schema = _load_json(schema_path, "schema")

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        _eprint(
            f"ERROR: schema is not a valid JSON Schema (draft 2020-12): "
            f"{schema_path}\n       {exc.message}"
        )
        return 1

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))

    if not errors:
        print(f"OK: demo data matches schema ({data_path} ✓ {schema_path})")
        return 0

    _eprint(
        f"FAIL: demo data does not match schema "
        f"({len(errors)} violation{'s' if len(errors) != 1 else ''})"
    )
    _eprint(f"      data:   {data_path}")
    _eprint(f"      schema: {schema_path}")
    _eprint("")
    for idx, err in enumerate(errors, start=1):
        location = _format_path(err.absolute_path)
        _eprint(f"  [{idx}] {location}")
        _eprint(f"      {err.message}")
        if err.validator:
            _eprint(f"      (failed validator: {err.validator})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
