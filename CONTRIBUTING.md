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
January, check them for the new year. Most years nothing changes and the
update is one line per table.

### How the app tells you

- **On the page.** A draft or advisor page for a year without its own table
  uses the latest earlier table and shows a warning: *"Figures for 2027 use
  the 2026 table — check that the official values did not change for 2027."*
  Open **How to check …** under it: what the table holds, the steps, links to
  the official pages and what to change once checked.
- **Checked automatically.** For Modelo 303, 130 and 390 the app compares the
  box numbers it uses with the official AEAT record layouts it ships
  (`modules/tax_es_forms/aeat_layouts/`) and shows the result in the same
  warning (✓ or ⚠). The test `tests/test_year_table_checks.py` does the same
  on every test run, so a renumbered box fails the build.

### Step 1 — check (no code)

| Data | Where you see it in the app | What to compare | Official source |
|------|-----------------------------|-----------------|-----------------|
| Modelo 303 and 130 boxes | Tax Drafts → 303 / 130, Box column | Each box number and its meaning (21 % VAT line [07]/[09]; result [71] / [19]) | [AEAT Modelo 303](https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G414.shtml), [AEAT Modelo 130](https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G601.shtml) → Ayuda → Instrucciones |
| Modelo 390 casillas | Tax Drafts → 390, "390 casilla" column | Each casilla number and its meaning | [AEAT Modelo 390](https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G412.shtml) → Ayuda → Instrucciones |
| Modelo 347 threshold | Tax Drafts → 347 (the threshold is in the note at the top) | 3.005,06 euros a year | [Real Decreto 1065/2007, art. 33](https://www.boe.es/buscar/act.php?id=BOE-A-2007-15984#a33), paragraph 1; [AEAT Modelo 347](https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI27.shtml) |
| AEAT record layouts (import files) | The ⬇ AEAT file buttons | A new "Diseño de registro" for the year | [Models 300–399](https://sede.agenciatributaria.gob.es/Sede/ayuda/disenos-registro/modelos-300-399.html), [models 100–199](https://sede.agenciatributaria.gob.es/Sede/ayuda/disenos-registro/modelos-100-199.html) |
| RETA brackets and rate | RETA Advisor → bracket table | Each tramo (net income from / to, base mínima / máxima), the contribution rate, the cotización order | [Seguridad Social: bases y tipos de cotización](https://www.seg-social.es/wps/portal/wss/internet/Trabajadores/CotizacionRecaudacionTrabajadores/36537), self-employed section |
| IRPF scales and deductions | IRPF Estimator | State and regional scales, minimums, deductions | AEAT *Manual práctico de Renta* of the year, regional laws |
| Filing calendar | Fiscal Calendar (dates marked *verify*) | Each filing window of the year | AEAT Sede, *Calendario del contribuyente* of the year |

### Step 2 — record the result

**Nothing changed** (the usual case): add the year to the table with the
same values, and say what you checked. For example, for Modelo 390 in
`modules/tax_es_forms/boxes.py`:

```python
MODELO_390_TABLES = {
    2025: MODELO_390_BOXES,
    2026: MODELO_390_BOXES,
    # 2027: checked against the AEAT Modelo 390 instructions for 2027 on 2027-01-20.
    2027: MODELO_390_BOXES,
}
```

| Data | File | Line to add |
|------|------|-------------|
| Modelo 303 and 130 boxes | `modules/tax_es_forms/boxes.py` | `2027: {"303": MODELO_303_BOXES, "130": MODELO_130_BOXES},` in `BOX_TABLES`; bump `BOX_TABLE_VERSION` |
| Modelo 390 casillas | `modules/tax_es_forms/boxes.py` | `2027: MODELO_390_BOXES,` in `MODELO_390_TABLES` |
| Modelo 347 threshold | `modules/tax_es_forms/boxes.py` | `2027: _MODELO_347_RULES_2025,` in `MODELO_347_TABLES` |
| RETA brackets and rate | `modules/reta_advisor/brackets.py` | The year in `BRACKET_TABLES` and `CONTRIBUTION_RATES`; bump `BRACKET_TABLE_VERSION` |
| IRPF scales | `modules/tax_es_irpf/params/` | Copy `es_irpf_2026.json` to `es_irpf_2027.json` and correct what changed |
| Filing calendar | `modules/fiscal_calendar/calendar_data.py` | The year in `VERIFIED_YEARS`, `WINDOW_OVERRIDES` for moved dates; bump `CALENDAR_VERSION` |

Then run the checks and open one pull request, *"data: 2027 tax tables"*:

```bash
python -m pytest tests/test_year_table_checks.py tests/test_year_keyed_tables.py
```

**Something changed:** open an issue with the link and what changed (for
example "Modelo 390 casilla 108 renamed …"). Changed box numbers need the
labels, the calculation and the tests updated, not only a new line.

### Step 3 — a new AEAT record layout

When the AEAT publishes a new "Diseño de registro" for 303, 130 or 390:
download the spreadsheet from the record layouts page above, rebuild the JSON
with `modules/tax_es_forms/aeat_layouts/build.py` (its first lines show the
command), add the file to `LAYOUT_FILES` in `aeat_files.py` under its first
exercise and run `python -m pytest tests/test_aeat_files.py
tests/test_year_table_checks.py`. The tests check positions, constants (each
"Tipo %" casilla's fixed rate) and every box the drafts use.

`year_tables.resolve()` implements the fallback; `year_tables.TableCheck`
describes how a page explains a table, and each module keeps its own checks
(`modules/tax_es_forms/table_checks.py`, `modules/reta_advisor/table_checks.py`).

## Reporting Bugs and Requesting Features

Use the issue templates under **Issues → New issue**. For questions and ideas
that are not yet concrete, use **Discussions**.
