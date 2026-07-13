"""F9 — E-invoice Phase 1: readiness check + Facturae XML generation."""
from datetime import date
from xml.etree import ElementTree as ET

import pytest

from modules.einvoice.facturae import (
    check_readiness,
    country_alpha3,
    generate_facturae,
)


def test_country_alpha3_mapping():
    assert country_alpha3('Spain') == 'ESP'
    assert country_alpha3('españa') == 'ESP'
    assert country_alpha3('Germany') == 'DEU'
    assert country_alpha3('') == 'ESP'          # single-user Spanish default


@pytest.fixture
def issued_invoice(loaded_modules):
    from app import db, Customer, Invoice, Settings, _issue_invoice

    settings = Settings.query.first()
    if not settings:
        settings = Settings()
        db.session.add(settings)
    settings.business_name = 'Mi Negocio SL'
    settings.vat_number = 'B12345678'
    settings.address = 'Calle Mayor 1'
    settings.postal_code = '12006'
    settings.city = 'Castellón'
    settings.country = 'Spain'

    cust = Customer(name='EU Kunde GmbH', tax_type='eu_b2b',
                    vat_number='DE123456789', address='Hauptstr. 5',
                    postal_code='10115', city='Berlin', country='Germany')
    db.session.add(cust)
    db.session.flush()
    inv = Invoice(invoice_number='EI-1', client_name=cust.name, amount_usd=0.0,
                  amount_eur=2000.0, exchange_rate=1.0,
                  invoice_date=date(2026, 5, 1), status='draft',
                  currency='EUR', customer_id=cust.id)
    db.session.add(inv)
    db.session.commit()
    _issue_invoice(inv)
    return inv, settings, cust


def test_readiness_flags_missing_data(loaded_modules):
    from app import db, Customer, Invoice

    cust = Customer(name='Bare Cli', tax_type='standard')  # no VAT, no address
    db.session.add(cust)
    db.session.flush()
    inv = Invoice(invoice_number='EI-BARE', client_name='Bare Cli',
                  amount_usd=0.0, amount_eur=10.0, exchange_rate=1.0,
                  invoice_date=date(2026, 5, 2), status='draft',
                  currency='EUR', customer_id=cust.id)
    db.session.add(inv)
    db.session.commit()

    em = loaded_modules.modules['einvoice']
    problems, _s, _c = em._readiness(inv)
    assert any('not issued' in p for p in problems)
    assert any('customer VAT' in p for p in problems)


def test_facturae_xml_structure_and_snapshot_values(issued_invoice):
    inv, settings, cust = issued_invoice
    problems = check_readiness(inv, settings, cust)
    assert problems == [], problems

    xml = generate_facturae(inv, settings, cust)
    root = ET.fromstring(xml)
    assert root.tag.endswith('Facturae')
    assert root.find('FileHeader/SchemaVersion').text == '3.2.2'

    # Buyer is German -> overseas address with DEU.
    buyer_cc = root.find('Parties/BuyerParty/LegalEntity/OverseasAddress/CountryCode')
    assert buyer_cc is not None and buyer_cc.text == 'DEU'
    # Seller residence in Spain.
    seller_addr = root.find('Parties/SellerParty/LegalEntity/AddressInSpain')
    assert seller_addr is not None

    # eu_b2b snapshot: 0% VAT frozen at issue.
    assert root.find('Invoices/Invoice/TaxesOutputs/Tax/TaxRate').text == '0.00'
    assert root.find('Invoices/Invoice/InvoiceTotals/InvoiceTotal').text == '2000.00'
    # Series + sequential number from F2.
    header = root.find('Invoices/Invoice/InvoiceHeader')
    assert header.find('InvoiceSeriesCode').text == inv.series
    assert header.find('InvoiceClass').text == 'OO'


def test_facturae_download_route(issued_invoice, client):
    inv, _s, _c = issued_invoice
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get(f'/einvoice/facturae/{inv.id}.xml')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/xml'
    assert b'Facturae' in resp.data
