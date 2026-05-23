"""Unit tests for ``docs/tools/check_frontmatter.py``.

Validates the frontmatter validator script behaves correctly for valid
inputs and the most important failure modes:

* lengths of ``title`` and ``description`` outside the configured range,
* duplicate ``title`` or ``description`` values across multiple pages.

Tests invoke the script as a subprocess (the same way CI calls it) and
assert on exit code and stdout/stderr contents. PyYAML is required by the
script under test; tests are skipped if it is not available.

Validates: Requirements 6.5, 8.1
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Skip the whole module if PyYAML is not installed (the script itself
# refuses to start without it, so testing without yaml provides no signal).
pytest.importorskip("yaml")

SCRIPT = Path(__file__).resolve().parents[1] / "check_frontmatter.py"


# Title length bounds per the script: 30-60 (inclusive).
# Description length bounds per the script: 50-160 (inclusive).
TITLE_OK = "Autonomos Platform Operational Overview Doc"  # 44 chars
TITLE_OK_2 = "Autonomos Documentation Reference Guide Page"  # 44 chars
DESC_OK = (
    "Autonomos documentation page describing platform features, deployment "
    "options, and operational guidance for teams."
)  # ~100 chars
DESC_OK_2 = (
    "Reference guide page covering API endpoints, configuration formats, "
    "and integration patterns for Autonomos modules."
)  # ~100 chars


def _write_md(path: Path, *, title: str, description: str, permalink: str) -> None:
    """Write a Markdown file with YAML frontmatter and a tiny body."""
    path.write_text(
        textwrap.dedent(
            f"""\
            ---
            title: "{title}"
            description: "{description}"
            permalink: {permalink}
            layout: default
            ---

            # {title}

            Page body.
            """
        ),
        encoding="utf-8",
    )


def _run(docs_dir: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the validator on *docs_dir* and capture its output."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(docs_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_files_pass(tmp_path: Path) -> None:
    """Two well-formed pages with distinct title/description validate."""
    _write_md(
        tmp_path / "alpha.md",
        title=TITLE_OK,
        description=DESC_OK,
        permalink="/alpha/",
    )
    _write_md(
        tmp_path / "beta.md",
        title=TITLE_OK_2,
        description=DESC_OK_2,
        permalink="/beta/",
    )

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "OK: validated 2 files" in result.stdout


def test_title_too_short_fails(tmp_path: Path) -> None:
    """A title shorter than 30 characters is rejected."""
    _write_md(
        tmp_path / "short.md",
        title="Short title",  # 11 chars
        description=DESC_OK,
        permalink="/short/",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "title too short" in combined


def test_title_too_long_fails(tmp_path: Path) -> None:
    """A title longer than 60 characters is rejected."""
    long_title = "A" * 80  # 80 chars
    _write_md(
        tmp_path / "long.md",
        title=long_title,
        description=DESC_OK,
        permalink="/long/",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "title too long" in combined


def test_description_out_of_range_fails(tmp_path: Path) -> None:
    """Descriptions shorter than 50 or longer than 160 chars are rejected."""
    _write_md(
        tmp_path / "desc-short.md",
        title=TITLE_OK,
        description="Too short.",  # 10 chars
        permalink="/short/",
    )
    _write_md(
        tmp_path / "desc-long.md",
        title=TITLE_OK_2,
        description="A" * 200,  # 200 chars
        permalink="/long/",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "description too short" in combined
    assert "description too long" in combined


def test_duplicate_title_fails(tmp_path: Path) -> None:
    """Two files sharing a ``title`` value trigger the uniqueness check."""
    _write_md(
        tmp_path / "page-a.md",
        title=TITLE_OK,
        description=DESC_OK,
        permalink="/a/",
    )
    _write_md(
        tmp_path / "page-b.md",
        title=TITLE_OK,  # duplicate on purpose
        description=DESC_OK_2,
        permalink="/b/",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "duplicate title" in combined


def test_duplicate_description_fails(tmp_path: Path) -> None:
    """Two files sharing a ``description`` value trigger the uniqueness check."""
    _write_md(
        tmp_path / "page-a.md",
        title=TITLE_OK,
        description=DESC_OK,
        permalink="/a/",
    )
    _write_md(
        tmp_path / "page-b.md",
        title=TITLE_OK_2,
        description=DESC_OK,  # duplicate on purpose
        permalink="/b/",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "duplicate description" in combined
