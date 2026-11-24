"""Contractor (expense supplier) routes: create, edit, delete.

Routes register on the core app under their own endpoint names
(`url_for('dashboard')`, …). They import from `contaautonomo.web`, never from
`contaautonomo.app`, and read `state.module_manager` when a request runs."""

from flask import (flash, redirect, render_template, request, url_for)

from contaautonomo.web import (app, db, logger, login_required)
from contaautonomo.models import (Contractor)


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
            logger.error('Error creating contractor: %s', e)
            flash('Error creating record. Please try again.', 'danger')

    return render_template('contractor_form.html', contractor=None)


@app.route('/contractors/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_contractor(id):
    """Edit a contractor"""
    contractor = db.get_or_404(Contractor, id)

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
            logger.error('Error updating contractor: %s', e)
            flash('Error processing form data. Please check your input.', 'danger')

    return render_template('contractor_form.html', contractor=contractor)


@app.route('/contractors/delete/<int:id>', methods=['POST'])
@login_required
def delete_contractor(id):
    """Delete a contractor"""
    contractor = db.get_or_404(Contractor, id)

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
        logger.error('Error deleting contractor: %s', e)
        flash('Error deleting record. Please try again.', 'danger')

    return redirect(url_for('settings') + '#contractors')
