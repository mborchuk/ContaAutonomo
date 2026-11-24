"""The dashboard: quarter and year totals, tax obligations from the modules, exchange rates, holidays, deadlines.

Routes register on the core app under their own endpoint names
(`url_for('dashboard')`, …). They import from `contaautonomo.web`, never from
`contaautonomo.app`, and read `state.module_manager` when a request runs."""
from datetime import date, datetime

from flask import (render_template)

from contaautonomo import state
from contaautonomo.web import (app, logger)
from contaautonomo.security.login import login_required
from contaautonomo.models import (Invoice, Settings)
from contaautonomo.utils.constants import (counts_as_income)
from contaautonomo.utils.currency_converter import get_currency_symbol


@app.route('/')
@login_required
def dashboard():
    """Main dashboard page with invoice summary table"""
    # Get all invoices with valid invoice_date
    invoices = Invoice.query.filter(Invoice.invoice_date.isnot(None)).order_by(Invoice.invoice_date.desc()).all()

    # Additional safety check - filter out any None objects or invoices with None dates
    invoices = [inv for inv in invoices if inv is not None and hasattr(inv, 'invoice_date') and inv.invoice_date is not None]

    # Get settings for tracked currencies and base currency
    app_settings = Settings.query.first()
    tracked_currencies = []
    base_currency = 'EUR'
    if app_settings:
        if app_settings.tracked_currencies:
            tracked_currencies = [c.strip() for c in app_settings.tracked_currencies.split(',') if c.strip()]
        else:
            tracked_currencies = ['USD', 'EUR', 'GBP', 'CZK']
        base_currency = app_settings.base_currency or 'EUR'
    else:
        tracked_currencies = ['USD', 'EUR', 'GBP', 'CZK']

    # Get current exchange rates for tracked currencies
    from contaautonomo.utils.currency_converter import get_multiple_exchange_rates, refresh_ecb_rates
    today = date.today().strftime('%Y-%m-%d')
    try:
        refresh_ecb_rates()
    except Exception as e:
        logger.warning('Could not refresh ECB rates for dashboard: %s', e)
    exchange_rates = get_multiple_exchange_rates(today, tracked_currencies, base_currency=base_currency)

    # Currency symbols
    base_currency_symbol = get_currency_symbol(base_currency)

    # Tax rate from settings (default 20% for backward compat)
    tax_rate = (app_settings.default_irpf_rate / 100.0) if app_settings and app_settings.default_irpf_rate is not None else 0.20

    # Group invoices by year and quarter
    invoices_by_year = {}
    for invoice in invoices:
        if not invoice or not hasattr(invoice, 'invoice_date') or invoice.invoice_date is None:
            continue  # Skip if somehow None

        year = invoice.invoice_date.year
        quarter = (invoice.invoice_date.month - 1) // 3 + 1  # Q1, Q2, Q3, Q4

        if year not in invoices_by_year:
            invoices_by_year[year] = {}
        if quarter not in invoices_by_year[year]:
            invoices_by_year[year][quarter] = []

        # Calculate amounts in base currency
        if base_currency == 'EUR':
            invoice.amount_base = invoice.amount_eur
        elif base_currency == 'USD':
            invoice.amount_base = invoice.amount_usd
        else:
            # Convert from EUR to base currency
            # exchange_rates gives us "1 base_currency = X EUR"
            # So to convert EUR to base_currency: EUR_amount / EUR_rate
            eur_rate = exchange_rates.get('EUR', 1.0)
            if eur_rate > 0:
                invoice.amount_base = invoice.amount_eur / eur_rate
            else:
                invoice.amount_base = invoice.amount_eur

        # Calculate display rate (invoice currency to base currency)
        invoice_currency = invoice.currency or 'USD'
        if invoice_currency == base_currency:
            # Same currency
            invoice.display_rate = 1.0
        elif invoice_currency == 'EUR' and base_currency == 'USD':
            # EUR to USD
            invoice.display_rate = invoice.amount_usd / invoice.amount_eur if invoice.amount_eur > 0 else 1.0
        elif invoice_currency == 'USD' and base_currency == 'EUR':
            # USD to EUR
            invoice.display_rate = invoice.amount_eur / invoice.amount_usd if invoice.amount_usd > 0 else 1.0
        elif invoice_currency == 'EUR':
            # EUR to other currency
            eur_rate = exchange_rates.get('EUR', 1.0)
            invoice.display_rate = 1 / eur_rate if eur_rate > 0 else 1.0
        elif invoice_currency == 'USD':
            # USD to other currency (via EUR)
            eur_rate = exchange_rates.get('EUR', 1.0)
            usd_to_eur = invoice.amount_eur / invoice.amount_usd if invoice.amount_usd > 0 else 1.0
            invoice.display_rate = usd_to_eur / eur_rate if eur_rate > 0 else 1.0
        else:
            # Fallback
            invoice.display_rate = 1.0

        # Calculate tax for each invoice
        invoice.tax_usd = invoice.amount_usd * tax_rate
        invoice.tax_eur = invoice.amount_eur * tax_rate
        invoice.tax_base = invoice.amount_base * tax_rate
        invoice.quarterly_sum_usd = invoice.amount_usd

        invoices_by_year[year][quarter].append(invoice)

    # Calculate quarter totals
    quarter_totals = {}
    for year in invoices_by_year:
        quarter_totals[year] = {}
        for quarter, quarter_invoices in invoices_by_year[year].items():
            quarter_totals[year][quarter] = {
                'sum_usd': sum(inv.amount_usd for inv in quarter_invoices),
                'sum_eur': sum(inv.amount_eur for inv in quarter_invoices),
                'sum_base': sum(inv.amount_base for inv in quarter_invoices),
                'quarterly_sum_usd': sum(inv.amount_usd for inv in quarter_invoices),
                'tax_usd': sum(inv.amount_usd * tax_rate for inv in quarter_invoices),
                'tax_eur': sum(inv.amount_eur * tax_rate for inv in quarter_invoices),
                'tax_base': sum(inv.amount_base * tax_rate for inv in quarter_invoices),
                'quarterly_tax_usd': sum(inv.amount_usd * tax_rate for inv in quarter_invoices)
            }

    # Calculate year totals
    year_totals = {}
    for year in invoices_by_year:
        all_year_invoices = []
        for quarter_invoices in invoices_by_year[year].values():
            all_year_invoices.extend(quarter_invoices)

        year_totals[year] = {
            'sum_usd': sum(inv.amount_usd for inv in all_year_invoices),
            'sum_eur': sum(inv.amount_eur for inv in all_year_invoices),
            'sum_base': sum(inv.amount_base for inv in all_year_invoices),
            'quarterly_sum_usd': sum(inv.amount_usd for inv in all_year_invoices),
            'tax_usd': sum(inv.amount_usd * tax_rate for inv in all_year_invoices),
            'tax_eur': sum(inv.amount_eur * tax_rate for inv in all_year_invoices),
            'tax_base': sum(inv.amount_base * tax_rate for inv in all_year_invoices),
            'quarterly_tax_usd': sum(inv.amount_usd * tax_rate for inv in all_year_invoices)
        }

    # Calculate grand totals
    totals = {
        'sum_usd': sum(inv.amount_usd for inv in invoices),
        'sum_eur': sum(inv.amount_eur for inv in invoices),
        'sum_base': sum(inv.amount_base for inv in invoices),
        'quarterly_sum_usd': sum(inv.amount_usd for inv in invoices),
        'tax_usd': sum(inv.amount_usd * tax_rate for inv in invoices),
        'tax_eur': sum(inv.amount_eur * tax_rate for inv in invoices),
        'tax_base': sum(inv.amount_base * tax_rate for inv in invoices),
        'quarterly_tax_usd': sum(inv.amount_usd * tax_rate for inv in invoices)
    }

    # Calculate stats
    stats = {
        'total_count': len(invoices),
        'total_usd': sum(inv.amount_usd for inv in invoices),
        'total_eur': sum(inv.amount_eur for inv in invoices),
        'total_base': sum(inv.amount_base for inv in invoices),
        'pending_count': len([inv for inv in invoices if inv.status == 'pending'])
    }

    # Calculate current year tax obligations
    current_year = datetime.now().year
    # Same rule as the tax drafts: every invoice dated this year except cancelled.
    current_year_invoices = [inv for inv in invoices
                             if inv.invoice_date.year == current_year and counts_as_income(inv)]

    # Income for current year
    current_year_income = sum(inv.amount_base for inv in current_year_invoices)

    # VAT collected — let modules override, fallback to configured rate
    vat_rate = (app_settings.default_vat_rate or 21.0) / 100.0 if app_settings else 0.21
    vat_collected = 0

    if state.module_manager:
        vat_override = state.module_manager.calculate_vat({
            'invoices': current_year_invoices,
            'settings': app_settings,
            'base_currency': base_currency,
        })
        if vat_override:
            vat_collected = vat_override['vat_collected']
            vat_rate = vat_override.get('vat_rate', vat_rate)

    if not vat_collected:
        for inv in current_year_invoices:
            if inv.customer and inv.customer.tax_type == 'standard':
                vat_collected += inv.amount_base * vat_rate

    # Collect tax obligation contributions from enabled modules
    module_deductions = 0
    module_tax_total = 0
    module_summary_columns = []
    module_breakdown_rows = []
    module_notes = []

    if state.module_manager:
        tax_context = {
            'current_year': current_year,
            'base_currency': base_currency,
            'exchange_rates': exchange_rates,
            'settings': app_settings,
            'vat_collected': vat_collected,
            'currency_symbol': base_currency_symbol
        }

        for item in state.module_manager.get_tax_obligations(tax_context):
            module_deductions += item.get('deductions', 0)
            module_tax_total += item.get('tax_total', 0)
            module_summary_columns.extend(item.get('summary_columns', []))
            module_breakdown_rows.extend(item.get('breakdown_rows', []))
            module_notes.extend(item.get('notes', []))

    # Calculate taxable income (income - deductions from modules)
    taxable_income = current_year_income - module_deductions

    # Income tax comes from the country's tax module (calculate_income_tax);
    # the core knows no country's scale. Without one, the dashboard says so.
    income_tax = 0
    irpf_breakdown = []
    income_tax_label = None

    if state.module_manager:
        tax_override = state.module_manager.calculate_income_tax({
            'taxable_income': taxable_income,
            'settings': app_settings,
            'base_currency': base_currency,
            'currency_symbol': base_currency_symbol,
        })
        if tax_override:
            income_tax = tax_override['income_tax']
            irpf_breakdown = tax_override.get('irpf_breakdown', [])
            income_tax_label = tax_override.get('label', 'Income Tax')

    # Total tax obligations = income tax + module contributions (VAT, SS, etc.)
    total_tax_obligations = income_tax + module_tax_total

    tax_breakdown = {
        'current_year': current_year,
        'income': current_year_income,
        'taxable_income': taxable_income,
        'income_tax': income_tax,
        'income_tax_label': income_tax_label,
        'irpf_breakdown': irpf_breakdown,
        'summary_columns': module_summary_columns,
        'breakdown_rows': module_breakdown_rows,
        'notes': module_notes,
        'total_obligations': total_tax_obligations
    }

    # Sort years in descending order
    sorted_years = sorted(invoices_by_year.keys(), reverse=True)

    # Get public holidays from Nager.Date API
    holidays = []
    try:
        import requests
        current_year = datetime.now().year
        country_code = app_settings.country_code if app_settings and hasattr(app_settings, 'country_code') and app_settings.country_code else 'ES'
        response = requests.get(f'https://date.nager.at/api/v3/PublicHolidays/{current_year}/{country_code}', timeout=3)
        if response.status_code == 200:
            all_holidays = response.json()
            today_date = datetime.now().date()
            upcoming = [h for h in all_holidays if datetime.strptime(h['date'], '%Y-%m-%d').date() >= today_date]
            holidays = upcoming[:3]
    except Exception:
        logger.exception("Failed to fetch public holidays from Nager.Date API")

    # Tax deadlines: whatever provides calendar.upcoming_deadlines. None = no
    # provider (the dashboard then shows a hint to enable the Fiscal Calendar).
    tax_deadlines = None
    functions = state.module_manager.functions if state.module_manager else None
    if functions is not None and functions.available('calendar.upcoming_deadlines'):
        try:
            tax_deadlines = functions.call('calendar.upcoming_deadlines', limit=4)
        except Exception:
            logger.exception('Fiscal Calendar deadlines unavailable')
            tax_deadlines = []

    # Collect module dashboard panels
    module_panels = []
    if state.module_manager:
        module_panels = state.module_manager.get_dashboard_panels()
        module_panels.sort(key=lambda p: p.get('order', 50))

    return render_template('dashboard.html',
                         invoices_by_year=invoices_by_year,
                         sorted_years=sorted_years,
                         quarter_totals=quarter_totals,
                         year_totals=year_totals,
                         totals=totals,
                         stats=stats,
                         tax_breakdown=tax_breakdown,
                         exchange_rates=exchange_rates,
                         tracked_currencies=tracked_currencies,
                         base_currency=base_currency,
                         base_currency_symbol=base_currency_symbol,
                         tax_rate_pct=tax_rate * 100,
                         current_date=today,
                         holidays=holidays,
                         tax_deadlines=tax_deadlines,
                         settings=app_settings,
                         module_panels=module_panels)
