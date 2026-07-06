"""F8 — Invoice email: SMTP send (mocked), send log, overdue reminders."""
import json
from datetime import date

import pytest


class FakeSMTP:
    """Records instead of sending. Shared inbox on the class."""
    sent = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def starttls(self):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise ConnectionError('boom')
        FakeSMTP.sent.append({
            'to': msg['To'], 'subject': msg['Subject'],
            'body': msg.get_content() if not msg.is_multipart() else '',
            'attachments': [p.get_filename() for p in msg.iter_attachments()]
            if msg.is_multipart() else [],
        })

    def quit(self):
        pass


@pytest.fixture
def email_module(loaded_modules, monkeypatch):
    import smtplib
    monkeypatch.setattr(smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setattr(smtplib, 'SMTP_SSL', FakeSMTP)
    FakeSMTP.sent = []
    FakeSMTP.fail = False

    em = loaded_modules.modules['invoice_email']
    em._save_smtp_config({
        'host': 'smtp.test', 'port': 587, 'security': 'starttls',
        'username': 'u', 'password': 'p', 'from_addr': 'me@test.dev',
        'reminders_enabled': True,
    })
    return em


def _invoice(db, Invoice, Customer, due, status='issued', email='client@x.dev',
             number=None):
    customer = Customer(name='Cli', tax_type='standard', email=email)
    db.session.add(customer)
    db.session.flush()
    inv = Invoice(invoice_number=number or f'EM-{due.isoformat()}-{customer.id}',
                  client_name='Cli', amount_usd=0.0, amount_eur=500.0,
                  exchange_rate=1.0, invoice_date=due, due_date=due,
                  status=status, currency='EUR', customer_id=customer.id)
    db.session.add(inv)
    db.session.commit()
    return inv


def test_send_invoice_logs_and_renders_placeholders(email_module):
    from app import db, Invoice, Customer

    inv = _invoice(db, Invoice, Customer, due=date(2026, 3, 1))
    ok, err = email_module._send_invoice(
        inv, 'client@x.dev', 'Invoice {invoice_number}',
        'Amount {amount} due {due_date}')
    assert ok and err is None
    assert FakeSMTP.sent[-1]['to'] == 'client@x.dev'
    assert inv.invoice_number in FakeSMTP.sent[-1]['subject']

    log = email_module.EmailLog.query.filter_by(invoice_id=inv.id).first()
    assert log.status == 'sent'


def test_send_failure_is_logged_not_raised(email_module):
    from app import db, Invoice, Customer

    inv = _invoice(db, Invoice, Customer, due=date(2026, 3, 2))
    FakeSMTP.fail = True
    ok, err = email_module._send_invoice(inv, 'client@x.dev', 's', 'b')
    assert not ok and 'boom' in err
    log = email_module.EmailLog.query.filter_by(
        invoice_id=inv.id, status='failed').first()
    assert log is not None


def test_unconfigured_smtp_raises(email_module):
    email_module._save_smtp_config(dict(email_module._DEFAULTS))
    with pytest.raises(ValueError):
        email_module._send_email('a@b.c', 's', 'b')


def test_overdue_reminder_fires_once_at_offset(email_module):
    from app import db, Invoice, Customer

    due = date(2026, 4, 10)
    inv = _invoice(db, Invoice, Customer, due=due)
    # due+3 -> reminder fires.
    sent = email_module._run_overdue_reminders(today=date(2026, 4, 13))
    assert (inv.id, 3) in sent
    # Same day rerun -> idempotent.
    assert email_module._run_overdue_reminders(today=date(2026, 4, 13)) == []


def test_reminders_respect_optin_and_paid_status(email_module):
    from app import db, Invoice, Customer

    paid = _invoice(db, Invoice, Customer, due=date(2026, 5, 1), status='paid')
    assert email_module._run_overdue_reminders(today=date(2026, 5, 4)) == []

    # Global toggle off -> nothing, even for overdue unpaid.
    cfg = email_module._smtp_config()
    cfg['reminders_enabled'] = False
    email_module._save_smtp_config(cfg)
    overdue = _invoice(db, Invoice, Customer, due=date(2026, 5, 2))
    assert email_module._run_overdue_reminders(today=date(2026, 5, 5)) == []


def test_notify_capability_registered(email_module, loaded_modules):
    channels = loaded_modules.find_capabilities('notify')
    assert any(c['method'] == 'email' for c in channels)
