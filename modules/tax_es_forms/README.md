# tax_es_forms — Modelo 303 / 130 Draft Calculator (F5)

Caveman summary: this module count numbers already in the app and put them in the
boxes of **Modelo 303 (IVA)** and **Modelo 130 (IRPF pago fraccionado)** so user
can transcribe them into AEAT portal. It **file nothing** and give **no tax
advice** — every page say "estimate, not tax advice".

## What it does

- Per-quarter draft pages: `/tax-forms-draft/303/<year>/<q>` and `/130/<year>/<q>`.
- Index at `/tax-forms-draft/` listing years × quarters.
- Copy-to-clipboard per box + print/save-as-PDF (print stylesheet).
- Dashboard Tax Obligations panel: current-quarter running 303/130 position
  (display-only — does not change the panel grand total).
- REST API: `GET /api/v1/m/tax_es_forms/draft/303/<year>/<q>` and `/130/...`.

## Box mapping (F5-D1) — STANDING RULE

Box numbers live in [`boxes.py`](boxes.py), stamped `BOX_TABLE_VERSION`. They are
the standard **régimen general / estimación directa** boxes. **Verify against the
current official AEAT models before each fiscal year** — box numbers change across
form revisions.

- Modelo 303 (IVA): AEAT Sede Electrónica → Modelo 303.
- Modelo 130 (IRPF pago fraccionado): AEAT Sede Electrónica → Modelo 130.

### 303 aggregation
- **IVA devengado (repercutido):** invoices in the quarter, `status != cancelled`
  (devengo/accrual). For **issued** invoices the frozen **F2-D4 fiscal snapshot**
  (`snap_vat_rate` / `snap_taxable_base` / `snap_vat_amount`) is used — so later
  customer/settings edits never change a filed quarter. Legacy/draft invoices
  fall back to deriving from customer `tax_type == 'standard'` × `default_vat_rate`
  (`eu_b2b` / `non_eu` carry no output VAT). Base = invoice `amount_eur`.
- **IVA soportado (deducible):** now uses **F4** per-expense
  `vat_amount` × `deductible_pct` (skips `deductible == False`). Legacy rows with
  no `vat_amount` are excluded and counted as "missing VAT data" (surfaced in UI).

### 130 aggregation
- **Cumulative year-to-date** to the end of the selected quarter (as the model
  requires). Ingresos = YTD invoice bases; Gastos = YTD expense **net** (F4
  `net_amount`) where present, gross fallback for legacy rows. Rendimiento =
  ingresos − gastos. Pago = 20% of positive rendimiento. Box 05 (prior payments)
  estimated from the prior quarters' cumulative rendimiento. Result never < 0.

## Scope exclusions (v1, shown in UI fine print)
módulos (régimen simplificado), recargo de equivalencia, prorrata, criterio de
caja (we use devengo only), intra-EU acquisition / inversión del sujeto pasivo
detail boxes.

## Dependencies (now satisfied)
- **F2-D4** (fiscal snapshot) — SHIPPED. Issued invoices carry frozen VAT; the
  engine prefers the snapshot and falls back to live derivation for legacy/draft.
- **F4** (expense VAT) — SHIPPED. IVA soportado and 130 net gastos use the real
  per-expense VAT/net; legacy VAT-less rows are excluded and counted.

## Fast-follow stub (F5-D5, not built)
Annual **Modelo 390** (resumen anual IVA) and **Modelo 100** (renta) summaries can
reuse `calculator.py` by summing the four quarters / the full year. Not in v1 —
tracked as a follow-up. No code shipped for these yet.
