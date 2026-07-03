#!/usr/bin/env python3
"""
Modelo 303 / 130 aggregation engine — pure functions, no Flask, no DB.

CAVEMAN NOTE: keep this file dumb and pure so tests feed plain objects and check
numbers. No queries here. Module (index.py) fetch data, pass it in.

Devengo (accrual) basis: invoices count by invoice_date; cancelled excluded.
IVA repercutido only on `standard` customers (eu_b2b / non_eu = reverse charge
or export, no output VAT) — same rule the dashboard already use.

IVA soportado (input VAT) need per-expense VAT amount. Until F4 add VAT fields to
Expense, expenses have no VAT split, so deductible IVA = 0 and every expense in
the period is counted as "missing VAT data" (surfaced to user). Code already
read `vat_amount` / `deductible_pct` if present, so it light up free when F4 land.
"""

from datetime import date


def quarter_of(d):
    """Return calendar quarter (1-4) for a date."""
    return (d.month - 1) // 3 + 1


def quarter_bounds(year, quarter):
    """Return (start_date, end_date) inclusive for a fiscal quarter."""
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = date(year, start_month, 1)
    if end_month == 12:
        end = date(year, 12, 31)
    else:
        # first day of next month minus one day
        end = date(year, end_month + 1, 1).fromordinal(
            date(year, end_month + 1, 1).toordinal() - 1)
    return start, end


def _round2(value):
    """Round to 2 decimals, half-up, returning a float."""
    # half-up to match how money is transcribed on the forms
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _invoice_base_eur(invoice):
    """Taxable base of an invoice in EUR.

    In this app the stored invoice amount IS the taxable base (VAT is derived,
    never added to the total), and amount_eur is always populated.
    """
    return invoice.amount_eur or 0.0


def _customer_tax_type(invoice):
    customer = getattr(invoice, "customer", None)
    return getattr(customer, "tax_type", None) if customer else None


def _active(invoice):
    """Devengo basis: everything except cancelled counts."""
    return getattr(invoice, "status", None) != "cancelled"


def _invoice_output_vat(invoice, vat_rate):
    """Return (standard_base_eur, output_cuota_eur) for one invoice.

    F5-D2: prefer the F2-D4 fiscal snapshot frozen at issue (snap_taxable_base /
    snap_vat_amount / snap_vat_rate); fall back to deriving from the customer's
    tax_type + the current rate for legacy/draft invoices without a snapshot.
    Only standard-rated operations feed the general-regime base (box 01);
    eu_b2b / non_eu carry no output VAT.
    """
    snap_rate = getattr(invoice, 'snap_vat_rate', None)
    if snap_rate is not None:
        # Issued invoice with a frozen snapshot.
        if snap_rate and snap_rate > 0:
            base = getattr(invoice, 'snap_taxable_base', None)
            if base is None:
                base = _invoice_base_eur(invoice)
            return base, (getattr(invoice, 'snap_vat_amount', None) or 0.0)
        return 0.0, 0.0
    # Legacy / draft: derive from tax_type.
    if _customer_tax_type(invoice) == 'standard':
        base = _invoice_base_eur(invoice)
        return base, base * vat_rate
    return 0.0, 0.0


def compute_modelo_303(invoices, expenses, vat_rate, convert_expense=None):
    """Compute Modelo 303 (IVA) figures for a single quarter.

    Args:
        invoices: iterable of invoice-like objects already filtered to the
            quarter. Need .amount_eur, .status, .customer.tax_type.
        expenses: iterable of expense-like objects already filtered to the
            quarter. Need .amount, .currency, .expense_date; optionally
            .vat_amount / .deductible / .deductible_pct (F4).
        vat_rate: output VAT rate as a fraction (e.g. 0.21).
        convert_expense: optional fn(amount, currency, when) -> EUR. Only used
            when an expense actually carries VAT data.

    Returns:
        dict with box values, subtotals, and a `meta` block.
    """
    base_devengado = 0.0
    cuota_devengado = 0.0
    for inv in invoices:
        if not _active(inv):
            continue
        base, cuota = _invoice_output_vat(inv, vat_rate)
        base_devengado += base
        cuota_devengado += cuota

    base_deducible = 0.0
    cuota_deducible = 0.0
    missing_vat_count = 0
    for exp in expenses:
        vat_amount = getattr(exp, "vat_amount", None)
        if vat_amount is None:
            # F4 not present (or receipt had no VAT captured) -> cannot deduct.
            missing_vat_count += 1
            continue
        deductible = getattr(exp, "deductible", True)
        if not deductible:
            continue
        pct = getattr(exp, "deductible_pct", 100.0) or 100.0
        net = getattr(exp, "net_amount", None)
        currency = getattr(exp, "currency", "EUR")
        when = getattr(exp, "expense_date", None)
        if convert_expense is not None:
            vat_eur = convert_expense(vat_amount, currency, when)
            net_eur = convert_expense(net, currency, when) if net is not None else 0.0
        else:
            vat_eur, net_eur = vat_amount, (net or 0.0)
        base_deducible += net_eur * (pct / 100.0)
        cuota_deducible += vat_eur * (pct / 100.0)

    total_devengado = cuota_devengado
    total_deducir = cuota_deducible
    resultado = total_devengado - total_deducir

    boxes = {
        "01": _round2(base_devengado),
        "03": _round2(cuota_devengado),
        "27": _round2(total_devengado),
        "28": _round2(base_deducible),
        "29": _round2(cuota_deducible),
        "45": _round2(total_deducir),
        "46": _round2(resultado),
        "71": _round2(resultado),
    }
    return {
        "form": "303",
        "boxes": boxes,
        "vat_rate": vat_rate,
        "meta": {
            "missing_expense_vat_count": missing_vat_count,
            "basis": "devengo",
        },
    }


def compute_modelo_130(invoices_ytd, expenses_ytd, irpf_rate,
                       prior_income=0.0, prior_expenses=0.0,
                       convert_expense=None):
    """Compute Modelo 130 (IRPF pago fraccionado) for a quarter, cumulative YTD.

    Modelo 130 is cumulative from 1 Jan to the end of the quarter; the amount due
    subtracts the payments already made in earlier quarters of the same year.

    Args:
        invoices_ytd: invoice-like objects from 1 Jan to end of the quarter.
        expenses_ytd: expense-like objects for the same YTD window.
        irpf_rate: advance rate as a fraction (Modelo 130 fixed 20% -> 0.20).
        prior_income / prior_expenses: YTD totals up to the END of the PREVIOUS
            quarter, used to estimate box 05 (prior payments).
        convert_expense: fn(amount, currency, when) -> EUR for expenses.
    """
    ingresos = 0.0
    for inv in invoices_ytd:
        if _active(inv):
            ingresos += _invoice_base_eur(inv)

    gastos = 0.0
    for exp in expenses_ytd:
        # IRPF gastos are the VAT-excluded base: prefer F4 net_amount when
        # present (VAT is declared separately on Modelo 303), else fall back to
        # the gross amount for legacy rows.
        net = getattr(exp, "net_amount", None)
        amount = net if net is not None else (getattr(exp, "amount", 0.0) or 0.0)
        currency = getattr(exp, "currency", "EUR")
        when = getattr(exp, "expense_date", None)
        gastos += convert_expense(amount, currency, when) if convert_expense else amount

    rendimiento = ingresos - gastos
    pago = max(0.0, rendimiento) * irpf_rate

    prior_rendimiento = prior_income - prior_expenses
    prior_pago = max(0.0, prior_rendimiento) * irpf_rate

    resultado = max(0.0, pago - prior_pago)

    boxes = {
        "01": _round2(ingresos),
        "02": _round2(gastos),
        "03": _round2(rendimiento),
        "04": _round2(pago),
        "05": _round2(prior_pago),
        "07": _round2(resultado),
    }
    return {
        "form": "130",
        "boxes": boxes,
        "irpf_rate": irpf_rate,
        "meta": {"basis": "cumulative_ytd"},
    }
