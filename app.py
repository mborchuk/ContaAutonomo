#!/usr/bin/env python3
"""
Invoice Management Web Application
Features: Save invoices, USD to EUR conversion, PDF generation, filtering
"""

from flask import Flask, render_template, request, redirect, url_for, send_file, flash, session, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from functools import wraps
import io
import os
import hashlib
from currency_converter import get_exchange_rate, convert_usd_to_eur, get_currency_symbol
import logging

logger = logging.getLogger(__name__)


app = Flask(__name__)
APP_VERSION = '0.1.0'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', 'sqlite:///invoices.db')
app.config['SECRET_KEY'] = os.environ.get(
    'SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Module Manager - initialized after models are defined (see bottom of file)
module_manager = None


def log_activity(action, category='system', details=None):
    """Log user/system activity via core logger (safe to call before init)"""
    if module_manager:
        module_manager.core.log_activity(action, category, details)


# Login required decorator
def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function



def find_customer_by_name(model, name):
    """Find a customer by normalized name (case-insensitive, stripped)."""
    normalized = name.strip()
    if not normalized:
        return None
    return model.query.filter(model.name.ilike(normalized)).first()


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    vat_number = db.Column(db.String(100))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(20))
    country = db.Column(db.String(100))
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    is_default = db.Column(db.Boolean, default=False)
    tax_type = db.Column(db.String(20), default='eu_b2b')  # non_eu, eu_b2b, standard
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    invoices = db.relationship('Invoice', backref='customer', lazy=True)

    def __repr__(self):
        return f'<Customer {self.name}>'



class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(50), unique=True, nullable=False)
    client_name = db.Column(db.String(200), nullable=False)
    amount_usd = db.Column(db.Float, nullable=False)
    amount_eur = db.Column(db.Float, nullable=False)
    exchange_rate = db.Column(db.Float, nullable=False)
    invoice_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    description = db.Column(db.Text)
    quantity = db.Column(db.Float, default=1)
    unit_price_usd = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    pdf_hash = db.Column(db.String(64))  # SHA256 hash of the PDF file
    pdf_storage_key = db.Column(db.String(500))  # Storage key for PDF (local path or remote ID)
    currency = db.Column(db.String(10), default='USD')  # Invoice currency
    payment_method = db.Column(db.String(100), default='Bank Transfer')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    bank_id = db.Column(db.Integer, db.ForeignKey('bank.id'), nullable=True)
    items = db.relationship('InvoiceItem', backref='invoice', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Invoice {self.invoice_number}>'


class InvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    description = db.Column(db.Text, nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=1)
    unit_price_usd = db.Column(db.Float, nullable=False)
    subtotal_usd = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<InvoiceItem {self.description}>'


class Bank(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    iban = db.Column(db.String(100), nullable=False)
    swift = db.Column(db.String(50))
    bank_name = db.Column(db.String(200))
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    invoices = db.relationship('Invoice', backref='bank', lazy=True)

    def __repr__(self):
        return f'<Bank {self.name}>'


class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Personal/Business Info
    business_name = db.Column(db.String(200))
    owner_name = db.Column(db.String(200))
    vat_number = db.Column(db.String(100))
    nie_number = db.Column(db.String(100))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(20))
    country = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(200))
    # Other
    default_payment_terms = db.Column(db.String(200))
    default_description = db.Column(db.Text)
    default_notes = db.Column(db.Text)
    default_currency = db.Column(db.String(10), default='USD')
    tracked_currencies = db.Column(db.Text, default='USD,EUR,GBP,CZK')  # Comma-separated list
    base_currency = db.Column(db.String(10), default='EUR')  # Base currency for conversions and display
    report_template = db.Column(db.String(100), default='official_template')  # Report template name
    invoice_template = db.Column(db.String(100), default='default_template')  # Invoice PDF template name
    # Dashboard settings
    show_currency_panel = db.Column(db.Boolean, default=True)  # Show currency/holidays panel on dashboard
    show_tax_panel = db.Column(db.Boolean, default=True)  # Show tax obligations panel on dashboard
    # Backup settings
    auto_backup_enabled = db.Column(db.Boolean, default=False)  # Enable automatic backup on startup
    backup_retention_count = db.Column(db.Integer, default=5)  # Number of backups to keep (deprecated, use daily_backup_retention_count)
    daily_backup_retention_count = db.Column(db.Integer, default=4)  # Number of daily backups to keep
    # Social Security settings
    social_security_monthly = db.Column(db.Float, default=0.0)  # Monthly SS quota (cuota autónomo)
    # Logging settings
    log_path = db.Column(db.String(500), default='')  # Custom log directory
    log_retention_days = db.Column(db.Integer, default=30)  # 0 = keep forever
    log_use_external_storage = db.Column(db.Boolean, default=False)
    log_storage = db.Column(db.String(10), default='file')  # 'file' or 'db'
    # Tax rates (configurable per country)
    default_vat_rate = db.Column(db.Float, default=21.0)  # VAT/IVA rate in %
    default_irpf_rate = db.Column(db.Float, default=20.0)  # Income tax retention rate in %
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<Settings {self.business_name}>'


class Contractor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    vat_number = db.Column(db.String(100))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(20))
    country = db.Column(db.String(100))
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expenses = db.relationship('Expense', backref='contractor', lazy=True)

    def __repr__(self):
        return f'<Contractor {self.name}>'


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractor.id'))
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='EUR')
    category = db.Column(db.String(100))
    description = db.Column(db.Text)
    expense_date = db.Column(db.Date, nullable=False)
    file_path = db.Column(db.String(500))
    invoice_number = db.Column(db.String(100))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Expense {self.id} - {self.amount} {self.currency}>'


class TaxForm(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    form_type = db.Column(db.String(50), nullable=False)  # 349, 390, 303, 130, 100
    year = db.Column(db.Integer, nullable=False)
    quarter = db.Column(db.Integer)  # 1-4 for quarterly forms, NULL for annual
    file_path = db.Column(db.String(500), nullable=False)
    original_filename = db.Column(db.String(200))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    def __repr__(self):
        if self.quarter:
            return f'<TaxForm {self.form_type}-Q{self.quarter} {self.year}>'
        return f'<TaxForm {self.form_type} {self.year}>'


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(300), nullable=False)
    source = db.Column(db.String(100))  # Agencia Tributaria, Seguridad Social, etc.
    document_date = db.Column(db.Date)
    description = db.Column(db.Text)
    file_path = db.Column(db.String(500), nullable=False)
    original_filename = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Document {self.name}>'


class SSPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    payment_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<SSPayment {self.payment_date} {self.amount}>'



@app.route('/customers')
@login_required
def customers():
    """Redirect to settings page where customers are now managed"""
    return redirect(url_for('settings') + '#customers')


@app.route('/customers/create', methods=['GET', 'POST'])
@login_required
def create_customer():
    if request.method == 'POST':
        customer = Customer(
            name=request.form['name'],
            vat_number=request.form.get('vat_number'),
            address=request.form.get('address'),
            city=request.form.get('city'),
            postal_code=request.form.get('postal_code'),
            country=request.form.get('country'),
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            tax_type=request.form.get('tax_type', 'eu_b2b')
        )
        db.session.add(customer)
        db.session.commit()
        flash('Customer created successfully!', 'success')
        return redirect(url_for('settings') + '#customers')

    return render_template('customer_form.html', customer=None)


@app.route('/customers/<int:id>')
@login_required
def view_customer(id):
    customer = Customer.query.get_or_404(id)

    # Get base currency from settings
    app_settings = Settings.query.first()
    base_currency = app_settings.base_currency if app_settings and app_settings.base_currency else 'EUR'
    base_currency_symbol = get_currency_symbol(base_currency)

    return render_template('customer_view.html', customer=customer,
                         base_currency=base_currency,
                         base_currency_symbol=base_currency_symbol)


@app.route('/customers/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_customer(id):
    customer = Customer.query.get_or_404(id)

    if request.method == 'POST':
        customer.name = request.form['name']
        customer.vat_number = request.form.get('vat_number')
        customer.address = request.form.get('address')
        customer.city = request.form.get('city')
        customer.postal_code = request.form.get('postal_code')
        customer.country = request.form.get('country')
        customer.email = request.form.get('email')
        customer.phone = request.form.get('phone')
        customer.tax_type = request.form.get('tax_type', 'eu_b2b')

        db.session.commit()
        flash('Customer updated successfully!', 'success')
        return redirect(url_for('view_customer', id=customer.id))

    return render_template('customer_form.html', customer=customer)


@app.route('/customers/<int:id>/delete')
@login_required
def delete_customer(id):
    customer = Customer.query.get_or_404(id)
    db.session.delete(customer)
    db.session.commit()
    flash('Customer deleted successfully!', 'success')
    return redirect(url_for('settings') + '#customers')


def _get_upcoming_tax_deadlines():
    """
    Get upcoming Spanish Autónomo tax deadlines grouped by date.
    If a deadline falls on weekend, it moves to the next business day.
    """
    from datetime import date, timedelta

    today = date.today()
    year = today.year

    # All deadlines: (date, list of forms)
    raw_deadlines = [
        (date(year, 1, 20), [
            ('130, 303, 349', f'Q4 {year-1}'),
        ]),
        (date(year, 1, 30), [
            ('390', f'Annual VAT {year-1}'),
        ]),
        (date(year, 4, 20), [
            ('130, 303, 349', f'Q1 {year}'),
        ]),
        (date(year, 6, 30), [
            ('100', f'Renta {year-1}'),
        ]),
        (date(year, 7, 20), [
            ('130, 303, 349', f'Q2 {year}'),
        ]),
        (date(year, 10, 20), [
            ('130, 303, 349', f'Q3 {year}'),
        ]),
        (date(year + 1, 1, 20), [
            ('130, 303, 349', f'Q4 {year}'),
        ]),
        (date(year + 1, 1, 30), [
            ('390', f'Annual VAT {year}'),
        ]),
    ]

    def adjust_to_business_day(d):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    upcoming = []
    for deadline_date, forms in raw_deadlines:
        adjusted = adjust_to_business_day(deadline_date)
        if adjusted >= today:
            days_left = (adjusted - today).days
            upcoming.append({
                'date': adjusted.strftime('%d/%m/%Y'),
                'days_left': days_left,
                'urgent': days_left <= 14,
                'forms': forms,
            })

    upcoming.sort(key=lambda x: x['days_left'])
    return upcoming[:4]


@app.route('/')
@login_required
def dashboard():
    """Main dashboard page with invoice summary table"""
    # Get all invoices with valid invoice_date
    invoices = Invoice.query.filter(Invoice.invoice_date != None).order_by(Invoice.invoice_date.desc()).all()

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
    from datetime import date
    from currency_converter import get_multiple_exchange_rates
    today = date.today().strftime('%Y-%m-%d')
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
    from datetime import datetime
    current_year = datetime.now().year
    current_year_invoices = [inv for inv in invoices if inv.invoice_date.year == current_year and inv.status == 'paid']

    # Income for current year
    current_year_income = sum(inv.amount_base for inv in current_year_invoices)

    # VAT collected — let modules override, fallback to configured rate
    vat_label = 'VAT'
    vat_rate = (app_settings.default_vat_rate or 21.0) / 100.0 if app_settings else 0.21
    vat_collected = 0

    if module_manager:
        vat_override = module_manager.calculate_vat({
            'invoices': current_year_invoices,
            'settings': app_settings,
            'base_currency': base_currency,
        })
        if vat_override:
            vat_collected = vat_override['vat_collected']
            vat_rate = vat_override.get('vat_rate', vat_rate)
            vat_label = vat_override.get('label', 'VAT')

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

    if module_manager:
        tax_context = {
            'current_year': current_year,
            'base_currency': base_currency,
            'exchange_rates': exchange_rates,
            'settings': app_settings,
            'vat_collected': vat_collected,
            'currency_symbol': base_currency_symbol
        }

        for item in module_manager.get_tax_obligations(tax_context):
            module_deductions += item.get('deductions', 0)
            module_tax_total += item.get('tax_total', 0)
            module_summary_columns.extend(item.get('summary_columns', []))
            module_breakdown_rows.extend(item.get('breakdown_rows', []))
            module_notes.extend(item.get('notes', []))

    # Calculate taxable income (income - deductions from modules)
    taxable_income = current_year_income - module_deductions

    # Income tax — let modules override, fallback to Spanish IRPF brackets
    income_tax = 0
    irpf_breakdown = []
    income_tax_label = 'Income Tax (IRPF)'

    if module_manager:
        tax_override = module_manager.calculate_income_tax({
            'taxable_income': taxable_income,
            'settings': app_settings,
            'base_currency': base_currency,
            'currency_symbol': base_currency_symbol,
        })
        if tax_override:
            income_tax = tax_override['income_tax']
            irpf_breakdown = tax_override.get('irpf_breakdown', [])
            income_tax_label = tax_override.get('label', 'Income Tax')

    if not income_tax and not irpf_breakdown and taxable_income > 0:
        # Default: Spanish IRPF progressive brackets
        brackets = [
            (12450, 0.19, '€0 - €12,450'),
            (20200, 0.24, '€12,450 - €20,200'),
            (35200, 0.30, '€20,200 - €35,200'),
            (60000, 0.37, '€35,200 - €60,000'),
            (float('inf'), 0.45, '€60,000+'),
        ]
        prev_limit = 0
        for limit, rate, label in brackets:
            if taxable_income <= prev_limit:
                break
            amount = min(taxable_income, limit) - prev_limit
            tax = amount * rate
            income_tax += tax
            active = taxable_income <= limit
            irpf_breakdown.append({
                'bracket': label, 'rate': rate * 100,
                'amount': amount, 'tax': tax, 'active': active,
            })
            prev_limit = limit

    # Total tax obligations = IRPF + module contributions (VAT, SS, etc.)
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
        from datetime import datetime
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

    # Tax deadlines for Spanish Autónomo
    from datetime import date, timedelta
    tax_deadlines = _get_upcoming_tax_deadlines()

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
                         settings=app_settings)


@app.route('/invoices')
@login_required
def index():
    from sqlalchemy import select

    status_filter = request.args.get('status', '')
    client_filter = request.args.get('client', '')
    invoice_number_filter = request.args.get('invoice_number', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    sort_by = request.args.get('sort_by', 'invoice_date')
    sort_order = request.args.get('sort_order', 'desc')

    # Build select statement
    stmt = select(Invoice).where(Invoice.id != None)

    if status_filter:
        stmt = stmt.where(Invoice.status == status_filter)
    if client_filter:
        stmt = stmt.where(Invoice.client_name.ilike(f'%{client_filter}%'))
    if invoice_number_filter:
        stmt = stmt.where(Invoice.invoice_number.ilike(f'%{invoice_number_filter}%'))
    if date_from:
        stmt = stmt.where(Invoice.invoice_date >= datetime.strptime(date_from, '%Y-%m-%d').date())
    if date_to:
        stmt = stmt.where(Invoice.invoice_date <= datetime.strptime(date_to, '%Y-%m-%d').date())

    # Apply sorting
    sort_column = getattr(Invoice, sort_by, Invoice.invoice_date)
    if sort_order == 'asc':
        stmt = stmt.order_by(sort_column.asc())
    else:
        stmt = stmt.order_by(sort_column.desc())

    # Paginate results using db.paginate()
    pagination = db.paginate(stmt, page=page, per_page=per_page, error_out=False)

    # Filter out None objects from pagination items
    valid_invoices = [inv for inv in pagination.items if inv is not None and hasattr(inv, 'amount_usd')]
    pagination.items = valid_invoices

    customers = Customer.query.order_by(Customer.name).all()

    # Get base currency from settings
    app_settings = Settings.query.first()
    base_currency = app_settings.base_currency if app_settings and app_settings.base_currency else 'EUR'
    base_currency_symbol = get_currency_symbol(base_currency)

    return render_template('index.html',
                         invoices=pagination.items,
                         pagination=pagination,
                         customers=customers,
                         base_currency=base_currency,
                         base_currency_symbol=base_currency_symbol,
                         sort_by=sort_by,
                         sort_order=sort_order)


@app.route('/create', methods=['GET', 'POST'])
@login_required
def create_invoice():
    if request.method == 'POST':
        try:
            invoice_number = request.form['invoice_number']
            client_name = request.form['client_name']

            invoice_date_str = request.form['invoice_date']
            due_date_str = request.form.get('due_date')
            status = request.form['status']
            notes = request.form.get('notes', '')
            bank_id = request.form.get('bank_id')

            # Parse items from form
            items_data = []
            total_usd = 0

            # Get all item keys from form
            item_keys = set()
            for key in request.form.keys():
                if key.startswith('items[') and '][description]' in key:
                    item_id = key.split('[')[1].split(']')[0]
                    item_keys.add(item_id)

            if not item_keys:
                flash('At least one item is required', 'danger')
                customers = Customer.query.order_by(Customer.name).all()
                banks = Bank.query.order_by(Bank.name).all()
                app_settings = Settings.query.first()
                return render_template('create.html', customers=customers, banks=banks, settings=app_settings)

            # Parse each item
            for item_id in item_keys:
                description = request.form.get(f'items[{item_id}][description]', '')
                quantity = float(request.form.get(f'items[{item_id}][quantity]', 1))
                unit_price = float(request.form.get(f'items[{item_id}][unit_price]', 0))
                subtotal = quantity * unit_price

                items_data.append({
                    'description': description,
                    'quantity': quantity,
                    'unit_price_usd': unit_price,
                    'subtotal_usd': subtotal
                })
                total_usd += subtotal

        except (KeyError, ValueError) as e:
            db.session.rollback()
            logger.error('Error processing invoice form data: %s', e, exc_info=True)
            flash(f'Error processing form data: {str(e)}', 'danger')
            customers = Customer.query.order_by(Customer.name).all()
            banks = Bank.query.order_by(Bank.name).all()
            app_settings = Settings.query.first()
            tracked_currencies = []
            if app_settings and app_settings.tracked_currencies:
                tracked_currencies = [c.strip() for c in app_settings.tracked_currencies.split(',') if c.strip()]
            else:
                tracked_currencies = ['USD', 'EUR', 'GBP', 'CZK']
            return render_template('create.html', customers=customers, banks=banks, settings=app_settings,
                                 tracked_currencies=tracked_currencies)

        # Client details
        client_vat = request.form.get('client_vat', '')
        client_address = request.form.get('client_address', '')
        customer_id = request.form.get('customer_id')
        currency = request.form.get('currency', 'USD')

        invoice_date = datetime.strptime(invoice_date_str, '%Y-%m-%d').date()
        due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else None

        # Get exchange rate and calculate amounts based on currency
        exchange_rate, rate_date = get_exchange_rate(invoice_date_str)

        # Convert amounts based on selected currency
        if currency == 'EUR':
            # If currency is EUR, total_usd is actually in EUR
            amount_eur = total_usd  # The entered amount is already in EUR
            amount_usd = total_usd * exchange_rate  # Convert to USD for storage
        elif currency == 'USD':
            # If currency is USD, keep as is
            amount_usd = total_usd
            amount_eur = convert_usd_to_eur(total_usd, exchange_rate)
        else:
            # For other currencies, treat as USD for now (can be extended)
            amount_usd = total_usd
            amount_eur = convert_usd_to_eur(total_usd, exchange_rate)

        # Handle customer selection or creation
        customer = None
        if customer_id:
            customer = db.session.get(Customer, int(customer_id))
        elif client_name:
            customer = find_customer_by_name(Customer, client_name)
            if not customer:
                customer = Customer(
                    name=client_name,
                    vat_number=client_vat,
                    address=client_address
                )
                db.session.add(customer)
                db.session.flush()
                flash(f'New customer "{client_name}" created!', 'success')
            else:
                flash(f'Using existing customer "{customer.name}"', 'info')

        # Create invoice (keep old fields for backward compatibility)
        invoice = Invoice(
            invoice_number=invoice_number,
            client_name=client_name,
            amount_usd=amount_usd,
            amount_eur=amount_eur,
            exchange_rate=exchange_rate,
            invoice_date=invoice_date,
            due_date=due_date,
            description=items_data[0]['description'] if items_data else '',
            quantity=items_data[0]['quantity'] if items_data else 1,
            unit_price_usd=items_data[0]['unit_price_usd'] if items_data else 0,
            notes=notes,
            status=status,
            currency=currency,
            customer_id=customer.id if customer else None,
            bank_id=int(bank_id) if bank_id else None
        )

        db.session.add(invoice)
        db.session.flush()

        # Create invoice items
        for i, item_data in enumerate(items_data):
            invoice_item = InvoiceItem(
                invoice_id=invoice.id,
                description=item_data['description'],
                quantity=item_data['quantity'],
                unit_price_usd=item_data['unit_price_usd'],
                subtotal_usd=item_data['subtotal_usd']
            )
            db.session.add(invoice_item)

        db.session.commit()
        logger.info('Invoice #%s created: %s, %s %s, %d items',
                    invoice.invoice_number, client_name, currency, total_usd, len(items_data))
        flash('Invoice created successfully!', 'success')
        log_activity('invoice_created', 'invoice',
                     f'#{invoice.invoice_number} for {client_name}')

        # Let modules process their custom fields
        if module_manager:
            module_manager.on_invoice_created(invoice, request)

        # Auto-generate PDF and save via core.storage
        if module_manager:
            try:
                import importlib.util
                from pathlib import Path
                app_settings_obj = Settings.query.first()
                template_name = app_settings_obj.invoice_template if app_settings_obj and app_settings_obj.invoice_template else 'default_template'
                template_path = None
                for tpl in module_manager.get_invoice_templates():
                    if tpl['id'] == template_name:
                        template_path = Path(tpl['path'])
                        break
                if not template_path or not template_path.exists():
                    template_path = Path('invoice_templates') / f'{template_name}.py'
                if not template_path.exists():
                    template_path = Path('invoice_templates') / 'default_template.py'
                spec = importlib.util.spec_from_file_location(template_name, template_path)
                tmpl = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(tmpl)
                import inspect
                sig = inspect.signature(tmpl.generate_invoice_pdf)
                cust = invoice.customer
                if 'Bank' in sig.parameters:
                    buf = tmpl.generate_invoice_pdf(invoice, cust, app_settings_obj, Bank)
                else:
                    buf = tmpl.generate_invoice_pdf(invoice, cust, app_settings_obj)
                pdf_content = buf.read()
                pdf_filename = f'invoice_{invoice.invoice_number}.pdf'
                relative_path = os.path.join('invoices_pdf', pdf_filename)
                new_key = module_manager.core.storage.save(pdf_content, relative_path)
                invoice.pdf_storage_key = new_key
                invoice.pdf_hash = hashlib.sha256(pdf_content).hexdigest()
                db.session.commit()

                # Auto-sign PDF if pdf_signature module requested it
                try:
                    pdf_sig_mod = module_manager.modules.get('pdf_signature')
                    if pdf_sig_mod:
                        sig_rec = pdf_sig_mod.PDFSignatureInvoice.query.filter_by(
                            invoice_id=invoice.id
                        ).first()
                        if sig_rec and (sig_rec.has_visual or sig_rec.has_digital):
                            app.logger.info(
                                'Auto-signing invoice #%s after PDF generation '
                                '(visual=%s, digital=%s)',
                                invoice.invoice_number,
                                sig_rec.has_visual, sig_rec.has_digital,
                            )
                            pdf_sig_mod._sign_invoice_pdf(
                                invoice,
                                visual=sig_rec.has_visual,
                                digital=sig_rec.has_digital,
                            )
                except Exception as sign_err:
                    app.logger.error(
                        'Auto-sign failed for invoice #%s: %s',
                        invoice.invoice_number, sign_err,
                    )
            except Exception as e:
                logger.error('PDF auto-generation failed for invoice #%s: %s',
                             invoice.invoice_number, e, exc_info=True)
                log_activity('invoice_pdf_autogen_failed', 'invoice',
                             f'#{invoice.invoice_number}: {e}')

        logger.info('=== Invoice #%s done ===', invoice.invoice_number)
        return redirect(url_for('index'))

    # GET request - load customers and banks for dropdown
    customers = Customer.query.order_by(Customer.name).all()
    banks = Bank.query.order_by(Bank.name).all()
    app_settings = Settings.query.first()
    default_customer = Customer.query.filter_by(is_default=True).first()
    default_bank = Bank.query.filter_by(is_default=True).first()

    # Get tracked currencies for dropdown
    tracked_currencies = []
    if app_settings and app_settings.tracked_currencies:
        tracked_currencies = [c.strip() for c in app_settings.tracked_currencies.split(',') if c.strip()]
    else:
        tracked_currencies = ['USD', 'EUR', 'GBP', 'CZK']

    return render_template('create.html', customers=customers, banks=banks, settings=app_settings,
                         default_customer=default_customer, default_bank=default_bank,
                         tracked_currencies=tracked_currencies)


@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_invoice(id):
    invoice = Invoice.query.get_or_404(id)

    # Prevent editing paid invoices
    if invoice.status == 'paid':
        flash('Cannot edit invoice with status PAID. Please change status first if needed.', 'danger')
        return redirect(url_for('view_invoice', id=id))

    if request.method == 'POST':
        try:
            invoice.invoice_number = request.form['invoice_number']
            invoice.client_name = request.form['client_name']

            invoice_date_str = request.form['invoice_date']
            due_date_str = request.form.get('due_date')
            invoice.status = request.form['status']
            invoice.notes = request.form.get('notes', '')
            bank_id = request.form.get('bank_id')

            # Parse items from form
            items_data = []
            total_usd = 0

            # Get all item keys from form
            item_keys = set()
            for key in request.form.keys():
                if key.startswith('items[') and '][description]' in key:
                    item_id = key.split('[')[1].split(']')[0]
                    item_keys.add(item_id)

            if not item_keys:
                flash('At least one item is required', 'danger')
                customers = Customer.query.order_by(Customer.name).all()
                banks = Bank.query.order_by(Bank.name).all()
                return render_template('edit.html', invoice=invoice, customers=customers, banks=banks)

            # Parse each item
            for item_id in item_keys:
                description = request.form.get(f'items[{item_id}][description]', '')
                quantity = float(request.form.get(f'items[{item_id}][quantity]', 1))
                unit_price = float(request.form.get(f'items[{item_id}][unit_price]', 0))
                subtotal = quantity * unit_price

                items_data.append({
                    'description': description,
                    'quantity': quantity,
                    'unit_price_usd': unit_price,
                    'subtotal_usd': subtotal
                })
                total_usd += subtotal

        except (KeyError, ValueError) as e:
            flash(f'Error processing form data: {str(e)}', 'danger')
            customers = Customer.query.order_by(Customer.name).all()
            banks = Bank.query.order_by(Bank.name).all()
            return render_template('edit.html', invoice=invoice, customers=customers, banks=banks)

        # Client details
        client_vat = request.form.get('client_vat', '')
        client_address = request.form.get('client_address', '')
        customer_id = request.form.get('customer_id')
        currency = request.form.get('currency', 'USD')

        invoice.invoice_date = datetime.strptime(invoice_date_str, '%Y-%m-%d').date()
        invoice.due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else None

        # Recalculate EUR amount based on currency
        exchange_rate, _ = get_exchange_rate(invoice_date_str)
        invoice.exchange_rate = exchange_rate

        # Convert amounts based on selected currency
        if currency == 'EUR':
            # If currency is EUR, total_usd is actually in EUR
            invoice.amount_eur = total_usd  # The entered amount is already in EUR
            invoice.amount_usd = total_usd * exchange_rate  # Convert to USD for storage
        elif currency == 'USD':
            # If currency is USD, keep as is
            invoice.amount_usd = total_usd
            invoice.amount_eur = convert_usd_to_eur(total_usd, exchange_rate)
        else:
            # For other currencies, treat as USD for now
            invoice.amount_usd = total_usd
            invoice.amount_eur = convert_usd_to_eur(total_usd, exchange_rate)

        invoice.currency = currency

        # Update old fields for backward compatibility
        if items_data:
            invoice.description = items_data[0]['description']
            invoice.quantity = items_data[0]['quantity']
            invoice.unit_price_usd = items_data[0]['unit_price_usd']

        # Update bank
        invoice.bank_id = int(bank_id) if bank_id else None

        # Handle customer selection or creation
        if customer_id:
            # Use existing customer
            invoice.customer_id = int(customer_id)
        elif invoice.client_name:
            # Check if customer exists by normalized name
            customer = find_customer_by_name(Customer, invoice.client_name)
            if not customer:
                # Create new customer
                customer = Customer(
                    name=invoice.client_name,
                    vat_number=client_vat,
                    address=client_address
                )
                db.session.add(customer)
                db.session.flush()
                invoice.customer_id = customer.id
                flash(f'New customer "{invoice.client_name}" created!', 'success')
            else:
                invoice.customer_id = customer.id
                flash(f'Using existing customer "{customer.name}"', 'info')

        # Delete old items and create new ones
        InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()

        for item_data in items_data:
            invoice_item = InvoiceItem(
                invoice_id=invoice.id,
                description=item_data['description'],
                quantity=item_data['quantity'],
                unit_price_usd=item_data['unit_price_usd'],
                subtotal_usd=item_data['subtotal_usd']
            )
            db.session.add(invoice_item)

        db.session.commit()
        flash('Invoice updated successfully!', 'success')
        log_activity('invoice_updated', 'invoice', f'#{invoice.invoice_number}')

        # Let modules process their custom fields
        if module_manager:
            module_manager.on_invoice_updated(invoice, request)

        return redirect(url_for('index'))

    # GET request - load customers and banks for dropdown
    customers = Customer.query.order_by(Customer.name).all()
    banks = Bank.query.order_by(Bank.name).all()

    # Get tracked currencies for dropdown
    app_settings = Settings.query.first()
    tracked_currencies = []
    if app_settings and app_settings.tracked_currencies:
        tracked_currencies = [c.strip() for c in app_settings.tracked_currencies.split(',') if c.strip()]
    else:
        tracked_currencies = ['USD', 'EUR', 'GBP', 'CZK']

    return render_template('edit.html', invoice=invoice, customers=customers, banks=banks,
                         tracked_currencies=tracked_currencies)




@app.route('/view/<int:id>')
@login_required
def view_invoice(id):
    invoice = Invoice.query.get_or_404(id)

    # Check if invoice is paid and has hash — verify integrity via storage
    hash_mismatch = False
    if invoice.status == 'paid' and invoice.pdf_hash and module_manager:
        svc = module_manager.core.invoice_service
        result = svc.get_pdf(invoice)
        if result:
            file_bytes, _ = result
            current_hash = hashlib.sha256(file_bytes).hexdigest()
            if current_hash != invoice.pdf_hash:
                hash_mismatch = True

    customers = Customer.query.order_by(Customer.name).all()
    banks = Bank.query.order_by(Bank.name).all()
    return render_template('view.html', invoice=invoice, customers=customers, banks=banks, hash_mismatch=hash_mismatch)


@app.route('/delete/<int:id>')
@login_required
def delete_invoice(id):
    invoice = Invoice.query.get_or_404(id)

    # Prevent deleting paid invoices
    if invoice.status == 'paid':
        flash('Cannot delete invoice with status PAID. Please change status first if needed.', 'danger')
        return redirect(url_for('index'))

    # Delete PDF from storage
    if module_manager:
        svc = module_manager.core.invoice_service
        key = svc._resolve_storage_key(invoice)
        if key:
            try:
                module_manager.core.storage.delete(key)
                log_activity('invoice_pdf_deleted', 'invoice',
                             f'PDF deleted for #{invoice.invoice_number}')
            except Exception as e:
                logger.error('Failed to delete PDF for #%s: %s', invoice.invoice_number, e)

    db.session.delete(invoice)
    db.session.commit()
    log_activity('invoice_deleted', 'invoice', f'#{invoice.invoice_number}')
    flash('Invoice deleted successfully!', 'success')
    return redirect(url_for('index'))


@app.route('/generate-pdf/<int:id>')
@login_required
def generate_pdf(id):
    """Generate invoice PDF using the configured template"""
    import importlib.util
    from pathlib import Path

    invoice = Invoice.query.get_or_404(id)
    customer = invoice.customer
    app_settings = Settings.query.first()

    # Check if user wants to force regeneration
    force_regenerate = request.args.get('force') == 'true'

    pdf_filename = f'invoice_{invoice.invoice_number}.pdf'
    storage = module_manager.core.storage if module_manager else None
    svc = module_manager.core.invoice_service if module_manager else None

    # Check if PDF already exists in storage
    file_exists = False
    if svc:
        file_exists = svc.has_pdf(invoice)

    # Case 1: File exists and has hash in DB — serve existing
    if file_exists and invoice.pdf_hash and not force_regenerate:
        result = svc.get_pdf(invoice)
        if result:
            file_bytes, _ = result
            existing_hash = hashlib.sha256(file_bytes).hexdigest()
            if existing_hash == invoice.pdf_hash:
                return send_file(
                    io.BytesIO(file_bytes), as_attachment=True,
                    download_name=pdf_filename, mimetype='application/pdf')
            else:
                return render_template('confirm_regenerate.html', invoice=invoice)

    # Case 2/3: Generate new PDF
    template_name = app_settings.invoice_template if app_settings and app_settings.invoice_template else 'default_template'

    # Resolve template path: check module_manager registry first, then core directory
    template_path = None
    if module_manager:
        for tpl in module_manager.get_invoice_templates():
            if tpl['id'] == template_name:
                template_path = Path(tpl['path'])
                break

    if not template_path or not template_path.exists():
        template_path = Path('invoice_templates') / f'{template_name}.py'

    if not template_path.exists():
        flash(f'Invoice template "{template_name}" not found. Using default template.', 'warning')
        template_path = Path('invoice_templates') / 'default_template.py'

    try:
        spec = importlib.util.spec_from_file_location(template_name, template_path)
        template_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(template_module)

        import inspect
        sig = inspect.signature(template_module.generate_invoice_pdf)
        if 'Bank' in sig.parameters:
            buffer = template_module.generate_invoice_pdf(invoice, customer, app_settings, Bank)
        else:
            buffer = template_module.generate_invoice_pdf(invoice, customer, app_settings)

        pdf_content = buffer.read()
        buffer.seek(0)

        pdf_hash = hashlib.sha256(pdf_content).hexdigest()

        # Save via core.storage
        if storage:
            relative_path = os.path.join('invoices_pdf', pdf_filename)
            new_key = storage.save(pdf_content, relative_path)
            if hasattr(invoice, 'pdf_storage_key'):
                invoice.pdf_storage_key = new_key
        else:
            # Fallback: direct write (no module_manager)
            pdf_dir = os.path.join(os.path.dirname(__file__), 'invoices_pdf')
            os.makedirs(pdf_dir, exist_ok=True)
            with open(os.path.join(pdf_dir, pdf_filename), 'wb') as f:
                f.write(pdf_content)

        if invoice.status == 'paid':
            invoice.pdf_hash = pdf_hash
        db.session.commit()

        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=pdf_filename, mimetype='application/pdf')

    except Exception as e:
        flash(f'Error generating PDF: {str(e)}', 'danger')
        return redirect(url_for('index'))


@app.route('/preview-pdf/<int:id>')
@login_required
def preview_invoice_pdf(id):
    """Preview invoice PDF inline in the browser"""
    invoice = Invoice.query.get_or_404(id)
    if not module_manager:
        flash('Module manager not available.', 'danger')
        return redirect(url_for('index'))

    svc = module_manager.core.invoice_service
    result = svc.get_pdf(invoice)

    if result:
        file_bytes, _ = result
        return Response(file_bytes, mimetype='application/pdf',
                        headers={'Content-Disposition': 'inline'})

    # PDF not in storage — generate on the fly for preview
    try:
        import importlib.util
        from pathlib import Path

        app_settings = Settings.query.first()
        customer = invoice.customer
        template_name = (app_settings.invoice_template
                         if app_settings and app_settings.invoice_template
                         else 'default_template')

        template_path = None
        for tpl in module_manager.get_invoice_templates():
            if tpl['id'] == template_name:
                template_path = Path(tpl['path'])
                break
        if not template_path or not template_path.exists():
            template_path = Path('invoice_templates') / f'{template_name}.py'
        if not template_path.exists():
            template_path = Path('invoice_templates') / 'default_template.py'

        spec = importlib.util.spec_from_file_location(template_name, template_path)
        tmpl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tmpl)

        import inspect
        sig = inspect.signature(tmpl.generate_invoice_pdf)
        if 'Bank' in sig.parameters:
            buf = tmpl.generate_invoice_pdf(invoice, customer, app_settings, Bank)
        else:
            buf = tmpl.generate_invoice_pdf(invoice, customer, app_settings)

        pdf_content = buf.read()
        return Response(pdf_content, mimetype='application/pdf',
                        headers={'Content-Disposition': 'inline'})
    except Exception as e:
        flash(f'Error generating PDF preview: {e}', 'danger')
        return redirect(url_for('index'))








@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Configure business and personal settings"""
    # Get or create settings (only one record should exist)
    app_settings = Settings.query.first()

    # Get all banks, customers, and contractors
    banks = Bank.query.order_by(Bank.name).all()
    customers = Customer.query.order_by(Customer.name).all()
    contractors = Contractor.query.order_by(Contractor.name).all()


    if request.method == 'POST':
        if not app_settings:
            app_settings = Settings()
            db.session.add(app_settings)

        # Update personal/business info (only from core tabs, not module tabs)
        active_tab = request.form.get('_settings_tab', '')
        if active_tab in ('general', 'business', 'defaults', ''):
            field_map = {
                'business_name': 'business_name',
                'owner_name': 'owner_name',
                'vat_number': 'vat_number',
                'nie_number': 'nie_number',
                'address': 'address',
                'city': 'city',
                'postal_code': 'postal_code',
                'country': 'country',
                'phone': 'phone',
                'email': 'email',
                'default_payment_terms': 'default_payment_terms',
                'default_description': 'default_description',
                'default_notes': 'default_notes',
                'default_currency': 'default_currency',
                'tracked_currencies': 'tracked_currencies',
                'base_currency': 'base_currency',
                'report_template': 'report_template',
                'invoice_template': 'invoice_template',
            }
            for form_key, attr in field_map.items():
                if form_key in request.form:
                    setattr(app_settings, attr, request.form.get(form_key, ''))

            # Update dashboard settings (checkboxes — only from general tab)
            if active_tab == 'general':
                app_settings.show_currency_panel = request.form.get('show_currency_panel') == 'on'
                app_settings.show_tax_panel = request.form.get('show_tax_panel') == 'on'

                # Tax rates
                if 'default_vat_rate' in request.form:
                    try:
                        app_settings.default_vat_rate = float(request.form.get('default_vat_rate', 21))
                    except (ValueError, TypeError):
                        app_settings.default_vat_rate = 21.0
                if 'default_irpf_rate' in request.form:
                    try:
                        app_settings.default_irpf_rate = float(request.form.get('default_irpf_rate', 20))
                    except (ValueError, TypeError):
                        app_settings.default_irpf_rate = 20.0

            # Update log settings (only if log fields present in form)
            if 'log_retention_days' in request.form:
                app_settings.log_storage = request.form.get('log_storage', 'file')
                try:
                    app_settings.log_retention_days = int(request.form.get('log_retention_days', '30'))
                except ValueError:
                    app_settings.log_retention_days = 30
                app_settings.log_use_external_storage = request.form.get('log_use_external_storage') == 'on'


        # Let modules handle their settings
        if module_manager:
            module_manager.save_settings(app_settings, request.form)

        db.session.commit()
        flash('Settings saved successfully!', 'success')
        # Re-apply log settings BEFORE logging (so the correct logger is active)
        if module_manager and 'log_retention_days' in request.form:
            _apply_log_settings(app_settings, module_manager)
        log_activity('settings_updated', 'settings')
        return redirect(url_for('settings'))

    # Get module states for the Modules tab
    module_states = module_manager.get_all_module_states() if module_manager else []
    module_settings_panels = module_manager.get_settings_html(app_settings) if module_manager else []
    field_labels = module_manager.get_field_labels() if module_manager else {}

    return render_template('settings.html', settings=app_settings, banks=banks, customers=customers, contractors=contractors, module_states=module_states, module_settings_panels=module_settings_panels, field_labels=field_labels)


@app.route('/settings/banks/add', methods=['GET', 'POST'])
@login_required
def add_bank():
    """Add a new bank"""
    if request.method == 'POST':
        name = request.form.get('name', '')
        iban = request.form.get('iban', '')
        swift = request.form.get('swift', '')
        bank_name = request.form.get('bank_name', '')
        is_default = request.form.get('is_default') == 'on'

        if not name or not iban:
            flash('Bank name and IBAN are required', 'danger')
            return redirect(url_for('settings') + '#banks')

        # If this is set as default, unset other defaults
        if is_default:
            Bank.query.update({Bank.is_default: False})

        new_bank = Bank(
            name=name,
            iban=iban,
            swift=swift,
            bank_name=bank_name,
            is_default=is_default
        )
        db.session.add(new_bank)
        db.session.commit()

        flash(f'Bank "{name}" added successfully!', 'success')
        return redirect(url_for('settings') + '#banks')

    return render_template('bank_form.html', bank=None)


@app.route('/settings/banks/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_bank(id):
    """Edit a bank"""
    bank = Bank.query.get_or_404(id)

    if request.method == 'POST':
        bank.name = request.form.get('name', '')
        bank.iban = request.form.get('iban', '')
        bank.swift = request.form.get('swift', '')
        bank.bank_name = request.form.get('bank_name', '')
        is_default = request.form.get('is_default') == 'on'

        if not bank.name or not bank.iban:
            flash('Bank name and IBAN are required', 'danger')
            return render_template('bank_form.html', bank=bank)

        # If this is set as default, unset other defaults
        if is_default and not bank.is_default:
            Bank.query.update({Bank.is_default: False})

        bank.is_default = is_default
        db.session.commit()

        flash(f'Bank "{bank.name}" updated successfully!', 'success')
        return redirect(url_for('settings') + '#banks')

    return render_template('bank_form.html', bank=bank)


@app.route('/settings/banks/<int:id>/delete')
@login_required
def delete_bank(id):
    """Delete a bank"""
    bank = Bank.query.get_or_404(id)

    # Check if any invoices use this bank
    invoice_count = Invoice.query.filter_by(bank_id=id).count()
    if invoice_count > 0:
        flash(f'Cannot delete bank "{bank.name}" - it is used by {invoice_count} invoice(s)', 'danger')
        return redirect(url_for('settings') + '#banks')

    db.session.delete(bank)
    db.session.commit()
    flash(f'Bank "{bank.name}" deleted successfully!', 'success')
    return redirect(url_for('settings') + '#banks')


@app.route('/settings/banks/<int:id>/set-default')
@login_required
def set_default_bank(id):
    """Set a bank as default"""
    # Unset all defaults
    Bank.query.update({Bank.is_default: False})

    # Set this one as default
    bank = Bank.query.get_or_404(id)
    bank.is_default = True
    db.session.commit()

    flash(f'Bank "{bank.name}" set as default!', 'success')
    return redirect(url_for('settings') + '#banks')


@app.route('/settings/customers/<int:id>/set-default')
@login_required
def set_default_customer(id):
    """Set a customer as default"""
    # Unset all defaults
    Customer.query.update({Customer.is_default: False})

    # Set this one as default
    customer = Customer.query.get_or_404(id)
    customer.is_default = True
    db.session.commit()

    flash(f'Customer "{customer.name}" set as default!', 'success')
    return redirect(url_for('settings') + '#customers')





# Backup routes are now handled by the backup module
# Legacy routes removed - functionality moved to modules/backup/


# ============================================================================
# BLUEPRINTS REGISTRATION
# ============================================================================

# Register Auth Blueprint (must be first)
from auth_routes import auth_bp
app.register_blueprint(auth_bp)

# Expenses are now handled by the expenses module
# Legacy routes removed - functionality moved to modules/expenses/

# Reports are now handled by the reports module
# Legacy routes removed - functionality moved to modules/reports/

# Tax Forms and SS Payments are now handled by the tax_management module
# Legacy routes removed - functionality moved to modules/tax_management/

# Documents are now handled by the documents module
# Legacy routes removed - functionality moved to modules/documents/


# ============================================================================
# CONTRACTORS ROUTES
# ============================================================================

@app.route('/contractors/create', methods=['GET', 'POST'])
@login_required
def create_contractor():
    """Create a new contractor"""
    if request.method == 'POST':
        try:
            contractor = Contractor(
                name=request.form['name'],
                vat_number=request.form.get('vat_number'),
                address=request.form.get('address'),
                city=request.form.get('city'),
                postal_code=request.form.get('postal_code'),
                country=request.form.get('country'),
                email=request.form.get('email'),
                phone=request.form.get('phone'),
                notes=request.form.get('notes')
            )

            db.session.add(contractor)
            db.session.commit()

            flash('Contractor created successfully!', 'success')
            return redirect(url_for('settings') + '#contractors')
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating contractor: {str(e)}', 'danger')

    return render_template('contractor_form.html', contractor=None)


@app.route('/contractors/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_contractor(id):
    """Edit a contractor"""
    contractor = Contractor.query.get_or_404(id)

    if request.method == 'POST':
        try:
            contractor.name = request.form['name']
            contractor.vat_number = request.form.get('vat_number')
            contractor.address = request.form.get('address')
            contractor.city = request.form.get('city')
            contractor.postal_code = request.form.get('postal_code')
            contractor.country = request.form.get('country')
            contractor.email = request.form.get('email')
            contractor.phone = request.form.get('phone')
            contractor.notes = request.form.get('notes')

            db.session.commit()
            flash('Contractor updated successfully!', 'success')
            return redirect(url_for('settings') + '#contractors')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating contractor: {str(e)}', 'danger')

    return render_template('contractor_form.html', contractor=contractor)


@app.route('/contractors/delete/<int:id>', methods=['POST'])
@login_required
def delete_contractor(id):
    """Delete a contractor"""
    contractor = Contractor.query.get_or_404(id)

    # Check if contractor has expenses
    if contractor.expenses:
        flash('Cannot delete contractor with existing expenses', 'danger')
        return redirect(url_for('settings') + '#contractors')

    try:
        db.session.delete(contractor)
        db.session.commit()
        flash('Contractor deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting contractor: {str(e)}', 'danger')

    return redirect(url_for('settings') + '#contractors')


# ============================================================================
# SYSTEM LOGS
# ============================================================================

@app.route('/logs')
@login_required
def system_logs():
    """View system activity logs"""
    cat = request.args.get('category', '')
    search = request.args.get('search', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = int(request.args.get('page', 1))
    per_page = 50

    logger = module_manager.core.activity_logger if module_manager else None
    entries = []
    categories = []
    if logger and hasattr(logger, 'get_entries'):
        entries = logger.get_entries(
            limit=per_page, offset=(page - 1) * per_page,
            category=cat or None,
            date_from=date_from or None,
            date_to=date_to or None,
            search=search or None)
        if hasattr(logger, 'get_categories'):
            categories = logger.get_categories()

    return render_template('logs.html',
                           entries=entries,
                           categories=categories,
                           filters={'category': cat, 'search': search,
                                    'date_from': date_from, 'date_to': date_to},
                           page=page, per_page=per_page)


@app.route('/scheduler')
@login_required
def scheduled_tasks():
    """View registered scheduled tasks"""
    jobs = []
    if module_manager:
        jobs = module_manager.core.scheduler.get_jobs()
    return render_template('scheduler.html', jobs=jobs)


# ============================================================================
# MODULE MANAGEMENT ROUTES
# ============================================================================

@app.route('/modules/toggle/<module_id>', methods=['POST'])
@login_required
def toggle_module(module_id):
    """Enable or disable a module"""
    global module_manager
    if not module_manager:
        flash('Module system not initialized', 'danger')
        return redirect(url_for('settings') + '#modules')

    action = request.form.get('action', 'enable')
    if action == 'enable':
        module_manager.enable_module(module_id)
        log_activity('module_enabled', 'system', module_id)
        flash(f'Module enabled. Please restart the application for changes to take effect.', 'success')
    else:
        module_manager.disable_module(module_id)
        log_activity('module_disabled', 'system', module_id)
        flash(f'Module disabled. Please restart the application for changes to take effect.', 'success')

    return redirect(url_for('settings') + '#modules')


# ============================================================================
# MODULE MANAGER INITIALIZATION
# ============================================================================

def init_module_manager():
    """Initialize the module manager, discover and load enabled modules"""
    global module_manager
    from module_manager import ModuleManager

    module_manager = ModuleManager(app, db)
    module_manager.init_db()
    module_manager.discover_modules()
    module_manager.load_enabled_modules()

    # Start the task scheduler (only in the main worker, not the reloader)
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        module_manager.core.scheduler.start()

    # Make module_manager and version available in templates
    app.jinja_env.globals['module_manager'] = module_manager
    app.jinja_env.globals['app_version'] = APP_VERSION
    app.jinja_env.globals['current_year'] = datetime.now().year

    return module_manager


def _apply_log_settings(s, mgr):
    """Apply log path / retention / storage type from Settings to the core logger."""
    from module_manager import FileActivityLogger, DbActivityLogger
    storage = getattr(s, 'log_storage', 'file') or 'file'
    current = mgr.core.activity_logger

    if storage == 'db':
        if not isinstance(current, DbActivityLogger):
            mgr.core.set_activity_logger(DbActivityLogger(db, app))
    else:
        if not isinstance(current, FileActivityLogger):
            mgr.core.set_activity_logger(FileActivityLogger(app.root_path))

    logger = mgr.core.activity_logger
    if hasattr(logger, 'cleanup') and s.log_retention_days is not None:
        logger.cleanup(s.log_retention_days)

    # Register daily log cleanup job
    retention = s.log_retention_days if s.log_retention_days else 0
    if retention > 0:
        def _log_cleanup():
            mgr.core.activity_logger.cleanup(retention)
        mgr.core.scheduler.add_job(
            job_id='core.log_cleanup',
            func=_log_cleanup,
            job_type='daily',
            time_str='04:00',
            description=f'Log cleanup (retain {retention} days)',
        )
    else:
        mgr.core.scheduler.remove_job('core.log_cleanup')


if __name__ == '__main__':
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    app.logger.setLevel(logging.INFO)

    with app.app_context():
        db.create_all()

        # Migrate: add social_security_monthly column if missing
        from sqlalchemy import inspect as sa_inspect, text
        inspector = sa_inspect(db.engine)
        columns = [c['name'] for c in inspector.get_columns('settings')]
        if 'social_security_monthly' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE settings ADD COLUMN social_security_monthly FLOAT DEFAULT 0.0'))
                conn.commit()

        # Migrate: add log settings columns if missing
        columns = [c['name'] for c in inspector.get_columns('settings')]
        for col, typedef in [('log_path', "VARCHAR(500) DEFAULT ''"),
                             ('log_retention_days', 'INTEGER DEFAULT 30'),
                             ('log_use_external_storage', 'BOOLEAN DEFAULT 0'),
                             ('log_storage', "VARCHAR(10) DEFAULT 'file'")]:
            if col not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text(f'ALTER TABLE settings ADD COLUMN {col} {typedef}'))
                    conn.commit()

        # Migrate: add pdf_storage_key column to invoice if missing
        inv_columns = [c['name'] for c in inspector.get_columns('invoice')]
        if 'pdf_storage_key' not in inv_columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE invoice ADD COLUMN pdf_storage_key VARCHAR(500)'))
                conn.commit()

        # Migrate: add tax rate columns if missing
        columns = [c['name'] for c in inspector.get_columns('settings')]
        for col, typedef in [('default_vat_rate', 'FLOAT DEFAULT 21.0'),
                             ('default_irpf_rate', 'FLOAT DEFAULT 20.0')]:
            if col not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text(f'ALTER TABLE settings ADD COLUMN {col} {typedef}'))
                    conn.commit()

        # Initialize module system
        mgr = init_module_manager()

        # Ensure default Settings row exists (first run)
        s = Settings.query.first()
        if not s:
            s = Settings(
                tracked_currencies='USD,EUR,GBP,CZK',
                base_currency='EUR',
                default_currency='EUR',
                default_vat_rate=21.0,
                default_irpf_rate=20.0,
            )
            db.session.add(s)
            db.session.commit()
            logger.info('Created default Settings row (first run)')

        # Apply log settings from DB
        if s and mgr:
            _apply_log_settings(s, mgr)

    app.run(debug=os.environ.get('FLASK_DEBUG', '1') == '1')
