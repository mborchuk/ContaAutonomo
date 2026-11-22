# Contributing to ContaAutónomo

Thanks for your interest in improving ContaAutónomo. This guide explains how to
set up the project, the expectations for changes, and how pull requests are
reviewed.

## Table of Contents

1. [Ground Rules](#ground-rules)
2. [Development Setup](#development-setup)
3. [Making Changes](#making-changes)
4. [Pull Request Process](#pull-request-process)
5. [Annual Tax Data Update](#annual-tax-data-update)
6. [Reporting Bugs and Requesting Features](#reporting-bugs-and-requesting-features)

## Ground Rules

- Be respectful — see the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Security issues are never reported publicly.** Follow the
  [Security Policy](SECURITY.md).
- Never commit secrets, real customer data, databases (`instance/`), or
  backups.

## Development Setup

```bash
git clone https://github.com/mborchuk/ContaAutonomo.git
cd ContaAutonomo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

FLASK_DEBUG=1 python app.py      # http://127.0.0.1:5000
```

Dependencies are locked with hashes. Add or change a dependency in
`requirements.in` (runtime) or `requirements-dev.in` (tests), then run
`make lock` (needs Docker; compiles on the image's Python) and commit both
the `.in` and the regenerated `.txt` files. Never edit the `.txt` files by
hand. Use Python 3.14, the version of the image and CI.

Run the test suite (in-memory SQLite, no network required):

```bash
python -m pytest
```

## Making Changes

- **Build features as modules.** Most features live in `modules/<name>/` and
  plug into the core through hooks. See
  [`modules/README.md`](modules/README.md) and
  [`MODULES_DOCUMENTATION.md`](MODULES_DOCUMENTATION.md) before changing
  `app.py` or `module_manager.py`.
- **Tests are required** for anything touching tax calculations, invoice
  lifecycle state, or data deletion. Bug fixes should include a regression
  test that fails without the fix.
- **Schema changes** use the existing idempotent `ALTER TABLE` pattern — they
  must run safely on both a fresh and an existing database. A new column on a
  core model goes into `CORE_COLUMNS` in `schema_migrations.py`, the one
  routine every entry point runs; module columns go in the module's
  `on_enable()`.
- **Tax logic** must state its scope and carry "estimate, not tax advice"
  framing; cite the official source (AEAT, BOE, Seguridad Social) for any
  rates, boxes, or deadlines you add or change.
- Run `make lint` before pushing; CI runs the same `ruff check` (rules in
  `pyproject.toml`, intentionally small for now and tightened over time).
  `make format` formats the Python files you changed; the repository is not
  formatted as a whole yet, so do not reformat unrelated code.
- `make coverage` runs the tests with a coverage report; CI publishes the
  coverage table in each run's summary.
- Update the documentation (`README.md`, `MODULES_DOCUMENTATION.md`, module
  READMEs, `CHANGELOG.md`) when behavior changes.

## Pull Request Process

1. Fork the repository and create a branch from `main`
   (for example `fix/document-delete` or `feat/recurring-invoices`).
2. Make your change with tests, and run `python -m pytest` locally.
3. Open a pull request against `main` and fill in the template.
4. Automated checks must pass: **pytest**, **CodeQL Analysis**, and the
   **Trivy Docker Image Scan** (which also proves the image builds).
5. A code owner reviews the change. All review conversations must be resolved
   before merging.
6. Pull requests are **squash-merged**, so the PR title becomes the commit
   message — use a clear, descriptive title.

`main` is protected: direct pushes, force pushes, and branch deletion are
blocked, and history is kept linear.

## Annual Tax Data Update

Some tax data is valid for one year: box numbers of the forms, the Modelo 347
threshold, the RETA brackets, the IRPF scales and the filing calendar. Every
January, check them for the new year. Most years nothing changes.

### In the app — Taxes → Tax Data (no code)

The **Tax Data** module (on by default) lists every yearly table the enabled
modules use, for the year you pick:

- **Status.** *"2027 table in the app"*, *"Uses the 2026 table — not
  confirmed"* or *"Confirmed 20/01/2027"*. A page that shows 2027 with an
  unconfirmed 2026 table warns: *"Figures for 2027 use the 2026 table — check
  that the official values did not change for 2027, then confirm it or enter
  the new values in Tax Data."*
- **How to check it.** What the table holds, numbered steps and links to the
  official AEAT, BOE and Seguridad Social pages.
- **Checked automatically.** For Modelo 303, 130 and 390 the app compares the
  box numbers it uses with the official AEAT record layouts it ships
  (`modules/tax_es_forms/aeat_layouts/`) and shows ✓ or ⚠.
  `tests/test_year_table_checks.py` does the same on every test run, so a
  renumbered box fails the build.
- **Confirm or change.** *Confirm unchanged for 2027* (box tables), or edit
  the values and *Save for 2027* (Modelo 347 threshold; RETA rate, bracket
  limits and monthly quotas). The pages use the saved values for that year and
  stop warning. *Remove the confirmation* undoes it.

Box numbers cannot be edited: a renumbered box changes the calculation and
needs an app update (below). The same holds for a RETA table with a different
number of brackets.

| Table | Official source |
|-------|-----------------|
| Modelo 303 and 130 boxes | [AEAT Modelo 303](https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G414.shtml), [AEAT Modelo 130](https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G601.shtml) → Ayuda → Instrucciones |
| Modelo 390 casillas | [AEAT Modelo 390](https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G412.shtml) → Ayuda → Instrucciones |
| Modelo 347 threshold | [Real Decreto 1065/2007, art. 33](https://www.boe.es/buscar/act.php?id=BOE-A-2007-15984#a33), paragraph 1; [AEAT Modelo 347](https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI27.shtml) |
| RETA brackets and rate | [Seguridad Social: bases y tipos de cotización](https://www.seg-social.es/wps/portal/wss/internet/Trabajadores/CotizacionRecaudacionTrabajadores/36537), self-employed section |

Not in Tax Data yet (check them by hand, and update the code below):

| Data | Where you see it in the app | Official source |
|------|-----------------------------|-----------------|
| AEAT record layouts (import files) | The ⬇ AEAT file buttons | [Models 300–399](https://sede.agenciatributaria.gob.es/Sede/ayuda/disenos-registro/modelos-300-399.html), [models 100–199](https://sede.agenciatributaria.gob.es/Sede/ayuda/disenos-registro/modelos-100-199.html) |
| IRPF scales and deductions | IRPF Estimator | AEAT *Manual práctico de Renta* of the year, regional laws |
| Filing calendar | Fiscal Calendar (dates marked *verify*) | AEAT Sede, *Calendario del contribuyente* of the year |

### In the code — a release ships the new year

A confirmation in Tax Data is kept in the owner's database. A release adds
the checked year to the tables, so new installations do not warn:

| Data | File | Line to add |
|------|------|-------------|
| Modelo 303 and 130 boxes | `modules/tax_es_forms/boxes.py` | `2027: {"303": MODELO_303_BOXES, "130": MODELO_130_BOXES},` in `BOX_TABLES`; bump `BOX_TABLE_VERSION` |
| Modelo 390 casillas | `modules/tax_es_forms/boxes.py` | `2027: MODELO_390_BOXES,` in `MODELO_390_TABLES` |
| Modelo 347 threshold | `modules/tax_es_forms/boxes.py` | `2027: _MODELO_347_RULES_2025,` in `MODELO_347_TABLES` (a new rules dict if the amount changed) |
| RETA brackets and rate | `modules/reta_advisor/brackets.py` | The year in `BRACKET_TABLES` and `CONTRIBUTION_RATES`; bump `BRACKET_TABLE_VERSION` |
| IRPF scales | `modules/tax_es_irpf/params/` | Copy `es_irpf_2026.json` to `es_irpf_2027.json` and correct what changed |
| Filing calendar | `modules/fiscal_calendar/calendar_data.py` | The year in `VERIFIED_YEARS`, `WINDOW_OVERRIDES` for moved dates; bump `CALENDAR_VERSION` |

Say in a comment what was checked and when, run the checks and open one pull
request, *"data: 2027 tax tables"*:

```bash
python -m pytest tests/test_year_table_checks.py tests/test_year_keyed_tables.py
```

**Box numbers changed:** open an issue with the link and what changed (for
example "Modelo 390 casilla 108 renamed …"). The labels, the calculation and
the tests need updating, not only a new line.

### A new AEAT record layout

When the AEAT publishes a new "Diseño de registro" for 303, 130 or 390:
download the spreadsheet from the record layouts page above, rebuild the JSON
with `modules/tax_es_forms/aeat_layouts/build.py` (its first lines show the
command), add the file to `LAYOUT_FILES` in `aeat_files.py` under its first
exercise and run `python -m pytest tests/test_aeat_files.py
tests/test_year_table_checks.py`. The tests check positions, constants (each
"Tipo %" casilla's fixed rate) and every box the drafts use.

### For module authors

A module with a yearly table declares a `year_table` capability (contract in
`modules/tax_data/index.py`): how to check the table, its values for a year
as fields, an optional automatic check and validation. It reads the owner's
confirmation with `taxdata.year_values` and shows `taxdata.year_notice` on
its pages; without the Tax Data module it shows a short warning. Examples:
`modules/tax_es_forms/yearly.py` and `table_checks.py`,
`modules/reta_advisor/yearly.py` and `brackets.py`.

## Reporting Bugs and Requesting Features

Use the issue templates under **Issues → New issue**. For questions and ideas
that are not yet concrete, use **Discussions**.
