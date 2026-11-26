"""Invoice routes and the invoice lifecycle: list, create, edit, view, delete, issue, mark paid, annul, rectify, PDF.

Routes register on the core app under their own endpoint names
(`url_for('dashboard')`, …). They import from `contaautonomo.web`, never from
`contaautonomo.app`, and read `state.module_manager` when a request runs."""
import hashlib
import io
import os
from datetime import datetime

from flask import (Response, flash, jsonify, redirect, render_template, request, send_file,
                   url_for)
from sqlalchemy.exc import IntegrityError

from contaautonomo import paths
from contaautonomo import state
from contaautonomo.web import (app, db, logger, log_activity)
from contaautonomo.security.login import login_required
from contaautonomo.models import (Bank, Customer, Invoice, InvoiceItem, Settings, find_customer_by_name)
from contaautonomo.utils.constants import (INITIAL_INVOICE_STATUS, LOCKED_INVOICE_STATUSES,
                                           invoice_locked, is_legacy_settled)
from contaautonomo.utils.currency_converter import convert_usd_to_eur, get_currency_symbol
from contaautonomo.utils.logsafe import clean
from contaautonomo.utils.timeutil import utcnow


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
    stmt = select(Invoice).where(Invoice.id.isnot(None))

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


# --- F2: Invoice lifecycle -------------------------------------------------- #
# The lock rule and the initial status live in constants.py (LOCKED_INVOICE_STATUSES,
# INITIAL_INVOICE_STATUS, invoice_locked, is_legacy_settled).
_invoice_locked = invoice_locked
_is_legacy_settled = is_legacy_settled


def _next_sequence_for_series(series):
    """Next per-series sequence, derived from MAX (survives restart, no memory)."""
    from sqlalchemy import func
    max_seq = db.session.query(func.max(Invoice.sequence_number)).filter(
        Invoice.series == series).scalar()
    return (max_seq or 0) + 1


def _build_customer_snapshot(invoice):
    """Freeze the customer's fiscal identity as JSON at issue time."""
    import json as _json
    c = invoice.customer
    return _json.dumps({
        'name': c.name if c else invoice.client_name,
        'vat_number': c.vat_number if c else None,
        'country': c.country if c else None,
        'tax_type': c.tax_type if c else None,
    })


def _discard_generated_pdf(invoice):
    """Forget a PDF the app generated for this invoice, so the next preview or
    download rebuilds it from the current data (number, snapshot, QR).

    PDFs attached by the user (pdf_generated False) and PDFs stored before the
    flag existed (None) are kept.
    """
    if not getattr(invoice, 'pdf_generated', None) or not invoice.pdf_storage_key:
        return
    if state.module_manager:
        try:
            state.module_manager.core.delete_file(invoice.pdf_storage_key)
        except OSError:
            logger.warning('Could not delete the old PDF of invoice %s', invoice.id,
                           exc_info=True)
    invoice.pdf_storage_key = None
    invoice.pdf_hash = None
    invoice.pdf_generated = None


def _issue_invoice(invoice, series=None, request_obj=None):
    """Assign series+sequence, freeze the fiscal snapshot, mark issued.

    Transactional: the on_invoice_issued hooks run before commit, so a vetoing
    module (or a unique-number collision) rolls the whole transition back with
    no sequence gap. Raises ValueError on failure.
    """
    from sqlalchemy.exc import IntegrityError

    if _invoice_locked(invoice):
        raise ValueError('Invoice is already issued')

    app_settings = Settings.query.first()
    default_vat = (app_settings.default_vat_rate
                   if app_settings and app_settings.default_vat_rate is not None else 21.0)
    tax_type = invoice.customer.tax_type if invoice.customer else None
    rate = default_vat if tax_type == 'standard' else 0.0
    base = invoice.amount_eur or 0.0

    invoice.snap_vat_rate = rate
    invoice.snap_taxable_base = base
    invoice.snap_vat_amount = round(base * rate / 100.0, 2)
    invoice.snap_customer = _build_customer_snapshot(invoice)
    for item in invoice.items:
        if item.vat_rate is None:
            item.vat_rate = rate

    series = series or invoice.series or str(invoice.invoice_date.year)
    invoice.series = series

    last_error = None
    for _ in range(5):
        seq = _next_sequence_for_series(series)
        invoice.sequence_number = seq
        invoice.invoice_number = f'{series}/{seq:04d}'
        invoice.status = 'issued'
        invoice.issued_at = utcnow()
        try:
            db.session.flush()  # surfaces unique-number collisions early
            break
        except IntegrityError as e:
            last_error = e
            db.session.rollback()
            continue
    else:
        raise ValueError(f'Could not assign a unique invoice number: {last_error}')

    # The draft PDF shows the draft number and no QR: rebuild it on next view.
    _discard_generated_pdf(invoice)

    # Issue-blocking hooks (may veto). Runs before commit -> abort is clean.
    if state.module_manager:
        state.module_manager.on_invoice_issued(invoice, request_obj)

    db.session.commit()
    return invoice


@app.route('/issue/<int:id>', methods=['POST'])
@login_required
def issue_invoice(id):
    invoice = db.get_or_404(Invoice, id)
    if _invoice_locked(invoice):
        flash('Invoice is already issued.', 'warning')
        return redirect(url_for('view_invoice', id=id))
    try:
        _issue_invoice(invoice, series=request.form.get('series') or None,
                       request_obj=request)
        log_activity('invoice_issued', 'invoice', f'#{invoice.invoice_number}')
        flash(f'Invoice issued as {invoice.invoice_number}. It is now locked; '
              f'use Rectify to change it.', 'success')
    except Exception as e:
        db.session.rollback()
        # Exception text and route params may carry user input — keep both out
        # of the log line (log injection); invoice.id is DB-derived and the
        # traceback via exc_info has the details.
        logger.error('Issue failed for invoice %s', invoice.id, exc_info=True)
        flash(f'Could not issue invoice: {e}', 'danger')
    return redirect(url_for('view_invoice', id=id))


def _mark_invoice_paid(invoice):
    """issued/pending -> paid. The one legal forward transition on a locked
    invoice (F2). Shared by the mark-paid route and bank reconciliation (F12).
    Raises ValueError when the transition is not allowed."""
    if invoice.status == 'paid':
        return invoice
    if invoice.status not in ('issued', 'pending'):
        raise ValueError(f'Cannot mark a {invoice.status} invoice as paid')
    invoice.status = 'paid'
    db.session.commit()
    log_activity('invoice_paid', 'invoice', f'#{invoice.invoice_number}')
    return invoice


@app.route('/mark-paid/<int:id>', methods=['POST'])
@login_required
def mark_invoice_paid(id):
    invoice = db.get_or_404(Invoice, id)
    try:
        _mark_invoice_paid(invoice)
        flash(f'Invoice {invoice.invoice_number} marked as paid.', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    except Exception:
        db.session.rollback()
        logger.error('Mark-paid failed for invoice %s', invoice.id, exc_info=True)
        flash('Could not mark invoice as paid.', 'danger')
    return redirect(url_for('view_invoice', id=id))


@app.route('/annul/<int:id>', methods=['POST'])
@login_required
def annul_invoice(id):
    invoice = db.get_or_404(Invoice, id)
    if invoice.status not in LOCKED_INVOICE_STATUSES:
        flash('Only issued invoices can be annulled.', 'warning')
        return redirect(url_for('view_invoice', id=id))
    try:
        invoice.status = 'cancelled'
        db.session.flush()
        if state.module_manager:
            state.module_manager.on_invoice_annulled(invoice, request)
        db.session.commit()
        log_activity('invoice_annulled', 'invoice', f'#{invoice.invoice_number}')
        flash('Invoice annulled — retained for audit, excluded from income.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error('Annul failed for invoice %s', invoice.id, exc_info=True)
        flash(f'Could not annul invoice: {e}', 'danger')
    return redirect(url_for('view_invoice', id=id))


@app.route('/rectify/<int:id>', methods=['POST'])
@login_required
def rectify_invoice(id):
    original = db.get_or_404(Invoice, id)
    if original.status not in LOCKED_INVOICE_STATUSES:
        flash('Only issued invoices can be rectified.', 'warning')
        return redirect(url_for('view_invoice', id=id))
    rtype = request.form.get('rectification_type', 'sustitucion')
    if rtype not in ('sustitucion', 'diferencias'):
        rtype = 'sustitucion'
    year = utcnow().year
    try:
        # New DRAFT pre-filled from the original; original is never mutated.
        draft = Invoice(
            invoice_number=f'RECT-{original.id}-{utcnow().strftime("%Y%m%d%H%M%S")}',
            client_name=original.client_name,
            amount_usd=original.amount_usd,
            amount_eur=original.amount_eur,
            exchange_rate=original.exchange_rate,
            invoice_date=utcnow().date(),
            due_date=original.due_date,
            description=original.description,
            quantity=original.quantity,
            unit_price_usd=original.unit_price_usd,
            notes=original.notes,
            status='draft',
            currency=original.currency,
            payment_method=original.payment_method,
            customer_id=original.customer_id,
            bank_id=original.bank_id,
            series=f'R{year}',
            rectifies_invoice_id=original.id,
            rectification_type=rtype,
        )
        db.session.add(draft)
        db.session.flush()
        for item in original.items:
            db.session.add(InvoiceItem(
                invoice_id=draft.id, description=item.description,
                quantity=item.quantity, unit_price_usd=item.unit_price_usd,
                subtotal_usd=item.subtotal_usd, vat_rate=item.vat_rate))
        if state.module_manager:
            state.module_manager.on_invoice_rectified(draft, original, request)
        db.session.commit()
        log_activity('invoice_rectified', 'invoice',
                     f'#{original.invoice_number} -> rectificative draft #{draft.id}')
        flash('Rectificative draft created. Edit it, then issue.', 'success')
        return redirect(url_for('edit_invoice', id=draft.id))
    except Exception as e:
        db.session.rollback()
        logger.error('Rectify failed for invoice %s', original.id, exc_info=True)
        flash(f'Could not create rectificative: {e}', 'danger')
        return redirect(url_for('view_invoice', id=id))


class NoExchangeRate(ValueError):
    """No exchange rate to EUR is available for the invoice currency and date."""


def invoice_amounts(total, currency, date_str):
    """Convert an entered invoice total into the stored amount columns.

    Returns (amount_usd, amount_eur, exchange_rate). Legacy column naming:
    - EUR invoice: amount_eur = total, amount_usd = USD conversion.
    - Any other currency: amount_usd = total in the invoice currency (the
      column name is historical), amount_eur = EUR conversion — the tax base.
    exchange_rate is the USD->EUR rate for EUR/USD invoices and the
    <currency>->EUR rate for other currencies, so amount_eur =
    amount_usd * exchange_rate holds for every non-EUR invoice.

    Raises NoExchangeRate when the currency cannot be converted to EUR.
    """
    if currency in ('EUR', 'USD'):
        # Looked up at call time (not the module-level name) so tests can stub it.
        from contaautonomo.utils.currency_converter import get_exchange_rate as current_rate
        exchange_rate, _ = current_rate(date_str)
        if currency == 'EUR':
            return total * exchange_rate, total, exchange_rate
        return total, convert_usd_to_eur(total, exchange_rate), exchange_rate
    if not state.module_manager:
        raise NoExchangeRate(f'No currency service to convert {currency}')
    try:
        amount_eur, rate, _ = state.module_manager.core.currency_service.convert(
            total, currency, 'EUR', date_str)
    except Exception as e:
        raise NoExchangeRate(f'No {currency}->EUR rate for {date_str}') from e
    if not rate or rate <= 0:
        raise NoExchangeRate(f'No {currency}->EUR rate for {date_str}')
    return total, amount_eur, rate


def _invoice_with_number(number, exclude_id=None):
    """The invoice that already has `number` (other than `exclude_id`), or None."""
    query = Invoice.query.filter(Invoice.invoice_number == number)
    if exclude_id is not None:
        query = query.filter(Invoice.id != exclude_id)
    return query.first()


def _number_taken_message(number, other):
    when = other.invoice_date.strftime('%d/%m/%Y') if other.invoice_date else 'no date'
    return (f'Invoice number "{number}" is already used by the invoice for '
            f'{other.client_name} ({when}). Choose another number.')


@app.route('/invoices/check-number', methods=['POST'])
@login_required
def check_invoice_number():
    """JSON {taken, message}: whether an invoice number is already used; the
    create and edit forms ask before they are sent."""
    data = request.get_json(silent=True) or {}
    number = str(data.get('invoice_number') or '').strip()[:100]
    try:
        exclude_id = int(data['exclude_id']) if data.get('exclude_id') else None
    except (TypeError, ValueError):
        exclude_id = None
    other = _invoice_with_number(number, exclude_id) if number else None
    if other is None:
        return jsonify({'taken': False, 'message': ''})
    return jsonify({'taken': True, 'message': _number_taken_message(number, other)})


@app.route('/create', methods=['GET', 'POST'])
@login_required
def create_invoice():
    if request.method == 'POST':
        # The number is unique: refuse a used one before anything is written.
        taken = _invoice_with_number((request.form.get('invoice_number') or '').strip())
        if taken is not None:
            flash(_number_taken_message(request.form['invoice_number'].strip(), taken), 'danger')
            return redirect(url_for('create_invoice'))
        try:
            invoice_number = request.form['invoice_number'].strip()
            client_name = request.form['client_name']

            invoice_date_str = request.form['invoice_date']
            due_date_str = request.form.get('due_date')
            # F2 — new invoices always start as DRAFT. Issue, Mark paid and
            # Annul are the only ways to change the status afterwards.
            status = request.form.get('status') or INITIAL_INVOICE_STATUS
            if status != INITIAL_INVOICE_STATUS:
                flash('New invoices are created as drafts. Issue the invoice, '
                      'then use Mark paid or Annul to change its status.', 'danger')
                return redirect(url_for('create_invoice'))
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
                payment_methods = [m.strip() for m in app_settings.payment_methods.split(',') if m.strip()] if app_settings and app_settings.payment_methods else []
                return render_template('create.html', customers=customers, banks=banks, settings=app_settings,
                                     payment_methods=payment_methods)

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
            flash('Error processing form data. Please check your input.', 'danger')
            customers = Customer.query.order_by(Customer.name).all()
            banks = Bank.query.order_by(Bank.name).all()
            app_settings = Settings.query.first()
            tracked_currencies = []
            if app_settings and app_settings.tracked_currencies:
                tracked_currencies = [c.strip() for c in app_settings.tracked_currencies.split(',') if c.strip()]
            else:
                tracked_currencies = ['USD', 'EUR', 'GBP', 'CZK']
            payment_methods = [m.strip() for m in app_settings.payment_methods.split(',') if m.strip()] if app_settings and app_settings.payment_methods else []
            return render_template('create.html', customers=customers, banks=banks, settings=app_settings,
                                 tracked_currencies=tracked_currencies,
                                 payment_methods=payment_methods)

        # Client details
        client_vat = request.form.get('client_vat', '')
        client_address = request.form.get('client_address', '')
        customer_id = request.form.get('customer_id')
        currency = request.form.get('currency', 'USD')

        invoice_date = datetime.strptime(invoice_date_str, '%Y-%m-%d').date()
        due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else None

        # total_usd holds the entered total in the invoice currency.
        try:
            amount_usd, amount_eur, exchange_rate = invoice_amounts(
                total_usd, currency, invoice_date_str)
        except NoExchangeRate as e:
            logger.warning('Invoice not created: %s', e)
            flash(f'No exchange rate to EUR is available for {currency} on this date. '
                  'Choose another currency or check the exchange rate provider in Settings.',
                  'danger')
            return redirect(url_for('create_invoice'))

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
            payment_method=request.form.get('payment_method', 'Bank Transfer'),
            customer_id=customer.id if customer else None,
            bank_id=int(bank_id) if bank_id else None
        )

        db.session.add(invoice)
        try:
            db.session.flush()
        except IntegrityError:
            # Taken by another request since the check above.
            db.session.rollback()
            flash(f'Invoice number "{invoice_number}" is already used. Choose another number.',
                  'danger')
            return redirect(url_for('create_invoice'))

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
                    clean(invoice.invoice_number), clean(client_name), clean(currency),
                    total_usd, len(items_data))
        flash('Invoice created successfully!', 'success')
        log_activity('invoice_created', 'invoice',
                     f'#{invoice.invoice_number} for {client_name}')

        # Let modules process their custom fields
        if state.module_manager:
            state.module_manager.on_invoice_created(invoice, request)
            # Refresh to pick up any PDF attached by modules (e.g. invoice_attachments)
            db.session.refresh(invoice)

        # Auto-generate PDF and save via core.storage (skip if module already attached a PDF)
        if state.module_manager and not getattr(invoice, 'pdf_hash', None):
            try:
                pdf_content = _render_invoice_pdf(invoice)['pdf']
                pdf_filename = f'invoice_{invoice.invoice_number}.pdf'
                relative_path = os.path.join('invoices_pdf', pdf_filename)
                new_key = state.module_manager.core.storage.save(pdf_content, relative_path)
                invoice.pdf_storage_key = new_key
                invoice.pdf_hash = hashlib.sha256(pdf_content).hexdigest()
                invoice.pdf_generated = True
                db.session.commit()

                # Sign the new PDF if its form asked for it (pdf.sign_invoice).
                try:
                    if state.module_manager.functions.available('pdf.sign_invoice'):
                        result = state.module_manager.functions.call('pdf.sign_invoice',
                                                               invoice_id=invoice.id)
                        if result['requested']:
                            app.logger.info('Auto-signed invoice #%s after PDF generation: %s',
                                            clean(invoice.invoice_number), result['signed'])
                except Exception as sign_err:
                    app.logger.error(
                        'Auto-sign failed for invoice #%s: %s',
                        clean(invoice.invoice_number), type(sign_err).__name__,
                    )
            except Exception as e:
                logger.error('PDF auto-generation failed for invoice #%s: %s',
                             clean(invoice.invoice_number), e, exc_info=True)
                log_activity('invoice_pdf_autogen_failed', 'invoice',
                             f'#{invoice.invoice_number}: {e}', level='warning')

        logger.info('=== Invoice #%s done ===', clean(invoice.invoice_number))
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
                         tracked_currencies=tracked_currencies,
                         payment_methods=[m.strip() for m in app_settings.payment_methods.split(',') if m.strip()] if app_settings and app_settings.payment_methods else [])


@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_invoice(id):
    invoice = db.get_or_404(Invoice, id)

    # F2 — issued/paid invoices are immutable; changes go via a rectificative.
    if _invoice_locked(invoice):
        flash('This invoice is issued and locked. Use "Rectify" to correct it.', 'danger')
        return redirect(url_for('view_invoice', id=id))

    if request.method == 'POST':
        number = (request.form.get('invoice_number') or '').strip()
        taken = _invoice_with_number(number, exclude_id=invoice.id)
        if taken is not None:
            flash(_number_taken_message(number, taken), 'danger')
            return redirect(url_for('edit_invoice', id=id))
        try:
            invoice.invoice_number = request.form['invoice_number'].strip()
            invoice.client_name = request.form['client_name']

            invoice_date_str = request.form['invoice_date']
            due_date_str = request.form.get('due_date')
            # F2 — the status never changes through the edit form: issuing,
            # payment and annulment are lifecycle actions on the invoice view.
            posted_status = request.form.get('status')
            if posted_status is not None and posted_status != invoice.status:
                flash('The status cannot be changed here. Use Issue, Mark paid '
                      'or Annul on the invoice page.', 'danger')
                return redirect(url_for('edit_invoice', id=id))
            invoice.notes = request.form.get('notes', '')
            invoice.payment_method = request.form.get('payment_method', 'Bank Transfer')
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
                payment_methods = [m.strip() for m in app_settings.payment_methods.split(',') if m.strip()] if app_settings and app_settings.payment_methods else []
                return render_template('edit.html', invoice=invoice, customers=customers, banks=banks,
                                     payment_methods=payment_methods)

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
            logger.error('Error processing invoice form data: %s', e)
            flash('Error processing form data. Please check your input.', 'danger')
            customers = Customer.query.order_by(Customer.name).all()
            banks = Bank.query.order_by(Bank.name).all()
            app_settings = Settings.query.first()
            payment_methods = [m.strip() for m in app_settings.payment_methods.split(',') if m.strip()] if app_settings and app_settings.payment_methods else []
            return render_template('edit.html', invoice=invoice, customers=customers, banks=banks,
                                 payment_methods=payment_methods)

        # Client details
        client_vat = request.form.get('client_vat', '')
        client_address = request.form.get('client_address', '')
        customer_id = request.form.get('customer_id')
        currency = request.form.get('currency', 'USD')

        invoice.invoice_date = datetime.strptime(invoice_date_str, '%Y-%m-%d').date()
        invoice.due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date() if due_date_str else None

        # Recalculate the stored amounts; total_usd is in the invoice currency.
        try:
            invoice.amount_usd, invoice.amount_eur, invoice.exchange_rate = invoice_amounts(
                total_usd, currency, invoice_date_str)
        except NoExchangeRate as e:
            db.session.rollback()
            logger.warning('Invoice %s not updated: %s', invoice.id, e)
            flash(f'No exchange rate to EUR is available for {currency} on this date. '
                  'Choose another currency or check the exchange rate provider in Settings.',
                  'danger')
            return redirect(url_for('edit_invoice', id=invoice.id))

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

        # The stored PDF shows the old data: rebuild it on the next view.
        _discard_generated_pdf(invoice)
        db.session.commit()
        flash('Invoice updated successfully!', 'success')
        log_activity('invoice_updated', 'invoice', f'#{invoice.invoice_number}')

        # Let modules process their custom fields
        if state.module_manager:
            state.module_manager.on_invoice_updated(invoice, request)

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
                         tracked_currencies=tracked_currencies,
                         payment_methods=[m.strip() for m in app_settings.payment_methods.split(',') if m.strip()] if app_settings and app_settings.payment_methods else [])




@app.route('/view/<int:id>')
@login_required
def view_invoice(id):
    invoice = db.get_or_404(Invoice, id)

    # Check if invoice is paid and has hash — verify integrity via storage
    hash_mismatch = False
    if invoice.status == 'paid' and invoice.pdf_hash and state.module_manager:
        svc = state.module_manager.core.invoice_service
        result = svc.get_pdf(invoice)
        if result:
            file_bytes, _ = result
            current_hash = hashlib.sha256(file_bytes).hexdigest()
            if current_hash != invoice.pdf_hash:
                hash_mismatch = True

    customers = Customer.query.order_by(Customer.name).all()
    banks = Bank.query.order_by(Bank.name).all()
    return render_template('view.html', invoice=invoice, customers=customers, banks=banks,
                           hash_mismatch=hash_mismatch,
                           legacy_settled=_is_legacy_settled(invoice),
                           currency_symbol=get_currency_symbol(invoice.currency or 'USD'))


@app.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete_invoice(id):
    # POST-only + CSRF token. A GET delete could be triggered by an
    # <img src="/delete/42"> and is not covered by CSRF protection.
    invoice = db.get_or_404(Invoice, id)

    # F2 — issued/paid invoices cannot be deleted; annul them instead.
    if _invoice_locked(invoice):
        flash('Issued invoices cannot be deleted. Use "Annul" to cancel it.', 'danger')
        return redirect(url_for('index'))

    # The PDF file is removed only after the database delete succeeds.
    pdf_key = (state.module_manager.core.invoice_service._resolve_storage_key(invoice)
               if state.module_manager else None)

    from sqlalchemy.exc import IntegrityError
    number, invoice_id = invoice.invoice_number, invoice.id   # values from the database
    try:
        if state.module_manager:
            state.module_manager.on_invoice_deleted(invoice)   # module rows first
        db.session.delete(invoice)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        logger.exception('Invoice %s could not be deleted', invoice_id)
        flash('This invoice could not be deleted because other records still refer '
              'to it. Nothing was changed; the server log names the record.', 'danger')
        return redirect(url_for('view_invoice', id=invoice_id))
    if pdf_key:
        try:
            state.module_manager.core.storage.delete(pdf_key)
            log_activity('invoice_pdf_deleted', 'invoice', f'PDF deleted for #{number}')
        except Exception as e:  # the invoice is gone; a leftover file is only logged
            logger.error('Failed to delete PDF for #%s: %s', clean(number), e, exc_info=True)
    log_activity('invoice_deleted', 'invoice', f'#{number}')
    flash('Invoice deleted successfully!', 'success')
    return redirect(url_for('index'))



def _render_invoice_pdf(invoice):
    """{'pdf', 'template', 'fallback'}: the invoice's PDF in the template chosen
    in Settings, through the registry function invoice.render_pdf
    (invoice_pdf.py)."""
    if state.module_manager is None:
        raise RuntimeError('Module manager not available')
    return state.module_manager.functions.call('invoice.render_pdf', invoice_id=invoice.id)


@app.route('/generate-pdf/<int:id>')
@login_required
def generate_pdf(id):
    """Download the invoice PDF: the stored one when it is unchanged, else a
    new one in the template chosen in Settings (invoice.render_pdf), stored."""
    invoice = db.get_or_404(Invoice, id)

    # Check if user wants to force regeneration
    force_regenerate = request.args.get('force') == 'true'

    pdf_filename = f'invoice_{invoice.invoice_number}.pdf'
    storage = state.module_manager.core.storage if state.module_manager else None
    svc = state.module_manager.core.invoice_service if state.module_manager else None

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
    try:
        result = _render_invoice_pdf(invoice)
        pdf_content = result['pdf']
        if result['fallback']:
            flash('The invoice template chosen in Settings is not available. '
                  'Using the default template.', 'warning')

        # Save via core.storage
        if storage:
            relative_path = os.path.join('invoices_pdf', pdf_filename)
            new_key = storage.save(pdf_content, relative_path)
            if hasattr(invoice, 'pdf_storage_key'):
                invoice.pdf_storage_key = new_key
            invoice.pdf_generated = True
        else:
            # Fallback: direct write (no state.module_manager)
            pdf_dir = os.path.join(paths.data_dir(), 'invoices_pdf')
            os.makedirs(pdf_dir, exist_ok=True)
            with open(os.path.join(pdf_dir, pdf_filename), 'wb') as f:
                f.write(pdf_content)

        if invoice.status == 'paid':
            invoice.pdf_hash = hashlib.sha256(pdf_content).hexdigest()
        db.session.commit()
        return send_file(io.BytesIO(pdf_content), as_attachment=True,
                         download_name=pdf_filename, mimetype='application/pdf')
    except Exception as e:
        logger.error('Error generating PDF: %s', e)
        flash('Error generating PDF. Check server logs.', 'danger')
        return redirect(url_for('index'))


@app.route('/preview-pdf/<int:id>')
@login_required
def preview_invoice_pdf(id):
    """Preview invoice PDF inline in the browser: the stored one, else one
    built on the fly (invoice.render_pdf), not stored."""
    invoice = db.get_or_404(Invoice, id)
    if not state.module_manager:
        flash('Module manager not available.', 'danger')
        return redirect(url_for('index'))

    result = state.module_manager.core.invoice_service.get_pdf(invoice)
    if result:
        file_bytes, _ = result
        return Response(file_bytes, mimetype='application/pdf',
                        headers={'Content-Disposition': 'inline'})

    try:
        pdf_content = _render_invoice_pdf(invoice)['pdf']
    except Exception as e:
        logger.error('Error generating PDF preview: %s', e)
        flash('Error generating PDF. Check server logs.', 'danger')
        return redirect(url_for('index'))
    return Response(pdf_content, mimetype='application/pdf',
                    headers={'Content-Disposition': 'inline'})
