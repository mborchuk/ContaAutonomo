"""Documents: expiry alerts are messages in the message center (top-bar bell),
not a dashboard panel. Each alert is posted once per document and expiry date,
also after it was read or deleted."""
from datetime import date, timedelta

import pytest

TODAY = date(2035, 6, 15)


@pytest.fixture
def documents(loaded_modules):
    from sqlalchemy.exc import OperationalError
    from app import Message, db
    db.create_all()
    dm = loaded_modules.modules['documents']

    def clear():
        try:
            dm.Document.query.filter(dm.Document.name.like('Synthetic %')).delete(
                synchronize_session=False)
            Message.query.filter_by(source='documents').delete()
            db.session.commit()
        except OperationalError:   # another test's app fixture dropped the tables
            db.session.rollback()
        db.session.expunge_all()
    clear()
    yield dm
    clear()


def _doc(dm, name, expiry):
    from app import db
    doc = dm.Document(name=name, file_path='_multi_', expiry_date=expiry)
    db.session.add(doc)
    db.session.commit()
    return doc


def _messages():
    from app import Message
    return Message.query.filter_by(source='documents').order_by(Message.id).all()


def test_expiring_and_recently_expired_documents_get_one_message_each(documents):
    soon = _doc(documents, 'Synthetic insurance', TODAY + timedelta(days=10))
    gone = _doc(documents, 'Synthetic [rent] contract', TODAY - timedelta(days=3))
    _doc(documents, 'Synthetic old permit', TODAY - timedelta(days=90))     # too old
    _doc(documents, 'Synthetic passport', TODAY + timedelta(days=400))      # not soon
    assert documents.post_expiry_messages(today=TODAY) == 2
    msgs = _messages()
    assert [(m.level, m.dedupe_key) for m in msgs] == [
        ('danger', f'expired:{gone.id}:{gone.expiry_date.isoformat()}'),
        ('warning', f'expiry-soon:{soon.id}:{soon.expiry_date.isoformat()}')]
    assert msgs[1].text == ('📄 Document "Synthetic insurance" expires on 25/06/2035 — '
                            f'[open it](/documents/view/{soon.id})')
    # Brackets in a name cannot turn into a link.
    assert '"Synthetic (rent) contract" expired on 12/06/2035' in msgs[0].text
    # Run again: nothing new.
    assert documents.post_expiry_messages(today=TODAY) == 0
    assert len(_messages()) == 2


def test_a_read_or_deleted_alert_does_not_come_back(documents, loaded_modules):
    from app import db
    doc = _doc(documents, 'Synthetic licence', TODAY + timedelta(days=5))
    documents.post_expiry_messages(today=TODAY)
    center = loaded_modules.core.message_center
    first = _messages()[0]
    center.mark_read(first.id)
    assert documents.post_expiry_messages(today=TODAY) == 0
    center.delete(first.id)
    assert documents.post_expiry_messages(today=TODAY) == 0
    # A renewed document (new expiry date) gets a new alert when it is due.
    doc.expiry_date = TODAY + timedelta(days=20)
    db.session.commit()
    assert documents.post_expiry_messages(today=TODAY) == 1
    # Once it expires, the "expired" alert is a separate message.
    assert documents.post_expiry_messages(today=TODAY + timedelta(days=21)) == 1


def test_no_dashboard_panel_any_more(documents):
    _doc(documents, 'Synthetic tax certificate', date.today() + timedelta(days=7))
    assert documents.get_dashboard_panels() == []
    assert len(_messages()) == 1            # opening the dashboard posted it


def test_the_daily_job_is_registered(loaded_modules):
    jobs = {j['id']: j for j in loaded_modules.core.scheduler.get_jobs()}
    job = jobs['documents.expiry_messages']
    assert job['type'] == 'daily' and job['time_str'] == '07:30'


def test_without_the_message_function_nothing_breaks(documents, monkeypatch):
    _doc(documents, 'Synthetic visa', TODAY + timedelta(days=1))
    monkeypatch.setattr(documents.core.functions, 'available', lambda name: False)
    assert documents.post_expiry_messages(today=TODAY) == 0
    assert _messages() == []
