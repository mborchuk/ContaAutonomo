"""D-3 — official AEAT formats: huella (hash), QR URL, QR on the invoice PDF."""
import io
from datetime import date

import pytest
from pypdf import PdfReader

from modules.verifactu.aeat import (
    huella_alta,
    huella_anulacion,
    huella_input,
    ALTA_FIELDS,
    qr_url,
)
from modules.verifactu.pdf_qr import stamp_qr

# Worked examples from "Detalle de las especificaciones técnicas para
# generación de la huella o hash de los registros de facturación" v0.1.2.
CASE_1 = {'IDEmisorFactura': '89890001K', 'NumSerieFactura': '12345678/G33',
          'FechaExpedicionFactura': '01-01-2024', 'TipoFactura': 'F1',
          'CuotaTotal': '12.35', 'ImporteTotal': '123.45', 'Huella': '',
          'FechaHoraHusoGenRegistro': '2024-01-01T19:20:30+01:00'}
H1 = '3C464DAF61ACB827C65FDA19F352A4E3BDC2C640E9E9FC4CC058073F38F12F60'
H2 = 'F7B94CFD8924EDFF273501B01EE5153E4CE8F259766F88CF6ACB8935802A2B97'
H3 = '177547C0D57AC74748561D054A9CEC14B4C4EA23D1BEFD6F2E69E3A388F90C68'


def test_huella_case_1_first_record():
    assert huella_alta(CASE_1) == H1


def test_huella_case_2_chained_alta():
    values = dict(CASE_1, NumSerieFactura='12345679/G34', Huella=H1,
                  FechaHoraHusoGenRegistro='2024-01-01T19:20:35+01:00')
    assert huella_alta(values) == H2


def test_huella_case_3_anulacion():
    assert huella_anulacion({
        'IDEmisorFacturaAnulada': '89890001K', 'NumSerieFacturaAnulada': '12345679/G34',
        'FechaExpedicionFacturaAnulada': '01-01-2024', 'Huella': H2,
        'FechaHoraHusoGenRegistro': '2024-01-01T19:20:40+01:00'}) == H3


def test_huella_trims_and_ignores_trailing_zeros():
    padded = dict(CASE_1, NumSerieFactura='  12345678/G33  ', ImporteTotal='123.450',
                  CuotaTotal='12.350')
    assert huella_alta(padded) == H1
    assert huella_input(ALTA_FIELDS, {}).startswith('IDEmisorFactura=&NumSerieFactura=&')


def test_qr_url_encoding_matches_aeat_example():
    # AEAT QR spec v0.5.0, section 4 (test environment, numserie with '&').
    url = qr_url('89890001K', '12345678&G33', date(2024, 1, 1), 241.4,
                 mode='verifactu', environment='test')
    assert url == ('https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?nif=89890001K'
                   '&numserie=12345678%26G33&fecha=01-01-2024&importe=241.40')


def test_qr_url_no_verifactu_default_and_ascii_only():
    url = qr_url('89890001K', '2026/0001', date(2026, 3, 2), 121)
    assert url.startswith('https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/'
                          'ValidarQRNoVerifactu?')
    assert 'numserie=2026%2F0001' in url and 'importe=121.00' in url
    with pytest.raises(ValueError):
        qr_url('89890001K', 'Nº 1', date(2026, 3, 2), 1)


def _pdf(text='Invoice body text'):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 800, 'TOP LINE ' + text)
    c.showPage()
    c.drawString(72, 800, 'Second page')
    c.save()
    return buf.getvalue()


def test_stamp_adds_label_once_and_phrase_only_for_verifactu():
    stamped = stamp_qr(_pdf(), 'https://example.invalid/qr', phrase=None)
    pages = [p.extract_text() for p in PdfReader(io.BytesIO(stamped)).pages]
    assert len(pages) == 2
    assert 'QR tributario:' in pages[0] and 'QR tributario:' not in pages[1]
    assert 'TOP LINE' in pages[0]                   # original content kept
    assert 'VERI*FACTU' not in pages[0]
    with_phrase = stamp_qr(_pdf(), 'https://example.invalid/qr', phrase='VERI*FACTU')
    assert 'VERI*FACTU' in PdfReader(io.BytesIO(with_phrase)).pages[0].extract_text()


# --- module hook ------------------------------------------------------------------

def _issue(db, number):
    from app import Customer, Invoice, _issue_invoice
    cust = Customer(name=f'{number} SL', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    inv = Invoice(invoice_number=number, client_name=cust.name, amount_usd=0.0,
                  amount_eur=100.0, exchange_rate=1.0, invoice_date=date(2026, 3, 1),
                  status='draft', currency='EUR', customer_id=cust.id)
    db.session.add(inv)
    db.session.commit()
    return inv, _issue_invoice


def test_hook_stamps_issued_invoices_only(loaded_modules):
    from app import db
    vf = loaded_modules.modules['verifactu']
    inv, issue = _issue(db, 'D3-A')
    draft_pdf = _pdf()
    assert vf.filter_invoice_pdf(inv, draft_pdf) is draft_pdf      # draft: untouched
    issue(inv)
    stamped = loaded_modules.filter_invoice_pdf(inv, _pdf())
    assert 'QR tributario:' in PdfReader(io.BytesIO(stamped)).pages[0].extract_text()


def test_generated_pdf_of_issued_invoice_carries_qr(loaded_modules, client):
    from app import db
    inv, issue = _issue(db, 'D3-B')
    issue(inv)
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get(f'/preview-pdf/{inv.id}')
    assert resp.status_code == 200 and resp.mimetype == 'application/pdf'
    text = PdfReader(io.BytesIO(resp.data)).pages[0].extract_text()
    assert 'QR tributario:' in text
