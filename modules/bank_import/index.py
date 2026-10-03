#!/usr/bin/env python3
"""
Bank Import Module (F12) — statement import (Norma 43 / CSV) + reconciliation.

CAVEMAN NOTE: import movements, dedup by hash, suggest matches against unpaid
invoices (matching.py rank, user always confirm), one click -> invoice paid via
the NORMAL core transition (_mark_invoice_paid) so F2/F1 semantics hold —
Verifactu see nothing (paid is not a record event), lifecycle log fire.
Import-good-skip-bad with a report; batches reversible until a movement is
matched.
"""

import json
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash

from module_manager import BaseModule

from .parsers import parse_n43, parse_csv
from .matching import suggest_for_movement


class BankImportModule(BaseModule):
    """Bank statement import and invoice/expense reconciliation."""

    @property
    def module_id(self):
        return 'bank_import'

    @property
    def name(self):
        return 'Bank Import'

    @property
    def description(self):
        return ('Import bank movements (Norma 43 / CSV), match them to '
                'invoices and expenses, and mark invoices paid on confirm.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Bank Import', 'endpoint': 'bank_import.bank_index',
             'icon': '🏦', 'group': 'System'}
        ]

    def register_models(self, db):
        self._db = db

        class ImportBatch(db.Model):
            __tablename__ = 'bank_import_batch'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            filename = db.Column(db.String(300))
            fmt = db.Column(db.String(10))          # n43 | csv
            imported = db.Column(db.Integer, default=0)
            skipped = db.Column(db.Integer, default=0)
            errors = db.Column(db.Text)             # JSON list of bad-line reports
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

        class BankMovement(db.Model):
            __tablename__ = 'bank_movement'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            batch_id = db.Column(db.Integer, nullable=False)
            date = db.Column(db.Date, nullable=False)
            amount = db.Column(db.Float, nullable=False)  # credits +, debits -
            currency = db.Column(db.String(10), default='EUR')
            description = db.Column(db.Text)
            counterparty = db.Column(db.String(300))
            hash = db.Column(db.String(64), unique=True, nullable=False)
            status = db.Column(db.String(20), default='new')  # new|matched|ignored
            matched_type = db.Column(db.String(20))            # invoice|expense
            matched_id = db.Column(db.Integer)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

        class BankImportConfig(db.Model):
            """CSV column-mapping profiles, one row per profile name."""
            __tablename__ = 'bank_import_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            key = db.Column(db.String(100), unique=True, nullable=False)
            value = db.Column(db.Text)

        self.ImportBatch = ImportBatch
        self.BankMovement = BankMovement
        self.Config = BankImportConfig
        return {'ImportBatch': ImportBatch, 'BankMovement': BankMovement,
                'BankImportConfig': BankImportConfig}

    # --- Import ---------------------------------------------------------- #

    def _import_movements(self, movements, errors, filename, fmt):
        """Persist parsed movements with dedup; returns the batch."""
        batch = self.ImportBatch(filename=filename, fmt=fmt,
                                 errors=json.dumps(errors) if errors else None)
        self._db.session.add(batch)
        self._db.session.flush()

        imported = skipped = 0
        for m in movements:
            exists = self.BankMovement.query.filter_by(hash=m['hash']).first()
            if exists:
                skipped += 1
                continue
            self._db.session.add(self.BankMovement(
                batch_id=batch.id, date=m['date'], amount=m['amount'],
                currency=m['currency'], description=m['description'],
                counterparty=m.get('counterparty') or '', hash=m['hash']))
            imported += 1
        batch.imported, batch.skipped = imported, skipped
        self._db.session.commit()
        self.core.log_activity(
            'bank_import', 'system',
            f'{filename}: {imported} imported, {skipped} duplicates skipped, '
            f'{len(errors)} bad lines')
        return batch

    def _csv_profiles(self):
        out = {}
        for row in self.Config.query.all():
            if row.key.startswith('csv_profile:'):
                try:
                    out[row.key.split(':', 1)[1]] = json.loads(row.value)
                except (ValueError, TypeError):
                    continue
        return out

    def _save_csv_profile(self, name, mapping):
        key = f'csv_profile:{name}'
        row = self.Config.query.filter_by(key=key).first()
        if not row:
            row = self.Config(key=key)
            self._db.session.add(row)
        row.value = json.dumps(mapping)
        self._db.session.commit()

    # --- Matching ---------------------------------------------------------- #

    def _unpaid_invoices(self):
        from app import Invoice
        return Invoice.query.filter(
            Invoice.status.in_(('issued', 'pending'))).all()

    def _suggestions(self, movements):
        """{movement_id: [(score, invoice, reason), ...top 3]}"""
        invoices = self._unpaid_invoices()
        out = {}
        for m in movements:
            ranked = suggest_for_movement(m, invoices)
            if ranked:
                out[m.id] = ranked[:3]
        return out

    # --- Routes -------------------------------------------------------------- #

    def register_routes(self, app):
        bp = Blueprint('bank_import', __name__,
                       template_folder='templates',
                       url_prefix='/bank-import')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def bank_index():
            unmatched = module.BankMovement.query.filter_by(status='new') \
                .order_by(module.BankMovement.date.desc()).all()
            batches = module.ImportBatch.query.order_by(
                module.ImportBatch.id.desc()).limit(10).all()
            batch_errors = {b.id: json.loads(b.errors) for b in batches if b.errors}
            return render_template('bank_import.html',
                                   unmatched=unmatched,
                                   suggestions=module._suggestions(unmatched),
                                   batches=batches,
                                   batch_errors=batch_errors,
                                   csv_profiles=module._csv_profiles())

        @bp.route('/upload', methods=['POST'])
        @login_required
        def bank_upload():
            file = request.files.get('file')
            if not file or not file.filename:
                flash('No file selected.', 'danger')
                return redirect(url_for('bank_import.bank_index'))
            fmt = request.form.get('fmt', 'n43')
            data = file.read()
            try:
                if fmt == 'csv':
                    mapping = {
                        'date': request.form.get('col_date', '').strip(),
                        'amount': request.form.get('col_amount', '').strip(),
                        'description': request.form.get('col_description', '').strip(),
                        'counterparty': request.form.get('col_counterparty', '').strip() or None,
                        'date_format': request.form.get('date_format', '').strip() or '%d/%m/%Y',
                        'decimal_comma': request.form.get('decimal_comma') == '1',
                    }
                    profile = request.form.get('profile_name', '').strip()
                    if profile:
                        module._save_csv_profile(profile, mapping)
                    movements, errors = parse_csv(data, mapping)
                else:
                    movements, errors = parse_n43(data)
            except Exception as e:
                module.logger.error('Bank import parse failed: %s', e)
                flash(f'Could not parse file: {e}', 'danger')
                return redirect(url_for('bank_import.bank_index'))

            batch = module._import_movements(movements, errors,
                                             file.filename, fmt)
            msg = (f'Imported {batch.imported} movement(s), '
                   f'{batch.skipped} duplicate(s) skipped.')
            if errors:
                msg += f' {len(errors)} unparseable line(s) reported on the batch.'
            flash(msg, 'success' if batch.imported else 'warning')
            return redirect(url_for('bank_import.bank_index'))

        @bp.route('/confirm/<int:movement_id>', methods=['POST'])
        @login_required
        def bank_confirm(movement_id):
            movement = module.BankMovement.query.get_or_404(movement_id)
            invoice_id = request.form.get('invoice_id', type=int)
            if not invoice_id:
                flash('Pick an invoice to match.', 'danger')
                return redirect(url_for('bank_import.bank_index'))
            from app import Invoice, _mark_invoice_paid
            invoice = Invoice.query.get_or_404(invoice_id)
            try:
                # Normal core transition -> lifecycle log; F2/F1 semantics hold.
                _mark_invoice_paid(invoice)
                movement.status = 'matched'
                movement.matched_type = 'invoice'
                movement.matched_id = invoice.id
                module._db.session.commit()
                module.core.log_activity(
                    'bank_movement_matched', 'invoice',
                    f'{movement.date} {movement.amount:.2f} -> '
                    f'#{invoice.invoice_number} (paid)')
                flash(f'Matched — invoice {invoice.invoice_number} marked paid.',
                      'success')
            except ValueError as e:
                module._db.session.rollback()
                flash(str(e), 'danger')
            return redirect(url_for('bank_import.bank_index'))

        @bp.route('/link-expense/<int:movement_id>', methods=['POST'])
        @login_required
        def bank_link_expense(movement_id):
            movement = module.BankMovement.query.get_or_404(movement_id)
            expense_id = request.form.get('expense_id', type=int)
            from app import Expense
            expense = Expense.query.get_or_404(expense_id)
            movement.status = 'matched'
            movement.matched_type = 'expense'
            movement.matched_id = expense.id
            module._db.session.commit()
            flash('Movement linked to expense.', 'success')
            return redirect(url_for('bank_import.bank_index'))

        @bp.route('/ignore/<int:movement_id>', methods=['POST'])
        @login_required
        def bank_ignore(movement_id):
            movement = module.BankMovement.query.get_or_404(movement_id)
            movement.status = 'ignored'
            module._db.session.commit()
            return redirect(url_for('bank_import.bank_index'))

        @bp.route('/batch/<int:batch_id>/delete', methods=['POST'])
        @login_required
        def bank_batch_delete(batch_id):
            batch = module.ImportBatch.query.get_or_404(batch_id)
            matched = module.BankMovement.query.filter(
                module.BankMovement.batch_id == batch.id,
                module.BankMovement.status == 'matched').count()
            if matched:
                flash('Batch has matched movements — cannot undo.', 'danger')
                return redirect(url_for('bank_import.bank_index'))
            module.BankMovement.query.filter_by(batch_id=batch.id).delete()
            module._db.session.delete(batch)
            module._db.session.commit()
            flash('Batch deleted (import undone).', 'success')
            return redirect(url_for('bank_import.bank_index'))

        app.register_blueprint(bp)

    # --- Dashboard + API (F12-D4) ----------------------------------------------- #

    def get_dashboard_panels(self):
        count = self.BankMovement.query.filter_by(status='new').count()
        if not count:
            return []
        return [{
            'id': 'bank_unmatched',
            'title': '🏦 Bank Reconciliation',
            'template': 'bank_panel.html',
            'data': {'count': count},
            'order': 25,
        }]

    def get_api_routes(self):
        return [
            {'path': 'movements', 'methods': ['GET'],
             'handler': self._api_movements,
             'summary': 'List bank movements (filter: status)'},
        ]

    def _api_movements(self, request):
        query = self.BankMovement.query
        status = request.args.get('status')
        if status:
            query = query.filter_by(status=status)
        rows = query.order_by(self.BankMovement.date.desc()).limit(200).all()
        return {'data': [{
            'id': m.id, 'date': m.date.isoformat(), 'amount': m.amount,
            'currency': m.currency, 'description': m.description,
            'status': m.status, 'matched_type': m.matched_type,
            'matched_id': m.matched_id,
        } for m in rows]}, 200
