"""Verifactu and e-invoicing obligation dates (G-1)."""
import re
from datetime import date
from pathlib import Path

from modules.verifactu.obligations import (
    ANNOUNCED, IN_FORCE, OBLIGATIONS, STATUS_LABELS, SUPERSEDED, obligations)

ROOT = Path(__file__).resolve().parent.parent


def test_every_date_cites_its_source_and_has_a_known_status():
    for entry in OBLIGATIONS:
        assert entry['source'].strip()
        assert entry['status'] in (IN_FORCE, ANNOUNCED, SUPERSEDED)
        if entry['status'] == SUPERSEDED:
            assert 'BOE' in entry['superseded_by'], entry
        if entry['status'] == IN_FORCE:
            assert 'BOE' in entry['source'], entry
            assert isinstance(entry['date'], date)


def test_dates_in_force_follow_the_boe():
    """Real Decreto-ley 43/2026: Verifactu for everyone on 6 October 2028,
    the B2B e-invoicing date for autónomos; the 2027 dates are superseded."""
    verifactu = [e for e in obligations('verifactu') if e['status'] == IN_FORCE]
    assert [e['date'] for e in verifactu] == [date(2028, 10, 6)]
    assert 'BOE-A-2026-24701' in verifactu[0]['source']
    superseded = {e['date'] for e in obligations('verifactu') if e['status'] == SUPERSEDED}
    assert superseded == {date(2027, 1, 1), date(2027, 7, 1)}
    einvoice = [e['date'] for e in obligations('einvoice')]
    assert date(2027, 10, 6) in einvoice and date(2028, 10, 6) in einvoice
    assert verifactu[0]['date'] in einvoice


def test_superseded_dates_come_last_and_can_be_left_out():
    entries = obligations('verifactu')
    assert entries[0]['status'] == IN_FORCE
    assert [e['status'] for e in entries[1:]] == [SUPERSEDED, SUPERSEDED]
    assert all(e['status'] != SUPERSEDED
               for e in obligations('verifactu', include_superseded=False))


def test_nothing_is_announced_without_its_norm():
    for entry in obligations():
        if entry['status'] == ANNOUNCED:
            assert entry['status_label'] == STATUS_LABELS[ANNOUNCED]
            assert 'not yet in force' in entry['status_label'].lower()


def test_no_obligation_starts_in_2026():
    assert all(e['date'] is None or e['date'].year >= 2027 for e in OBLIGATIONS)


def test_documents_do_not_give_2026_as_the_verifactu_start():
    pattern = re.compile(r'verifactu[^\n]{0,80}\b(1 july|1 de julio|july|julio)\s*2026',
                         re.IGNORECASE)
    for doc in ('README.md', 'docs/MODULES_DOCUMENTATION.md', 'CHANGELOG.md',
                'modules/verifactu/README.md', 'modules/einvoice/README.md'):
        text = (ROOT / doc).read_text(encoding='utf-8')
        assert not pattern.search(text), doc


def test_status_page_shows_the_date_in_force_and_the_superseded_ones(loaded_modules, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/verifactu/').get_data(as_text=True)
    assert '6 October 2028' in html and 'BOE-A-2026-24701' in html
    assert '1 July 2027' in html and '1 January 2027' in html
    assert 'Superseded by Real Decreto-ley 43/2026' in html
    assert 'BOE-A-2025-24446' in html
