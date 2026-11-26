"""Event records (registros de evento) of a «NO VERI*FACTU» system — pure
functions, no Flask, no DB.

Sources:
- Orden HAC/1177/2024 (BOE-A-2024-22138), artículo 9 (which events),
  artículo 13.1.c (what the hash covers), anexo apartado 5 and lists L1E
  (anomaly types) and L2E (event types).
- AEAT, "Detalle de las especificaciones técnicas para generación de la huella
  o hash de los registros de facturación", v0.1.2 (27/08/2024), section 3.c:
  the fields and their order for an event record.
- AEAT, EventosSIF.xsd: the XML design of RegistroEvento.

An event record is chained like a billing record: it carries the type, time
and hash of the event before it (EventoAnterior), or PrimerEvento=S.
"""
import hashlib
from datetime import datetime
from xml.sax.saxutils import escape

from .aeat import _value

# L2E — event types (TipoEvento)
EVENT_TYPES = {
    '01': 'Start of operation as a «NO VERI*FACTU» system',
    '02': 'End of operation as a «NO VERI*FACTU» system',
    '03': 'Anomaly detection started on the billing records',
    '04': 'Anomaly detected in the billing records',
    '05': 'Anomaly detection started on the event records',
    '06': 'Anomaly detected in the event records',
    '07': 'Backup restored',
    '08': 'Billing records of a period exported',
    '09': 'Event records of a period exported',
    '10': 'Summary of events',
    '90': 'Other event',
}
START, END = '01', '02'
CHECK_BILLING, ANOMALY_BILLING = '03', '04'
CHECK_EVENTS, ANOMALY_EVENTS = '05', '06'
RESTORE, EXPORT_BILLING, EXPORT_EVENTS, SUMMARY = '07', '08', '09', '10'

# L1E — anomaly types (TipoAnomalia)
ANOMALY_TYPES = {
    '01': 'Integrity: hash',
    '02': 'Integrity: signature',
    '03': 'Integrity: other',
    '04': 'Traceability, chain: a record that is not the first has no previous record',
    '05': 'Traceability, chain: a record that is not the last has no next record',
    '06': 'Traceability, chain: other',
    '07': "Traceability, hash: the record's hash is not the 'previous hash' of the next record",
    '08': "Traceability, hash: the 'previous hash' is not the hash of the previous record",
    '09': 'Traceability, hash: other',
    '10': 'Traceability, chain: other',
    '11': 'Traceability, dates: earlier than the previous record',
    '12': 'Traceability, dates: later than the next record',
    '13': 'Traceability, dates: later than the current system time',
    '14': 'Traceability, dates: other',
    '15': 'Traceability: other',
    '90': 'Other',
}

# Hash input of an event record, in this order (hash document v0.1.2, 3.c).
# NIF appears twice: the producer of the system, then the obligado a emisión.
HASH_FIELDS = ('NIF', 'ID', 'IdSistemaInformatico', 'Version', 'NumeroInstalacion',
               'NIF', 'TipoEvento', 'HuellaEvento', 'FechaHoraHusoGenEvento')

SUMMARY_EVERY_SECONDS = 6 * 3600     # artículo 9.2: at least one summary per 6 hours
FUTURE_TOLERANCE_SECONDS = 60        # artículo 7.f: the clock may be off by one minute
XML_NAMESPACE = ('https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/'
                 'aplicaciones/es/aeat/tike/cont/ws/EventosSIF.xsd')
ID_VERSION = '1.0'                   # L15


def huella_input(system, issuer_nif, event_type, prev_huella, generated_at):
    """'NIF=…&ID=…&IdSistemaInformatico=…&…' for an event record.

    `system` holds the SistemaInformatico values: nif (or id_otro), system_id,
    version, installation_number. `prev_huella` is '' for the first event."""
    values = (system.get('nif'), system.get('id_otro'), system.get('system_id'),
              system.get('version'), system.get('installation_number'), issuer_nif,
              event_type, prev_huella, generated_at)
    return '&'.join(f'{name}={_value(name, value)}' for name, value in zip(HASH_FIELDS, values))


def huella_evento(system, issuer_nif, event_type, prev_huella, generated_at):
    """SHA-256 of the input string: 64 uppercase hexadecimal characters."""
    text = huella_input(system, issuer_nif, event_type, prev_huella, generated_at)
    return hashlib.sha256(text.encode('utf-8')).hexdigest().upper()


def timestamp(moment):
    """ISO 8601 with the time zone offset, to the second:
    2026-11-26T10:00:00+01:00 (FechaHoraHusoGenEvento)."""
    return moment.replace(microsecond=0).isoformat()


def parse_timestamp(text):
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else None


def _get(event, name):
    return event.get(name) if isinstance(event, dict) else getattr(event, name, None)


def check_chain(events, now, signature_ok=None):
    """Check an ordered list of event records.

    Each event exposes: event_type, generated_at, prev_huella, prev_event_type,
    prev_generated_at, huella, issuer_nif and `system` (a dict). Returns a
    list of anomalies, each {'index', 'type' (L1E), 'reason'}; empty when the
    chain is intact. `signature_ok(event)` checks the app's signature.
    """
    anomalies = []

    def add(index, code, reason):
        anomalies.append({'index': index, 'type': code, 'reason': reason})

    previous = None
    for index, event in enumerate(events):
        system = _get(event, 'system') or {}
        expected = huella_evento(system, _get(event, 'issuer_nif'), _get(event, 'event_type'),
                                 _get(event, 'prev_huella') or '', _get(event, 'generated_at'))
        if _get(event, 'huella') != expected:
            add(index, '01', 'the hash does not match the record (changed?)')
        if signature_ok is not None and not signature_ok(event):
            add(index, '02', 'the signature does not verify')
        moment = parse_timestamp(_get(event, 'generated_at'))
        if moment is None:
            add(index, '14', 'the generation time is not a date with a time zone')
        elif (moment - now).total_seconds() > FUTURE_TOLERANCE_SECONDS:
            add(index, '13', 'generated later than the current system time')
        if previous is None:
            if _get(event, 'prev_huella'):
                add(index, '04', 'the first record kept links to an earlier record')
        else:
            if (_get(event, 'prev_huella') or '') != (_get(previous, 'huella') or ''):
                add(index, '08', "the 'previous hash' is not the hash of the previous record")
            elif (_get(event, 'prev_event_type') != _get(previous, 'event_type')
                  or _get(event, 'prev_generated_at') != _get(previous, 'generated_at')):
                add(index, '06', 'the previous event type or time does not match')
            before = parse_timestamp(_get(previous, 'generated_at'))
            if moment is not None and before is not None and moment < before:
                add(index, '11', 'generated before the previous record')
        previous = event
    return anomalies


# --- XML (anexo apartado 5, EventosSIF.xsd) ------------------------------------------ #

def _el(name, value, indent):
    return f'{indent}<sf:{name}>{escape(str(value))}</sf:{name}>'


def _block(name, children, indent):
    inner = '\n'.join(c for c in children if c)
    return f'{indent}<sf:{name}>\n{inner}\n{indent}</sf:{name}>'


def _invoice_ref(name, ref, indent):
    """IDFacturaExpedidaHuellaType: issuer NIF, number, date and hash of a
    billing record."""
    i = indent + '    '
    return _block(name, [_el('IDEmisorFactura', ref['issuer_nif'], i),
                         _el('NumSerieFactura', ref['number'], i),
                         _el('FechaExpedicionFactura', ref['date'], i),
                         _el('Huella', ref['huella'], i)], indent)


def _own_data(event_type, data, indent):
    """DatosPropiosEvento for the event types that have it, else ''."""
    i, j = indent + '    ', indent + '        '
    if event_type in (CHECK_BILLING, CHECK_EVENTS):
        kind, noun = (('RegFacturacion', 'Facturacion') if event_type == CHECK_BILLING
                      else ('RegEvento', 'Evento'))
        rows = []
        for check, label in (('hashes', 'IntegridadHuellas'), ('signatures', 'IntegridadFirmas'),
                             ('chain', 'TrazabilidadCadena'), ('dates', 'TrazabilidadFechas')):
            done = check in data.get('checks', {})
            rows.append(_el(f'RealizadoProcesoSobre{label}{kind}', 'S' if done else 'N', j))
            if done:
                rows.append(_el(f'NumeroDeRegistros{noun}ProcesadosSobre{label}',
                                data['checks'][check], j))
        body = _block(f'LanzamientoProcesoDeteccionAnomalias{kind}', rows, i)
    elif event_type in (ANOMALY_BILLING, ANOMALY_EVENTS):
        kind = 'RegFacturacion' if event_type == ANOMALY_BILLING else 'RegEvento'
        rows = [_el('TipoAnomalia', data['anomaly'], j)]
        if data.get('reason'):
            rows.append(_el('OtrosDatosAnomalia', data['reason'][:100], j))
        if event_type == ANOMALY_BILLING and data.get('record'):
            rec = data['record']
            k = j + '    '
            rows.append(_block('RegistroFacturacionAnomalo', [
                _el('IDEmisorFactura', rec['issuer_nif'], k),
                _el('NumSerieFactura', rec['number'], k),
                _el('FechaExpedicionFactura', rec['date'], k)], j))
        if event_type == ANOMALY_EVENTS and data.get('event'):
            ev = data['event']
            k = j + '    '
            rows.append(_block('RegEventoAnomalo', [
                _el('TipoEvento', ev['event_type'], k),
                _el('FechaHoraHusoEvento', ev['generated_at'], k),
                _el('HuellaEvento', ev['huella'], k)], j))
        body = _block(f'DeteccionAnomalias{kind}', rows, i)
    elif event_type == EXPORT_BILLING:
        rows = [_el('FechaHoraHusoInicioPeriodoExport', data['from'], j),
                _el('FechaHoraHusoFinPeriodoExport', data['to'], j)]
        if data.get('first'):
            rows += [_invoice_ref('RegistroFacturacionInicialPeriodo', data['first'], j),
                     _invoice_ref('RegistroFacturacionFinalPeriodo', data['last'], j)]
        rows += [_el('NumeroDeRegistrosFacturacionAltaExportados', data['altas'], j),
                 _el('SumaCuotaTotalAlta', f"{data['cuota_total']:.2f}", j),
                 _el('SumaImporteTotalAlta', f"{data['importe_total']:.2f}", j),
                 _el('NumeroDeRegistrosFacturacionAnulacionExportados', data['anulaciones'], j),
                 _el('RegistrosFacturacionExportadosDejanDeConservarse', 'N', j)]
        body = _block('ExportacionRegFacturacionPeriodo', rows, i)
    elif event_type == EXPORT_EVENTS:
        rows = [_el('FechaHoraHusoInicioPeriodoExport', data['from'], j),
                _el('FechaHoraHusoFinPeriodoExport', data['to'], j)]
        k = j + '    '
        for tag, ev in (('RegistroEventoInicialPeriodo', data.get('first')),
                        ('RegistroEventoFinalPeriodo', data.get('last'))):
            if ev:
                rows.append(_block(tag, [_el('TipoEvento', ev['event_type'], k),
                                         _el('FechaHoraHusoEvento', ev['generated_at'], k),
                                         _el('HuellaEvento', ev['huella'], k)], j))
        rows += [_el('NumeroDeRegEventoExportados', data['count'], j),
                 _el('RegEventoExportadosDejanDeConservarse', 'N', j)]
        body = _block('ExportacionRegEventoPeriodo', rows, i)
    elif event_type == SUMMARY:
        k = j + '    '
        rows = [_block('TipoEvento', [_el('TipoEvento', code, k),
                                      _el('NumeroDeEventos', count, k)], j)
                for code, count in sorted(data.get('counts', {}).items())][:20]
        if not rows:   # no event in the period: says so with a zero count
            rows = [_block('TipoEvento', [_el('TipoEvento', SUMMARY, k),
                                          _el('NumeroDeEventos', 0, k)], j)]
        if data.get('first'):
            rows += [_invoice_ref('RegistroFacturacionInicialPeriodo', data['first'], j),
                     _invoice_ref('RegistroFacturacionFinalPeriodo', data['last'], j)]
        rows += [_el('NumeroDeRegistrosFacturacionAltaGenerados', data.get('altas', 0), j),
                 _el('SumaCuotaTotalAlta', f"{data.get('cuota_total', 0):.2f}", j),
                 _el('SumaImporteTotalAlta', f"{data.get('importe_total', 0):.2f}", j),
                 _el('NumeroDeRegistrosFacturacionAnulacionGenerados',
                     data.get('anulaciones', 0), j)]
        body = _block('ResumenEventos', rows, i)
    else:
        return ''
    return _block('DatosPropiosEvento', [body], indent)


def event_xml(event, data):
    """One event record as XML (anexo apartado 5: a file holds one
    RegistroEvento). `data` is the event's own data (DatosPropiosEvento).

    The ds:Signature the design requires (XAdES with a qualified certificate,
    artículo 14) is not produced: the app signs with its own key instead,
    shown in a comment. See SPEC_COMPARISON.md."""
    system = event.system
    i1, i2, i3 = '    ', '        ', '            '
    sif = [_el('NombreRazon', system.get('producer_name', ''), i3)]
    if system.get('nif'):
        sif.append(_el('NIF', system['nif'], i3))
    if system.get('system_name'):
        sif.append(_el('NombreSistemaInformatico', system['system_name'], i3))
    sif += [_el('IdSistemaInformatico', system.get('system_id', ''), i3),
            _el('Version', system.get('version', ''), i3),
            _el('NumeroInstalacion', system.get('installation_number', ''), i3),
            _el('TipoUsoPosibleSoloVerifactu', 'N', i3),
            _el('TipoUsoPosibleMultiOT', 'N', i3),
            _el('IndicadorMultiplesOT', 'N', i3)]
    if event.prev_huella:
        chain = _block('Encadenamiento', [_block('EventoAnterior', [
            _el('TipoEvento', event.prev_event_type, i3 + '    '),
            _el('FechaHoraHusoGenEvento', event.prev_generated_at, i3 + '    '),
            _el('HuellaEvento', event.prev_huella, i3 + '    ')], i3)], i2)
    else:
        chain = _block('Encadenamiento', [_el('PrimerEvento', 'S', i3)], i2)
    parts = [
        _block('SistemaInformatico', sif, i2),
        _block('ObligadoEmision', [_el('NombreRazon', event.issuer_name or '', i3),
                                   _el('NIF', event.issuer_nif or '', i3)], i2),
        _el('FechaHoraHusoGenEvento', event.generated_at, i2),
        _el('TipoEvento', event.event_type, i2),
        _own_data(event.event_type, data or {}, i2),
        _el('OtrosDatosEvento', data['other'][:100], i2) if (data or {}).get('other') else '',
        chain,
        _el('TipoHuella', '01', i2),
        _el('HuellaEvento', event.huella, i2),
        f'{i2}<!-- Signed by ContaAutonomo, not XAdES: HMAC-SHA256 {escape(event.signature)} '
        f'key {escape(event.key_id or "")} -->',
    ]
    body = '\n'.join(p for p in parts if p)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<sf:RegistroEvento xmlns:sf="{XML_NAMESPACE}">\n'
            f'{i1}<sf:IDVersion>{ID_VERSION}</sf:IDVersion>\n'
            f'{i1}<sf:Evento>\n{body}\n{i1}</sf:Evento>\n'
            f'</sf:RegistroEvento>\n')
