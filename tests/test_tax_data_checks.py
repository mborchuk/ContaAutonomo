"""Data checks shown on the Modelo 303 draft before filing (synthetic data)."""
from datetime import date


def test_unpaid_invoices_and_unmarked_vat_free_purchases_are_flagged(app, loaded_modules):
    from app import Customer, Expense, Invoice, db

    customer = Customer(name='ACME Synthetic s.r.o.', tax_type='eu_b2b')
    db.session.add(customer)
    db.session.flush()
    for number, status in (('C-1', 'paid'), ('C-2', 'pending')):
        db.session.add(Invoice(invoice_number=number, client_name=customer.name,
                               customer_id=customer.id, amount_usd=100.0, amount_eur=100.0,
                               exchange_rate=1.0, currency='EUR',
                               invoice_date=date(2026, 9, 1), status=status))

    def expense(description, vat, category='Services', reverse_charge=None):
        db.session.add(Expense(expense_date=date(2026, 9, 2), category=category,
                               description=description, amount=10.0, net_amount=10.0,
                               vat_amount=vat, vat_rate=0.0 if not vat else 21.0,
                               currency='EUR', reverse_charge=reverse_charge))

    expense('Synthetic SaaS', 0.0)                                  # flagged
    expense('Synthetic API', 0.0, reverse_charge='non_eu')         # marked: fine
    expense('Synthetic insurance', 0.0, category='Insurance')      # exempt: fine
    expense('Synthetic office', 2.1)                                # has VAT: fine
    db.session.commit()

    checks = loaded_modules.modules['tax_es_forms']._draft_303(2026, 3)['meta']['checks']
    assert checks == [
        "1 invoice(s) of this quarter are not marked paid — they are still counted "
        "(invoice date): C-2",
        '1 expense(s) without Spanish VAT are not marked "Reverse charge" — if the '
        'supplier is outside Spain, mark them: Synthetic SaaS']
