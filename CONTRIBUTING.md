# Contributing to ContaAutónomo

Thank you for considering a contribution. Bug reports, documentation improvements, translations, tests, and focused code changes are welcome.

## Before you start

1. Search existing issues and pull requests to avoid duplicate work.
2. For substantial changes, open an issue first to discuss the problem and proposed approach.
3. Keep pull requests focused and explain the user problem they solve.
4. Never include real invoices, customer data, credentials, API keys, database files, or private documents in issues, tests, screenshots, or commits.

## Development setup

The project currently targets Python 3.14.

```bash
git clone https://github.com/mborchuk/ContaAutonomo.git
cd ContaAutonomo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Create local secrets and configuration if needed:

```bash
make setup
```

Run the development server:

```bash
FLASK_DEBUG=1 python app.py
```

## Checks

Run tests and lint before submitting:

```bash
make test
make lint
```

Add or update tests for behavior changes, especially where money, exchange rates, tax estimates, dates, invoice numbering, or historical invoice snapshots are involved.

## Architecture guidelines

- Follow the existing modular architecture and conventions.
- For module work, use the documented `BaseModule` and `CoreServices` interfaces.
- Avoid importing `app` or another module from a module implementation; use core services.
- Keep module-specific templates and static assets with the module.
- Do not silently change existing financial records or historical invoice snapshots.
- Keep optional integrations optional; core workflows should not require third-party API keys.
- Describe tax and compliance behavior precisely. Do not claim certification or legal compliance without verifiable evidence.

See [MODULES_DOCUMENTATION.md](MODULES_DOCUMENTATION.md) and [modules/README.md](modules/README.md) for module development details.

## Pull request checklist

- [ ] The change has a clear, focused purpose.
- [ ] Tests were added or updated where appropriate.
- [ ] `make test` passes.
- [ ] `make lint` passes.
- [ ] Documentation and configuration examples are updated.
- [ ] No secrets, personal data, or generated runtime files are included.
- [ ] Tax/compliance-related claims are accurate and appropriately qualified.

By submitting a contribution, you agree that your contribution will be distributed under the repository's MIT license.
