# Contributing to ContaAutónomo

Thanks for your interest in improving ContaAutónomo. This guide explains how to
set up the project, the expectations for changes, and how pull requests are
reviewed.

## Table of Contents

1. [Ground Rules](#ground-rules)
2. [Development Setup](#development-setup)
3. [Making Changes](#making-changes)
4. [Pull Request Process](#pull-request-process)
5. [Reporting Bugs and Requesting Features](#reporting-bugs-and-requesting-features)

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

## Reporting Bugs and Requesting Features

Use the issue templates under **Issues → New issue**. For questions and ideas
that are not yet concrete, use **Discussions**.
