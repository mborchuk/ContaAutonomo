#!/usr/bin/env python3
"""Validate the screenshots data file for the docs site.

Usage:
    python3 check_screenshots.py [docs_dir]

Where ``docs_dir`` defaults to the current working directory. The script
loads ``<docs_dir>/_data/screenshots.yml`` and validates that:

* Every referenced image exists at
  ``<docs_dir>/assets/img/screenshots/<file>``.
* Each ``alt`` text is between 10 and 150 characters (inclusive).
* Each ``caption`` text is between 5 and 150 characters (inclusive).

The expected structure of ``screenshots.yml`` is::

    groups:
      - id: core
        title: "Core screens"
        items:
          - file: dashboard.png
            alt: "..."
            caption: "..."

Exit codes:
    0 - all checks passed.
    1 - the data file is missing, malformed, or any item failed validation.

Implements requirements 5.1, 5.2, 5.6, 8.5 from the
``github-pages-site`` spec.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable, List

try:
    import yaml  # type: ignore[import-untyped]
except ImportError as exc:  # pragma: no cover - hard failure
    print(
        "ERROR: PyYAML is required to run check_screenshots.py "
        "(install with `pip install pyyaml`).",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


ALT_MIN, ALT_MAX = 10, 150
CAPTION_MIN, CAPTION_MAX = 5, 150


def _format_location(group_idx: int, group_id: str, item_idx: int, file: str) -> str:
    """Return a stable location string used in error messages."""
    return f"groups[{group_idx}](id={group_id!r}).items[{item_idx}](file={file!r})"


def _validate_length(
    value: Any,
    field: str,
    min_len: int,
    max_len: int,
    where: str,
) -> List[str]:
    errors: List[str] = []
    if not isinstance(value, str):
        errors.append(f"{where}: {field!r} must be a string, got {type(value).__name__}")
        return errors
    length = len(value)
    if length < min_len or length > max_len:
        errors.append(
            f"{where}: {field!r} length {length} is outside allowed range "
            f"{min_len}..{max_len}"
        )
    return errors


def _iter_items(data: Any) -> Iterable[tuple[int, str, int, dict]]:
    if not isinstance(data, dict):
        raise ValueError("screenshots.yml: top-level value must be a mapping")
    groups = data.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("screenshots.yml: 'groups' must be a non-empty list")
    for g_idx, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(f"screenshots.yml: groups[{g_idx}] must be a mapping")
        group_id = str(group.get("id", ""))
        items = group.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(
                f"screenshots.yml: groups[{g_idx}](id={group_id!r}).items "
                "must be a non-empty list"
            )
        for i_idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(
                    f"screenshots.yml: groups[{g_idx}].items[{i_idx}] must be a mapping"
                )
            yield g_idx, group_id, i_idx, item


def validate(docs_dir: Path) -> tuple[int, List[str]]:
    """Run all validations.

    Returns a tuple ``(checked_count, errors)``. ``errors`` is empty when
    everything passed.
    """
    errors: List[str] = []
    data_file = docs_dir / "_data" / "screenshots.yml"
    if not data_file.is_file():
        errors.append(f"missing data file: {data_file}")
        return 0, errors

    try:
        raw = yaml.safe_load(data_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"failed to parse {data_file}: {exc}")
        return 0, errors

    try:
        items = list(_iter_items(raw))
    except ValueError as exc:
        errors.append(str(exc))
        return 0, errors

    screenshots_dir = docs_dir / "assets" / "img" / "screenshots"
    checked = 0
    for g_idx, group_id, i_idx, item in items:
        file = item.get("file")
        if not isinstance(file, str) or not file:
            where = f"groups[{g_idx}](id={group_id!r}).items[{i_idx}]"
            errors.append(f"{where}: 'file' must be a non-empty string")
            continue

        where = _format_location(g_idx, group_id, i_idx, file)
        target = screenshots_dir / file
        if not target.is_file():
            errors.append(f"{where}: missing file {target}")

        errors.extend(_validate_length(item.get("alt"), "alt", ALT_MIN, ALT_MAX, where))
        errors.extend(
            _validate_length(
                item.get("caption"), "caption", CAPTION_MIN, CAPTION_MAX, where
            )
        )
        checked += 1

    return checked, errors


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate docs/_data/screenshots.yml and referenced images.",
    )
    parser.add_argument(
        "docs_dir",
        nargs="?",
        default=".",
        help="Path to the docs directory (defaults to current directory).",
    )
    args = parser.parse_args(argv)

    docs_dir = Path(args.docs_dir).resolve()
    checked, errors = validate(docs_dir)

    if errors:
        print(f"FAIL: {len(errors)} screenshot validation error(s) in {docs_dir}:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"OK: validated {checked} screenshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
