"""Unit tests for the tax_management module's CSV export helpers."""
from datetime import date
from types import SimpleNamespace

from modules.tax_management.index import build_ss_payments_csv, _csv_safe


def _payment(payment_date, amount, description=None):
    return SimpleNamespace(
        payment_date=payment_date, amount=amount, description=description
    )


def test_csv_has_header_rows_and_total():
    payments = [
        _payment(date(2026, 1, 31), 299.57, 'SS Jan 2026'),
        _payment(date(2026, 2, 28), 299.57, 'SS Feb 2026'),
    ]
    lines = build_ss_payments_csv(payments).strip().splitlines()
    assert lines[0] == 'Date,Description,Amount (EUR)'
    assert lines[1] == '2026-01-31,SS Jan 2026,299.57'
    assert lines[2] == '2026-02-28,SS Feb 2026,299.57'
    assert lines[3] == ',Total,599.14'


def test_csv_empty_payments_still_has_header_and_zero_total():
    lines = build_ss_payments_csv([]).strip().splitlines()
    assert lines[0] == 'Date,Description,Amount (EUR)'
    assert lines[1] == ',Total,0.00'


def test_csv_handles_missing_description():
    payments = [_payment(date(2026, 3, 31), 100.0)]
    lines = build_ss_payments_csv(payments).strip().splitlines()
    assert lines[1] == '2026-03-31,,100.00'


def test_csv_quotes_descriptions_with_commas():
    payments = [_payment(date(2026, 4, 30), 50.0, 'Regularización, 2024')]
    lines = build_ss_payments_csv(payments).strip().splitlines()
    assert lines[1] == '2026-04-30,"Regularización, 2024",50.00'


def test_csv_safe_neutralizes_formula_injection():
    assert _csv_safe('=SUM(A1:A9)') == "'=SUM(A1:A9)"
    assert _csv_safe('+1234') == "'+1234"
    assert _csv_safe('-1234') == "'-1234"
    assert _csv_safe('@cmd') == "'@cmd"
    assert _csv_safe('normal text') == 'normal text'
    assert _csv_safe('') == ''
    assert _csv_safe(None) is None


def test_csv_formula_injection_in_description_is_escaped():
    payments = [_payment(date(2026, 5, 31), 10.0, '=HYPERLINK("http://evil")')]
    out = build_ss_payments_csv(payments)
    assert '"\'=HYPERLINK(""http://evil"")"' in out
