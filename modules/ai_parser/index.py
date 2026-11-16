#!/usr/bin/env python3
"""
AI Parser Module
Pluggable AI-powered document parsing for invoices and expenses.
Supports multiple backends: OpenAI, Anthropic Claude, Google Document AI.
"""

from module_manager import BaseModule
from settings_forms import secret_from_form
from flask import Blueprint, request, jsonify, render_template
from secret_box import EncryptedString, encrypt_existing

from . import catalog
from .catalog import CLI_DEFAULT_MODELS, DEFAULT_MODELS
from .cli_agents import (ALLOW_ENV, CLI_PROVIDERS, SCANNED_PDF_MESSAGE, CLIError,
                         ScannedPDF, cli_allowed, valid_model)
from .media import UNSUPPORTED_MESSAGE, UnsupportedFile
from .providers import (
    NOT_A_DOCUMENT_MESSAGE,
    PROVIDERS,
    NotADocument,
    describe_error,
)

# API providers plus the CLI agents, in the order the settings tab shows them.
ALL_PROVIDERS = {**PROVIDERS, **CLI_PROVIDERS}
MODEL_PROVIDERS = ('openai', 'anthropic', *CLI_PROVIDERS)


def rejection_message(exc):
    """The user message for a file the parser refuses (422). Fixed strings,
    so no exception text reaches the response."""
    if isinstance(exc, NotADocument):
        return NOT_A_DOCUMENT_MESSAGE
    if isinstance(exc, ScannedPDF):
        return SCANNED_PDF_MESSAGE
    return UNSUPPORTED_MESSAGE


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class AIParserModule(BaseModule):

    @property
    def module_id(self):
        return 'ai_parser'

    @property
    def name(self):
        return 'AI Document Parser'

    @property
    def description(self):
        return ('AI-powered parsing of invoices and expenses (OpenAI, Anthropic, '
                'Google Document AI, Claude Code CLI, Kiro CLI)')

    @property
    def version(self):
        return '0.1.0'

    def register_models(self, db):
        self._db = db

        class AIParserConfig(db.Model):
            __tablename__ = 'ai_parser_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            provider = db.Column(db.String(50), default='openai')
            openai_api_key = db.Column(EncryptedString(500), default='')
            openai_model = db.Column(db.String(100), default=DEFAULT_MODELS['openai'])
            anthropic_api_key = db.Column(EncryptedString(500), default='')
            anthropic_model = db.Column(db.String(100), default=DEFAULT_MODELS['anthropic'])
            google_project_id = db.Column(db.String(200), default='')
            google_processor_id = db.Column(db.String(200), default='')
            google_location = db.Column(db.String(50), default='eu')
            claude_cli_model = db.Column(db.String(100), default=CLI_DEFAULT_MODELS['claude_cli'])
            kiro_cli_model = db.Column(db.String(100), default=CLI_DEFAULT_MODELS['kiro_cli'])
            # JSON of the last live model listing per provider (catalog.store_cache).
            models_cache = db.Column(db.Text, default='')

        self.AIParserConfig = AIParserConfig
        return {'ai_parser_config': AIParserConfig}

    def _get_config(self):
        """Load config from DB, create default if missing."""
        cfg = self.AIParserConfig.query.first()
        if not cfg:
            cfg = self.AIParserConfig()
            self._db.session.add(cfg)
            self._db.session.commit()
        return cfg

    @property
    def interface(self):
        # Used by the ledger assistant (G-7) to reuse the configured provider.
        return ['provider_settings']

    def provider_settings(self):
        """The active provider and its credentials, read only (no default row
        is created): {'provider', 'display_name', 'api_key', 'model',
        'configured'}. `api_key` is the decrypted key; never log it."""
        cfg = self.AIParserConfig.query.first()
        provider = (cfg.provider if cfg else None) or 'openai'
        key = model = ''
        if cfg is not None and provider == 'openai':
            key, model = cfg.openai_api_key or '', cfg.openai_model or DEFAULT_MODELS['openai']
        elif cfg is not None and provider == 'anthropic':
            key, model = (cfg.anthropic_api_key or '',
                          cfg.anthropic_model or DEFAULT_MODELS['anthropic'])
        cls = ALL_PROVIDERS.get(provider)
        configured = bool(key) if provider in ('openai', 'anthropic') else bool(
            cfg is not None and cls is not None and cls(self._provider_config(cfg)).is_configured())
        return {'provider': provider,
                'display_name': cls.display_name if cls else provider,
                'api_key': key, 'model': model, 'configured': configured}

    @staticmethod
    def _provider_config(cfg):
        return {
            'openai_api_key': cfg.openai_api_key,
            'openai_model': cfg.openai_model,
            'anthropic_api_key': cfg.anthropic_api_key,
            'anthropic_model': cfg.anthropic_model,
            'google_project_id': cfg.google_project_id,
            'google_processor_id': cfg.google_processor_id,
            'google_location': cfg.google_location,
            'claude_cli_model': cfg.claude_cli_model,
            'kiro_cli_model': cfg.kiro_cli_model,
        }

    def _get_provider(self):
        """Get configured AI provider instance."""
        cfg = self._get_config()
        provider_cls = ALL_PROVIDERS.get(cfg.provider)
        if not provider_cls:
            return None
        provider = provider_cls(self._provider_config(cfg))
        if not provider.is_configured():
            return None
        return provider

    def _not_configured_message(self):
        cfg = self._get_config()
        cls = CLI_PROVIDERS.get(cfg.provider)
        reason = cls(self._provider_config(cfg)).unavailable_reason() if cls else None
        return reason or 'AI provider not configured. Go to Settings → AI Parser.'

    @property
    def nav_items(self):
        return [
            {'label': 'AI Parser', 'endpoint': 'ai_parser.parse_page', 'icon': '🤖'}
        ]

    def register_routes(self, app):
        bp = Blueprint('ai_parser', __name__,
                       template_folder='templates',
                       url_prefix='/ai-parser')
        login_required = self.core.login_required
        module = self

        # Rate-limit the AI parse endpoint (calls an external AI provider).
        from app import limiter

        def _rl(rule):
            def deco(f):
                return limiter.limit(rule)(f) if limiter else f
            return deco

        @bp.route('/')
        @login_required
        def parse_page():
            """Standalone parse page."""
            return render_template('parse.html')

        @bp.route('/parse', methods=['POST'])
        @_rl('10/minute')
        @login_required
        def parse_document():
            """Parse uploaded document via AI and return JSON."""
            file = request.files.get('file')
            if not file or not file.filename:
                return jsonify({'error': 'No file uploaded'}), 400

            doc_type = request.form.get('doc_type', 'invoice')
            provider = module._get_provider()
            if not provider:
                return jsonify({'error': module._not_configured_message()}), 400

            try:
                file_data = file.read()
                result = provider.parse_document(file_data, file.filename, doc_type)
                module.core.log_activity(
                    'ai_parse_success', 'ai_parser',
                    f'Parsed {doc_type}: {file.filename} via {provider.display_name}')
                module.logger.info('Parsed %s: %s via %s', doc_type, file.filename, provider.name)
                return jsonify(result)
            except (UnsupportedFile, NotADocument) as e:
                return jsonify({'error': rejection_message(e)}), 422
            except Exception as e:
                module.logger.error('AI parse failed: %s — %s', file.filename, e)
                module.core.log_activity(
                    'ai_parse_failed', 'ai_parser',
                    f'Failed to parse {file.filename}: {e}')
                return jsonify({'error': describe_error(e)
                                or 'Parsing failed. Check server logs.'}), 500

        @bp.route('/models/<provider>/refresh', methods=['POST'])
        @_rl('10/minute')
        @login_required
        def refresh_models(provider):
            """Fetch the provider's model list and cache it. The key typed in
            the settings form (JSON `api_key`) is used when given, so the list
            can be loaded before saving; it is not stored here."""
            body = request.get_json(silent=True)
            body = body if isinstance(body, dict) else {}
            payload, status = module.refresh_models(
                provider, str(body.get('api_key') or ''), str(body.get('current') or ''))
            return jsonify(payload), status

        app.register_blueprint(bp)

    def refresh_models(self, provider, api_key='', current=''):
        """Load the provider's model list into the cache. Returns
        (catalog.choices payload or {'error'}, HTTP status)."""
        if provider not in catalog.LISTABLE:
            return {'error': 'Unknown provider'}, 404
        # Use the allowlisted constant from here on, not the URL value.
        provider = next(p for p in catalog.LISTABLE if p == provider)
        cfg = self._get_config()
        try:
            if provider in catalog.KEYED:
                api_key = api_key.strip() or getattr(cfg, f'{provider}_api_key')
                if not api_key:
                    return {'error': 'Enter the API key first.'}, 400
                models = catalog.list_models(provider, api_key)
            else:
                agent = CLI_PROVIDERS[provider](self._provider_config(cfg))
                reason = agent.unavailable_reason()
                if reason:
                    return {'error': reason}, 400
                models = agent.list_models()
        except Exception as e:
            self.logger.warning('Model listing failed for %s: %s', provider,
                                e if isinstance(e, CLIError) else type(e).__name__)
            return {'error': describe_error(e)
                    or 'Could not load the model list. Check server logs.'}, 502
        cfg.models_cache = catalog.store_cache(cfg.models_cache, provider, models)
        self._db.session.commit()
        self.logger.info('Model list refreshed for %s: %d models', provider, len(models))
        current = current.strip() or getattr(cfg, f'{provider}_model')
        return catalog.choices(provider, cfg.models_cache, current), 200

    # --- REST API (served under /api/v1/m/ai_parser/... ) ---

    def get_api_routes(self):
        """Expose AI document parsing.

        POST multipart with a `file` field (and optional `doc_type` form field)
        to /api/v1/m/ai_parser/parse. Returns the extracted fields as JSON.
        """
        return [
            {'path': 'parse', 'methods': ['POST'],
             'handler': self._api_parse,
             'summary': 'Parse an uploaded document (PDF/image) into fields via AI'},
        ]

    def _api_parse(self, request):
        from api_errors import ApiError
        file = request.files.get('file')
        if not file or not file.filename:
            raise ApiError(400, 'bad_request',
                           'Multipart "file" field is required')
        doc_type = request.form.get('doc_type', 'invoice')
        provider = self._get_provider()
        if not provider:
            raise ApiError(400, 'bad_request', self._not_configured_message())
        try:
            result = provider.parse_document(file.read(), file.filename, doc_type)
        except (UnsupportedFile, NotADocument) as e:
            raise ApiError(422, 'unprocessable', rejection_message(e))
        except Exception as e:
            self.logger.error('AI parse failed via API: %s — %s', file.filename, e)
            raise ApiError(502, 'parse_failed',
                           describe_error(e) or 'Parsing failed. Check server logs.')
        self.core.log_activity('ai_parse_success', 'ai_parser',
                               {'file': file.filename, 'doc_type': doc_type,
                                'provider': provider.name, 'via': 'api'})
        return {'data': result}, 200

    # --- Settings integration ---

    @property
    def settings_tab(self):
        return {'id': 'ai_parser', 'label': 'AI Parser'}

    def on_enable(self):
        # C-2: encrypt API keys stored in plaintext by earlier versions.
        encrypt_existing(self._db, 'ai_parser_config', ['openai_api_key', 'anthropic_api_key'])
        self._migrate()

    def _migrate(self):
        """Add columns introduced after the table shipped and move stored
        settings off retired models. Idempotent."""
        from sqlalchemy import inspect as sa_inspect, text
        try:
            cols = {c['name'] for c in sa_inspect(self._db.engine).get_columns('ai_parser_config')}
            with self._db.engine.connect() as conn:
                added = [('models_cache', "TEXT DEFAULT ''")] + [
                    (f'{p}_model', f"VARCHAR(100) DEFAULT '{m}'") for p, m in CLI_DEFAULT_MODELS.items()]
                for col, typedef in added:
                    if col not in cols:
                        conn.execute(text(f'ALTER TABLE ai_parser_config ADD COLUMN {col} {typedef}'))
                for provider, retired in catalog.RETIRED_MODELS.items():
                    column = f'{provider}_model'
                    params = {f'm{i}': m for i, m in enumerate(sorted(retired))}
                    marks = ', '.join(f':{k}' for k in params)
                    moved = conn.execute(text(
                        f'UPDATE ai_parser_config SET {column} = :new WHERE {column} IN ({marks})'),
                        {'new': DEFAULT_MODELS[provider], **params}).rowcount
                    if moved:
                        self.logger.info('AI Parser: moved %s off a retired model to %s',
                                         column, DEFAULT_MODELS[provider])
                conn.commit()
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error('AI Parser migration failed: %s', e)

    def get_settings_html(self, settings):
        cfg = self._get_config()
        models = {p: catalog.choices(p, cfg.models_cache, getattr(cfg, f'{p}_model'))
                  for p in MODEL_PROVIDERS}
        config = self._provider_config(cfg)
        providers_info = [{'id': pid, 'name': cls.display_name,
                           'configured': cls(config).is_configured()}
                          for pid, cls in ALL_PROVIDERS.items()]
        cli = {pid: {'reason': cls(config).unavailable_reason(), 'binary': cls.binary,
                     'bin_env': cls.bin_env}
               for pid, cls in CLI_PROVIDERS.items()}
        return self.render_settings_template('ai_parser_settings.html',
                               cfg=cfg, providers=providers_info, models=models,
                               cli=cli, cli_allowed=cli_allowed(), allow_env=ALLOW_ENV)

    def save_settings(self, settings, form):
        if 'ai_provider' not in form:
            return
        cfg = self._get_config()
        if form.get('ai_provider') in ALL_PROVIDERS:
            cfg.provider = form.get('ai_provider')
        cfg.openai_api_key = secret_from_form(form, 'ai_openai_api_key', cfg.openai_api_key)
        cfg.openai_model = (form.get('ai_openai_model', cfg.openai_model)
                            or DEFAULT_MODELS['openai'])
        cfg.anthropic_api_key = secret_from_form(form, 'ai_anthropic_api_key', cfg.anthropic_api_key)
        cfg.anthropic_model = (form.get('ai_anthropic_model', cfg.anthropic_model)
                               or DEFAULT_MODELS['anthropic'])
        cfg.google_project_id = form.get('ai_google_project_id', cfg.google_project_id)
        cfg.google_processor_id = form.get('ai_google_processor_id', cfg.google_processor_id)
        cfg.google_location = form.get('ai_google_location', cfg.google_location) or 'eu'
        for pid in CLI_PROVIDERS:
            column = f'{pid}_model'
            try:
                value = valid_model(form.get(f'ai_{column}', getattr(cfg, column)))
            except ValueError:
                self.logger.warning('AI Parser: ignored an invalid %s value', column)
                continue
            setattr(cfg, column, value or CLI_DEFAULT_MODELS[pid])
        self._db.session.commit()
        self.logger.info('AI Parser settings saved: provider=%s', cfg.provider)

    # --- Form injection: invoice create ---

    def get_create_form_html(self):
        return '''
    <div class="form-group" style="background: var(--color-primary-tint); padding: 15px; border-radius: 8px; border: 1px solid var(--color-primary); margin-bottom: 20px;">
        <label style="font-weight: bold; color: var(--color-primary);">🤖 AI Document Parser</label>
        <div style="display: flex; gap: 10px; align-items: end; margin-top: 8px;">
            <div style="flex: 1;">
                <input type="file" id="ai_parse_file" accept=".pdf,.jpg,.jpeg,.png,.gif,.webp"
                       style="padding: 8px; border: 1px dashed var(--color-primary); border-radius: 5px; background: var(--color-surface); width: 100%;">
            </div>
            <button type="button" onclick="aiParseDocument('invoice')" class="btn btn-primary"
                    id="ai_parse_btn" style="white-space: nowrap;">
                🔍 Parse with AI
            </button>
        </div>
        <small style="display: block; margin-top: 5px; color: var(--color-text-muted);">
            Upload an invoice PDF or photo — AI will extract fields and fill the form automatically.
        </small>
        <div id="ai_parse_status" style="margin-top: 8px; display: none;"></div>
    </div>
    <script>
    function aiParseDocument(docType) {
        const fileInput = document.getElementById('ai_parse_file');
        const statusDiv = document.getElementById('ai_parse_status');
        const btn = document.getElementById('ai_parse_btn');

        if (!fileInput.files.length) {
            statusDiv.style.display = 'block';
            statusDiv.innerHTML = '<span style="color: var(--color-danger-fg);">Please select a file first.</span>';
            return;
        }

        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('doc_type', docType);

        btn.disabled = true;
        btn.textContent = '⏳ Parsing...';
        statusDiv.style.display = 'block';
        statusDiv.innerHTML = '<span style="color: var(--color-primary);">Sending to AI...</span>';

        fetch('/ai-parser/parse', { method: 'POST', body: formData })
            .then(r => r.json())
            .then(data => {
                btn.disabled = false;
                btn.textContent = '🔍 Parse with AI';
                if (data.error) {
                    statusDiv.innerHTML = '<span style="color: var(--color-danger-fg);">Error: ' + data.error + '</span>';
                    return;
                }
                statusDiv.innerHTML = '<span style="color: var(--color-success-fg);">✓ Parsed successfully! Fields filled.</span>';
                aiFillInvoiceForm(data);
            })
            .catch(err => {
                btn.disabled = false;
                btn.textContent = '🔍 Parse with AI';
                statusDiv.innerHTML = '<span style="color: var(--color-danger-fg);">Request failed: ' + err + '</span>';
            });
    }

    function aiFillInvoiceForm(data) {
        if (data.invoice_number) setVal('invoice_number', data.invoice_number);
        if (data.invoice_date) setVal('invoice_date', data.invoice_date);
        if (data.due_date) setVal('due_date', data.due_date);
        if (data.currency) {
            const sel = document.getElementById('currency');
            if (sel) { sel.value = data.currency; sel.dispatchEvent(new Event('change')); }
        }
        if (data.client_name) {
            // Unlock fields first
            const nameF = document.getElementById('client_name');
            const vatF = document.getElementById('client_vat');
            const addrF = document.getElementById('client_address');
            if (nameF) { nameF.readOnly = false; nameF.value = data.client_name; }
            if (vatF) { vatF.readOnly = false; vatF.value = data.client_vat || ''; }
            if (addrF) { addrF.readOnly = false; addrF.value = data.client_address || ''; }
            // Reset customer dropdown
            const custSel = document.getElementById('customer_select');
            if (custSel) custSel.value = '';
        }
        if (data.notes) setVal('notes', data.notes);

        // Fill items
        if (data.items && data.items.length > 0) {
            // Remove existing items
            const container = document.getElementById('items-container');
            if (container) container.innerHTML = '';
            if (typeof itemCounter !== 'undefined') itemCounter = 0;

            data.items.forEach(function(item) {
                if (typeof addItem === 'function') addItem();
                const rows = document.querySelectorAll('.item-row');
                const lastRow = rows[rows.length - 1];
                if (!lastRow) return;
                const descField = lastRow.querySelector('textarea[name*="description"]');
                const qtyField = lastRow.querySelector('input[name*="quantity"]');
                const priceField = lastRow.querySelector('input[name*="unit_price"]');
                if (descField) descField.value = item.description || '';
                if (qtyField) qtyField.value = item.quantity || 1;
                if (priceField) priceField.value = item.unit_price || 0;
                if (typeof updateItemTotal === 'function' && typeof itemCounter !== 'undefined') {
                    updateItemTotal(itemCounter);
                }
            });
        }
    }

    function setVal(id, val) {
        const el = document.getElementById(id);
        if (el) el.value = val;
    }
    </script>
        '''
