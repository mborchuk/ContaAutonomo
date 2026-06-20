"""Load year-versioned IRPF params. Next year's law = drop a JSON file, no code.

params/es_irpf_<year>.json holds rates, brackets, caps, mínimos per year. The
store loads by tax year and can merge an optional DB override on top (lets an
advanced user tweak a number in the UI without editing files).
"""

from __future__ import annotations

import copy
import json
import os
from functools import lru_cache
from typing import Any

_PARAMS_DIR = os.path.join(os.path.dirname(__file__), "params")


class ParamsNotFoundError(FileNotFoundError):
    """No param file for the requested tax year — caller decides the fallback."""


def available_years() -> list[int]:
    """Years that have a param file on disk (sorted)."""
    years: list[int] = []
    if not os.path.isdir(_PARAMS_DIR):
        return years
    for name in os.listdir(_PARAMS_DIR):
        if name.startswith("es_irpf_") and name.endswith(".json"):
            try:
                years.append(int(name[len("es_irpf_"):-len(".json")]))
            except ValueError:
                continue
    return sorted(years)


@lru_cache(maxsize=None)
def _load_file(year: int) -> dict[str, Any]:
    path = os.path.join(_PARAMS_DIR, f"es_irpf_{year}.json")
    if not os.path.isfile(path):
        raise ParamsNotFoundError(f"No IRPF params for year {year}: {path}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base (override wins)."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_params(
    year: int,
    *,
    override: dict[str, Any] | None = None,
    fallback_to_latest: bool = True,
) -> dict[str, Any]:
    """Return params for `year`, optionally merged with a DB override.

    If the exact year file is missing and `fallback_to_latest` is on, use the
    most recent available year (and stamp the requested year so callers see it).
    """
    try:
        params = _load_file(year)
    except ParamsNotFoundError:
        if not fallback_to_latest:
            raise
        years = available_years()
        if not years:
            raise
        params = copy.deepcopy(_load_file(years[-1]))
        params["_requested_year"] = year
        params["_fallback_from"] = years[-1]
    if override:
        params = _deep_merge(params, override)
    return params
