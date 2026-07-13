#!/usr/bin/env python3
"""
Facturae 3.2.2 XML generation — pure functions, no Flask, no DB.

CAVEMAN NOTE (F9-D3 spike outcome): XML here is UNSIGNED. Legally valid
Facturae need an XAdES-EPES enveloped signature; no XAdES library ship with
this app (pyHanko cover PAdES/PDF only). Adding `signxml` + reusing the
pdf_signature PFX upload flow is the follow-up — until then, treat output as
a structured draft for platforms that accept unsigned files or sign on upload.

STANDING RULE (F9-D6): validate generated files against the official Facturae
XSD + the EU EN 16931 validator before real B2B use. Spec pinned:
Facturae v3.2.2 (namespace below).
"""

from datetime import date
from xml.etree import ElementTree as ET

FACTURAE_NS = 'http://www.facturae.gob.es/formato/Versiones/Facturaev3_2_2.xml'
SCHEMA_VERSION = '3.2.2'

# Facturae uses ISO 3166-1 alpha-3. Common names seen in this app's free-text
# country fields; fallback ESP (single-user Spanish app).
_COUNTRY_ALPHA3 = {
    'spain': 'ESP', 'españa': 'ESP', 'es': 'ESP', 'esp': 'ESP',
    'portugal': 'PRT', 'france': 'FRA', 'francia': 'FRA',
    'germany': 'DEU', 'alemania': 'DEU', 'de': 'DEU',
    'italy': 'ITA', 'italia': 'ITA',
    'netherlands': 'NLD', 'poland': 'POL', 'polonia': 'POL', 'pl': 'POL',
    'ukraine': 'UKR', 'ucrania': 'UKR', 'ua': 'UKR',
    'united kingdom': 'GBR', 'uk': 'GBR', 'gb': 'GBR',
    'united states': 'USA', 'us': 'USA', 'usa': 'USA',
    'czech republic': 'CZE', 'czechia': 'CZE', 'cz': 'CZE',
}


def country_alpha3(name):
    return _COUNTRY_ALPHA3.get((name or '').strip().lower(), 'ESP')


def check_readiness(invoice, seller, customer):
    """EN 16931 data-readiness check. Returns list of missing-data problems.

    `seller` = Settings row, `customer` = Customer row (may be None).
    Empty list = e-invoice ready.
    """
    problems = []
    if invoice.status not in ('issued', 'paid'):
        problems.append('invoice is not issued yet')
    if getattr(invoice, 'snap_vat_rate', None) is None:
        problems.append('invoice has no fiscal snapshot (issued pre-F2?)')
    if not seller or not (seller.vat_number or seller.nie_number):
        problems.append('seller NIF/VAT missing (Settings)')
    for field, label in (('address', 'seller address'),
                         ('postal_code', 'seller postal code'),
                         ('city', 'seller town')):
        if not seller or not getattr(seller, field, None):
            problems.append(f'{label} missing (Settings)')
    if not customer:
        problems.append('invoice has no linked customer')
    else:
        if not customer.vat_number:
            problems.append('customer VAT/NIF missing')
        for field, label in (('address', 'customer address'),
                             ('postal_code', 'customer postal code'),
                             ('city', 'customer town')):
            if not getattr(customer, field, None):
                problems.append(f'customer {label.split(" ", 1)[1]} missing')
    return problems


def _sub(parent, tag, text=None):
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def _party(parent, tag, name, nif, address, postal_code, town, province, country):
    party = _sub(parent, tag)
    tax = _sub(party, 'TaxIdentification')
    _sub(tax, 'PersonTypeCode', 'J')          # J = legal entity (v1 default)
    alpha3 = country_alpha3(country)
    _sub(tax, 'ResidenceTypeCode', 'R' if alpha3 == 'ESP' else
         ('U' if alpha3 in ('PRT', 'FRA', 'DEU', 'ITA', 'NLD', 'POL', 'CZE') else 'E'))
    _sub(tax, 'TaxIdentificationNumber', nif or '')
    legal = _sub(party, 'LegalEntity')
    _sub(legal, 'CorporateName', name or '')
    if alpha3 == 'ESP':
        addr = _sub(legal, 'AddressInSpain')
        _sub(addr, 'Address', address or '')
        _sub(addr, 'PostCode', postal_code or '')
        _sub(addr, 'Town', town or '')
        _sub(addr, 'Province', province or town or '')
        _sub(addr, 'CountryCode', 'ESP')
    else:
        addr = _sub(legal, 'OverseasAddress')
        _sub(addr, 'Address', address or '')
        _sub(addr, 'PostCodeAndTown', f'{postal_code or ""} {town or ""}'.strip())
        _sub(addr, 'Province', province or town or '')
        _sub(addr, 'CountryCode', alpha3)


def generate_facturae(invoice, seller, customer, items=None):
    """Build unsigned Facturae 3.2.2 XML for one ISSUED invoice.

    Uses the F2 fiscal snapshot (frozen VAT) and per-line rates when present.
    Returns UTF-8 XML bytes.
    """
    ET.register_namespace('fe', FACTURAE_NS)
    root = ET.Element(f'{{{FACTURAE_NS}}}Facturae')

    header = _sub(root, 'FileHeader')
    _sub(header, 'SchemaVersion', SCHEMA_VERSION)
    _sub(header, 'Modality', 'I')             # individual file
    _sub(header, 'InvoiceIssuerType', 'EM')   # issued by the seller

    batch = _sub(header, 'Batch')
    _sub(batch, 'BatchIdentifier', invoice.invoice_number)
    _sub(batch, 'InvoicesCount', 1)
    total = f'{invoice.amount_eur or 0:.2f}'
    for tag in ('TotalInvoicesAmount', 'TotalOutstandingAmount',
                'TotalExecutableAmount'):
        _sub(_sub(batch, tag), 'TotalAmount', total)
    _sub(batch, 'InvoiceCurrencyCode', 'EUR')

    parties = _sub(root, 'Parties')
    _party(parties, 'SellerParty',
           getattr(seller, 'business_name', None) or getattr(seller, 'owner_name', ''),
           seller.vat_number or seller.nie_number,
           seller.address, seller.postal_code, seller.city,
           getattr(seller, 'city', None), getattr(seller, 'country', 'Spain'))
    _party(parties, 'BuyerParty',
           customer.name, customer.vat_number,
           customer.address, customer.postal_code, customer.city,
           getattr(customer, 'city', None), customer.country)

    invoices = _sub(root, 'Invoices')
    inv_el = _sub(invoices, 'Invoice')

    ih = _sub(inv_el, 'InvoiceHeader')
    _sub(ih, 'InvoiceNumber', invoice.sequence_number or invoice.invoice_number)
    _sub(ih, 'InvoiceSeriesCode', invoice.series or '')
    _sub(ih, 'InvoiceDocumentType', 'FC')
    if getattr(invoice, 'rectifies_invoice_id', None):
        _sub(ih, 'InvoiceClass', 'OR')        # rectificative
        corr = _sub(ih, 'Corrective')
        _sub(corr, 'InvoiceNumber', invoice.rectifies_invoice_id)
        _sub(corr, 'ReasonCode', '01' if invoice.rectification_type == 'sustitucion' else '02')
        _sub(corr, 'CorrectionMethod',
             '01' if invoice.rectification_type == 'sustitucion' else '02')
    else:
        _sub(ih, 'InvoiceClass', 'OO')        # original

    data = _sub(inv_el, 'InvoiceIssueData')
    _sub(data, 'IssueDate', (invoice.invoice_date or date.today()).isoformat())
    _sub(data, 'InvoiceCurrencyCode', 'EUR')
    _sub(data, 'TaxCurrencyCode', 'EUR')
    _sub(data, 'LanguageName', 'es')

    # Snapshot-frozen tax data (F2-D4).
    vat_rate = getattr(invoice, 'snap_vat_rate', None) or 0.0
    vat_amount = getattr(invoice, 'snap_vat_amount', None) or 0.0
    base = getattr(invoice, 'snap_taxable_base', None)
    if base is None:
        base = invoice.amount_eur or 0.0

    taxes = _sub(inv_el, 'TaxesOutputs')
    tax = _sub(taxes, 'Tax')
    _sub(tax, 'TaxTypeCode', '01')            # IVA
    _sub(tax, 'TaxRate', f'{vat_rate:.2f}')
    _sub(_sub(tax, 'TaxableBase'), 'TotalAmount', f'{base:.2f}')
    _sub(_sub(tax, 'TaxAmount'), 'TotalAmount', f'{vat_amount:.2f}')

    totals = _sub(inv_el, 'InvoiceTotals')
    _sub(totals, 'TotalGrossAmount', f'{base:.2f}')
    _sub(totals, 'TotalGrossAmountBeforeTaxes', f'{base:.2f}')
    _sub(totals, 'TotalTaxOutputs', f'{vat_amount:.2f}')
    _sub(totals, 'TotalTaxesWithheld', '0.00')
    invoice_total = f'{base + vat_amount:.2f}'
    _sub(totals, 'InvoiceTotal', invoice_total)
    _sub(totals, 'TotalOutstandingAmount', invoice_total)
    _sub(totals, 'TotalExecutableAmount', invoice_total)

    items_el = _sub(inv_el, 'Items')
    lines = items if items is not None else list(getattr(invoice, 'items', []) or [])
    if not lines:
        lines = [None]                        # single synthetic line
    for item in lines:
        line = _sub(items_el, 'InvoiceLine')
        if item is None:
            desc = invoice.description or 'Services'
            qty, price, subtotal = 1.0, base, base
            line_rate = vat_rate
        else:
            desc = item.description
            qty = item.quantity or 1
            price = item.unit_price_usd or 0
            subtotal = item.subtotal_usd or (qty * price)
            line_rate = item.vat_rate if getattr(item, 'vat_rate', None) is not None else vat_rate
        _sub(line, 'ItemDescription', desc)
        _sub(line, 'Quantity', f'{qty:.2f}')
        _sub(line, 'UnitOfMeasure', '01')
        _sub(line, 'UnitPriceWithoutTax', f'{price:.6f}')
        _sub(line, 'TotalCost', f'{subtotal:.2f}')
        _sub(line, 'GrossAmount', f'{subtotal:.2f}')
        line_taxes = _sub(line, 'TaxesOutputs')
        ltax = _sub(line_taxes, 'Tax')
        _sub(ltax, 'TaxTypeCode', '01')
        _sub(ltax, 'TaxRate', f'{line_rate:.2f}')
        _sub(_sub(ltax, 'TaxableBase'), 'TotalAmount', f'{subtotal:.2f}')

    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
