# Modelo 303/130 Drafts (`tax_es_forms`)

Box-level quarterly drafts of **Modelo 303** (IVA) and **Modelo 130** (IRPF
pago fraccionado), computed from the invoices and expenses already in the app,
so the figures can be transcribed into the AEAT portal. The module files
nothing and provides no tax advice — every page carries a non-dismissible
"estimate, not tax advice" banner.

## Contents

1. [Features](#features)
2. [Box mapping](#box-mapping)
3. [How the figures are computed](#how-the-figures-are-computed)
4. [Scope exclusions](#scope-exclusions)

## Features

- Draft pages per quarter: `/tax-forms-draft/303/<year>/<q>` and
  `/tax-forms-draft/130/<year>/<q>`, with an index at `/tax-forms-draft/`
- Copy-to-clipboard per box; print / save-as-PDF via a print stylesheet
- Dashboard Tax Obligations panel: current-quarter running 303/130 position
  (display-only — it never changes the panel's grand total)
- REST API: `GET /api/v1/m/tax_es_forms/draft/303/<year>/<q>` and `/130/...`

## Box mapping

Box numbers live in [`boxes.py`](boxes.py), stamped with `BOX_TABLE_VERSION`.
They are the standard **régimen general / estimación directa** boxes. Box
numbers change across AEAT form revisions — **verify against the current
official models before each fiscal year**:

- Modelo 303 (IVA): AEAT Sede Electrónica → Modelo 303
- Modelo 130 (IRPF pago fraccionado): AEAT Sede Electrónica → Modelo 130

## How the figures are computed

The math lives in [`calculator.py`](calculator.py) as pure functions
(no Flask, no database), which keeps it directly unit-testable.

**Modelo 303**

- *IVA repercutido (output VAT)*: invoices in the quarter with
  `status != cancelled` (accrual basis). Issued invoices use the fiscal
  snapshot frozen at issue (`snap_vat_rate` / `snap_taxable_base` /
  `snap_vat_amount`), so later edits to customers or settings never change a
  past quarter. Legacy and draft invoices fall back to deriving from the
  customer's `tax_type` and the configured VAT rate; `eu_b2b` and `non_eu`
  customers carry no output VAT.
- *IVA soportado (input VAT)*: per-expense `vat_amount` × `deductible_pct`,
  skipping non-deductible expenses. Legacy expenses without VAT data are
  excluded and counted — the page shows how many, so the user knows the
  deduction figure is understated. Expenses with zero Spanish VAT are not in
  the deductible base. Equipment (bienes de inversión: an equipment category
  at or above the threshold set in Settings → IRPF; default "Equipment",
  300 EUR net) goes to boxes 30/31, everything else to 28/29.
- *Box 59 / 120*: taxable base of invoices to EU business customers
  (`eu_b2b`) and to customers outside the EU (`non_eu`).
- *Box 110 / 78 / 87*: the credit carried from the previous quarter's
  **filed** Modelo 303 uploaded in Tax Forms (its box 87 plus its negative
  result, unless a refund was requested); applied to a positive result;
  box 71 = 46 − 78.

**Modelo 130**

- When the **IRPF Estimator** (`tax_es_irpf`) is enabled, the page and the API
  show its figures — one Modelo 130 calculation in the app: income by invoice
  date (unpaid included, cancelled excluded), expenses without VAT, Social
  Security payments, equipment depreciation and retenciones (box 06), as set
  in Settings → IRPF. `meta.source` says which module produced the boxes.
- Without the IRPF Estimator, the fallback below applies.
- Cumulative year-to-date, as the official model requires: income minus
  expenses (net amounts where known, gross for legacy rows), 20% of the
  positive result, minus the estimated payments from earlier quarters of the
  same year. The result never goes below zero.

## Scope exclusions

Out of scope in v1 (also listed in the UI fine print): régimen simplificado
(módulos), recargo de equivalencia, prorrata, criterio de caja (accrual basis
only), and the intra-EU acquisition / reverse-charge detail boxes.

A fast-follow for annual summaries (Modelo 390 and 100) can reuse the same
engine by aggregating four quarters; it is intentionally not built yet.
