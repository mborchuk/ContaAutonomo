#!/usr/bin/env python3
"""
Recurring Invoices Module (F7) — templates + cadence -> auto-generated DRAFTS.

CAVEMAN NOTE: module never issue anything. It only materialize DRAFT invoices
on schedule; user review and press Issue (F2). Draft-first is deliberate:
auto-issuing would mint legal records unattended (bad with Verifactu coming).

Generation is idempotent: `next_run_date` persist after each run, catch-up is
capped, so restarts / missed days never duplicate.
"""

import json
from datetime import date, datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash

from module_manager import BaseModule

from .cadence import CADENCES, due_dates


class RecurringInvoicesModule(BaseModule):
    """Recurring invoice templates that generate drafts on schedule."""

    @property
    def module_id(self):
        return 'recurring_invoices'

    @property
    def name(self):
        return 'Recurring Invoices'

    @property
    def description(self):
        return ('Define invoice templates with a monthly/quarterly cadence; '
                'drafts are generated automatically and wait for your review.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Recurring', 'endpoint': 'recurring_invoices.recurring_index',
             'icon': '🔁', 'group': 'Invoices'}
        ]

    def register_models(self, db):
        self._db = db

        class RecurringInvoice(db.Model):
            __tablename__ = 'recurring_invoice'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(200), nullable=False)
            customer_id = db.Column(db.Integer)
            bank_id = db.Column(db.Integer)
            client_name = db.Column(db.String(200))
            currency = db.Column(db.String(10), default='EUR')
            payment_method = db.Column(db.String(100), default='Bank Transfer')
            items_json = db.Column(db.Text, nullable=False)  # [{description, quantity, unit_price}]
            notes = db.Column(db.Text)
            cadence = db.Column(db.String(20), nullable=False, default='monthly')
            next_run_date = db.Column(db.Date, nullable=False)
            active = db.Column(db.Boolean, default=True)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

        class RecurringGeneration(db.Model):
            """Log: one row per draft materialized (feeds the dashboard nudge)."""
            __tablename__ = 'recurring_generation'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            template_id = db.Column(db.Integer, nullable=False)
            invoice_id = db.Column(db.Integer, nullable=False)
            run_date = db.Column(db.Date, nullable=False)
            generated_at = db.Column(db.DateTime, default=datetime.utcnow)

        self.RecurringInvoice = RecurringInvoice
        self.RecurringGeneration = RecurringGeneration
        return {'RecurringInvoice': RecurringInvoice,
                'RecurringGeneration': RecurringGeneration}

    def on_enable(self):
        try:
            self.core.scheduler.add_job(
                job_id='recurring_invoices.generate',
                func=self._generate_due,
                job_type='daily',
                time_str='06:00',
                description='Generate due recurring invoice drafts',
            )
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error('Failed to register generation job: %s', e)

    # --- Generation (F7-D2) ------------------------------------------------ #

    def _generate_due(self, today=None):
        """Materialize every due template into a DRAFT invoice. Idempotent.

        Runs inside app context (scheduler) — no request context. Returns list
        of created invoice ids (also used by tests).
        """
        today = today or date.today()
        created = []
        templates = self.RecurringInvoice.query.filter_by(active=True).all()
        for tpl in templates:
            due, new_next = due_dates(tpl.next_run_date, today, tpl.cadence)
            for run_date in due:
                try:
                    inv = self._materialize(tpl, run_date)
                    created.append(inv.id)
                except Exception as e:
                    self._db.session.rollback()
                    self.logger.error('Generation failed for template %s: %s',
                                      tpl.id, e)
                    break  # keep next_run_date at the failed period -> retried next run
            else:
                if due:
                    tpl.next_run_date = new_next
                    self._db.session.commit()
        return created

    def _materialize(self, tpl, run_date):
        """Create one DRAFT invoice from a template for `run_date`."""
        from app import Invoice, InvoiceItem
        from currency_converter import get_exchange_rate, convert_usd_to_eur

        items = json.loads(tpl.items_json)
        total = sum(float(i['quantity']) * float(i['unit_price']) for i in items)

        # Same currency convention as the core create route.
        exchange_rate, _rate_date = get_exchange_rate(run_date.strftime('%Y-%m-%d'))
        if tpl.currency == 'EUR':
            amount_eur = total
            amount_usd = total * exchange_rate
        else:
            amount_usd = total
            amount_eur = convert_usd_to_eur(total, exchange_rate)

        # Placeholder number; the real sequential number is assigned at ISSUE.
        number = f'REC{tpl.id}-{run_date.strftime("%Y%m%d")}'
        suffix = 1
        while Invoice.query.filter_by(invoice_number=number).first():
            suffix += 1
            number = f'REC{tpl.id}-{run_date.strftime("%Y%m%d")}-{suffix}'

        inv = Invoice(
            invoice_number=number,
            client_name=tpl.client_name or tpl.name,
            amount_usd=amount_usd,
            amount_eur=amount_eur,
            exchange_rate=exchange_rate,
            invoice_date=run_date,
            description=items[0]['description'] if items else '',
            quantity=float(items[0]['quantity']) if items else 1,
            unit_price_usd=float(items[0]['unit_price']) if items else 0,
            notes=tpl.notes,
            status='draft',
            currency=tpl.currency,
            payment_method=tpl.payment_method or 'Bank Transfer',
            customer_id=tpl.customer_id,
            bank_id=tpl.bank_id,
        )
        self._db.session.add(inv)
        self._db.session.flush()
        for item in items:
            qty = float(item['quantity'])
            price = float(item['unit_price'])
            self._db.session.add(InvoiceItem(
                invoice_id=inv.id, description=item['description'],
                quantity=qty, unit_price_usd=price, subtotal_usd=qty * price))
        self._db.session.add(self.RecurringGeneration(
            template_id=tpl.id, invoice_id=inv.id, run_date=run_date))
        self._db.session.commit()

        # Normal draft: let every module react, same as a manual create.
        try:
            self.core.module_manager.on_invoice_created(inv, None)
        except Exception as e:  # pragma: no cover - fan-out already swallows
            self.logger.warning('on_invoice_created fan-out error: %s', e)
        self.core.log_activity(
            'recurring_invoice_generated', 'invoice',
            f'Draft {inv.invoice_number} from template "{tpl.name}"')
        return inv

    # --- Dashboard nudge (F7-D3) ------------------------------------------- #

    def get_dashboard_panels(self):
        from app import Invoice
        gen_rows = self.RecurringGeneration.query.all()
        draft_ids = [g.invoice_id for g in gen_rows]
        pending = 0
        if draft_ids:
            pending = Invoice.query.filter(
                Invoice.id.in_(draft_ids), Invoice.status == 'draft').count()
        if not pending:
            return []
        return [{
            'id': 'recurring_pending',
            'title': '🔁 Recurring Drafts',
            'template': 'recurring_panel.html',
            'data': {'pending': pending},
            'order': 20,
        }]

    # --- "Make recurring" action on invoice view (F7-D1) ------------------- #

    def get_invoice_actions(self, invoice):
        url = url_for('recurring_invoices.recurring_from_invoice', invoice_id=invoice.id)
        return [
            f'<form method="POST" action="{url}" style="display:inline;">'
            f'<button type="submit" class="btn btn-secondary">Make recurring</button>'
            f'</form>'
        ]

    # --- Routes ------------------------------------------------------------- #

    def register_routes(self, app):
        bp = Blueprint('recurring_invoices', __name__,
                       template_folder='templates',
                       url_prefix='/recurring-invoices')
        login_required = self.core.login_required
        module = self

        def _form_ctx():
            from app import Customer, Bank
            settings = module.core.get_settings()
            tracked = ['USD', 'EUR', 'GBP', 'CZK']
            if settings and settings.tracked_currencies:
                tracked = [c.strip() for c in settings.tracked_currencies.split(',')
                           if c.strip()]
            return {
                'customers': Customer.query.order_by(Customer.name).all(),
                'banks': Bank.query.order_by(Bank.name).all(),
                'tracked_currencies': tracked,
                'cadences': CADENCES,
            }

        def _parse_items(form):
            items = []
            ids = set()
            for key in form.keys():
                if key.startswith('items[') and '][description]' in key:
                    ids.add(key.split('[')[1].split(']')[0])
            for item_id in sorted(ids):
                desc = form.get(f'items[{item_id}][description]', '').strip()
                if not desc:
                    continue
                items.append({
                    'description': desc,
                    'quantity': float(form.get(f'items[{item_id}][quantity]', 1)),
                    'unit_price': float(form.get(f'items[{item_id}][unit_price]', 0)),
                })
            if not items:
                raise ValueError('At least one item is required')
            return items

        def _apply_form(tpl, form):
            tpl.name = form['name']
            tpl.client_name = form.get('client_name') or tpl.name
            tpl.customer_id = int(form['customer_id']) if form.get('customer_id') else None
            tpl.bank_id = int(form['bank_id']) if form.get('bank_id') else None
            tpl.currency = form.get('currency', 'EUR')
            tpl.payment_method = form.get('payment_method', 'Bank Transfer')
            cadence = form.get('cadence', 'monthly')
            tpl.cadence = cadence if cadence in CADENCES else 'monthly'
            tpl.next_run_date = datetime.strptime(
                form['next_run_date'], '%Y-%m-%d').date()
            tpl.notes = form.get('notes')
            tpl.items_json = json.dumps(_parse_items(form))

        @bp.route('/')
        @login_required
        def recurring_index():
            templates = module.RecurringInvoice.query.order_by(
                module.RecurringInvoice.name).all()
            items_map = {t.id: json.loads(t.items_json) for t in templates}
            return render_template('recurring_list.html',
                                   templates=templates, items_map=items_map)

        @bp.route('/create', methods=['GET', 'POST'])
        @login_required
        def recurring_create():
            if request.method == 'POST':
                try:
                    tpl = module.RecurringInvoice(name='', items_json='[]',
                                                  next_run_date=date.today())
                    _apply_form(tpl, request.form)
                    module._db.session.add(tpl)
                    module._db.session.commit()
                    flash('Recurring template created.', 'success')
                    return redirect(url_for('recurring_invoices.recurring_index'))
                except Exception as e:
                    module._db.session.rollback()
                    module.logger.error('Create recurring failed: %s', e)
                    flash('Error creating template. Check your input.', 'danger')
            return render_template('recurring_form.html', template=None,
                                   items=[], **_form_ctx())

        @bp.route('/edit/<int:id>', methods=['GET', 'POST'])
        @login_required
        def recurring_edit(id):
            tpl = module.RecurringInvoice.query.get_or_404(id)
            if request.method == 'POST':
                try:
                    _apply_form(tpl, request.form)
                    module._db.session.commit()
                    flash('Recurring template updated.', 'success')
                    return redirect(url_for('recurring_invoices.recurring_index'))
                except Exception as e:
                    module._db.session.rollback()
                    module.logger.error('Edit recurring failed: %s', e)
                    flash('Error updating template. Check your input.', 'danger')
            return render_template('recurring_form.html', template=tpl,
                                   items=json.loads(tpl.items_json), **_form_ctx())

        @bp.route('/toggle/<int:id>', methods=['POST'])
        @login_required
        def recurring_toggle(id):
            tpl = module.RecurringInvoice.query.get_or_404(id)
            tpl.active = not tpl.active
            module._db.session.commit()
            flash(f'Template "{tpl.name}" {"resumed" if tpl.active else "paused"}.',
                  'success')
            return redirect(url_for('recurring_invoices.recurring_index'))

        @bp.route('/delete/<int:id>', methods=['POST'])
        @login_required
        def recurring_delete(id):
            tpl = module.RecurringInvoice.query.get_or_404(id)
            module._db.session.delete(tpl)
            module._db.session.commit()
            flash('Template deleted (generated invoices are kept).', 'success')
            return redirect(url_for('recurring_invoices.recurring_index'))

        @bp.route('/from-invoice/<int:invoice_id>', methods=['POST'])
        @login_required
        def recurring_from_invoice(invoice_id):
            from app import Invoice
            inv = Invoice.query.get_or_404(invoice_id)
            items = [{'description': it.description, 'quantity': it.quantity,
                      'unit_price': it.unit_price_usd} for it in inv.items]
            if not items:
                items = [{'description': inv.description or 'Service',
                          'quantity': inv.quantity or 1,
                          'unit_price': inv.unit_price_usd or 0}]
            tpl = module.RecurringInvoice(
                name=f'{inv.client_name} (from {inv.invoice_number})',
                client_name=inv.client_name,
                customer_id=inv.customer_id,
                bank_id=inv.bank_id,
                currency=inv.currency,
                payment_method=inv.payment_method,
                notes=inv.notes,
                items_json=json.dumps(items),
                cadence='monthly',
                next_run_date=date.today(),
                active=False,  # start paused: user reviews cadence/date first
            )
            module._db.session.add(tpl)
            module._db.session.commit()
            flash('Recurring template created (paused). Review cadence and '
                  'next run date, then resume.', 'success')
            return redirect(url_for('recurring_invoices.recurring_edit', id=tpl.id))

        app.register_blueprint(bp)

    # --- REST API (F7-D3) --------------------------------------------------- #

    def get_api_routes(self):
        return [
            {'path': 'templates', 'methods': ['GET'],
             'handler': self._api_templates,
             'summary': 'List recurring invoice templates'},
        ]

    def _api_templates(self, request):
        rows = self.RecurringInvoice.query.order_by(self.RecurringInvoice.id).all()
        return {'data': [{
            'id': t.id, 'name': t.name, 'cadence': t.cadence,
            'next_run_date': t.next_run_date.isoformat() if t.next_run_date else None,
            'active': t.active, 'currency': t.currency,
            'items': json.loads(t.items_json),
        } for t in rows]}, 200
