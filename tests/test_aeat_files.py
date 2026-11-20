"""G-5 — AEAT import files: every field at its official position."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.tax_es_forms import aeat_files as af

LAYOUTS = Path(af.LAYOUT_DIR)
SETTINGS = SimpleNamespace(vat_number='ES12345678Z', nie_number=None,
                           owner_name='Nombre Apellido Segundo', business_name=None,
                           phone='600 000 000')


def _field(layout, page, box=None, n=None):
    for f in layout['pages'][page]:
        if (box is not None and f[4] == box) or (n is not None and f[0] == n):
            return f
    raise KeyError(box or n)


def _cut(page_text, field):
    _n, pos, length = field[:3]
    return page_text[pos - 1:pos - 1 + length]


def _pages(content, model, page_ids):
    """Split a <T…> file into its page records."""
    out, rest = {}, content[328:]          # 328 = header page 00
    for pid in page_ids:
        start = rest.index(f'<T{model}{pid}')
        end_tag = rest.index('>', rest.index('</T', start)) + 1
        out[pid] = rest[start:end_tag]
        rest = rest[end_tag:]
    return out


# --- layouts ---------------------------------------------------------------- #

@pytest.mark.parametrize('filename', sorted(os.listdir(af.LAYOUT_DIR)))
def test_layout_pages_are_contiguous_and_tagged(filename):
    if not filename.endswith('.json'):
        return
    layout = json.loads((LAYOUTS / filename).read_text(encoding='utf-8'))
    assert layout['source'].startswith('https://sede.agenciatributaria.gob.es/')
    for page_id, fields in layout['pages'].items():
        pos = 1
        for n, start, length, typ, _box, const, _desc in fields:
            assert start == pos, (filename, page_id, n)
            assert typ in ('A', 'An', 'Num', 'N'), (filename, page_id, n, typ)
            assert const is None or len(const) == length
            pos += length
        assert fields[0][5] == '<T'
        if page_id != '00':
            assert fields[-1][5].startswith('</T')


# --- Modelo 303 ------------------------------------------------------------- #

def _draft_303(**boxes):
    base = {k: 0.0 for k in ('07', '09', '10', '11', '12', '13', '27', '28', '29', '30', '31',
                             '36', '37', '38', '39', '45', '46', '59', '120', '110', '78',
                             '87', '71')}
    base.update(boxes)
    return {'year': 2026, 'quarter': 3, 'boxes': base}


def test_303_file_places_each_box_at_its_official_position():
    draft = _draft_303(**{'07': 10000.0, '09': 2100.0, '27': 2100.0, '28': 1000.0,
                          '29': 210.0, '45': 210.0, '46': 1890.0, '59': 5000.0,
                          '110': 90.0, '78': 90.0, '87': 0.0, '71': 1800.0})
    content, info = af.modelo_303(draft, SETTINGS)
    layout = af.load_layout('303', 2026)
    assert content.startswith('<T30302026' + '3T0000><AUX>')
    assert content.endswith('</T303020263T0000>')
    pages = _pages(content, '303', ['01000', '03000', 'DID00'])
    p1, p3 = pages['01000'], pages['03000']
    assert len(p1) == 1581 and len(p3) == 1017 and len(pages['DID00']) == 823
    assert _cut(p1, _field(layout, '01', box='07')) == '00000000001000000'   # 21 % base
    assert _cut(p1, _field(layout, '01', box='08')) == '02100'               # 21.00 %
    assert _cut(p1, _field(layout, '01', box='09')) == '00000000000210000'
    assert _cut(p1, _field(layout, '01', box='01')) == '0' * 17              # 4 % line empty
    assert _cut(p1, _field(layout, '01', box='46')) == '00000000000189000'
    assert _cut(p1, _field(layout, '01', n=7)) == '12345678Z'
    assert _cut(p1, _field(layout, '01', n=6)) == 'I'
    assert _cut(p3, _field(layout, '03', box='59')) == '00000000000500000'
    assert _cut(p3, _field(layout, '03', box='65')) == '10000'
    assert _cut(p3, _field(layout, '03', box='69')) == '00000000000180000'
    assert _cut(p3, _field(layout, '03', box='71')) == '00000000000180000'
    assert info['declaration_type'] == 'I'


def test_303_negative_result_is_signed_and_compensated():
    content, info = af.modelo_303(_draft_303(**{'46': -50.0, '71': -50.0}), SETTINGS)
    layout = af.load_layout('303', 2026)
    p3 = _pages(content, '303', ['01000', '03000', 'DID00'])['03000']
    assert _cut(p3, _field(layout, '03', box='71')) == 'N0000000000005000'
    assert info['declaration_type'] == 'C'


def test_303_before_the_layout_year_is_unsupported():
    with pytest.raises(af.UnsupportedYear):
        af.modelo_303(dict(_draft_303(), year=2025), SETTINGS)


def _numeric_problems(content, model, year):
    """Numeric fields (`Num`, `N`) of every page record that hold anything but
    digits (an `N` field may start with the minus sign `N`). Constants come
    from the official layout and are left out."""
    import re
    layout = af.load_layout(model, year)
    problems = []
    for pid, fields in layout['pages'].items():
        if pid == '00':
            continue
        code = next(f[5] for f in fields if f[0] == 3)      # page number constant
        start = content.find(f'<T{model}{code}')
        if start < 0:
            continue
        record = content[start:start + sum(f[2] for f in fields)]
        for n, pos, length, typ, _box, const, desc in fields:
            if const is not None:          # the layout's own value, e.g. page '02B00'
                continue
            value = record[pos - 1:pos - 1 + length]
            ok = re.fullmatch(r'N?\d+' if typ == 'N' else r'\d+', value) if typ in ('Num', 'N') else True
            if not ok:
                problems.append((pid, n, value, desc[:60]))
    return problems


@pytest.mark.parametrize('quarter', [1, 2, 3, 4])
def test_303_numeric_fields_hold_only_digits(quarter):
    """The Sede rejected a 3T file: fields 17 and 18 (prorrata especial) were
    blank, and a numeric field takes digits only (E010170, E010180); they take
    only 1 or 2 (E010171, E010181)."""
    content, _ = af.modelo_303(dict(_draft_303(**{'07': 1000.0, '09': 210.0, '71': 210.0}),
                                    quarter=quarter), SETTINGS)
    assert _numeric_problems(content, '303', 2026) == []
    layout = af.load_layout('303', 2026)
    p1 = content[content.index('<T30301000'):]
    flags = _cut(p1, _field(layout, '01', n=17)) + _cut(p1, _field(layout, '01', n=18))
    assert flags == '22'
    assert _cut(p1, _field(layout, '01', box='169')) == '00050'      # E010541: only 0,5


def test_every_303_rate_field_has_its_fixed_rate():
    """Each "Tipo %" casilla of the 303 holds a fixed rate the Sede checks
    (E010541: [169] only admits 0,5); the layout must carry it as a constant,
    also after a rebuild from the AEAT spreadsheet."""
    layout = af.load_layout('303', 2026)
    rates = {f[4]: f[5] for fields in layout['pages'].values() for f in fields
             if 'Tipo %' in (f[6] or '')}
    assert len(rates) == 11 and all(rates.values()), rates
    assert rates['169'] == '00050' and rates['08'] == '02100'


@pytest.mark.parametrize('quarter', [1, 2, 3, 4])
def test_130_numeric_fields_hold_only_digits(quarter):
    draft = {'year': 2026, 'quarter': quarter, 'boxes': {
        '01': 100.0, '02': 600.0, '03': -500.0, '04': 0.0, '05': 0.0, '06': 0.0, '07': 0.0}}
    content, _ = af.modelo_130(draft, SETTINGS)
    assert _numeric_problems(content, '130', 2026) == []


def test_390_numeric_fields_hold_only_digits():
    boxes = {k: 0.0 for k in af._390_BOXES}
    boxes.update({'05': 40000.0, '06': 8400.0})
    content, _ = af.modelo_390({'year': 2026, 'boxes': boxes}, SETTINGS)
    assert _numeric_problems(content, '390', 2026) == []


def test_a_blank_numeric_field_is_refused():
    layout = af.load_layout('303', 2026)
    with pytest.raises(ValueError, match='field 17 is numeric'):
        af.render_page(layout['pages']['01'], {17: ' '})


# --- Modelo 130 ------------------------------------------------------------- #

def test_130_file():
    draft = {'year': 2026, 'quarter': 2, 'boxes': {
        '01': 20000.0, '02': 5000.0, '03': 15000.0, '04': 3000.0, '05': 1000.0,
        '06': 200.0, '07': 1800.0}}
    content, _ = af.modelo_130(draft, SETTINGS)
    layout = af.load_layout('130', 2026)
    page = _pages(content, '130', ['01000'])['01000']
    assert len(page) == 600
    assert _cut(page, _field(layout, '01', box='01')) == '00000000002000000'
    assert _cut(page, _field(layout, '01', box='07')) == '00000000000180000'
    assert _cut(page, _field(layout, '01', box='19')) == '00000000000180000'
    assert _cut(page, _field(layout, '01', n=8)).strip() == 'APELLIDO SEGUNDO'
    assert _cut(page, _field(layout, '01', n=9)).strip() == 'NOMBRE'
    assert content.endswith('</T130020262T0000>')


def test_130_negative_quarter():
    draft = {'year': 2026, 'quarter': 1, 'boxes': {
        '01': 100.0, '02': 600.0, '03': -500.0, '04': 0.0, '05': 0.0, '06': 0.0, '07': 0.0}}
    content, info = af.modelo_130(draft, SETTINGS)
    layout = af.load_layout('130', 2026)
    page = _pages(content, '130', ['01000'])['01000']
    assert _cut(page, _field(layout, '01', box='03')) == 'N0000000000050000'
    assert info['declaration_type'] == 'N'


# --- Modelo 390 ------------------------------------------------------------- #

def test_390_file_has_every_page_and_the_casillas():
    boxes = {k: 0.0 for k in af._390_BOXES}
    boxes.update({'05': 40000.0, '06': 8400.0, '108': 55000.0, '95': 8000.0})
    content, _ = af.modelo_390({'year': 2026, 'boxes': boxes}, SETTINGS, files_347=True)
    layout = af.load_layout('390', 2026)
    assert content.startswith('<T39002026' + '0A0000><AUX>')
    ids = ['01000', '02000', '02B00', '03000', '04000', '05000', '06000', '07000', '08000']
    pages = _pages(content, '390', ids)
    for pid, key in zip(ids, af._390_PAGE_ORDER):
        assert len(pages[pid]) == sum(f[2] for f in layout['pages'][key])
    assert _cut(pages['02000'], _field(layout, '02', box='05')) == '00000000004000000'
    assert _cut(pages['06000'], _field(layout, '06', box='108')) == '00000000005500000'
    assert _cut(pages['01000'], _field(layout, '01', n=44)) == '1'   # files a 347


# --- Modelo 349 ------------------------------------------------------------- #

def test_349_records():
    draft = {'year': 2026, 'quarter': 3, 'operators': [
        {'country': 'DE', 'vat_number': '123456789', 'name': 'Kunde GmbH', 'key': 'S',
         'base': 1234.56},
        {'country': 'FR', 'vat_number': '12345678901', 'name': 'Client Façade SARL',
         'key': 'S', 'base': 100.0}]}
    content, _ = af.modelo_349(draft, SETTINGS)
    records = content.split('\r\n')[:-1]
    assert [len(r) for r in records] == [500, 500, 500]
    head, first = records[0], records[1]
    assert head[0:8] == '13492026' and head[8:17] == '12345678Z'
    assert head[135:137] == '3T'
    assert head[137:146] == '000000002'
    assert head[146:161] == '000000000133456'
    assert first[0:4] == '2349' and first[75:77] == 'DE'
    assert first[77:92] == '123456789      '
    assert first[132] == 'S' and first[133:146] == '0000000123456'
    assert 'FAÇADE' in records[2]
    assert af.encode(content).count('Ç'.encode('iso-8859-1')) == 1


# --- Modelo 347 ------------------------------------------------------------- #

def test_347_records():
    draft = {'year': 2026, 'lines': [
        {'key': 'B', 'name': 'Cliente Uno SL', 'nif': 'B12345674', 'country': 'España',
         'postal_code': '46001', 'quarters': [1000.0, 0.0, 2500.5, 0.0], 'total': 3500.5,
         'reverse_charge': False},
        {'key': 'A', 'name': 'Cloud Ltd', 'nif': 'GB123456789', 'country': 'United Kingdom',
         'postal_code': '', 'quarters': [0.0, 4000.0, 0.0, 0.0], 'total': 4000.0,
         'reverse_charge': True}]}
    content, info = af.modelo_347(draft, SETTINGS)
    records = content.split('\r\n')[:-1]
    assert [len(r) for r in records] == [500, 500, 500]
    head, spanish, foreign = records
    assert head[0:8] == '13472026' and head[57] == 'T'
    assert head[135:144] == '000000002'
    assert head[144:160] == ' 000000000750050'
    assert spanish[17:26] == 'B12345674' and spanish[75] == 'D'
    assert spanish[76:78] == '46' and spanish[81] == 'B'
    assert spanish[82:98] == ' 000000000350050'
    assert spanish[135:151] == ' 000000000100000'      # Q1
    assert spanish[199:215] == ' 000000000250050'      # Q3
    assert foreign[17:26] == ' ' * 9 and foreign[76:80] == '99GB'
    assert foreign[81] == 'A' and foreign[281] == 'X'   # inversión del sujeto pasivo
    assert info['warnings'] == []


def test_347_line_without_postal_code_is_warned():
    draft = {'year': 2026, 'lines': [
        {'key': 'B', 'name': 'Sin CP SL', 'nif': 'B12345674', 'country': '',
         'postal_code': '', 'quarters': [4000.0, 0, 0, 0], 'total': 4000.0}]}
    _content, info = af.modelo_347(draft, SETTINGS)
    assert 'postal code' in info['warnings'][0]


# --- module routes ---------------------------------------------------------- #

def _auth(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def test_download_routes(loaded_modules, app, client):
    from app import Settings, db
    settings = Settings.query.first()
    if settings is None:
        settings = Settings()
        db.session.add(settings)
    settings.vat_number, settings.owner_name = '12345678Z', 'Nombre Apellido'
    db.session.commit()
    _auth(client)
    for url, name in (('/tax-forms-draft/303/2031/1/aeat-file', '303_2031_1T.txt'),
                      ('/tax-forms-draft/130/2031/1/aeat-file', '130_2031_1T.txt'),
                      ('/tax-forms-draft/349/2031/1/aeat-file', '349_2031_1T.txt'),
                      ('/tax-forms-draft/390/2031/aeat-file', '390_2031.txt'),
                      ('/tax-forms-draft/347/2031/aeat-file', '347_2031.txt')):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert 'iso-8859-1' in resp.headers['Content-Type']
        assert name in resp.headers['Content-Disposition']
    page = client.get('/tax-forms-draft/303/2031/1').get_data(as_text=True)
    assert 'AEAT file (not validated)' in page


def test_download_for_a_year_without_layout_redirects(loaded_modules, client):
    _auth(client)
    resp = client.get('/tax-forms-draft/303/2024/1/aeat-file')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/tax-forms-draft/')


def test_download_requires_login_and_known_form(loaded_modules, client):
    assert client.get('/tax-forms-draft/303/2031/1/aeat-file').status_code == 302
    _auth(client)
    assert client.get('/tax-forms-draft/100/2031/aeat-file').status_code in (302, 404)


def test_supported_models_are_documented():
    doc = (LAYOUTS.parent / 'AEAT_FILES.md').read_text(encoding='utf-8')
    for model in ('303', '130', '349', '390', '347'):
        assert f'Modelo {model}' in doc
    assert 'not validated' in doc.lower()


def test_347_page_warns_about_lines_the_file_cannot_complete(loaded_modules, app, client):
    from datetime import date
    from app import Customer, Invoice, db
    cust = Customer(name='Sin Codigo Postal SL', vat_number='B76543210', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    db.session.add(Invoice(invoice_number='AF-347', client_name=cust.name, customer_id=cust.id,
                           amount_usd=0.0, amount_eur=5000.0, exchange_rate=1.0,
                           invoice_date=date(2032, 3, 3), status='draft', currency='EUR'))
    db.session.commit()
    _auth(client)
    html = client.get('/tax-forms-draft/347/2032').get_data(as_text=True)
    assert 'AEAT file: Sin Codigo Postal SL: no Spanish postal code' in html


def test_generation_error_redirects_and_logs_no_request_value(loaded_modules, client,
                                                             monkeypatch, caplog):
    forms = loaded_modules.modules['tax_es_forms']

    def broken(form, year, quarter=None):
        raise ValueError('negative amount in an unsigned field')
    monkeypatch.setattr(forms, 'aeat_file', broken)
    _auth(client)
    with caplog.at_level('ERROR'):
        resp = client.get('/tax-forms-draft/349/2031/2/aeat-file')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/tax-forms-draft/')
    messages = [r.getMessage() for r in caplog.records if 'AEAT file' in r.getMessage()]
    assert messages == ['AEAT file generation failed']
