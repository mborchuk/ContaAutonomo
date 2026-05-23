#!/usr/bin/env python3
"""Validate YAML frontmatter of Markdown pages in the docs/ site.

Usage:
    check_frontmatter.py [docs_dir]

If `docs_dir` is omitted, the current working directory is used.

The script recursively walks the given directory, locating every `*.md`
file (skipping build/output directories such as `_site/`, `vendor/`,
`.bundle/`, `.jekyll-cache/`, and any other hidden directories), then
parses the YAML frontmatter at the start of each file and validates:

* `title` is a string with length 30–60 characters (inclusive).
* `description` is a string with length 50–160 characters (inclusive).
* `permalink` is present and a non-empty string.
* `title` and `description` values are unique site-wide.

Files which deliberately opt out of normal page rendering (frontmatter
contains `sitemap: false` or `layout: null` / `layout: none`) are skipped
from validation. In the current site only `.md` files are scanned, so
HTML opt-outs like `404.html` are never visited.

On success the script prints `OK: validated N files` and exits 0.
On any failure it prints one diagnostic line per problem (file path +
issue) and exits 1.

Dependencies: PyYAML (`pip install pyyaml`) plus the Python standard
library.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - import guard for clarity
    sys.stderr.write(
        "error: PyYAML is required. Install with `pip install pyyaml`.\n"
    )
    sys.exit(2)


# Length bounds (inclusive) per requirement 8.1 and the design document.
TITLE_MIN, TITLE_MAX = 30, 60
DESCRIPTION_MIN, DESCRIPTION_MAX = 50, 160

# Directories to skip during the walk. Hidden directories (starting with
# `.`) are also skipped automatically.
SKIP_DIRS = {"_site", "vendor", ".bundle", ".jekyll-cache", "node_modules"}


def iter_markdown_files(root: Path) -> Iterable[Path]:
    """Yield Markdown files under *root*, skipping build/output dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Filter directories in-place so os.walk does not descend into them.
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            if name.endswith(".md"):
                yield Path(dirpath) / name


def extract_frontmatter(path: Path) -> tuple[dict | None, str | None]:
    """Return (data, error). data is None if no frontmatter is present."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read file: {exc}"

    # Frontmatter is a YAML block delimited by `---` lines at file start.
    # Tolerate a UTF-8 BOM, but require the first non-BOM characters to be
    # the opening `---` marker.
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")

    if not text.startswith("---"):
        return None, "missing frontmatter (file does not start with '---')"

    lines = text.splitlines()
    # First line must be exactly `---` (allowing trailing whitespace).
    if lines[0].strip() != "---":
        return None, "missing frontmatter (file does not start with '---')"

    # Find the closing delimiter.
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, "frontmatter not terminated by '---'"

    block = "\n".join(lines[1:end_idx])
    try:
        data = yaml.safe_load(block) if block.strip() else {}
    except yaml.YAMLError as exc:
        return None, f"invalid YAML frontmatter: {exc}"

    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None, "frontmatter is not a mapping"
    return data, None


def is_skipped(data: dict) -> bool:
    """Pages with `sitemap: false` or `layout: null/none` opt out."""
    if data.get("sitemap") is False:
        return True
    layout = data.get("layout", "missing")
    if layout in (None, "none"):
        return True
    return False


def validate_file(data: dict) -> list[str]:
    """Return a list of issue messages for a single file."""
    issues: list[str] = []

    # title
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        issues.append("title is missing or not a string")
    else:
        n = len(title)
        if n < TITLE_MIN:
            issues.append(
                f"title too short ({n} chars, need {TITLE_MIN}-{TITLE_MAX})"
            )
        elif n > TITLE_MAX:
            issues.append(
                f"title too long ({n} chars, need {TITLE_MIN}-{TITLE_MAX})"
            )

    # description
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        issues.append("description is missing or not a string")
    else:
        n = len(description)
        if n < DESCRIPTION_MIN:
            issues.append(
                f"description too short ({n} chars, need "
                f"{DESCRIPTION_MIN}-{DESCRIPTION_MAX})"
            )
        elif n > DESCRIPTION_MAX:
            issues.append(
                f"description too long ({n} chars, need "
                f"{DESCRIPTION_MIN}-{DESCRIPTION_MAX})"
            )

    # permalink
    permalink = data.get("permalink")
    if permalink is None:
        issues.append("permalink is missing")
    elif not isinstance(permalink, str) or not permalink.strip():
        issues.append("permalink is empty or not a string")

    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate YAML frontmatter (title, description, permalink) "
            "of Markdown pages under a docs directory."
        )
    )
    parser.add_argument(
        "docs_dir",
        nargs="?",
        default=".",
        help="Directory to scan recursively (default: current directory).",
    )
    args = parser.parse_args(argv)

    root = Path(args.docs_dir).resolve()
    if not root.exists():
        print(f"error: path does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    problems: list[tuple[Path, str]] = []
    titles: dict[str, list[Path]] = defaultdict(list)
    descriptions: dict[str, list[Path]] = defaultdict(list)
    validated_count = 0

    for md_path in sorted(iter_markdown_files(root)):
        data, err = extract_frontmatter(md_path)
        if err is not None:
            problems.append((md_path, err))
            continue
        assert data is not None  # for type-checkers; err is None means data set

        if is_skipped(data):
            continue

        for issue in validate_file(data):
            problems.append((md_path, issue))

        title = data.get("title")
        if isinstance(title, str) and title.strip():
            titles[title].append(md_path)

        description = data.get("description")
        if isinstance(description, str) and description.strip():
            descriptions[description].append(md_path)

        validated_count += 1

    # Uniqueness checks across the whole site.
    def _others(p: Path, paths: list[Path]) -> str:
        return ", ".join(
            str(o.relative_to(root)) for o in paths if o != p
        )

    for value, paths in titles.items():
        if len(paths) > 1:
            for p in paths:
                problems.append(
                    (p, f"duplicate title (also in: {_others(p, paths)})")
                )

    for value, paths in descriptions.items():
        if len(paths) > 1:
            for p in paths:
                problems.append(
                    (
                        p,
                        f"duplicate description (also in: {_others(p, paths)})",
                    )
                )

    if problems:
        # Group problems by file for stable, readable output.
        grouped: dict[Path, list[str]] = defaultdict(list)
        for path, issue in problems:
            grouped[path].append(issue)

        print(
            f"FAIL: {len(problems)} issue(s) in "
            f"{len(grouped)} file(s):",
            file=sys.stderr,
        )
        for path in sorted(grouped):
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            for issue in grouped[path]:
                print(f"  {rel}: {issue}", file=sys.stderr)
        return 1

    print(f"OK: validated {validated_count} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
