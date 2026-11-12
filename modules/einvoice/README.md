# E-Invoice / Facturae (`einvoice`)

Phase 1 of structured B2B e-invoicing (RD 238/2026): export issued invoices as
**Facturae 3.2.2 XML**, with a per-invoice readiness checklist that lists
exactly which party data is still missing. Generation uses the fiscal snapshot
frozen at issue and per-line VAT rates.

## Contents

1. [What Phase 1 provides](#what-phase-1-provides)
2. [Data readiness](#data-readiness)
3. [XAdES signature](#xades-signature)
4. [Known gaps](#known-gaps)
5. [Phase 2 design notes](#phase-2-design-notes)

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
  missing-data list when the invoice is not ready; `signed` tells whether the
  XML carries a signature; `?unsigned=1` returns the unsigned file)

## Data readiness

Customer and Settings already carry structured address fields
(street, postal code, town, country), so no schema changes were needed.
The readiness check requires:

| Party | Required fields |
|-------|-----------------|
| Seller (Settings) | NIF/VAT number, address, postal code, town |
| Customer | VAT/NIF number, address, postal code, town |
| Invoice | Issued (or paid) status and a fiscal snapshot |

## XAdES signature

When the **PDF Signature** module is enabled and has a PFX/P12 certificate
(Settings → PDF Signature), the export is signed with an enveloped
**XAdES-EPES** signature (`xades.py`, library `signxml`):

| Element | Value |
|---------|-------|
| Signature policy | `http://www.facturae.es/politica_de_firma_formato_facturae/politica_de_firma_formato_facturae_v3_1.pdf` |
| Policy hash | SHA-1 `Ohixl6upD6av8N7pEvDABhEL6hM=` |
| Signer role | `emisor` |
| Signature | RSA-SHA256, certificate chain in `KeyInfo` |

- The signed file downloads as `facturae_<number>.xsig`. Without a certificate
  the unsigned `.xml` is served as before; `?unsigned=1` forces it.
- A wrong certificate password shows an error and serves nothing.

**Validate before real use.** Upload one signed file to the official
validator — *Validar factura* on [facturae.gob.es](https://www.facturae.gob.es)
(or the FACe validation service) — and confirm that both the format and the
signature are accepted. Repeat after changing the certificate.

## Known gaps

- **Official validation** of a signed file with a real certificate has not
  been run from this repository (tests use a throwaway self-signed
  certificate); do it once as described above.
- **Validation of content**: run generated files through the official
  Facturae XSD and the EU EN 16931 reference validator before real B2B use.

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
