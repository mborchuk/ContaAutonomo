# Verifactu: Comparison with the Official AEAT Specifications

## Contents

1. [Sources](#sources)
2. [Summary](#summary)
3. [Record hash (huella)](#record-hash-huella)
4. [Record content](#record-content)
5. [QR code and cotejo URL](#qr-code-and-cotejo-url)
6. [Submission to AEAT](#submission-to-aeat)
7. [Next steps](#next-steps)

## Sources

| Document | Version | Used for |
|----------|---------|----------|
| *Detalle de las especificaciones técnicas para generación de la huella o hash de los registros de facturación* (AEAT, web de desarrolladores) | 0.1.2, 27/08/2024 | Hash input, field order, output format, worked examples |
| *Detalle de las especificaciones técnicas del código «QR» de la factura y de la «URL» del servicio de cotejo o remisión de información por parte del receptor de la factura* (AEAT) | 0.5.0, 10/12/2025 | QR content, URLs, encoding, size, position, texts |
| Orden HAC/1177/2024, articles 20 and 21 (quoted in the QR document) | — | Legal requirements for the QR and the VERI\*FACTU phrase |

Comparison made on 12 November 2026 against `main@5b77078`.

## Summary

| Area | Official requirement | App before this change | Status |
|------|----------------------|------------------------|--------|
| Hash algorithm | SHA-256 | SHA-256 | ✅ Same |
| Hash input | `Field=value&…` over 8 named fields (alta) / 5 (anulación) | Previous hash + JSON of the whole payload | ❌ Different — official function now implemented (`aeat.py`), chain not yet switched |
| Hash output | 64 uppercase hex characters | 64 lowercase hex characters | ❌ Different (same as above) |
| First record | `Huella=` empty, `PrimerRegistro=S` | Links to a sentinel of 64 zeros | ❌ Different (same as above) |
| Record signature | Not part of the hash spec (XML records are signed when required by the order) | HMAC-SHA256 with a local key | ⚠️ App-specific tamper evidence; keep alongside |
| QR URL | `ValidarQR` (VERI\*FACTU) or `ValidarQRNoVerifactu`, prod/test hosts | Always `ValidarQR`, production | ✅ Fixed |
| QR parameters | URL-encoded UTF-8, printable ASCII only | Not encoded (`/` in the number broke the URL) | ✅ Fixed |
| QR amount | Invoice total | Taxable base (the app stores the base; IVA is derived) | ✅ Fixed — base + IVA |
| QR NIF | NIF of the issuer | VAT number as entered (may carry `ES`) | ✅ Fixed — `ES` prefix removed |
| QR size and error correction | 30–40 mm, level M | Screen only, default level | ✅ Fixed — 30 mm, level M |
| QR on the invoice | Page 1, top, centred, "QR tributario:" above | Invoice view only | ✅ Fixed — every generated PDF of an issued invoice |
| VERI\*FACTU phrase | Only for systems that submit records | — | ✅ Not printed (mode `no_verifactu`) |
| Submission | Real-time remission to AEAT (VERI\*FACTU mode) | Scaffold, no-op | ⛔ Not available — see below |

## Record hash (huella)

The official hash of an *alta* record is SHA-256 over this UTF-8 string, in
this order, with values trimmed and numeric fields compared without trailing
zeros:

```text
IDEmisorFactura=…&NumSerieFactura=…&FechaExpedicionFactura=DD-MM-AAAA&TipoFactura=…
&CuotaTotal=…&ImporteTotal=…&Huella=<previous huella or empty>&FechaHoraHusoGenRegistro=…
```

An *anulación* uses `IDEmisorFacturaAnulada`, `NumSerieFacturaAnulada`,
`FechaExpedicionFacturaAnulada`, `Huella`, `FechaHoraHusoGenRegistro`.

`modules/verifactu/aeat.py` implements this (`huella_alta`, `huella_anulacion`)
and is tested against the three worked examples of the AEAT document — all
three hashes match exactly.

The record chain in `chain.py` still uses the draft format (`CHAIN_SPEC_VERSION
= "draft-1"`). Switching it is a data migration, described under
[Next steps](#next-steps).

## Record content

The official *RegistroAlta* needs data the app does not store yet:

| Official field | App today |
|----------------|-----------|
| `TipoFactura` (F1, F2, R1–R5…) | Not stored; derivable (F1 normal, R1–R4 rectificative by reason) |
| `CuotaTotal`, `ImporteTotal` | Snapshot IVA and base; total = base + IVA |
| `FechaHoraHusoGenRegistro` (ISO 8601 with time zone) | `timestamp` in naive UTC |
| `Desglose` (per rate, `ClaveRegimen`, `CalificacionOperacion`) | Single rate snapshot; no regime keys |
| `SistemaInformatico` (producer, system ID, version, installation number) | Not stored |
| Rectification data (`TipoRectificativa`, `FacturasRectificadas`) | `rectifies_invoice_id`, `rectification_type` |

## QR code and cotejo URL

Implemented in `aeat.qr_url()` and `pdf_qr.stamp_qr()`:

- URL per mode and environment (`VERIFACTU_MODE`: `no_verifactu` by default,
  `verifactu` only when records are submitted; `VERIFACTU_QR_ENV`:
  `production` or `test`).
- Parameters `nif`, `numserie`, `fecha` (DD-MM-AAAA), `importe` (dot decimal),
  URL-encoded; non-printable-ASCII values are rejected.
- QR 30 × 30 mm, error correction M, at least 3 mm quiet zone, placed at the
  top of page 1, centred, with "QR tributario:" above it. The first page's
  content is scaled down slightly to make room, so every invoice template
  works unchanged. Later pages are untouched.
- The phrase "VERI\*FACTU" is printed under the QR only in `verifactu` mode.

## Submission to AEAT

**Not available.** Real-time remission needs access to the AEAT test
environment, a qualified certificate and the SOAP/XML record design
(*RegistroAlta*, *RegistroAnulacion*). None of this is available from this
repository. `_submit_pending` logs that submission is not available, and the
app therefore works as a **non-VERI\*FACTU** system: the QR uses
`ValidarQRNoVerifactu` and the VERI\*FACTU phrase is not printed.

## Next steps

1. Store the missing record fields (table above) at issue time.
2. Add `CHAIN_SPEC_VERSION = "aeat-1"`: new records carry the official huella
   (`aeat.huella_alta` / `huella_anulacion`), chained by the previous huella.
   Old `draft-1` records stay verifiable with the current algorithm; the
   verifier picks the algorithm by the record's `spec_version` (and the
   signing key by `key_id`, from the dedicated signing key).
3. With AEAT test access: build the XML records, sign and submit them,
   handle responses, then switch `VERIFACTU_MODE` to `verifactu`.
