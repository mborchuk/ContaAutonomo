# Tax Provision (`tax_provision`)

A dashboard panel that answers "how much should I set aside for tax right
now": one total and its four parts. Every part is a registry function of the
module that calculates it (bundled: Spanish Tax Forms, IRPF Estimator, RETA
Advisor), so a replacement module that declares the same name is used
instead; this module adds no tax rules.

| Part | Function | Figure | Next filing date |
|------|----------|--------|------------------|
| IVA, open quarter | `tax.draft_303(year, quarter)` | box 71 | Modelo 303 window |
| IRPF, open quarter | `tax.modelo130_boxes(year, quarter)` | box 07 | Modelo 130 window |
| Renta, year to date | `tax.modelo100_result(year)` | `modelo100_diferencial` | Modelo 100 window (April–June of next year) |
| RETA regularización | `reta.projection(today)` | `regularization_delta` | none — the Seguridad Social settles it after the year |

The open quarter is the calendar quarter of today.

## Rules

- **Total = sum of the parts that are available.** Positive is owed,
  negative is money coming back. A negative 303 result in Q1–Q3 is shown as a
  credit to compensate, in Q4 as a refund; a negative renta or RETA result is a
  refund. A negative part reduces the total.
- **No double counting.** The Modelo 100 estimate credits the year's Modelo
  130 payments, the open quarter's included, so adding the open quarter's 130
  and the renta result counts that payment once.
- **Missing parts.** A part that nothing provides, or that cannot be
  calculated (for example a foral comunidad in the IRPF profile), is left out
  of the total and listed as missing; the total is labelled incomplete.
- **Filing dates** come from `calendar.deadline_for`. Without a calendar
  provider the panel says to enable the Fiscal Calendar instead of a date.
- Each part links to its page in the bundled module; a part from a
  replacement module is shown without a link.

## Scope

Estimate, not tax advice: the panel carries that wording. Its figures are only
as good as the drafts they come from — see `modules/tax_es_forms/README.md`,
the IRPF Estimator and the RETA advisor for their own scope and sources.

The module has no tables, no settings and no dependencies; enable it in
Settings → Modules.

## Tests

`tests/test_tax_provision.py`: the total equals the four engine results for a
synthetic year; a VAT credit reduces the total; a disabled module is named as
missing; filing dates come from the calendar; the dashboard shows the panel
with the estimate wording. `tests/test_tax_functions.py`: a replacement that
declares `reta.projection` is used, without the bundled page link.
