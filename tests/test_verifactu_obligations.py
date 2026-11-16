"""Verifactu and e-invoicing obligation dates (G-1)."""
import re
from datetime import date
from pathlib import Path

from modules.verifactu.obligations import (
    ANNOUNCED, IN_FORCE, OBLIGATIONS, STATUS_LABELS, obligations)

ROOT = Path(__file__).resolve().parent.parent


def test_every_date_cites_its_source_and_has_a_known_status():
    for entry in OBLIGATIONS:
        assert entry['source'].strip()
        assert entry['status'] in (IN_FORCE, ANNOUNCED)
        if entry['status'] == IN_FORCE:
            assert 'BOE' in entry['source'], entry
            assert isinstance(entry['date'], date)


def test_dates_in_force_follow_the_boe():
    verifactu = {e['who']: e['date'] for e in obligations('verifactu')
                 if e['status'] == IN_FORCE}
    assert date(2027, 1, 1) in verifactu.values()
    assert date(2027, 7, 1) in verifactu.values()
    einvoice = [e['date'] for e in obligations('einvoice')]
    assert date(2027, 10, 6) in einvoice and date(2028, 10, 6) in einvoice


def test_announced_date_is_labelled_as_not_in_force():
    announced = [e for e in obligations() if e['status'] == ANNOUNCED]
    assert announced, 'the October 2028 announcement is tracked'
    for entry in announced:
        assert entry['status_label'] == STATUS_LABELS[ANNOUNCED]
        assert 'not yet in force' in entry['status_label'].lower()
        assert 'BOE' in entry['source']  # says it is not in the BOE yet


def test_no_obligation_starts_in_2026():
    assert all(e['date'] is None or e['date'].year >= 2027 for e in OBLIGATIONS)


def test_documents_do_not_give_2026_as_the_verifactu_start():
    pattern = re.compile(r'verifactu[^\n]{0,80}\b(1 july|1 de julio|july|julio)\s*2026',
                         re.IGNORECASE)
    for doc in ('README.md', 'MODULES_DOCUMENTATION.md', 'CHANGELOG.md',
                'modules/verifactu/README.md', 'modules/einvoice/README.md'):
        text = (ROOT / doc).read_text(encoding='utf-8')
        assert not pattern.search(text), doc


def test_status_page_shows_dates_and_the_announced_label(loaded_modules, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/verifactu/').get_data(as_text=True)
    assert '1 July 2027' in html and '1 January 2027' in html
    assert 'October 2028' in html
    assert 'Announced, not yet in force' in html
    assert 'BOE-A-2025-24446' in html
