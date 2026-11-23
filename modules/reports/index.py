#!/usr/bin/env python3
"""
Reports Module
Generate professional financial reports (PDF) for tax authorities or banks.
"""

from module_manager import BaseModule
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify
from datetime import datetime, date
import calendar
import io
import os
import importlib.util


DEFAULT_TEMPLATE = 'official_template'


class ReportsModule(BaseModule):

    @property
    def module_id(self):
        return 'reports'

    @property
    def name(self):
        return 'Reports'

    @property
    def description(self):
        return 'Generate professional financial reports (PDF) for tax authorities or banks'

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Reports', 'endpoint': 'reports.reports_index', 'icon': '📊'}
        ]

    def register_models(self, db):
        self._db = db

        # Reports only needs Invoice and Settings from core
        class Invoice(db.Model):
            __tablename__ = 'invoice'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            amount_usd = db.Column(db.Float)
            amount_eur = db.Column(db.Float)
            currency = db.Column(db.String(10))
            exchange_rate = db.Column(db.Float)

        class Settings(db.Model):
            __tablename__ = 'settings'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)

        class ReportsConfig(db.Model):
            """The report template (before 2.0.0 a column of the core Settings)."""
            __tablename__ = 'reports_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            report_template = db.Column(db.String(100), default=DEFAULT_TEMPLATE)

        self.Invoice = Invoice
        self.Settings = Settings
        self.ReportsConfig = ReportsConfig
        return {'ReportsConfig': ReportsConfig}

    # --- Settings → Reports ------------------------------------------------ #

    @property
    def settings_tab(self):
        return {'id': 'reports', 'label': 'Reports'}

    def get_settings_html(self, settings):
        return self.render_settings_template(
            'reports_settings_panel.html', templates=self._report_templates(),
            current=self._report_template())

    def save_settings(self, settings, form):
        name = form.get('report_template')
        if name in self._report_templates():
            self._get_config().report_template = name

    @staticmethod
    def _report_templates():
        """The report templates in report_templates/ (file names without .py)."""
        folder = os.path.join(os.path.dirname(__file__), 'report_templates')
        return sorted(f[:-3] for f in os.listdir(folder)
                      if f.endswith('.py') and not f.startswith('_'))

    def _report_template(self):
        """The chosen template, or the default when it is not available."""
        name = self._get_config().report_template
        return name if name in self._report_templates() else DEFAULT_TEMPLATE

    def _get_config(self):
        cfg = self.ReportsConfig.query.first()
        if cfg is None:
            cfg = self.ReportsConfig(report_template=self._legacy_template() or DEFAULT_TEMPLATE)
            self._db.session.add(cfg)
            self._db.session.commit()
        return cfg

    def _legacy_template(self):
        """The template chosen in the core Settings table before 2.0.0 (the
        old column stays in an upgraded database; a new one does not have it)."""
        from sqlalchemy import text
        try:
            row = self._db.session.execute(text('SELECT * FROM settings LIMIT 1')).mappings().first()
        except Exception:
            return None
        return (row or {}).get('report_template')

    def _get_available_sections(self):
        """Collect available report sections from enabled modules."""
        sections = [
            {'id': 'income', 'title': 'Income (Invoices)',
             'description': 'Invoice data: number, date, client, amount and status.',
             'selected_default': True, 'attach_default': False}
        ]
        mgr = self.core.module_manager
        if mgr:
            for s in mgr.get_report_sections():
                sections.append({
                    'id': s.get('id', ''),
                    'title': s.get('title', s.get('id', 'Unknown')),
                    'description': s.get('description', ''),
                    'has_files': s.get('has_files', False),
                    # Per-section UI defaults: whether the section checkbox and
                    # its "attach files" checkbox start checked.
                    'selected_default': s.get('selected_default', True),
                    'attach_default': s.get('attach_default', False),
                })
        return sections

    def register_routes(self, app):
        bp = Blueprint('reports', __name__,
                       template_folder='templates',
                       url_prefix='/reports')
        login_required = self.core.login_required
        module = self
        self._app = app

        @bp.route('/')
        @login_required
        def reports_index():
            current_year = datetime.now().year
            available_sections = module._get_available_sections()
            settings = module.core.get_settings()
            base_currency = settings.base_currency if settings and settings.base_currency else 'EUR'
            return render_template('reports.html',
                                   current_year=current_year,
                                   years=module.core.data_years(),
                                   available_sections=available_sections,
                                   base_currency=base_currency)

        @bp.route('/generate', methods=['POST'])
        @login_required
        def reports_generate():
            return module._generate_report()

        @bp.route('/file-list', methods=['POST'])
        @login_required
        def file_list():
            return module._get_file_list()

        app.register_blueprint(bp)

        # --- Dedicated REST API blueprint for heavy report generation ---
        # Kept off the generic /api/v1/m dispatcher (which shares READ_LIMIT) so
        # generation gets its own HEAVY rate limit.
        from app import csrf, limiter
        HEAVY = '10/minute'  # mirror modules.api.index.HEAVY_LIMIT

        api_bp = Blueprint('reports_api', __name__, url_prefix='/api/v1/reports')

        def generate_view():
            return module._api_generate(request)

        if limiter:
            generate_view = limiter.limit(HEAVY)(generate_view)
        api_bp.add_url_rule('/generate', 'reports_api_generate',
                            generate_view, methods=['POST'])
        app.register_blueprint(api_bp)
        if csrf:
            csrf.exempt(api_bp)  # token replaces CSRF, same as the api module

    # --- REST API: read endpoints via the generic /api/v1/m dispatcher ---

    def get_api_routes(self):
        """Expose the list of available report sections (read-only).

        Heavy PDF generation is served separately at
        `POST /api/v1/reports/generate` (own HEAVY rate limit, registered in
        register_routes) rather than through this generic dispatcher. This read
        endpoint lets an AI discover what sections exist first.
        """
        return [
            {'path': 'sections', 'methods': ['GET'],
             'handler': self._api_sections,
             'summary': 'List available report sections'},
        ]

    def _api_sections(self, request):
        sections = self._get_available_sections()
        data = [{'id': s.get('id'), 'title': s.get('title'),
                 'description': s.get('description', '')} for s in sections]
        return {'data': data, 'total': len(data)}, 200

    def _token_ok(self, sent):
        """Whether `sent` is the API token; False without a provider of api.check_token."""
        functions = getattr(self.core, 'functions', None)
        if not sent or functions is None or not functions.available('api.check_token'):
            return False
        if functions.problem_with('api.check_token', {'token': sent}):
            return False
        return functions.call('api.check_token', token=sent) is True

    def _api_generate(self, request):
        """REST: build a report from a JSON body and return the PDF/ZIP bytes.

        Token-authenticated (via the api module) and HEAVY rate-limited by the
        route decorator. Body:
            {
              "sections": ["income", "expenses"],   # required, non-empty
              "year": 2026,                          # default: current year
              "period_type": "quarter"|"quarters"|"year",
              "quarters": [1,2],                     # for quarter/quarters
              "include_cancelled": false,
              "currency_mode": "base"|"original",
              "file_ids": {"documents": [1,2]},      # optional, per section
              "include_files": ["documents"]         # optional, attach files as ZIP
            }
        """
        # Auth: the API token check of whatever provides api.check_token.
        if not self._token_ok(request.headers.get('X-API-Token', '')):
            return jsonify(error='unauthorized',
                           message='Missing or invalid X-API-Token'), 401

        body = request.get_json(silent=True) or {}
        sections = body.get('sections')
        if not isinstance(sections, list) or not sections:
            return jsonify(error='bad_request',
                           message='sections must be a non-empty array'), 400
        try:
            year = int(body.get('year', datetime.now().year))
        except (TypeError, ValueError):
            return jsonify(error='bad_request', message='year must be an integer'), 400

        period_type = body.get('period_type', 'quarter')
        if period_type == 'year':
            quarters = [1, 2, 3, 4]
        else:
            quarters = body.get('quarters') or [1]
            try:
                quarters = [int(q) for q in quarters]
            except (TypeError, ValueError):
                return jsonify(error='bad_request',
                               message='quarters must be a list of integers'), 400
            if not all(1 <= q <= 4 for q in quarters):
                return jsonify(error='bad_request',
                               message='quarters must be between 1 and 4'), 400

        file_ids = body.get('file_ids') or {}
        if not isinstance(file_ids, dict):
            return jsonify(error='bad_request', message='file_ids must be an object'), 400
        include_files_sids = body.get('include_files') or []

        try:
            data, filename, mimetype = self._build_report(
                selected_sections=sections, year=year, period_type=period_type,
                quarters=quarters,
                include_cancelled=bool(body.get('include_cancelled')),
                currency_mode=body.get('currency_mode', 'base'),
                file_ids=file_ids, include_files_sids=include_files_sids)
        except ValueError as e:
            # _build_report raises ValueError for invalid params. sections/quarters
            # are already validated above, so log the detail server-side and return
            # a generic message (don't echo raw exception text to the client).
            self.logger.warning('API report generation rejected: %s', e)
            return jsonify(error='bad_request',
                           message='Invalid report parameters'), 400
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('API report generation failed: %s', e, exc_info=True)
            return jsonify(error='server_error',
                           message='Report generation failed. Check server logs.'), 500

        self.core.log_activity('report_generated', 'system',
                               {'sections': sections, 'year': year,
                                'quarters': quarters, 'via': 'api'})
        return send_file(io.BytesIO(data), mimetype=mimetype,
                         as_attachment=True, download_name=filename)

    def _get_file_list(self):
        """AJAX: return list of documents for a section and period."""
        data = request.get_json(silent=True) or {}
        section_id = data.get('section_id', '')
        year = int(data.get('year', datetime.now().year))
        quarters = [int(q) for q in data.get('quarters', [1])]

        if not quarters:
            return jsonify({'files': []})

        start_month = (min(quarters) - 1) * 3 + 1
        end_month = max(quarters) * 3
        start_date = date(year, start_month, 1)
        last_day = calendar.monthrange(year, end_month)[1]
        end_date = date(year, end_month, last_day)

        # Find the section's list_fn from modules
        mgr = self.core.module_manager
        files = []
        if mgr:
            for section in mgr.get_report_sections():
                if section.get('id') == section_id and section.get('list_fn'):
                    files = section['list_fn'](start_date, end_date)
                    break
        return jsonify({'files': files})

    def _generate_report(self):
        """Web route: parse the form, build the report, send it as a download.

        Thin wrapper over `_build_report`; the heavy logic is shared with the
        REST API route (`_api_generate`)."""
        try:
            selected_sections = request.form.getlist('sections')
            year = int(request.form.get('year', datetime.now().year))
            period_type = request.form.get('period_type', 'quarter')
            include_cancelled = request.form.get('include_cancelled') == '1'
            currency_mode = request.form.get('currency_mode', 'base')

            if not selected_sections:
                flash('Please select at least one section for the report', 'danger')
                return redirect(url_for('reports.reports_index'))

            if period_type == 'quarter':
                quarters = [int(request.form.get('quarter', 1))]
            elif period_type == 'quarters':
                quarters = [int(q) for q in request.form.getlist('quarters')]
            else:
                quarters = [1, 2, 3, 4]
            if not quarters:
                flash('Please select at least one quarter', 'danger')
                return redirect(url_for('reports.reports_index'))

            # Per-section file selection from the form.
            file_ids, include_files_sids = {}, set()
            mgr = self.core.module_manager
            if mgr:
                for section in mgr.get_report_sections():
                    sid = section.get('id', '')
                    ids = request.form.getlist(f'file_ids_{sid}')
                    if ids:
                        file_ids[sid] = [int(i) for i in ids]
                    if request.form.get(f'include_files_{sid}') == '1':
                        include_files_sids.add(sid)

            data, filename, mimetype = self._build_report(
                selected_sections=selected_sections, year=year,
                period_type=period_type, quarters=quarters,
                include_cancelled=include_cancelled, currency_mode=currency_mode,
                file_ids=file_ids, include_files_sids=include_files_sids)
            return send_file(io.BytesIO(data), mimetype=mimetype,
                             as_attachment=True, download_name=filename)
        except ValueError as e:
            flash(str(e), 'danger')
            return redirect(url_for('reports.reports_index'))
        except Exception as e:
            self.logger.error('Error generating report: %s', e)
            flash('Error generating PDF. Check server logs.', 'danger')
            return redirect(url_for('reports.reports_index'))

    def _invoice_vat_lines(self, start_date, end_date):
        """Output VAT of each invoice in the period, keyed by invoice id as
        text — from the tax module that owns `vat.invoice_lines`. Without
        one, the report leaves the IVA columns blank ('–')."""
        functions = self.core.functions
        if not functions.available('vat.invoice_lines'):
            return {}
        try:
            return functions.call('vat.invoice_lines', date_from=start_date,
                                  date_to=end_date).get('lines') or {}
        except Exception as e:  # never break a report over its IVA columns
            self.logger.warning('vat.invoice_lines failed: %s', e)
            return {}

    def _build_report(self, *, selected_sections, year, period_type, quarters,
                      include_cancelled=False, currency_mode='base',
                      file_ids=None, include_files_sids=None):
        """Build a financial report from plain params (no request access).

        Collects data via module_manager.get_report_sections() and renders the
        configured report template. Returns (data_bytes, filename, mimetype).
        Raises ValueError on invalid input. Shared by the web form route and the
        REST API route.
        """
        from repositories import InvoiceRepository, SettingsRepository

        invoice_repo = InvoiceRepository(self._db, self.Invoice)
        settings_repo = SettingsRepository(self._db, self.Settings)
        file_ids = file_ids or {}
        include_files_sids = set(include_files_sids or [])

        if not selected_sections:
            raise ValueError('Please select at least one section for the report')
        if not quarters:
            raise ValueError('Please select at least one quarter')

        start_month = (min(quarters) - 1) * 3 + 1
        end_month = max(quarters) * 3
        start_date = date(year, start_month, 1)
        last_day = calendar.monthrange(year, end_month)[1]
        end_date = date(year, end_month, last_day)

        # Income data (core — included if user selected 'income')
        income_data = []
        settings = settings_repo.get_settings()
        base_currency = settings.base_currency if settings and settings.base_currency else 'EUR'

        if 'income' in selected_sections:
            invoices = invoice_repo.get_by_date_range(start_date, end_date, include_cancelled)
            vat_lines = self._invoice_vat_lines(start_date, end_date)
            for inv in invoices:
                if currency_mode == 'original':
                    # Show amount in the invoice's own currency
                    inv_currency = getattr(inv, 'currency', None) or 'USD'
                    if inv_currency == 'EUR':
                        display_amount = inv.amount_eur
                    else:
                        display_amount = inv.amount_usd
                    display_currency = inv_currency
                else:
                    # Convert everything to base currency
                    display_amount = inv.amount_eur if base_currency == 'EUR' else inv.amount_usd
                    display_currency = base_currency

                vat = vat_lines.get(str(inv.id), {})
                income_data.append({
                    'invoice_number': inv.invoice_number,
                    'invoice_date': inv.invoice_date.strftime('%d/%m/%Y'),
                    'client_name': inv.client_name,
                    'amount': display_amount,
                    'currency': display_currency,
                    'amount_eur': inv.amount_eur,
                    'status': inv.status,
                    'vat_rate_pct': vat.get('vat_rate_pct'),
                    'vat_eur': vat.get('vat_eur'),
                    'reverse_charge': bool(vat.get('reverse_charge')),
                })

        # Collect data from enabled modules for selected sections
        expenses_data = []
        ss_data = []
        extra_sections = []
        mgr = self.core.module_manager
        if mgr:
            for section in mgr.get_report_sections():
                sid = section.get('id', '')
                if sid not in selected_sections:
                    continue
                query_fn = section.get('query_fn')
                if not query_fn:
                    continue
                # Known section types with dedicated rendering
                if sid == 'expenses':
                    expenses_data = query_fn(start_date, end_date)
                elif sid == 'ss_payments':
                    ss_data = query_fn(start_date, end_date)
                else:
                    # Generic section — pass selected doc IDs if caller picked specific files
                    selected_ids = file_ids.get(sid) or None
                    try:
                        data = query_fn(start_date, end_date, doc_ids=selected_ids)
                    except TypeError:
                        data = query_fn(start_date, end_date)
                    if data:
                        extra_sections.append({
                            'id': sid,
                            'title': section.get('title', sid),
                            'columns': section.get('columns'),
                            'data': data,
                            'total_field': section.get('total_field'),
                        })

        # Period text
        if period_type == 'year':
            period_text = f"Full Year {year}"
        elif len(quarters) == 1:
            period_text = f"Q{quarters[0]} {year}"
        else:
            quarters_str = ', '.join([f"Q{q}" for q in sorted(quarters)])
            period_text = f"{quarters_str} {year}"

        has_expenses = len(expenses_data) > 0
        has_income = len(income_data) > 0
        has_any_data = has_income or has_expenses or len(ss_data) > 0 or len(extra_sections) > 0

        # Build report title from selected sections
        if len(selected_sections) == 1 and selected_sections[0] == 'income':
            report_title = 'Income Report'
        elif len(selected_sections) == 1 and selected_sections[0] == 'expenses':
            report_title = 'Expenses Report'
        else:
            report_title = 'Financial Report'

        report_data = {
            'report_type': report_title,
            'period_text': period_text,
            'currency_mode': currency_mode,
            'base_currency': base_currency,
            'show_income': 'income' in selected_sections and has_income,
            'show_expenses': 'expenses' in selected_sections and has_expenses,
            'show_summary': has_any_data and len(selected_sections) > 1,
            'income_data': income_data,
            'expenses_data': expenses_data,
            'ss_data': ss_data,
            'extra_sections': extra_sections,
        }

        # Load report template
        template_name = self._report_template()
        template_path = os.path.join(os.path.dirname(__file__), 'report_templates', f'{template_name}.py')

        spec = importlib.util.spec_from_file_location(template_name, template_path)
        template_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(template_module)

        buffer = io.BytesIO()
        template_module.generate_report(buffer, report_data, settings)

        # Check if any section requested file inclusion
        include_files = {}
        if mgr:
            for section in mgr.get_report_sections():
                sid = section.get('id', '')
                if sid in selected_sections and section.get('files_fn'):
                    if sid in include_files_sids:
                        include_files[sid] = section['files_fn']

        if include_files:
            # Generate ZIP with PDF report + attached files
            import zipfile
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Add the PDF report
                pdf_filename = f"report_{period_text.replace(' ', '_')}.pdf"
                zf.writestr(pdf_filename, buffer.getvalue())

                # Add files from each section
                for sid, files_fn in include_files.items():
                    try:
                        # Pass selected document IDs if caller picked specific ones
                        selected_ids = file_ids.get(sid) or None
                        try:
                            files = files_fn(start_date, end_date, doc_ids=selected_ids)
                        except TypeError:
                            files = files_fn(start_date, end_date)  # fallback for modules without doc_ids param
                    except Exception as e:
                        self.logger.error('Error listing files for section %s: %s', sid, e)
                        continue
                    for f in files:
                        # One unreadable file must not drop the rest of the section.
                        try:
                            result = self.core.storage.get(f['storage_key'])
                        except Exception as e:
                            self.logger.error('Error reading file for section %s: %s', sid, e)
                            continue
                        if not result:
                            continue
                        file_bytes, resolved_name = result
                        name = str(f['name']).replace('/', '-').replace('\\', '-')
                        # Files may be PDFs or images; when the section could not
                        # tell the type (opaque storage key), take the extension
                        # from the name the storage backend resolved.
                        if '.' not in name and resolved_name and '.' in resolved_name:
                            name = f"{name}.{resolved_name.rsplit('.', 1)[-1]}"
                        zf.writestr(f"{sid}/{name}", file_bytes)

            zip_buffer.seek(0)
            zip_filename = f"report_{period_text.replace(' ', '_')}.zip"
            return zip_buffer.getvalue(), zip_filename, 'application/zip'

        filename = f"report_{period_text.replace(' ', '_')}.pdf"
        return buffer.getvalue(), filename, 'application/pdf'
