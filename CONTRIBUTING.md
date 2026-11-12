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
- Match the style of the surrounding code. There is no formatter or linter
  configuration; keep diffs focused on the change.
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

Some data is valid for one year only. Review it **every January** for the new
fiscal year, in one pull request. Years without their own data keep working:
the app uses the latest earlier table and shows *"figures use the <year>
table — verify"*.

| File | What to check | Official source | When done |
|------|---------------|-----------------|-----------|
| `modules/fiscal_calendar/calendar_data.py` | Generated filing windows for the new year (quarterly forms, 390, Renta); add `WINDOW_OVERRIDES` for dates AEAT moves (national holidays) | AEAT *Calendario del contribuyente* | Add the year to `VERIFIED_YEARS`, bump `CALENDAR_VERSION` |
| `modules/tax_es_forms/boxes.py` | Modelo 303 and 130 box numbers and labels | AEAT Sede, current Modelo 303 / 130 | Add the year to `BOX_TABLES` (same dicts if unchanged), bump `BOX_TABLE_VERSION` |
| `modules/tax_es_forms/boxes.py` (390) | Modelo 390 casillas in `MODELO_390_BOXES` | AEAT Sede, *Instrucciones modelo 390* of the year | Add the year to `MODELO_390_TABLES` |
| `modules/reta_advisor/brackets.py` | RETA brackets and quotas, generic deduction % | Seguridad Social (cotización por ingresos reales), BOE *orden de cotización* | Add the year to `BRACKET_TABLES`, bump `BRACKET_TABLE_VERSION` |
| `modules/tax_es_irpf/params/es_irpf_<year>.json` | IRPF scales, minimums, regional deductions | AEAT *Manual práctico de Renta*, regional laws | Add the year's file |

`year_tables.resolve()` implements the fallback for the box and bracket tables.

## Reporting Bugs and Requesting Features

Use the issue templates under **Issues → New issue**. For questions and ideas
that are not yet concrete, use **Discussions**.
