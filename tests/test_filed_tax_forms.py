"""Filed AEAT figures: read from the uploaded PDF, used by the calculations.

The PDFs here are synthetic, drawn with ReportLab in the AEAT layout (box
number, then the amount to its right on the same line). No real form is used.
"""
import io
import json
from datetime import date

import pytest
from reportlab.pdfgen import canvas

from modules.tax_management.aeat_pdf import read_filed_form
from tests.test_irpf_modelo130 import year_2026  # noqa: F401  (fixture)


def aeat_pdf(form, year, period, boxes):
    """A two-page PDF shaped like an AEAT filing receipt."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(50, 800, 'INFORMACIÓN DE LA PRESENTACIÓN DE LA DECLARACIÓN')
    c.drawString(50, 780, f'Modelo {form}')
    c.showPage()
    c.drawString(450, 800, 'Modelo')
    c.drawString(450, 785, form)
    c.drawString(50, 760, f'{year} {period}')
    y = 700
    for box, value in boxes:
        c.drawString(300, y, box)
        c.drawRightString(540, y, value)
        y -= 20
    c.save()
    return buf.getvalue()


def test_reads_modelo_130_boxes():
    pdf = aeat_pdf('130', 2026, '3T', [('01', '30.000,00'), ('02', '3.920,50'),
                                       ('05', '3.422,00'), ('07', '1.793,90'),
                                       ('19', '1.793,90')])
    parsed = read_filed_form(pdf)
    assert parsed == {'form': '130', 'year': 2026, 'quarter': 3,
                      'boxes': {'01': 30000.0, '02': 3920.5, '05': 3422.0,
                                '07': 1793.9, '19': 1793.9}}


def test_modelo_303_ignores_printed_rates_and_keeps_negatives():
    pdf = aeat_pdf('303', 2026, '2T', [('08', '21,00'), ('28', '427,03'),
                                       ('29', '89,68'), ('71', '-217,94'),
                                       ('110', '53,35')])
    boxes = read_filed_form(pdf)['boxes']
    assert '08' not in boxes
    assert boxes == {'28': 427.03, '29': 89.68, '71': -217.94, '110': 53.35}


def test_other_pdfs_are_not_read():
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(50, 800, 'Invoice 2026-001  Total 1.210,00')
    c.save()
    assert read_filed_form(buf.getvalue()) is None
    assert read_filed_form(b'not a pdf') is None


@pytest.fixture
def tax_forms(app, loaded_modules, client, monkeypatch):
    tm = loaded_modules.modules['tax_management']
    # Keep uploads out of the working tree: storage keys only.
    monkeypatch.setattr(tm.core, 'save_file', lambda f, sub, name: f'{sub}/{name}')
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    return tm


def _upload(client, form, year, quarter, pdf):
    return client.post('/tax-forms/upload', data={
        'form_type': form, 'year': str(year), 'quarter': str(quarter),
        'file': (io.BytesIO(pdf), f'M{form}-{quarter}T-{year}.pdf')},
        content_type='multipart/form-data')


def test_upload_stores_filed_boxes_and_amount(tax_forms, client):
    pdf = aeat_pdf('130', 2026, '1T', [('01', '10.000,00'), ('07', '1.612,00'),
                                       ('19', '1.612,00')])
    _upload(client, '130', 2026, 1, pdf)
    stored = tax_forms.TaxForm.query.filter_by(form_type='130', year=2026, quarter=1).one()
    assert stored.boxes == {'01': 10000.0, '07': 1612.0, '19': 1612.0}
    assert stored.amount == 1612.0
    assert tax_forms.filed_boxes('130', 2026, 1)['07'] == 1612.0
    page = client.get('/tax-forms/').get_data(as_text=True)
    assert '[07] 1612.00' in page


def test_read_boxes_of_earlier_uploads(tax_forms, client, monkeypatch):
    from app import db

    pdf = aeat_pdf('303', 2026, '1T', [('29', '53,35'), ('71', '-53,35')])
    db.session.add(tax_forms.TaxForm(form_type='303', year=2026, quarter=1,
                                     file_path='tax_forms/2026/Q1/303-Q1.pdf',
                                     original_filename='M303-1T-2026.pdf'))
    db.session.commit()
    monkeypatch.setattr(tax_forms.core.storage, 'get',
                        lambda key: (pdf, '303-Q1.pdf'))
    client.post('/tax-forms/read-boxes')
    assert tax_forms.filed_boxes('303', 2026, 1) == {'29': 53.35, '71': -53.35}


def test_upload_for_another_period_stores_no_boxes(tax_forms, client):
    pdf = aeat_pdf('130', 2026, '2T', [('07', '1.000,00')])
    _upload(client, '130', 2026, 1, pdf)
    stored = tax_forms.TaxForm.query.filter_by(form_type='130', year=2026, quarter=1).one()
    assert stored.boxes == {}


def test_box_05_uses_the_filed_result_of_earlier_quarters(year_2026, loaded_modules):
    from app import db

    tm = loaded_modules.modules['tax_management']
    estimated_q2, _ = year_2026.modelo130_boxes(2026, 2)
    db.session.add(tm.TaxForm(form_type='130', year=2026, quarter=1,
                              file_path='', status='filed',
                              filed_boxes=json.dumps({'07': 1500.0})))
    db.session.commit()
    boxes, _ = year_2026.modelo130_boxes(2026, 2)
    assert boxes['05'] == 1500.0
    assert estimated_q2['05'] != 1500.0
    assert boxes['07'] == pytest.approx(boxes['04'] - 1500.0)
