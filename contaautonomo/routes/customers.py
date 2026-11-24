"""Customer routes: list, create, view, edit, delete.

Routes register on the core app under their own endpoint names
(`url_for('dashboard')`, …); `core_app.module_manager` is read when a request
runs, so it is always the current one."""

from flask import (flash, redirect, render_template, request, url_for)

from contaautonomo.app import (app, db, login_required)
from contaautonomo.models import (Customer, Settings)
from contaautonomo.utils.currency_converter import get_currency_symbol


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
    customer = db.get_or_404(Customer, id)

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
    customer = db.get_or_404(Customer, id)

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


@app.route('/customers/<int:id>/delete', methods=['POST'])
@login_required
def delete_customer(id):
    customer = db.get_or_404(Customer, id)
    db.session.delete(customer)
    db.session.commit()
    flash('Customer deleted successfully!', 'success')
    return redirect(url_for('settings') + '#customers')
