"""F12 — Bank import: N43/CSV parsers, dedup, matching, confirm -> paid."""
from datetime import date

import pytest

from modules.bank_import.parsers import parse_n43, parse_csv, movement_hash
from modules.bank_import.matching import suggest_for_movement


# --- N43 parser ---------------------------------------------------------------

# Minimal but structurally valid AEB43 fixture: header (11), credit movement
# (22, haber flag '2', 1210.50 EUR on 10/02/26), concept line (23), footer.
N43_FIXTURE = (
    "11" + "0049" + "1500" + "0123456789" + "260210" + "260210" + "1" + "0" * 13 + "978" + " " * 40 + "\n"
    "22" + "00491500" + "260210" + "260210" + "02" + "045" + "2" + "00000000121050" + "1234567890" + "TRANSFERENCIA ACME\n"
    "2301" + "FRA 2026/0001 ACME SPAIN SL\n"
    "33" + "0" * 78 + "\n"
    "88" + "0" * 78 + "\n"
)


def test_parse_n43_credit_movement():
    movements, errors = parse_n43(N43_FIXTURE)
    assert errors == []
    assert len(movements) == 1
    m = movements[0]
    assert m['date'] == date(2026, 2, 10)
    assert m['amount'] == 1210.50           # haber flag '2' -> positive
    assert m['currency'] == 'EUR'
    assert '2026/0001' in m['description']  # 23 concept line appended
    assert len(m['hash']) == 64


def test_parse_n43_debit_is_negative():
    line22 = ("22" + "00491500" + "260315" + "260315" + "02" + "045" + "1"
              + "00000000005000" + "0" * 10 + "RECIBO LUZ\n")
    movements, errors = parse_n43(line22)
    assert movements[0]['amount'] == -50.0


def test_parse_n43_bad_line_reported_good_imported():
    bad = "22" + "XXXX BROKEN LINE\n"
    movements, errors = parse_n43(N43_FIXTURE + bad)
    assert len(movements) == 1
    assert len(errors) == 1


# --- CSV parser ------------------------------------------------------------------

CSV_FIXTURE = (
    "Fecha,Concepto,Importe\n"
    "10/02/2026,TRANSFERENCIA FRA 2026/0001,\"1.210,50\"\n"
    "11/02/2026,RECIBO LUZ,\"-85,22\"\n"
    "badrow,,,\n"
)


def test_parse_csv_with_mapping():
    mapping = {'date': 'Fecha', 'amount': 'Importe', 'description': 'Concepto',
               'decimal_comma': True}
    movements, errors = parse_csv(CSV_FIXTURE, mapping)
    assert len(movements) == 2
    assert movements[0]['amount'] == 1210.50
    assert movements[1]['amount'] == -85.22
    assert len(errors) == 1                  # bad row reported, not fatal


def test_parse_csv_requires_mapping():
    with pytest.raises(ValueError):
        parse_csv(CSV_FIXTURE, {'date': 'Fecha'})


def test_movement_hash_stable():
    a = movement_hash('2026-02-10', 1210.50, 'X')
    assert a == movement_hash('2026-02-10', 1210.50, 'X')
    assert a != movement_hash('2026-02-10', 1210.51, 'X')


# --- matching -----------------------------------------------------------------------

class _Inv:
    def __init__(self, id, number, amount, d, client='ACME'):
        self.id, self.invoice_number, self.amount_eur = id, number, amount
        self.invoice_date, self.client_name = d, client


def test_suggest_ranks_number_match_highest():
    invoices = [_Inv(1, '2026/0001', 1210.50, date(2026, 2, 1)),
                _Inv(2, '2026/0002', 1210.50, date(2026, 2, 5))]
    movement = {'amount': 1210.50, 'date': date(2026, 2, 10),
                'description': 'transferencia fra 2026/0001'}
    ranked = suggest_for_movement(movement, invoices)
    assert ranked[0][1].id == 1              # number-in-description wins
    assert ranked[0][0] > ranked[1][0]


def test_suggest_ignores_debits_and_wrong_amounts():
    invoices = [_Inv(1, 'X-1', 100.0, date(2026, 2, 1))]
    assert suggest_for_movement({'amount': -100.0, 'date': date(2026, 2, 2),
                                 'description': ''}, invoices) == []
    assert suggest_for_movement({'amount': 250.0, 'date': date(2026, 2, 2),
                                 'description': ''}, invoices) == []


# --- module integration --------------------------------------------------------------

@pytest.fixture
def bank(loaded_modules):
    return loaded_modules.modules['bank_import']


def test_import_dedups_across_batches(bank):
    movements, errors = parse_n43(N43_FIXTURE)
    b1 = bank._import_movements(movements, errors, 'st.n43', 'n43')
    assert b1.imported == 1
    b2 = bank._import_movements(movements, [], 'st.n43', 'n43')
    assert b2.imported == 0 and b2.skipped == 1


def test_confirm_marks_invoice_paid_via_core_path(bank, client):
    from app import db, Customer, Invoice, _issue_invoice

    cust = Customer(name='BankCli', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    inv = Invoice(invoice_number='BK-1', client_name='BankCli', amount_usd=0.0,
                  amount_eur=777.0, exchange_rate=1.0,
                  invoice_date=date(2026, 4, 1), status='draft',
                  currency='EUR', customer_id=cust.id)
    db.session.add(inv)
    db.session.commit()
    _issue_invoice(inv)          # locked now; only paid transition allowed

    m = bank.BankMovement(batch_id=999, date=date(2026, 4, 5), amount=777.0,
                          currency='EUR', description=f'transf {inv.invoice_number}',
                          hash=movement_hash('2026-04-05', 777.0, 'bk1'))
    db.session.add(m)
    db.session.commit()

    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.post(f'/bank-import/confirm/{m.id}',
                       data={'invoice_id': inv.id}, follow_redirects=False)
    assert resp.status_code == 302
    assert db.session.get(Invoice, inv.id).status == 'paid'
    assert m.status == 'matched' and m.matched_id == inv.id
