"""Settings routes: the tabbed settings page, bank accounts, the default customer.

Routes register on the core app under their own endpoint names
(`url_for('dashboard')`, …). They import from `contaautonomo.web`, never from
`contaautonomo.app`, and read `state.module_manager` when a request runs."""

from flask import (flash, redirect, render_template, request, url_for)

from contaautonomo import state
from contaautonomo.web import (app, db, login_required, log_activity, _apply_log_settings, _apply_currency_provider)
from contaautonomo.models import (Bank, Contractor, Customer, Invoice, Settings)


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
                'invoice_template': 'invoice_template',
                'currency_provider': 'currency_provider',
                'payment_methods': 'payment_methods',
            }
            for form_key, attr in field_map.items():
                if form_key in request.form:
                    setattr(app_settings, attr, request.form.get(form_key, ''))
            # Secrets are never rendered back (C-1): blank keeps the stored key.
            if 'currency_provider_api_key' in request.form:
                from contaautonomo.utils.settings_forms import secret_from_form
                app_settings.currency_provider_api_key = secret_from_form(
                    request.form, 'currency_provider_api_key',
                    app_settings.currency_provider_api_key or '')

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
        if state.module_manager:
            state.module_manager.save_settings(app_settings, request.form)

        db.session.commit()
        flash('Settings saved successfully!', 'success')
        # Re-apply log settings BEFORE logging (so the correct logger is active)
        if state.module_manager and 'log_retention_days' in request.form:
            _apply_log_settings(app_settings, state.module_manager)
        # Re-apply currency provider if changed
        if state.module_manager and 'currency_provider' in request.form:
            _apply_currency_provider(app_settings, state.module_manager)
        log_activity('settings_updated', 'settings')
        return redirect(url_for('settings'))

    # Get module states for the Modules tab
    module_states = state.module_manager.get_all_module_states() if state.module_manager else []
    module_settings_panels = state.module_manager.get_settings_html(app_settings) if state.module_manager else []
    field_labels = state.module_manager.get_field_labels() if state.module_manager else {}

    invoice_templates = []
    if state.module_manager and state.module_manager.functions.available('invoice.list_pdf_templates'):
        invoice_templates = state.module_manager.functions.call('invoice.list_pdf_templates')['data']
    return render_template('settings.html', settings=app_settings, banks=banks, customers=customers, contractors=contractors, module_states=module_states, module_settings_panels=module_settings_panels, field_labels=field_labels,
                           invoice_templates=invoice_templates)


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
    bank = db.get_or_404(Bank, id)

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


@app.route('/settings/banks/<int:id>/delete', methods=['POST'])
@login_required
def delete_bank(id):
    """Delete a bank"""
    bank = db.get_or_404(Bank, id)

    # Check if any invoices use this bank
    invoice_count = Invoice.query.filter_by(bank_id=id).count()
    if invoice_count > 0:
        flash(f'Cannot delete bank "{bank.name}" - it is used by {invoice_count} invoice(s)', 'danger')
        return redirect(url_for('settings') + '#banks')

    db.session.delete(bank)
    db.session.commit()
    flash(f'Bank "{bank.name}" deleted successfully!', 'success')
    return redirect(url_for('settings') + '#banks')


@app.route('/settings/banks/<int:id>/set-default', methods=['POST'])
@login_required
def set_default_bank(id):
    """Set a bank as default"""
    # Unset all defaults
    Bank.query.update({Bank.is_default: False})

    # Set this one as default
    bank = db.get_or_404(Bank, id)
    bank.is_default = True
    db.session.commit()

    flash(f'Bank "{bank.name}" set as default!', 'success')
    return redirect(url_for('settings') + '#banks')


@app.route('/settings/customers/<int:id>/set-default', methods=['POST'])
@login_required
def set_default_customer(id):
    """Set a customer as default"""
    # Unset all defaults
    Customer.query.update({Customer.is_default: False})

    # Set this one as default
    customer = db.get_or_404(Customer, id)
    customer.is_default = True
    db.session.commit()

    flash(f'Customer "{customer.name}" set as default!', 'success')
    return redirect(url_for('settings') + '#customers')
