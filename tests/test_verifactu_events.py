"""Verifactu event log (registro de eventos, Orden HAC/1177/2024 art. 9):
the AEAT event hash, chain checks, the XML record, and the events the
module records (start, end, checks, anomalies, restore, exports, summary).
Synthetic data only."""
import hashlib
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import pytest

from modules.verifactu import events as ev

SYSTEM = {'producer_name': 'Example Producer SL', 'nif': '89890001K', 'id_otro': None,
          'system_name': 'ContaAutonomo', 'system_id': 'CA', 'version': '2+vf0.2.0',
          'installation_number': 'INST0001'}
TZ = timezone(timedelta(hours=1))
NS = {'sf': ev.XML_NAMESPACE}


# --- Hash (AEAT hash specification v0.1.2, section 3.c) ------------------------------

def test_event_hash_input_follows_the_aeat_order():
    text = ev.huella_input(SYSTEM, '12345678Z', '01', '', '2026-11-26T10:00:00+01:00')
    # The specification's own example starts like this: NIF given, ID empty.
    assert text.startswith('NIF=89890001K&ID=&IdSistemaInformatico=')
    assert text == ('NIF=89890001K&ID=&IdSistemaInformatico=CA&Version=2+vf0.2.0'
                    '&NumeroInstalacion=INST0001&NIF=12345678Z&TipoEvento=01&HuellaEvento='
                    '&FechaHoraHusoGenEvento=2026-11-26T10:00:00+01:00')
    expected = hashlib.sha256(text.encode('utf-8')).hexdigest().upper()
    assert ev.huella_evento(SYSTEM, '12345678Z', '01', '', '2026-11-26T10:00:00+01:00') \
        == expected
    assert len(expected) == 64


def test_values_are_trimmed_before_hashing():
    spaced = dict(SYSTEM, system_id=' CA ')
    assert ev.huella_input(spaced, ' 12345678Z ', '01', '', 't') == \
        ev.huella_input(SYSTEM, '12345678Z', '01', '', 't')


# --- Chain check ------------------------------------------------------------------

def _chain(times):
    rows, prev = [], None
    for i, moment in enumerate(times):
        generated = ev.timestamp(moment)
        huella = ev.huella_evento(SYSTEM, '12345678Z', '10' if i else '01',
                                  prev['huella'] if prev else '', generated)
        row = {'event_type': '10' if i else '01', 'generated_at': generated,
               'prev_huella': prev['huella'] if prev else '',
               'prev_event_type': prev['event_type'] if prev else None,
               'prev_generated_at': prev['generated_at'] if prev else None,
               'huella': huella, 'issuer_nif': '12345678Z', 'system': SYSTEM}
        rows.append(row)
        prev = row
    return rows


NOW = datetime(2026, 11, 26, 12, 0, tzinfo=TZ)
TIMES = [NOW - timedelta(hours=3), NOW - timedelta(hours=2), NOW - timedelta(hours=1)]


def test_an_intact_chain_has_no_anomaly():
    assert ev.check_chain(_chain(TIMES), NOW) == []


@pytest.mark.parametrize('change, code', [
    (lambda rows: rows[1].update(huella='0' * 64), '01'),
    (lambda rows: rows[2].update(prev_huella='F' * 64), '08'),
    (lambda rows: rows[0].update(prev_huella='A' * 64), '04'),
    (lambda rows: rows[2].update(prev_event_type='07'), '06'),
])
def test_a_changed_chain_is_reported_with_its_anomaly_type(change, code):
    rows = _chain(TIMES)
    change(rows)
    assert code in {a['type'] for a in ev.check_chain(rows, NOW)}


def test_dates_out_of_order_or_in_the_future_are_reported():
    rows = _chain([TIMES[1], TIMES[0]])
    assert [a['type'] for a in ev.check_chain(rows, NOW)] == ['11']
    rows = _chain([NOW + timedelta(minutes=5)])
    assert [a['type'] for a in ev.check_chain(rows, NOW)] == ['13']
    rows = _chain([NOW + timedelta(seconds=30)])     # within the one-minute margin
    assert ev.check_chain(rows, NOW) == []


def test_a_bad_signature_is_reported():
    rows = _chain(TIMES)
    found = ev.check_chain(rows, NOW, signature_ok=lambda e: e is not rows[1])
    assert [(a['index'], a['type']) for a in found] == [(1, '02')]


# --- XML (EventosSIF.xsd) ------------------------------------------------------------

class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _row(**kw):
    base = dict(system=SYSTEM, issuer_nif='12345678Z', issuer_name='Example Owner',
                generated_at='2026-11-26T10:00:00+01:00', event_type='01', prev_huella='',
                prev_event_type=None, prev_generated_at=None, huella='A' * 64,
                signature='b' * 64, key_id='k1')
    base.update(kw)
    return _Row(**base)


def _evento(xml):
    root = ET.fromstring(xml)
    assert root.tag == f'{{{ev.XML_NAMESPACE}}}RegistroEvento'
    return root.find('sf:Evento', NS)


def test_first_event_xml_follows_the_design():
    evento = _evento(ev.event_xml(_row(), {}))
    names = [child.tag.split('}')[1] for child in evento]
    assert names == ['SistemaInformatico', 'ObligadoEmision', 'FechaHoraHusoGenEvento',
                     'TipoEvento', 'Encadenamiento', 'TipoHuella', 'HuellaEvento']
    assert evento.findtext('sf:Encadenamiento/sf:PrimerEvento', namespaces=NS) == 'S'
    assert evento.findtext('sf:SistemaInformatico/sf:IdSistemaInformatico', namespaces=NS) == 'CA'
    assert evento.findtext('sf:TipoHuella', namespaces=NS) == '01'


def test_summary_xml_carries_counts_and_the_previous_event():
    row = _row(event_type='10', prev_huella='C' * 64, prev_event_type='01',
               prev_generated_at='2026-11-26T04:00:00+01:00')
    data = {'counts': {'01': 1, '03': 2}, 'altas': 2, 'anulaciones': 1,
            'cuota_total': 42.0, 'importe_total': 242.0,
            'first': {'issuer_nif': '12345678Z', 'number': 'T-1', 'date': '01-03-2026',
                      'huella': 'd' * 64},
            'last': {'issuer_nif': '12345678Z', 'number': 'T-2', 'date': '02-03-2026',
                     'huella': 'e' * 64}}
    evento = _evento(ev.event_xml(row, data))
    summary = evento.find('sf:DatosPropiosEvento/sf:ResumenEventos', NS)
    assert [(t.findtext('sf:TipoEvento', namespaces=NS),
             t.findtext('sf:NumeroDeEventos', namespaces=NS))
            for t in summary.findall('sf:TipoEvento', NS)] == [('01', '1'), ('03', '2')]
    assert summary.findtext('sf:SumaImporteTotalAlta', namespaces=NS) == '242.00'
    assert evento.findtext('sf:Encadenamiento/sf:EventoAnterior/sf:HuellaEvento',
                           namespaces=NS) == 'C' * 64


def test_an_empty_summary_says_there_was_no_event():
    evento = _evento(ev.event_xml(_row(event_type='10'), {'counts': {}}))
    assert evento.findtext('sf:DatosPropiosEvento/sf:ResumenEventos/sf:TipoEvento/'
                           'sf:NumeroDeEventos', namespaces=NS) == '0'


def test_text_is_escaped_in_the_xml():
    evento = _evento(ev.event_xml(_row(issuer_name='A & B <SL>'), {'other': 'x < y'}))
    assert evento.findtext('sf:ObligadoEmision/sf:NombreRazon', namespaces=NS) == 'A & B <SL>'


# --- The module ------------------------------------------------------------------

@pytest.fixture
def verifactu(loaded_modules, monkeypatch):
    from contaautonomo.app import db
    module = loaded_modules.modules['verifactu']
    monkeypatch.delenv('VERIFACTU_MODE', raising=False)
    monkeypatch.setitem(module.core.app.config, 'VERIFACTU_MODE', None)
    _clear(module, db)
    yield module
    _clear(module, db)


def _clear(module, db):
    from sqlalchemy.exc import OperationalError
    try:
        for model in (module.VerifactuEvent, module.VerifactuRecord, module.VerifactuConfig):
            model.query.delete()
        db.session.commit()
    except OperationalError:   # another fixture dropped the tables
        db.session.rollback()


def _types(module):
    return [e.event_type for e in module.VerifactuEvent.query.order_by(module.VerifactuEvent.id)]


def _issue(number):
    from contaautonomo.app import Customer, Invoice, _issue_invoice, db
    cust = Customer(name=f'{number} SL', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    inv = Invoice(invoice_number=number, client_name=cust.name, amount_usd=0.0,
                  amount_eur=100.0, exchange_rate=1.0, invoice_date=date(2026, 3, 1),
                  status='draft', currency='EUR', customer_id=cust.id)
    db.session.add(inv)
    db.session.commit()
    _issue_invoice(inv)
    return inv


@pytest.fixture
def owner(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    return client


def test_start_is_recorded_once_and_the_chain_verifies(verifactu):
    verifactu.record_start_or_end()
    verifactu.record_start_or_end()          # another worker starting
    assert _types(verifactu) == ['01']
    event = verifactu.VerifactuEvent.query.one()
    assert event.prev_huella == ''
    assert ev.parse_timestamp(event.generated_at).utcoffset() is not None
    assert verifactu.event_anomalies()[1] == []


def test_switching_to_verifactu_records_the_end_and_back_a_new_start(verifactu, monkeypatch):
    verifactu.record_start_or_end()
    monkeypatch.setitem(verifactu.core.app.config, 'VERIFACTU_MODE', 'verifactu')
    verifactu.record_start_or_end()
    verifactu.record_start_or_end()
    assert _types(verifactu) == ['01', '02']
    verifactu.record_event(ev.SUMMARY)       # not kept in VERI*FACTU mode
    assert _types(verifactu) == ['01', '02']
    monkeypatch.setitem(verifactu.core.app.config, 'VERIFACTU_MODE', 'no_verifactu')
    verifactu.record_start_or_end()
    assert _types(verifactu) == ['01', '02', '01']
    assert verifactu.event_anomalies()[1] == []


def test_a_backup_restore_is_an_event(verifactu, loaded_modules):
    loaded_modules.modules['backup'].log('backup_restored', 'manual_x.zip', category='backup',
                                         level='warning')
    loaded_modules.modules['expenses'].log('backup_restored', 'not from backup',
                                           category='backup')
    event = verifactu.VerifactuEvent.query.one()
    assert event.event_type == '07'
    assert json.loads(event.data_json) == {'other': 'backup_restored: manual_x.zip'}


def test_checks_record_launch_and_each_anomaly_once(verifactu):
    from contaautonomo.app import db
    first = _issue('EV-1')
    _issue('EV-2')
    billing, found = verifactu.run_checks()
    assert billing == [] and found == []
    assert _types(verifactu) == ['03', '05']
    checks = json.loads(verifactu.VerifactuEvent.query.first().data_json)
    assert checks == {'checks': {'chain': 2, 'dates': 2, 'hashes': 2, 'signatures': 2},
                      'found': 0}

    record = verifactu.VerifactuRecord.query.order_by(verifactu.VerifactuRecord.id).first()
    record.payload = record.payload.replace('100.0', '999.0')
    db.session.commit()
    verifactu.run_checks()
    verifactu.detect_on_view()               # the status page sees it too: not again
    assert _types(verifactu) == ['03', '05', '03', '04', '05']
    anomaly = json.loads(verifactu.VerifactuEvent.query.filter_by(event_type='04').one()
                         .data_json)
    assert anomaly['anomaly'] == '01'
    assert anomaly['record']['number'] == first.invoice_number
    assert anomaly['record']['date'] == '01-03-2026'


def test_a_changed_event_is_detected_and_alarmed(verifactu, owner):
    from contaautonomo.app import db
    verifactu.record_start_or_end()
    verifactu.record_event(ev.SUMMARY)
    first = verifactu.VerifactuEvent.query.order_by(verifactu.VerifactuEvent.id).first()
    first.event_type = '90'
    db.session.commit()
    html = owner.get('/verifactu/').get_data(as_text=True)
    assert 'EVENT LOG BROKEN' in html and 'Integrity: hash' in html
    assert '06' in _types(verifactu)
    assert 'status-badge status-failed">06' in html      # listed on the same view


def test_exports_are_recorded_with_their_period(verifactu, owner):
    number = _issue('EV-3').invoice_number
    today = date.today().isoformat()
    response = owner.get(f'/verifactu/export?date_from={today}&date_to={today}')
    assert response.status_code == 200 and number.encode() in response.data
    export = verifactu.VerifactuEvent.query.filter_by(event_type='08').one()
    data = json.loads(export.data_json)
    assert (data['altas'], data['anulaciones'], data['cuota_total'],
            data['importe_total']) == (1, 0, 21.0, 121.0)
    assert data['first']['number'] == number and data['from'].startswith(today)

    response = owner.get(f'/verifactu/events/export?date_from={today}&date_to={today}')
    assert response.status_code == 200 and response.mimetype == 'application/zip'
    names = zipfile.ZipFile(io.BytesIO(response.data)).namelist()
    assert len(names) == 1 and names[0].endswith('_08.xml')
    xml = zipfile.ZipFile(io.BytesIO(response.data)).read(names[0])
    assert _evento(xml).findtext('sf:TipoEvento', namespaces=NS) == '08'
    assert _types(verifactu)[-1] == '09'
    assert owner.get('/verifactu/export?date_from=x').status_code == 302


def test_summary_counts_events_and_records_since_the_last_one(verifactu):
    verifactu.record_start_or_end()
    _issue('EV-4')
    verifactu.record_summary()
    data = json.loads(verifactu.VerifactuEvent.query.filter_by(event_type='10').one().data_json)
    assert data['counts'] == {'01': 1} and data['altas'] == 1
    assert data['importe_total'] == 121.0
    verifactu.record_summary()
    second = json.loads(verifactu.VerifactuEvent.query.filter_by(event_type='10')
                        .order_by(verifactu.VerifactuEvent.id.desc()).first().data_json)
    assert second['counts'] == {} and second['altas'] == 0
    assert not verifactu.summary_due()
    later = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=6, minutes=1)
    assert verifactu.summary_due(later)


def test_the_chain_cannot_fork(verifactu):
    from sqlalchemy.exc import IntegrityError
    from contaautonomo.app import db
    first = verifactu.record_event(ev.START)
    copy = verifactu.VerifactuEvent(
        event_type='90', generated_at=first.generated_at, prev_huella=first.prev_huella,
        huella='F' * 64, signature='x', system_json='{}')
    db.session.add(copy)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_settings_name_the_system(verifactu, owner):
    verifactu.record_start_or_end()
    number = verifactu.system_info()['installation_number']
    assert len(number) == 16
    owner.post('/settings', data={'_settings_tab': 'verifactu', 'verifactu_submitted': '1',
                                  'verifactu_producer_name': 'Example Producer SL',
                                  'verifactu_producer_nif': ' 89890001k ',
                                  'verifactu_system_name': 'Books',
                                  'verifactu_system_id': 'BKX',
                                  'verifactu_installation_number': ''})
    system = verifactu.system_info()
    assert (system['producer_name'], system['nif'], system['system_name'],
            system['system_id'], system['installation_number']) == (
        'Example Producer SL', '89890001K', 'Books', 'BK', number)
    verifactu.record_event(ev.SUMMARY)
    assert verifactu.event_anomalies()[1] == []   # earlier events keep their own values
    html = owner.get('/settings').get_data(as_text=True)
    assert 'Event log (registro de eventos)' in html


def test_the_status_page_lists_events(verifactu, owner):
    verifactu.record_start_or_end()
    html = owner.get('/verifactu/').get_data(as_text=True)
    assert 'Event log' in html and 'Start of operation' in html
    response = owner.post('/verifactu/check', follow_redirects=True)
    assert 'no anomaly found' in response.get_data(as_text=True)
