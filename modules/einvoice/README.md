# E-Invoice / Facturae (`einvoice`)

Phase 1 of structured B2B e-invoicing (RD 238/2026): export issued invoices as
**Facturae 3.2.2 XML**, with a per-invoice readiness checklist that lists
exactly which party data is still missing. Generation uses the fiscal snapshot
frozen at issue and per-line VAT rates.

## Contents

1. [What Phase 1 provides](#what-phase-1-provides)
2. [Data readiness](#data-readiness)
3. [Known gaps](#known-gaps)
4. [Phase 2 design notes](#phase-2-design-notes)

## What Phase 1 provides

- `facturae.py` — a pure generator producing Facturae 3.2.2:
  - `FileHeader` / `Parties` with `AddressInSpain` vs. `OverseasAddress` and
    ISO 3166-1 alpha-3 country codes
  - `InvoiceHeader` including the `Corrective` block for rectificative
    invoices (reason and method derived from `rectification_type`)
  - `TaxesOutputs` and `InvoiceTotals` from the frozen fiscal snapshot
  - one `InvoiceLine` per item, with per-line VAT rates
- Invoice view integration: a **Facturae XML** action on issued/paid invoices
  (`/einvoice/facturae/<id>.xml`) and a readiness panel while data is missing
- REST API: `GET /api/v1/m/einvoice/facturae/<id>` (returns 409 with the
  missing-data list when the invoice is not ready)

## Data readiness

Customer and Settings already carry structured address fields
(street, postal code, town, country), so no schema changes were needed.
The readiness check requires:

| Party | Required fields |
|-------|-----------------|
| Seller (Settings) | NIF/VAT number, address, postal code, town |
| Customer | VAT/NIF number, address, postal code, town |
| Invoice | Issued (or paid) status and a fiscal snapshot |

## Known gaps

- **XAdES signature**: a legally valid Facturae requires an XAdES enveloped
  signature. No XAdES library is currently in the dependency set (pyHanko
  covers PDF/PAdES only), so the output is **unsigned** — a structured draft
  suitable for platforms that accept unsigned files or sign on upload.
  Follow-up: add `signxml` and reuse the certificate upload flow from
  `pdf_signature`.
- **Validation**: run generated files through the official Facturae XSD and
  the EU EN 16931 reference validator before real B2B use.

## Phase 2 design notes

Deferred until the RD 238/2026 ministerial order defines the technical
requirements (expected to specify the public exchange solution and API):

- **Exchange**: default route via the AEAT public solution; private platforms
  through an adapter interface mirroring the pluggable auth-provider pattern.
- **Status messages**: recipients must report accepted/rejected/paid within
  four business days — planned as an `einvoice_status` table, a scheduler
  polling job, and an invoice view panel, with alerts through the existing
  `notify` capability.
- **Receiving e-invoices (Phase 3)**: parse inbound Facturae/UBL into draft
  expenses, reusing the AI parser's prefill flow for review.
