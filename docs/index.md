---
layout: page
title: "ContaAutónomo — Self-hosted accounting for freelancers"
description: "Open-source Flask app for invoices, expenses and Spanish/Polish taxes. Modular, multi-currency, Docker-ready."
permalink: /
image: /assets/img/og-default.png
---

Invoices, expenses and taxes for freelancers (Autónomos) — self-hosted, modular, and built to fit your country. ContaAutónomo helps independent professionals keep their books, file taxes, and stay compliant without giving up control of their data.

<p><a class="cta-button" href="https://github.com/mborchuk/ContaAutonomo" target="_blank" rel="noopener noreferrer">View on GitHub →</a></p>

## Key Features

- **Invoice management** — create, edit and issue invoices with PDF export, status tracking and per-client numbering.
- **Expense tracking** — log expenses with receipts, contractors and categories, all linked to your accounts.
- **Tax management (Spain &amp; Poland)** — built-in support for Spanish VAT (IVA), income tax withholding (IRPF) and Social Security, plus Polish VAT, PIT and ZUS via the Tax Poland module.
- **Multi-currency with ECB rates** — work in 50+ currencies with automatic exchange rates from the European Central Bank.
- **Modular architecture** — enable only the modules you need; ship new capabilities (auth providers, storage backends, country tax packs) without touching the core.
- **Docker-ready deployment** — run the whole stack with a single `docker compose up -d`.

## Supported Countries

### Spain

Designed first for Spanish freelancers (Autónomos). Covers VAT (IVA) at the standard 21% rate, income tax withholding (IRPF) for invoices to Spanish clients, and the monthly Social Security contribution (cuota de autónomos). Reports follow the layouts expected by the Agencia Tributaria.

### Poland

Polish support is delivered through the **Tax Poland** module: VAT handling for domestic and EU B2B sales, personal income tax (PIT) calculations, and ZUS social contributions. The module reuses the same invoicing and expense flows, so your data model stays consistent across countries.

New countries can be supported by adding modules — see [Architecture]({{ "/architecture/" | relative_url }}) for the module lifecycle and capability system.

## Tech Stack

- **Flask** — web framework and Blueprint-based routing
- **SQLAlchemy** — ORM and database migrations
- **ReportLab** — server-side PDF generation for invoices and reports
- **Docker** — single-command deployment for self-hosting

## Quick Start

1. Clone the repository:

   ```bash
   git clone https://github.com/mborchuk/ContaAutonomo.git
   ```

2. Start with Docker:

   ```bash
   cd ContaAutonomo && docker compose up -d
   ```

3. Open the app: navigate to <http://localhost:5000> and complete the first-run setup.

For the full source, issues and contributions, visit the project on <a href="https://github.com/mborchuk/ContaAutonomo" target="_blank" rel="noopener noreferrer">GitHub</a>.
