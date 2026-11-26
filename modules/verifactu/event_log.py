"""The event log of a «NO VERI*FACTU» system (Orden HAC/1177/2024, artículo 9),
kept by the Verifactu module: which event is recorded when, the chain, the
checks, the summaries and the exports. The pure parts are in events.py.

Mixed into VerifactuModule; uses its VerifactuEvent, VerifactuRecord and
VerifactuConfig models, its signing key and its mode.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from contaautonomo.utils.timeutil import utcnow

from . import events as ev
from .chain import key_id, sign_hash, verify_signature

SYSTEM_DEFAULTS = {'producer_name': '', 'producer_nif': '', 'system_name': 'ContaAutonomo',
                   'system_id': 'CA', 'installation_number': ''}
# verify_chain() reasons (chain.py) -> L1E anomaly type
_BILLING_ANOMALY = (('signature', '02'), ('unknown key', '02'), ('link', '08'),
                    ('mismatch', '01'), ('JSON', '03'))
RESTORE_ACTIONS = ('backup_restored', 'demo_data_loaded')


def madrid_now():
    """Now in Spain's peninsular time zone, with its offset (artículo 7.e)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Europe/Madrid'))
    except Exception:   # no time zone data on the host: the host's own offset
        return datetime.now(timezone.utc).astimezone()


def _naive_utc(moment):
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _event_ref(row):
    return {'event_type': row.event_type, 'generated_at': row.generated_at, 'huella': row.huella}


class EventLogMixin:
    """Event records: chained, hashed (AEAT hash v0.1.2) and signed with the
    Verifactu key. Recorded only while the system works as «NO VERI*FACTU»
    (VERIFACTU_MODE=no_verifactu, the default)."""

    # --- Configuration: the system's identity (Settings → Verifactu) ---------- #

    def _config(self):
        out = dict(SYSTEM_DEFAULTS)
        try:
            row = self.VerifactuConfig.query.filter_by(key='system').first()
            if row and row.value:
                data = json.loads(row.value)
                if isinstance(data, dict):
                    out.update({k: data[k] for k in out if k in data})
        except Exception:  # pragma: no cover - defensive
            self.logger.warning('Reading the Verifactu settings failed', exc_info=True)
        return out

    def _save_config(self, data):
        row = self.VerifactuConfig.query.filter_by(key='system').first()
        if not row:
            row = self.VerifactuConfig(key='system')
            self._db.session.add(row)
        row.value = json.dumps(data, sort_keys=True)
        self._db.session.commit()

    def _installation_number(self, config):
        """Created once and kept: it identifies this installation."""
        if not config.get('installation_number'):
            config['installation_number'] = uuid.uuid4().hex[:16].upper()
            self._save_config(config)
        return config['installation_number']

    def system_info(self):
        """The SistemaInformatico block: who produced the system and which
        installation this is. The producer defaults to the owner (a system
        the owner runs for their own invoices)."""
        config = self._config()
        settings = self.core.get_settings()
        owner = (settings.business_name or settings.owner_name or '') if settings else ''
        return {
            'producer_name': (config['producer_name'] or owner)[:120],
            'nif': config['producer_nif'] or self._issuer_nif(settings),
            'system_name': (config['system_name'] or 'ContaAutonomo')[:30],
            'system_id': (config['system_id'] or 'CA')[:2],
            'version': f"{self.core.app.config.get('APP_VERSION', '2')}+vf{self.version}"[:50],
            'installation_number': self._installation_number(config)[:100],
        }

    # --- Writing events --------------------------------------------------------- #

    def events_active(self):
        return self._qr_mode() == 'no_verifactu'

    def _last_event(self, types=None):
        query = self.VerifactuEvent.query
        if types:
            query = query.filter(self.VerifactuEvent.event_type.in_(types))
        return query.order_by(self.VerifactuEvent.id.desc()).first()

    def _sign(self, huella):
        secret = self._secret()
        return sign_hash(huella, secret), key_id(secret)

    def _event_signature_ok(self, event):
        secret = self._secret()
        return (event.key_id == key_id(secret)
                and verify_signature(event.huella, event.signature, secret))

    def record_event(self, event_type, data=None, when=None, force=False):
        """Append one event record; returns it, or None when the event log is
        not kept (VERI*FACTU mode) or `when(last_event)` says no. Two workers
        writing at once cannot fork the chain: `prev_huella` is unique, and
        the loser builds its record again on the new last event."""
        if not (force or self.events_active()):
            return None
        settings = self.core.get_settings()
        issuer_nif = self._issuer_nif(settings)
        issuer_name = ((settings.business_name or settings.owner_name or '')
                       if settings else '')[:120]
        system = self.system_info()
        for _attempt in range(3):
            last = self._last_event()
            if when is not None and not when(self):
                return None
            generated_at = ev.timestamp(madrid_now())
            prev = last.huella if last else ''
            huella = ev.huella_evento(system, issuer_nif, event_type, prev, generated_at)
            signature, signer = self._sign(huella)
            event = self.VerifactuEvent(
                event_type=event_type, generated_at=generated_at,
                prev_event_type=last.event_type if last else None,
                prev_generated_at=last.generated_at if last else None,
                prev_huella=prev, huella=huella, signature=signature, key_id=signer,
                issuer_nif=issuer_nif, issuer_name=issuer_name,
                system_json=json.dumps(system, sort_keys=True),
                data_json=json.dumps(data or {}, sort_keys=True, ensure_ascii=False))
            self._db.session.add(event)
            try:
                self._db.session.commit()
            except IntegrityError:
                self._db.session.rollback()
                continue
            self.log('verifactu_event_recorded',
                     f'{event_type} {ev.EVENT_TYPES[event_type]}',
                     category='verifactu',
                     level='warning' if event_type in (ev.ANOMALY_BILLING,
                                                       ev.ANOMALY_EVENTS) else 'info')
            return event
        self.logger.error('Could not append Verifactu event %s: the chain kept moving',
                          event_type)
        return None

    # --- Start and end of operation as NO VERI*FACTU (01, 02) ----------------- #

    def _running_as_no_verifactu(self):
        last = self._last_event((ev.START, ev.END))
        return last is not None and last.event_type == ev.START

    def record_start_or_end(self):
        """At start-up: 01 when the system starts working as NO VERI*FACTU
        (first start, or after an 02); 02 when it now works as VERI*FACTU.
        Every worker calls it; only the first one writes."""
        if self.events_active():
            self.record_event(ev.START, when=lambda m: not m._running_as_no_verifactu())
        else:
            self.record_event(ev.END, {'other': 'VERIFACTU_MODE is verifactu'},
                              when=lambda m: m._running_as_no_verifactu(), force=True)

    def record_end(self, reason):
        self.record_event(ev.END, {'other': reason[:100]},
                          when=lambda m: m._running_as_no_verifactu(), force=True)

    # --- Anomaly detection (03-06) ------------------------------------------------ #

    def _billing_ref(self, record):
        try:
            payload = json.loads(record.payload)
        except (TypeError, ValueError):
            payload = {}
        date_iso = payload.get('invoice_date') or ''
        date_text = f'{date_iso[8:10]}-{date_iso[5:7]}-{date_iso[:4]}' if date_iso else ''
        nif = (payload.get('issuer_nif') or '').replace(' ', '').upper()
        if len(nif) == 11 and nif.startswith('ES'):
            nif = nif[2:]
        return {'issuer_nif': nif, 'number': record.invoice_number or '', 'date': date_text,
                'huella': record.record_hash}

    def billing_anomalies(self):
        """(records, anomalies) of the billing record chain: hash, link and
        signature (chain.verify_chain), and creation times in order and not
        in the future."""
        records, (ok, bad_idx, reason) = self._verify_all()
        anomalies = []
        if not ok:
            code = next((c for key, c in _BILLING_ANOMALY if key in (reason or '')), '15')
            anomalies.append({'index': bad_idx, 'type': code, 'reason': reason,
                              'record': self._billing_ref(records[bad_idx])})
        now = utcnow()
        for index, record in enumerate(records):
            created = record.created_at
            if created and (created - now).total_seconds() > ev.FUTURE_TOLERANCE_SECONDS:
                anomalies.append({'index': index, 'type': '13',
                                  'reason': 'created later than the current system time',
                                  'record': self._billing_ref(record)})
            if index and created and records[index - 1].created_at \
                    and created < records[index - 1].created_at:
                anomalies.append({'index': index, 'type': '11',
                                  'reason': 'created before the previous record',
                                  'record': self._billing_ref(record)})
        return records, anomalies

    def _event_rows(self):
        rows = self.VerifactuEvent.query.order_by(self.VerifactuEvent.id).all()
        for row in rows:
            row.system = json.loads(row.system_json or '{}')
        return rows

    def event_anomalies(self):
        rows = self._event_rows()
        found = ev.check_chain(rows, madrid_now(), self._event_signature_ok)
        for anomaly in found:
            row = rows[anomaly['index']]
            anomaly['event'] = {'event_type': row.event_type,
                                'generated_at': row.generated_at, 'huella': row.huella}
        return rows, found

    def _reported(self, event_type, anomaly):
        """Whether this anomaly already has an event since the last check
        that found nothing, so a page view does not record it again."""
        clean = None
        check_type = ev.CHECK_BILLING if event_type == ev.ANOMALY_BILLING else ev.CHECK_EVENTS
        for row in self.VerifactuEvent.query.filter_by(event_type=check_type).order_by(
                self.VerifactuEvent.id.desc()):
            if json.loads(row.data_json or '{}').get('found') == 0:
                clean = row.id
                break
        query = self.VerifactuEvent.query.filter_by(event_type=event_type)
        if clean:
            query = query.filter(self.VerifactuEvent.id > clean)
        key = self._anomaly_key(anomaly)
        return any(self._anomaly_key(json.loads(r.data_json or '{}')) == key for r in query)

    @staticmethod
    def _anomaly_key(data):
        target = data.get('record') or data.get('event') or {}
        return (data.get('anomaly') or data.get('type'),
                target.get('number') or target.get('huella'))

    def _report(self, event_type, anomalies):
        for anomaly in anomalies:
            if self._reported(event_type, anomaly):
                continue
            data = {'anomaly': anomaly['type'], 'reason': anomaly['reason']}
            if anomaly.get('record'):
                data['record'] = anomaly['record']
            if anomaly.get('event'):
                data['event'] = anomaly['event']
            self.record_event(event_type, data)

    def run_checks(self):
        """The anomaly detection process on both chains (03 then 04 for each
        anomaly; 05 then 06). Scheduled daily and on demand."""
        records, billing = self.billing_anomalies()
        n = len(records)
        self.record_event(ev.CHECK_BILLING, {
            'checks': {'hashes': n, 'signatures': n, 'chain': n, 'dates': n},
            'found': len(billing)})
        self._report(ev.ANOMALY_BILLING, billing)
        rows, found = self.event_anomalies()
        m = len(rows)
        self.record_event(ev.CHECK_EVENTS, {
            'checks': {'hashes': m, 'signatures': m, 'chain': m, 'dates': m},
            'found': len(found)})
        self._report(ev.ANOMALY_EVENTS, found)
        return billing, found

    def detect_on_view(self):
        """The status page checks both chains on every view: what it finds is
        recorded once (04, 06), without a detection-process event."""
        records, billing = self.billing_anomalies()
        _rows, found = self.event_anomalies()
        self._report(ev.ANOMALY_BILLING, billing)
        self._report(ev.ANOMALY_EVENTS, found)
        return records, billing, self._event_rows(), found   # with what was just recorded

    # --- Backup restore (07) -------------------------------------------------------- #

    def on_activity(self, entry):
        """Activity listener: a backup restored from the app is event 07."""
        if entry.get('source') == 'backup' and entry.get('action') in RESTORE_ACTIONS:
            details = entry.get('details')
            self.record_event(ev.RESTORE, {'other': f'{entry["action"]}: {details or ""}'[:100]})

    # --- Exports (08, 09) -------------------------------------------------------------- #

    @staticmethod
    def _period(date_from, date_to):
        """(start, end) as naive UTC datetimes for a period of whole days,
        and the same as Madrid timestamps for the event."""
        start = datetime.combine(date_from, datetime.min.time())
        end = datetime.combine(date_to, datetime.max.time()).replace(microsecond=0)
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo('Europe/Madrid')
        except Exception:
            zone = madrid_now().tzinfo
        start_local, end_local = start.replace(tzinfo=zone), end.replace(tzinfo=zone)
        return (_naive_utc(start_local), _naive_utc(end_local),
                ev.timestamp(start_local), ev.timestamp(end_local))

    def billing_records_between(self, start, end):
        model = self.VerifactuRecord
        return model.query.filter(model.created_at >= start, model.created_at <= end) \
            .order_by(model.id).all()

    def _billing_totals(self, records):
        altas = [r for r in records if r.record_type == 'alta']
        cuota = importe = 0.0
        for r in altas:
            try:
                payload = json.loads(r.payload)
            except (TypeError, ValueError):
                continue
            vat = payload.get('vat_amount') or 0.0
            base = payload.get('taxable_base')
            base = payload.get('total_eur') or 0.0 if base is None else base
            cuota += vat
            importe += base + vat
        out = {'altas': len(altas), 'anulaciones': len(records) - len(altas),
               'cuota_total': round(cuota, 2), 'importe_total': round(importe, 2)}
        if records:
            out['first'] = self._billing_ref(records[0])
            out['last'] = self._billing_ref(records[-1])
        return out

    def record_billing_export(self, date_from, date_to, records):
        _s, _e, start_text, end_text = self._period(date_from, date_to)
        data = {'from': start_text, 'to': end_text}
        data.update(self._billing_totals(records))
        return self.record_event(ev.EXPORT_BILLING, data)

    def events_between(self, start, end):
        model = self.VerifactuEvent
        rows = model.query.filter(model.created_at >= start, model.created_at <= end) \
            .order_by(model.id).all()
        for row in rows:
            row.system = json.loads(row.system_json or '{}')
        return rows

    def record_event_export(self, date_from, date_to, rows):
        _s, _e, start_text, end_text = self._period(date_from, date_to)
        data = {'from': start_text, 'to': end_text, 'count': len(rows)}
        if rows:
            data['first'], data['last'] = _event_ref(rows[0]), _event_ref(rows[-1])
        return self.record_event(ev.EXPORT_EVENTS, data)

    # --- Summary (10) ------------------------------------------------------------------ #

    def record_summary(self):
        """The summary of the events since the previous summary (or since the
        first event) and of the billing records made in that time. Every six
        hours the system runs, and before it stops."""
        if not self.events_active():
            return None
        model = self.VerifactuEvent
        previous = self._last_event((ev.SUMMARY,))
        query = model.query
        if previous:
            query = query.filter(model.id > previous.id)
        counts = {}
        for row in query:
            counts[row.event_type] = counts.get(row.event_type, 0) + 1
        since = previous.created_at if previous else None
        records_query = self.VerifactuRecord.query
        if since:
            records_query = records_query.filter(self.VerifactuRecord.created_at > since)
        data = {'counts': counts}
        data.update(self._billing_totals(records_query.order_by(self.VerifactuRecord.id).all()))
        return self.record_event(ev.SUMMARY, data)

    def summary_due(self, now=None):
        """True when six hours have passed since the last summary (or the
        first event)."""
        last = self._last_event((ev.SUMMARY,)) or \
            self.VerifactuEvent.query.order_by(self.VerifactuEvent.id).first()
        if last is None or last.created_at is None:
            return False
        return (now or utcnow()) - last.created_at >= timedelta(seconds=ev.SUMMARY_EVERY_SECONDS)

    def summary_job(self):
        """Scheduled every 30 minutes: a summary when six hours have passed."""
        if self.summary_due():
            self.record_summary()

    def summary_on_exit(self):
        """atexit: the summary before the system stops, written by the
        process that runs the scheduler."""
        try:
            if not self.core.scheduler.is_leader:
                return
            with self.core.app.app_context():
                self.record_summary()
        except Exception:   # the process is ending: never block it
            self.logger.warning('Verifactu summary at shutdown failed', exc_info=True)
