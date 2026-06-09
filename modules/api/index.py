#!/usr/bin/env python3
"""
API Module — ContaAutónomo AI Communication API

Exposes a plain REST/JSON API under /api/v1 so AI agents (and other external
tools) can read and write app data. Implements the design in API.MD:

- Token auth via the `X-API-Token` header (constant-time compare).
- The /api/v1 Blueprint is CSRF-exempt (token replaces CSRF), mirroring auth_bp.
- All invoice writes go through core.invoice_service so the PAID lock holds.
- Amounts are computed server-side via core.currency_service (never trusted
  from the client, never a hard-coded rate).
- Discovery via GET /api/v1/ (index) and GET /api/v1/openapi.json (manifest),
  both module-aware: only enabled modules are advertised.
- Rate limits via the existing Flask-Limiter instance.

The optional MCP wrapper (API.MD §12) lives outside this module/process.
"""

import hmac
import secrets
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, jsonify, request
from sqlalchemy import inspect as sa_inspect, text

from module_manager import BaseModule

# API surface version (path is /api/v1). Bump when the contract changes.
API_VERSION = 'v1'

# Rate limits per API.MD §11.
READ_LIMIT = '60/minute'
WRITE_LIMIT = '20/minute'
HEAVY_LIMIT = '10/minute'

# Fields a PATCH is allowed to touch. Everything else is rejected so a client
# can never set amounts/hashes directly (those are server-computed).
PATCHABLE_FIELDS = {
    'client_name', 'status', 'due_date', 'invoice_date',
    'notes', 'payment_method', 'currency', 'description',
}


class ApiError(Exception):
    """Raised inside handlers to short-circuit with a JSON error response."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _json_error(status, code, message):
    return jsonify(error=code, message=message), status


class ApiModule(BaseModule):
    """REST/JSON API for AI agents and external tools."""

    @property
    def module_id(self):
        return 'api'

    @property
    def name(self):
        return 'AI Communication API'

    @property
    def description(self):
        return ('REST/JSON API under /api/v1 for AI agents and external tools. '
                'Token-authenticated, reuses invoice_service and currency_service, '
                'respects the PAID invoice lock.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def settings_tab(self):
        # Custom Settings tab: Settings → API.
        return {'id': 'api', 'label': 'API'}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def on_enable(self):
        """Add the api_token column to `settings` (idempotent) and mint a
        first token if the singleton row already exists. Follows the guarded
        ALTER TABLE migration pattern (DOCUMENTATIONS.MD §12)."""
        db = self.core.db
        try:
            inspector = sa_inspect(db.engine)
            cols = [c['name'] for c in inspector.get_columns('settings')]
            if 'api_token' not in cols:
                with db.engine.connect() as conn:
                    conn.execute(
                        text('ALTER TABLE settings ADD COLUMN api_token VARCHAR(64)'))
                    conn.commit()
                self.logger.info('Added settings.api_token column')
        except Exception as e:
            self.logger.error('api_token migration failed: %s', e, exc_info=True)
            return

        # Generate an initial token if a settings row exists but has none.
        try:
            self._ensure_token()
        except Exception as e:
            self.logger.debug('Initial token generation deferred: %s', e)

    # ------------------------------------------------------------------ #
    # Token storage (raw SQL — keeps the api_token out of the core ORM model)
    # ------------------------------------------------------------------ #

    def _get_token(self):
        """Return the stored API token, or '' if unset/unavailable."""
        db = self.core.db
        try:
            row = db.session.execute(
                text('SELECT api_token FROM settings ORDER BY id LIMIT 1')).fetchone()
        except Exception:
            return ''
        if not row:
            return ''
        return row[0] or ''

    def _set_token(self, token):
        """Persist a new API token on the settings singleton."""
        db = self.core.db
        db.session.execute(
            text('UPDATE settings SET api_token = :t WHERE id = ('
                 'SELECT id FROM settings ORDER BY id LIMIT 1)'),
            {'t': token})
        db.session.commit()

    def _ensure_token(self):
        """Return an existing token, generating and storing one if missing.

        Requires the settings singleton row to exist; returns '' if it does not
        yet (the row is created on app startup)."""
        token = self._get_token()
        if token:
            return token
        db = self.core.db
        exists = db.session.execute(
            text('SELECT id FROM settings ORDER BY id LIMIT 1')).fetchone()
        if not exists:
            return ''
        token = secrets.token_urlsafe(32)
        self._set_token(token)
        self.core.log_activity('api_token_generated', 'system',
                               'A new API token was generated')
        return token

    def _rotate_token(self):
        token = secrets.token_urlsafe(32)
        self._set_token(token)
        self.core.log_activity('api_token_rotated', 'system',
                               'The API token was rotated')
        return token

    # ------------------------------------------------------------------ #
    # Auth decorator
    # ------------------------------------------------------------------ #

    def verify_token(self, request):
        """Public helper so other modules can token-protect their own API
        blueprint routes (e.g. a heavy, dedicated rate-limited route that does
        not fit the generic /m dispatcher). Returns True if the X-API-Token
        header matches, using the same constant-time compare."""
        token = self._get_token()
        sent = request.headers.get('X-API-Token', '')
        return bool(token) and hmac.compare_digest(sent, token)

    def _require_api_token(self, f):
        """Constant-time check of the X-API-Token header (API.MD §5)."""
        @wraps(f)
        def wrap(*args, **kwargs):
            token = self._get_token()
            sent = request.headers.get('X-API-Token', '')
            if not token or not hmac.compare_digest(sent, token):
                return _json_error(401, 'unauthorized',
                                   'Missing or invalid X-API-Token')
            return f(*args, **kwargs)
        return wrap

    # ------------------------------------------------------------------ #
    # Request helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _json_body():
        """Parse and return the JSON request body as a dict, or raise ApiError."""
        if not request.is_json:
            raise ApiError(415, 'unsupported_media_type',
                           'Request body must be application/json')
        try:
            body = request.get_json(silent=False)
        except Exception:
            raise ApiError(400, 'bad_request', 'Body is not valid JSON')
        if not isinstance(body, dict):
            raise ApiError(400, 'bad_request', 'JSON body must be an object')
        return body

    @staticmethod
    def _parse_date(value, field):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            raise ApiError(400, 'bad_request',
                           f'Invalid date for "{field}" (expected YYYY-MM-DD)')

    @staticmethod
    def _serialize_invoice(inv, svc):
        return {
            'id': inv.id,
            'invoice_number': inv.invoice_number,
            'client_name': inv.client_name,
            'amount_usd': inv.amount_usd,
            'amount_eur': inv.amount_eur,
            'currency': inv.currency,
            'exchange_rate': inv.exchange_rate,
            'invoice_date': inv.invoice_date.isoformat() if inv.invoice_date else None,
            'due_date': inv.due_date.isoformat() if inv.due_date else None,
            'status': inv.status,
            'has_pdf': svc.has_pdf(inv),
        }

    @staticmethod
    def _serialize_expense(exp):
        return {
            'id': exp.id,
            'contractor_id': exp.contractor_id,
            'amount': exp.amount,
            'currency': exp.currency,
            'category': exp.category,
            'description': exp.description,
            'expense_date': exp.expense_date.isoformat() if exp.expense_date else None,
            'invoice_number': exp.invoice_number,
        }

    @staticmethod
    def _serialize_customer(c):
        return {
            'id': c.id,
            'name': c.name,
            'vat_number': c.vat_number,
            'country': c.country,
            'email': c.email,
            'tax_type': c.tax_type,
            'is_default': bool(c.is_default),
        }

    def _gen_invoice_number(self, Invoice, year):
        """Generate a unique `YYYY-NNN` invoice number for the given year."""
        prefix = f'{year}-'
        existing = Invoice.query.filter(
            Invoice.invoice_number.like(prefix + '%')).all()
        seq = 0
        for inv in existing:
            tail = inv.invoice_number.rsplit('-', 1)[-1]
            if tail.isdigit():
                seq = max(seq, int(tail))
        seq += 1
        while True:
            candidate = f'{year}-{seq:03d}'
            if not Invoice.query.filter_by(invoice_number=candidate).first():
                return candidate
            seq += 1

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #

    def register_routes(self, app):
        from app import csrf, limiter

        bp = Blueprint('api', __name__, url_prefix='/api/' + API_VERSION)
        module = self
        require_token = self._require_api_token

        def limit(rule):
            """Apply a rate limit if Flask-Limiter is available, else no-op."""
            def deco(f):
                return limiter.limit(rule)(f) if limiter else f
            return deco

        # --- Discovery -------------------------------------------------- #

        @bp.route('/', methods=['GET'])
        @require_token
        @limit(READ_LIMIT)
        def api_index():
            from app import APP_VERSION
            return jsonify({
                'name': 'ContaAutónomo',
                'app_version': APP_VERSION,
                'api_version': API_VERSION,
                'enabled_modules': sorted(module.core.module_manager.modules.keys()),
                'manifest': f'/api/{API_VERSION}/openapi.json',
                'auth': 'X-API-Token header',
            })

        @bp.route('/openapi.json', methods=['GET'])
        @require_token
        @limit(READ_LIMIT)
        def api_openapi():
            return jsonify(module._build_openapi())

        @bp.route('/health', methods=['GET'])
        @require_token
        @limit(READ_LIMIT)
        def api_health():
            checks = {'db': False, 'storage': False}
            try:
                module.core.db.session.execute(text('SELECT 1'))
                checks['db'] = True
            except Exception:
                pass
            try:
                # exists() returning a bool (either value) proves the backend answers.
                module.core.storage.exists('__api_probe__')
                checks['storage'] = True
            except Exception:
                pass
            status = 200 if all(checks.values()) else 503
            return jsonify(checks), status

        # --- Invoices --------------------------------------------------- #

        @bp.route('/invoices', methods=['GET'])
        @require_token
        @limit(READ_LIMIT)
        def api_invoices_list():
            return module._handle(module._list_invoices)

        @bp.route('/invoices', methods=['POST'])
        @require_token
        @limit(WRITE_LIMIT)
        def api_invoices_create():
            return module._handle(module._create_invoice)

        @bp.route('/invoices/<int:invoice_id>', methods=['GET'])
        @require_token
        @limit(READ_LIMIT)
        def api_invoice_get(invoice_id):
            return module._handle(module._get_invoice, invoice_id)

        @bp.route('/invoices/<int:invoice_id>', methods=['PATCH'])
        @require_token
        @limit(WRITE_LIMIT)
        def api_invoice_update(invoice_id):
            return module._handle(module._update_invoice, invoice_id)

        @bp.route('/invoices/<int:invoice_id>', methods=['DELETE'])
        @require_token
        @limit(WRITE_LIMIT)
        def api_invoice_delete(invoice_id):
            return module._handle(module._delete_invoice, invoice_id)

        @bp.route('/invoices/<int:invoice_id>/pdf', methods=['GET'])
        @require_token
        @limit(HEAVY_LIMIT)
        def api_invoice_pdf(invoice_id):
            return module._handle(module._invoice_pdf, invoice_id)

        # --- Customers -------------------------------------------------- #

        @bp.route('/customers', methods=['GET'])
        @require_token
        @limit(READ_LIMIT)
        def api_customers_list():
            return module._handle(module._list_customers)

        # --- Expenses (only when the expenses module is enabled) -------- #

        @bp.route('/expenses', methods=['GET'])
        @require_token
        @limit(READ_LIMIT)
        def api_expenses_list():
            return module._handle(module._list_expenses)

        # --- Dashboard summary ----------------------------------------- #

        @bp.route('/dashboard/summary', methods=['GET'])
        @require_token
        @limit(READ_LIMIT)
        def api_dashboard_summary():
            return module._handle(module._dashboard_summary)

        # --- Exchange rates -------------------------------------------- #

        @bp.route('/rates', methods=['GET'])
        @require_token
        @limit(READ_LIMIT)
        def api_rates():
            return module._handle(module._rates)

        # --- Module-contributed endpoints (load-order safe dispatch) ---- #
        # Other modules add routes via get_api_routes(); they are served under
        # /api/v1/m/<module_id>/<subpath> and looked up lazily at request time.
        @bp.route('/m/<module_id>/<path:subpath>',
                  methods=['GET', 'POST', 'PATCH', 'DELETE'])
        @require_token
        @limit(READ_LIMIT)
        def api_module_dispatch(module_id, subpath):
            return module._handle(module._dispatch_module_route, module_id, subpath)

        # --- JSON error handlers for the limiter / payload guards ------- #
        @bp.app_errorhandler(429)
        def _api_rate_limited(e):
            if request.path.startswith('/api/'):
                return _json_error(429, 'rate_limited', 'Too many requests')
            return e

        @bp.app_errorhandler(413)
        def _api_too_large(e):
            if request.path.startswith('/api/'):
                return _json_error(413, 'payload_too_large', 'Request body too large')
            return e

        app.register_blueprint(bp)

        # Token replaces CSRF for the API (mirror csrf.exempt(auth_bp), app.py).
        if csrf:
            csrf.exempt(bp)

    # ------------------------------------------------------------------ #
    # Handler dispatch wrapper
    # ------------------------------------------------------------------ #

    def _handle(self, fn, *args):
        """Run a handler, mapping ApiError / ValueError to JSON responses.

        Handlers return either a Flask Response (passed through) or a
        (data, status) tuple that gets JSON-serialized."""
        try:
            result = fn(*args)
        except Exception as e:
            # ApiError may arrive from a module handler that imported it via the
            # normal import system, giving a *different* class object than the one
            # loaded by the module loader (importlib exec, not in sys.modules). So
            # match structurally by class name, not identity.
            if isinstance(e, ApiError) or type(e).__name__ == 'ApiError':
                return _json_error(getattr(e, 'status', 500),
                                   getattr(e, 'code', 'server_error'),
                                   getattr(e, 'message', str(e)))
            if isinstance(e, ValueError):
                # invoice_service raises ValueError on the PAID lock (API.MD §10).
                return _json_error(409, 'conflict', str(e))
            self.core.db.session.rollback()
            self.logger.error('API handler error: %s', e, exc_info=True)
            return _json_error(500, 'server_error', 'Unexpected server error')

        if isinstance(result, tuple):
            data, status = result
            return jsonify(data), status
        return result  # already a Flask Response

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    def _list_invoices(self):
        from app import Invoice
        svc = self.core.invoice_service

        status = request.args.get('status')
        try:
            page = max(1, int(request.args.get('page', 1)))
            per_page = min(100, max(1, int(request.args.get('per_page', 20))))
        except ValueError:
            raise ApiError(400, 'bad_request', 'page/per_page must be integers')

        query = Invoice.query
        if status:
            query = query.filter_by(status=status)
        total = query.count()
        rows = (query.order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
                .offset((page - 1) * per_page).limit(per_page).all())

        return {
            'data': [self._serialize_invoice(inv, svc) for inv in rows],
            'page': page,
            'per_page': per_page,
            'total': total,
        }, 200

    def _create_invoice(self):
        from app import Invoice, InvoiceItem, Customer, db
        body = self._json_body()
        svc = self.core.invoice_service

        client_name = (body.get('client_name') or '').strip()
        if not client_name:
            raise ApiError(400, 'bad_request', 'client_name is required')

        items = body.get('items')
        if not isinstance(items, list) or not items:
            raise ApiError(400, 'bad_request', 'items must be a non-empty array')

        settings = self.core.get_settings()
        currency = (body.get('currency')
                    or (settings.default_currency if settings else None)
                    or 'USD').upper()

        invoice_date = (self._parse_date(body['invoice_date'], 'invoice_date')
                        if body.get('invoice_date') else date.today())
        due_date = (self._parse_date(body['due_date'], 'due_date')
                    if body.get('due_date') else None)
        date_str = invoice_date.strftime('%Y-%m-%d')

        # Validate + total the line items.
        items_data = []
        total = 0.0
        for idx, raw in enumerate(items):
            if not isinstance(raw, dict):
                raise ApiError(400, 'bad_request', f'items[{idx}] must be an object')
            desc = (raw.get('description') or '').strip()
            try:
                qty = float(raw.get('quantity', 1))
                unit = float(raw.get('unit_price_usd', raw.get('unit_price', 0)))
            except (TypeError, ValueError):
                raise ApiError(400, 'bad_request',
                               f'items[{idx}] quantity/unit_price must be numbers')
            if not desc:
                raise ApiError(400, 'bad_request',
                               f'items[{idx}].description is required')
            subtotal = qty * unit
            items_data.append({'description': desc, 'quantity': qty,
                               'unit_price_usd': unit, 'subtotal_usd': subtotal})
            total += subtotal

        # Server computes amounts via the currency service (API.MD §8) —
        # the client is never trusted to compute money.
        cur = self.core.currency_service
        try:
            amount_eur, _, _ = cur.convert(total, currency, 'EUR', date_str)
            amount_usd, _, _ = cur.convert(total, currency, 'USD', date_str)
            exchange_rate, _ = cur.get_rate('USD', 'EUR', date_str)
        except Exception as e:
            self.logger.error('Rate lookup failed: %s', e)
            raise ApiError(400, 'bad_request',
                           f'Could not get exchange rate for {currency}')

        # Resolve optional customer / bank references.
        customer_id = body.get('customer_id')
        if customer_id is not None:
            try:
                customer_id = int(customer_id)
            except (TypeError, ValueError):
                raise ApiError(400, 'bad_request', 'customer_id must be an integer')
            if not db.session.get(Customer, customer_id):
                raise ApiError(404, 'not_found',
                               f'Customer #{customer_id} not found')
        bank_id = body.get('bank_id')
        if bank_id is not None:
            try:
                bank_id = int(bank_id)
            except (TypeError, ValueError):
                raise ApiError(400, 'bad_request', 'bank_id must be an integer')

        invoice_number = (body.get('invoice_number')
                          or self._gen_invoice_number(Invoice, invoice_date.year))
        if Invoice.query.filter_by(invoice_number=invoice_number).first():
            raise ApiError(409, 'conflict',
                           f'invoice_number {invoice_number} already exists')

        status = body.get('status', 'pending')
        first = items_data[0]
        invoice = Invoice(
            invoice_number=invoice_number,
            client_name=client_name,
            amount_usd=amount_usd,
            amount_eur=amount_eur,
            exchange_rate=exchange_rate,
            invoice_date=invoice_date,
            due_date=due_date,
            description=first['description'],
            quantity=first['quantity'],
            unit_price_usd=first['unit_price_usd'],
            notes=body.get('notes', ''),
            status=status,
            currency=currency,
            payment_method=body.get('payment_method', 'Bank Transfer'),
            customer_id=customer_id,
            bank_id=bank_id,
        )
        db.session.add(invoice)
        db.session.flush()
        for item in items_data:
            db.session.add(InvoiceItem(invoice_id=invoice.id, **item))
        db.session.commit()

        self.core.log_activity('invoice_created', 'invoice',
                               {'invoice_number': invoice.invoice_number,
                                'client': client_name,
                                'currency': currency,
                                'amount_eur': round(amount_eur, 2),
                                'via': 'api'})

        # Let modules react (e.g. invoice_attachments) — same as the web path.
        mm = self.core.module_manager
        if mm:
            mm.on_invoice_created(invoice, request)
            db.session.refresh(invoice)

        base = f'/api/{API_VERSION}/invoices/{invoice.id}'
        return {
            'data': {'id': invoice.id,
                     'invoice_number': invoice.invoice_number,
                     'status': invoice.status},
            'links': {'self': base, 'pdf': f'{base}/pdf'},
        }, 201

    def _get_invoice(self, invoice_id):
        svc = self.core.invoice_service
        inv = svc.get(invoice_id)
        if not inv:
            raise ApiError(404, 'not_found', f'Invoice #{invoice_id} not found')
        data = self._serialize_invoice(inv, svc)
        data['items'] = [{
            'description': it.description,
            'quantity': it.quantity,
            'unit_price_usd': it.unit_price_usd,
            'subtotal_usd': it.subtotal_usd,
        } for it in inv.items]
        return {'data': data}, 200

    def _update_invoice(self, invoice_id):
        svc = self.core.invoice_service
        if not svc.get(invoice_id):
            raise ApiError(404, 'not_found', f'Invoice #{invoice_id} not found')
        body = self._json_body()

        fields = {}
        for key, value in body.items():
            if key not in PATCHABLE_FIELDS:
                raise ApiError(400, 'bad_request', f'Field "{key}" is not updatable')
            if key in ('due_date', 'invoice_date') and value is not None:
                value = self._parse_date(value, key)
            fields[key] = value
        if not fields:
            raise ApiError(400, 'bad_request', 'No updatable fields supplied')

        # update() raises ValueError on the PAID lock → mapped to 409 by _handle.
        inv = svc.update(invoice_id, **fields)
        return {'data': self._serialize_invoice(inv, svc)}, 200

    def _delete_invoice(self, invoice_id):
        from app import db
        svc = self.core.invoice_service
        inv = svc.get(invoice_id)
        if not inv:
            raise ApiError(404, 'not_found', f'Invoice #{invoice_id} not found')
        if svc.is_locked(inv):
            raise ApiError(409, 'conflict',
                           f'Invoice #{inv.invoice_number} is PAID and cannot be deleted')
        number = inv.invoice_number
        db.session.delete(inv)  # items cascade via relationship
        db.session.commit()
        self.core.log_activity('invoice_deleted', 'invoice',
                               {'invoice_number': number, 'via': 'api'})
        return {'data': {'deleted': True, 'invoice_number': number}}, 200

    def _invoice_pdf(self, invoice_id):
        svc = self.core.invoice_service
        inv = svc.get(invoice_id)
        if not inv:
            raise ApiError(404, 'not_found', f'Invoice #{invoice_id} not found')
        if not svc.has_pdf(inv):
            raise ApiError(404, 'not_found', 'No PDF for this invoice')
        return svc.send_pdf(inv)  # Flask response, passed through by _handle

    def _list_customers(self):
        from app import Customer
        rows = Customer.query.order_by(Customer.name).all()
        return {'data': [self._serialize_customer(c) for c in rows]}, 200

    def _list_expenses(self):
        mm = self.core.module_manager
        if not mm or 'expenses' not in mm.modules:
            raise ApiError(404, 'not_found', 'Expenses module is not enabled')
        from app import Expense
        try:
            page = max(1, int(request.args.get('page', 1)))
            per_page = min(100, max(1, int(request.args.get('per_page', 20))))
        except ValueError:
            raise ApiError(400, 'bad_request', 'page/per_page must be integers')
        query = Expense.query.order_by(Expense.expense_date.desc(), Expense.id.desc())
        total = query.count()
        rows = query.offset((page - 1) * per_page).limit(per_page).all()
        return {
            'data': [self._serialize_expense(e) for e in rows],
            'page': page, 'per_page': per_page, 'total': total,
        }, 200

    def _dashboard_summary(self):
        from app import Invoice
        settings = self.core.get_settings()
        base_currency = (settings.base_currency if settings else 'EUR') or 'EUR'
        vat_rate = (settings.default_vat_rate / 100.0
                    if settings and settings.default_vat_rate is not None else 0.21)
        irpf_rate = (settings.default_irpf_rate / 100.0
                     if settings and settings.default_irpf_rate is not None else 0.20)

        invoices = Invoice.query.all()
        by_status = {}
        total_eur = 0.0
        total_usd = 0.0
        vat_base_eur = 0.0
        for inv in invoices:
            by_status[inv.status] = by_status.get(inv.status, 0) + 1
            total_eur += inv.amount_eur or 0.0
            total_usd += inv.amount_usd or 0.0
            # VAT only applies to 'standard' tax_type customers (DOCS §4.1).
            cust = inv.customer
            if cust and cust.tax_type == 'standard':
                vat_base_eur += inv.amount_eur or 0.0

        return {
            'data': {
                'invoice_count': len(invoices),
                'by_status': by_status,
                'total_invoiced_eur': round(total_eur, 2),
                'total_invoiced_usd': round(total_usd, 2),
                'base_currency': base_currency,
                'vat_rate': vat_rate,
                'irpf_rate': irpf_rate,
                'estimated_vat_eur': round(vat_base_eur * vat_rate, 2),
                'estimated_irpf_eur': round(total_eur * irpf_rate, 2),
            }
        }, 200

    def _rates(self):
        frm = (request.args.get('from') or '').upper()
        to = (request.args.get('to') or '').upper()
        date_str = request.args.get('date') or date.today().strftime('%Y-%m-%d')
        if not frm or not to:
            raise ApiError(400, 'bad_request',
                           'Query params "from" and "to" are required')
        # Validate the date format.
        self._parse_date(date_str, 'date')
        rate, actual_date = self.core.currency_service.get_rate(frm, to, date_str)
        if rate is None:
            raise ApiError(400, 'bad_request',
                           f'No rate available for {frm}->{to} on {date_str}')
        return {'from': frm, 'to': to, 'rate': rate, 'actual_date': actual_date}, 200

    @staticmethod
    def _match_route_path(pattern, subpath):
        """Match a declared route path against a request sub-path.

        Supports Flask-style segments `<name>` and `<int:name>`. Returns a dict
        of captured params on match, or None on no match.
        """
        pseg = pattern.strip('/').split('/')
        sseg = subpath.strip('/').split('/')
        if len(pseg) != len(sseg):
            return None
        params = {}
        for p, s in zip(pseg, sseg):
            if p.startswith('<') and p.endswith('>'):
                spec = p[1:-1]
                typ, _, name = spec.partition(':')
                if not name:
                    typ, name = 'string', typ
                if typ == 'int':
                    if not s.lstrip('-').isdigit():
                        return None
                    params[name] = int(s)
                else:
                    params[name] = s
            elif p != s:
                return None
        return params

    def _dispatch_module_route(self, module_id, subpath):
        """Route a /api/v1/m/<module_id>/<subpath> request to a module handler
        declared via get_api_routes(). Supports path params and 405."""
        path_matched = False
        for route in self.core.module_manager.get_api_routes():
            if route['module_id'] != module_id:
                continue
            params = self._match_route_path(route['path'], subpath)
            if params is None:
                continue
            path_matched = True
            if request.method not in route.get('methods', ['GET']):
                continue
            return route['handler'](request, **params)
        if path_matched:
            raise ApiError(405, 'method_not_allowed',
                           f'{request.method} not allowed on /m/{module_id}/{subpath}')
        raise ApiError(404, 'not_found',
                       f'No API route /m/{module_id}/{subpath}')

    # ------------------------------------------------------------------ #
    # OpenAPI manifest (module-aware)
    # ------------------------------------------------------------------ #

    def _build_openapi(self):
        from app import APP_VERSION
        mm = self.core.module_manager

        def op(method, summary, has_body=False):
            spec = {'summary': summary, 'responses': {'200': {'description': 'OK'}}}
            if has_body:
                spec['requestBody'] = {
                    'required': True,
                    'content': {'application/json': {'schema': {'type': 'object'}}},
                }
            return {method: spec}

        paths = {
            '/': op('get', 'API index + discovery'),
            '/openapi.json': op('get', 'This OpenAPI manifest'),
            '/health': op('get', 'DB/storage health probe'),
        }
        paths['/invoices'] = {
            'get': {'summary': 'List invoices (status, page, per_page)',
                    'responses': {'200': {'description': 'OK'}}},
            'post': {'summary': 'Create invoice',
                     'requestBody': {'required': True,
                                     'content': {'application/json':
                                                 {'schema': {'type': 'object'}}}},
                     'responses': {'201': {'description': 'Created'}}},
        }
        paths['/invoices/{id}'] = {
            'get': {'summary': 'Get one invoice + items',
                    'responses': {'200': {'description': 'OK'}}},
            'patch': {'summary': 'Update invoice fields (blocked if PAID)',
                      'requestBody': {'required': True,
                                      'content': {'application/json':
                                                  {'schema': {'type': 'object'}}}},
                      'responses': {'200': {'description': 'OK'},
                                    '409': {'description': 'PAID lock'}}},
            'delete': {'summary': 'Delete invoice (blocked if PAID)',
                       'responses': {'200': {'description': 'OK'},
                                     '409': {'description': 'PAID lock'}}},
        }
        paths['/invoices/{id}/pdf'] = op('get', 'Download invoice PDF bytes')
        paths['/customers'] = op('get', 'List customers')
        paths['/dashboard/summary'] = op('get', 'Money summary + tax totals')
        paths['/rates'] = op('get', 'Exchange rate lookup (from, to, date)')

        # Module-aware: only advertise expenses when its module is enabled.
        if mm and 'expenses' in mm.modules:
            paths['/expenses'] = op('get', 'List expenses')

        # Reports' heavy generate route lives on its own blueprint (dedicated
        # rate limit), so advertise it explicitly when reports is enabled.
        if mm and 'reports' in mm.modules:
            paths['/reports/generate'] = {
                'post': {
                    'summary': 'Generate a financial report PDF/ZIP (heavy, 10/min)',
                    'requestBody': {'required': True,
                                    'content': {'application/json':
                                                {'schema': {'type': 'object'}}}},
                    'responses': {'200': {'description': 'PDF or ZIP file'}},
                }
            }

        # Module-contributed endpoints (get_api_routes()).
        if mm:
            for route in mm.get_api_routes():
                # Render Flask-style <int:id> / <id> segments as {id} for OpenAPI.
                segs = []
                for s in route['path'].strip('/').split('/'):
                    if s.startswith('<') and s.endswith('>'):
                        name = s[1:-1].partition(':')[2] or s[1:-1]
                        segs.append('{' + name + '}')
                    else:
                        segs.append(s)
                p = f"/m/{route['module_id']}/{'/'.join(segs)}"
                for method in route.get('methods', ['GET']):
                    paths.setdefault(p, {})[method.lower()] = {
                        'summary': route.get('summary',
                                             f"{route['module_id']} endpoint"),
                        'responses': {'200': {'description': 'OK'}},
                    }

        return {
            'openapi': '3.1.0',
            'info': {'title': 'ContaAutónomo API', 'version': str(APP_VERSION)},
            'servers': [{'url': f'/api/{API_VERSION}'}],
            'components': {
                'securitySchemes': {
                    'ApiTokenAuth': {'type': 'apiKey', 'in': 'header',
                                     'name': 'X-API-Token'},
                },
            },
            'security': [{'ApiTokenAuth': []}],
            'paths': paths,
        }

    # ------------------------------------------------------------------ #
    # Settings → API tab
    # ------------------------------------------------------------------ #

    def get_settings_html(self, settings):
        token = ''
        try:
            token = self._ensure_token()
        except Exception as e:
            self.logger.debug('Token unavailable for settings render: %s', e)

        base = f'/api/{API_VERSION}'
        manifest = f'{base}/openapi.json'

        if token:
            token_block = (
                '<p>Send this token in the <code>X-API-Token</code> header on '
                'every request.</p>'
                '<div style="display:flex;gap:.5rem;align-items:center;">'
                f'<input type="text" id="api-token-field" readonly value="{token}" '
                'style="flex:1;font-family:monospace;" onclick="this.select()">'
                '<button type="button" class="btn btn-secondary" '
                "onclick=\"(function(b){var i=document.getElementById('api-token-field');"
                'i.select();'
                'if(navigator.clipboard){navigator.clipboard.writeText(i.value);}'
                "else{document.execCommand('copy');}"
                "var t=b.textContent;b.textContent='Copied!';"
                'setTimeout(function(){b.textContent=t;},1200);})(this)">Copy</button>'
                '</div>'
                # Quick-start: real token templated in (url-safe charset, no injection).
                '<p style="margin-top:.75rem;">Quick start:</p>'
                '<pre style="white-space:pre-wrap;background:#f5f5f5;padding:.5rem;'
                'border-radius:4px;">'
                f'curl -H "X-API-Token: {token}" \\\n'
                f'  $ORIGIN{base}/invoices\n\n'
                f'curl -X POST -H "X-API-Token: {token}" \\\n'
                '  -H "Content-Type: application/json" \\\n'
                '  -d \'{"client_name":"ACME","items":'
                '[{"description":"Work","quantity":1,"unit_price_usd":100}]}\' \\\n'
                f'  $ORIGIN{base}/invoices'
                '</pre>'
                '<small>Replace <code>$ORIGIN</code> with your server URL '
                '(e.g. <code>https://your-host</code>).</small>'
            )
        else:
            token_block = '<p><em>No token yet — save once to generate one.</em></p>'

        return (
            '<h3>AI Communication API</h3>'
            f'<p>Base URL: <code>{base}</code> · '
            f'Manifest: <a href="{manifest}" target="_blank"><code>{manifest}</code></a></p>'
            '<div class="form-group">'
            '<label>API Token</label>'
            f'{token_block}'
            '</div>'
            '<div class="form-group">'
            '<label><input type="checkbox" name="api_rotate_token" value="1"> '
            'Rotate token on save</label>'
            '<small style="display:block;color:#a00;">Rotating invalidates the '
            'current token immediately. Serve the API over HTTPS only '
            '(set <code>FORCE_HTTPS=1</code>).</small>'
            '</div>'
        )

    def save_settings(self, settings, form):
        # Only act on this tab's submit, and only when asked to rotate.
        if 'api_rotate_token' not in form:
            return
        if form.get('api_rotate_token') == '1':
            self._rotate_token()
            self.core.flash('API token rotated.', 'success')
