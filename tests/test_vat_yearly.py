"""Yearly VAT return from 2027 (Real Decreto-ley 43/2026): the owner opts in
in Settings → Tax Forms; the drafts, the Fiscal Calendar and Tax Provision
follow it. Synthetic figures throughout."""
from datetime import date

import pytest
from werkzeug.datastructures import MultiDict

from modules.fiscal_calendar.calendar_data import apply_plan, deadlines_for_year
from modules.tax_es_forms import vat_yearly as vy


# ── Pure rules ───────────────────────────────────────────────────────────

def test_regime_starts_in_2027_and_ends_with_until():
    assert vy.in_regime({'from': 2026}, 2026) is False
    assert vy.in_regime({'from': 2026}, 2027) is True
    assert vy.in_regime({'from': 2027, 'until': 2029}, 2029) is True
    assert vy.in_regime({'from': 2027, 'until': 2029}, 2030) is False
    assert vy.in_regime({}, 2027) is False


@pytest.mark.parametrize('reference, advance', [(1000.0, 250.0), (0.0, 0.0),
                                                (-500.0, 0.0), (None, 0.0), (1001.0, 250.25)])
def test_each_advance_is_a_quarter_of_a_positive_reference(reference, advance):
    assert vy.advance_amount(reference) == advance


def test_advances_are_debited_on_the_20th():
    assert vy.advance_dates(2027) == [date(2027, 4, 20), date(2027, 7, 20), date(2027, 10, 20)]
    assert vy.advances_debited(100.0, 2027, date(2027, 4, 19)) == 0.0
    assert vy.advances_debited(100.0, 2027, date(2027, 7, 20)) == 200.0


def test_yearly_result_adds_the_quarters_and_deducts_the_advances():
    quarters = [{'27': 2100.0, '45': 210.0, '46': 1890.0}] * 4
    out = vy.yearly_result(quarters, 400.0)
    assert out['charged'] == 8400.0 and out['deductible'] == 840.0
    assert out['result'] == 7560.0 and out['advances'] == 1200.0
    assert out['to_settle'] == 6360.0


def test_volume_of_operations_limit():
    assert vy.volume_check(85000.0, 2027)[0] is True
    ok, note = vy.volume_check(85000.01, 2027)
    assert ok is False and 'cannot be applied in 2027' in note
    assert vy.volume_check(None, 2027)[0] is None
    # Started on 1 July 2026: 40.000 raised to the year is above 85.000.
    assert vy.volume_check(45000.0, 2027, started_in=date(2026, 7, 1))[0] is False
    assert vy.volume_check(None, 2027, started_in=date(2027, 2, 1))[0] is True


def test_a_plan_replaces_the_quarterly_303():
    plan = {'drop': [('303', q) for q in (1, 2, 3, 4)],
            'add': [{'form': '303', 'period': 'annual', 'period_label': 'Yearly VAT 2027',
                     'quarter': None, 'window_start': date(2028, 1, 1),
                     'window_end': date(2028, 1, 30)}]}
    entries = apply_plan(2027, deadlines_for_year(2027), plan)
    assert not [e for e in entries if e['form'] == '303' and e['quarter']]
    added = [e for e in entries if e['form'] == '303']
    assert len(added) == 1 and added[0]['year'] == 2027 and added[0]['verified'] is False
    assert [e for e in entries if e['form'] == '130' and e['quarter'] == 1]   # untouched


# ── Module, on synthetic data ────────────────────────────────────────────

@pytest.fixture
def forms(app, loaded_modules, monkeypatch):
    from contaautonomo.app import Customer, Invoice, Settings, db
    module = loaded_modules.modules['tax_es_forms']
    monkeypatch.setattr(module, '_today', staticmethod(lambda: date(2027, 3, 15)))
    if not Settings.query.first():
        db.session.add(Settings())
    customer = Customer(name='Synthetic Cliente SL', tax_type='standard')
    db.session.add(customer)
    db.session.flush()
    for number, day, amount in (('V-1', date(2026, 2, 10), 4000.0),
                                ('V-2', date(2026, 8, 10), 4000.0),
                                ('V-3', date(2027, 2, 10), 2000.0)):
        db.session.add(Invoice(invoice_number=number, client_name=customer.name,
                               customer_id=customer.id, amount_usd=amount,
                               amount_eur=amount, exchange_rate=1.0, currency='EUR',
                               invoice_date=day, status='paid'))
    db.session.commit()
    yield module
    module.Config.query.delete()
    db.session.commit()


def _opt_in(module, first=2027, **extra):
    form = MultiDict({'tax_forms_submitted': '1', 'vat_yearly': 'on',
                      'vat_yearly_from': str(first), **extra})
    module.save_settings(None, form)


def test_opting_in_from_2027(forms):
    assert forms.vat_yearly(2027) is False
    _opt_in(forms)
    assert forms.vat_yearly(2027) is True and forms.vat_yearly(2026) is False


def test_first_year_advances_come_from_the_previous_303s(forms):
    _opt_in(forms)
    draft = forms.vat_yearly_return(2027, today=date(2027, 8, 1))
    q1 = forms._draft_303(2026, 1)['boxes']['71']
    q3 = forms._draft_303(2026, 3)['boxes']['71']
    assert draft['reference'] == pytest.approx(q1 + q3) and q1 > 0
    assert draft['advance'] == round(draft['reference'] / 4, 2)
    assert 'Modelo 303 results of 2026' in draft['reference_from']
    assert draft['result'] == forms._draft_303(2027, 1)['boxes']['46']
    assert draft['debited'] == round(2 * draft['advance'], 2)
    assert draft['due_so_far'] == round(draft['result'] - draft['debited'], 2)
    assert draft['volume_ok'] is True


def test_the_amount_the_aeat_shows_wins(forms):
    _opt_in(forms, vat_advance_year='2027', vat_advance_amount='123,45')
    draft = forms.vat_yearly_return(2027)
    assert draft['advance'] == 123.45 and 'Settings → Tax Forms' in draft['advance_from']
    _opt_in(forms, vat_advance_year='2027', vat_advance_amount='')
    assert forms.vat_yearly_return(2027)['advance'] != 123.45


def test_leaving_keeps_the_years_already_in_it(forms):
    _opt_in(forms)
    forms.save_settings(None, MultiDict({'tax_forms_submitted': '1'}))   # unticked in 2027
    assert forms.vat_yearly(2027) is True and forms.vat_yearly(2028) is False


def test_other_tabs_do_not_change_it(forms):
    _opt_in(forms)
    forms.save_settings(None, MultiDict({'irpf_region': 'madrid'}))
    assert forms.vat_yearly(2027) is True


def test_pages_say_it(forms, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    _opt_in(forms)
    html = client.get('/tax-forms-draft/303/2027/1').get_data(as_text=True)
    assert 'you file VAT yearly: no quarterly' in html
    html = client.get('/tax-forms-draft/vat-yearly/2027').get_data(as_text=True)
    assert 'Yearly VAT return — 2027' in html and '20/04/2027' in html
    assert 'BOE-A-2026-24701' in html and 'Traceback' not in html
    html = client.get('/tax-forms-draft/').get_data(as_text=True)
    assert '/tax-forms-draft/vat-yearly/2027' in html
    html = client.get('/settings').get_data(as_text=True)
    assert 'Yearly VAT return (from 2027)' in html and 'name="vat_yearly"' in html


def test_fiscal_calendar_follows_the_plan(forms, loaded_modules):
    calendar = loaded_modules.modules['fiscal_calendar']
    _opt_in(forms)
    entries = [e for e in calendar.year_deadlines(2027) if e['form'] == '303']
    assert [e['period'] for e in entries] == ['advance1', 'advance2', 'advance3', 'annual']
    assert entries[0]['window_end'] == date(2027, 4, 20)
    assert calendar.deadline_for('303', 2027, 1) is None
    assert calendar.deadline_for('303', 2027)['period'] == 'annual'
    assert calendar.deadline_for('303', 2027)['window_end'] == date(2028, 1, 30)
    # 2026 keeps the quarterly 303.
    assert calendar.deadline_for('303', 2026, 4)['window_end'] == date(2027, 2, 1)
    keys = {calendar._key('window', e) for e in entries}
    assert len(keys) == 4                                   # one message each


def test_tax_provision_counts_the_year_so_far(forms, loaded_modules):
    _opt_in(forms)
    provision = loaded_modules.modules['tax_provision']
    part = provision._iva_part(2027, 1)
    assert part['label'] == 'IVA 2027 (yearly return)'
    assert part['amount'] == forms.vat_yearly_return(2027)['due_so_far']
    assert part['deadline']['period'] == 'annual'
    assert provision._iva_part(2026, 4)['label'] == 'IVA 4T 2026'
