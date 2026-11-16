#!/usr/bin/env python3
"""
AI Parser Module
Pluggable AI-powered document parsing for invoices and expenses.
Supports multiple backends: OpenAI, Anthropic Claude, Google Document AI.
"""

from module_manager import BaseModule
from settings_forms import secret_from_form
from flask import Blueprint, request, jsonify, render_template
import json
import base64
from datetime import datetime
from secret_box import EncryptedString, encrypt_existing

from . import catalog
from .catalog import DEFAULT_MODELS


def describe_error(exc):
    """A message the user can act on for a provider HTTP error, else None."""
    status = getattr(exc, 'status_code', None)
    if status == 404:
        return ('The selected model is not available for this API key. Choose another '
                'model in Settings → AI Parser ("Refresh list" shows the current ones).')
    if status in (401, 403):
        return 'The provider rejected the API key. Check it in Settings → AI Parser.'
    if status == 429:
        return 'The provider rate limit or quota was reached. Try again later.'
    return None


# ---------------------------------------------------------------------------
# AI Provider backends
# ---------------------------------------------------------------------------

class BaseAIProvider:
    """Base class for AI parsing providers."""
    name = 'base'
    display_name = 'Base Provider'

    def __init__(self, config):
        self.config = config

    def parse_document(self, file_data, filename, doc_type='invoice'):
        """Parse a document and return structured data.
        Args:
            file_data: file bytes
            filename: original filename
            doc_type: 'invoice' or 'expense'
        Returns:
            dict with parsed fields
        """
        raise NotImplementedError

    def is_configured(self):
        """Check if provider has required configuration."""
        raise NotImplementedError


class OpenAIProvider(BaseAIProvider):
    name = 'openai'
    display_name = 'OpenAI (GPT)'

    INVOICE_PROMPT = """Analyze this invoice document and extract the following fields as JSON:
{
  "invoice_number": "string",
  "invoice_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD or null",
  "client_name": "string",
  "client_vat": "string or null",
  "client_address": "string or null",
  "currency": "3-letter code (EUR, USD, etc.)",
  "items": [
    {"description": "string", "quantity": number, "unit_price": number}
  ],
  "total": number,
  "notes": "string or null"
}
Return ONLY valid JSON, no markdown fences."""

    EXPENSE_PROMPT = """Analyze this receipt/invoice document and extract the following fields as JSON:
{
  "invoice_number": "string or null",
  "expense_date": "YYYY-MM-DD",
  "contractor_name": "string (vendor/supplier name)",
  "amount": number,
  "currency": "3-letter code (EUR, USD, etc.)",
  "category": "one of: Office Supplies, Software, Travel, Equipment, Services, Utilities, Insurance, Professional Services, Telecommunications, Other",
  "description": "brief description of what was purchased",
  "net_amount": "number or null (taxable base, amount excluding VAT/IVA)",
  "vat_rate": "number or null (VAT/IVA percentage, e.g. 21)",
  "vat_amount": "number or null (VAT/IVA amount)"
}
"amount" is the gross total (VAT included). If the receipt shows no VAT
breakdown, set net_amount/vat_rate/vat_amount to null — never guess VAT data.
Return ONLY valid JSON, no markdown fences."""

    def is_configured(self):
        return bool(self.config.get('openai_api_key'))

    def parse_document(self, file_data, filename, doc_type='invoice'):
        import openai
        client = openai.OpenAI(api_key=self.config['openai_api_key'])
        model = self.config.get('openai_model') or DEFAULT_MODELS['openai']

        b64 = base64.b64encode(file_data).decode('utf-8')
        mime = 'application/pdf' if filename.lower().endswith('.pdf') else 'image/jpeg'
        if filename.lower().endswith('.png'):
            mime = 'image/png'

        prompt = self.INVOICE_PROMPT if doc_type == 'invoice' else self.EXPENSE_PROMPT

        response = client.chat.completions.create(
            model=model,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {
                        'url': f'data:{mime};base64,{b64}'
                    }}
                ]
            }],
            # Current models reject max_tokens and a non-default temperature;
            # reasoning tokens count toward this limit.
            max_completion_tokens=8000,
        )

        text = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text[3:]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        return json.loads(text)


class AnthropicProvider(BaseAIProvider):
    name = 'anthropic'
    display_name = 'Anthropic (Claude)'

    INVOICE_PROMPT = OpenAIProvider.INVOICE_PROMPT
    EXPENSE_PROMPT = OpenAIProvider.EXPENSE_PROMPT

    def is_configured(self):
        return bool(self.config.get('anthropic_api_key'))

    def parse_document(self, file_data, filename, doc_type='invoice'):
        import anthropic
        client = anthropic.Anthropic(api_key=self.config['anthropic_api_key'])
        model = self.config.get('anthropic_model') or DEFAULT_MODELS['anthropic']

        b64 = base64.b64encode(file_data).decode('utf-8')
        mime = 'application/pdf' if filename.lower().endswith('.pdf') else 'image/jpeg'
        if filename.lower().endswith('.png'):
            mime = 'image/png'

        prompt = self.INVOICE_PROMPT if doc_type == 'invoice' else self.EXPENSE_PROMPT

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'image', 'source': {
                        'type': 'base64',
                        'media_type': mime,
                        'data': b64
                    }},
                    {'type': 'text', 'text': prompt}
                ]
            }]
        )

        text = response.content[0].text.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text[3:]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        return json.loads(text)


class GoogleDocAIProvider(BaseAIProvider):
    name = 'google_docai'
    display_name = 'Google Document AI'

    def is_configured(self):
        return bool(self.config.get('google_project_id') and
                     self.config.get('google_processor_id'))

    def parse_document(self, file_data, filename, doc_type='invoice'):
        from google.cloud import documentai_v1 as documentai

        project_id = self.config['google_project_id']
        location = self.config.get('google_location', 'eu')
        processor_id = self.config['google_processor_id']

        client = documentai.DocumentProcessorServiceClient()
        resource_name = client.processor_path(project_id, location, processor_id)

        mime = 'application/pdf' if filename.lower().endswith('.pdf') else 'image/jpeg'
        if filename.lower().endswith('.png'):
            mime = 'image/png'

        raw_document = documentai.RawDocument(content=file_data, mime_type=mime)
        req = documentai.ProcessRequest(name=resource_name, raw_document=raw_document)
        result = client.process_document(request=req)
        document = result.document

        # Extract entities from Document AI response
        parsed = {}
        for entity in document.entities:
            key = entity.type_
            val = entity.mention_text
            parsed[key] = val

        # Map Document AI entity types to our schema
        if doc_type == 'invoice':
            items = []
            for entity in document.entities:
                if entity.type_ == 'line_item':
                    item = {}
                    for prop in entity.properties:
                        if prop.type_ == 'line_item/description':
                            item['description'] = prop.mention_text
                        elif prop.type_ == 'line_item/quantity':
                            try:
                                item['quantity'] = float(prop.mention_text.replace(',', '.'))
                            except ValueError:
                                item['quantity'] = 1
                        elif prop.type_ in ('line_item/unit_price', 'line_item/amount'):
                            try:
                                item['unit_price'] = float(
                                    prop.mention_text.replace(',', '.').replace('€', '').replace('$', '').strip())
                            except ValueError:
                                pass  # non-numeric price, skip
                    if item.get('description'):
                        items.append(item)

            return {
                'invoice_number': parsed.get('invoice_id', ''),
                'invoice_date': self._parse_date(parsed.get('invoice_date')),
                'due_date': self._parse_date(parsed.get('due_date')),
                'client_name': parsed.get('receiver_name') or parsed.get('supplier_name', ''),
                'client_vat': parsed.get('receiver_tax_id', ''),
                'client_address': parsed.get('receiver_address', ''),
                'currency': parsed.get('currency', 'EUR'),
                'items': items,
                'total': self._parse_amount(parsed.get('total_amount', '0')),
                'notes': None
            }
        else:
            total = self._parse_amount(parsed.get('total_amount', '0'))
            vat = self._parse_amount(parsed.get('total_tax_amount', '0'))
            return {
                'invoice_number': parsed.get('invoice_id', ''),
                'expense_date': self._parse_date(parsed.get('invoice_date')),
                'contractor_name': parsed.get('supplier_name', ''),
                'amount': total,
                'currency': parsed.get('currency', 'EUR'),
                'category': 'Other',
                'description': parsed.get('supplier_name', ''),
                # F11-D2 — VAT split parity with the prompt-based providers.
                'vat_amount': vat or None,
                'net_amount': round(total - vat, 2) if total and vat else None,
                'vat_rate': None,  # Document AI gives amounts, not the rate
            }

    def _parse_date(self, val):
        if not val:
            return None
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d.%m.%Y', '%m/%d/%Y'):
            try:
                return datetime.strptime(val.strip(), fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        return val

    def _parse_amount(self, val):
        if not val:
            return 0
        try:
            cleaned = val.replace(',', '.').replace('€', '').replace('$', '').replace('£', '').strip()
            return float(cleaned)
        except ValueError:
            return 0


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS = {
    'openai': OpenAIProvider,
    'anthropic': AnthropicProvider,
    'google_docai': GoogleDocAIProvider,
}


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
        return 'AI-powered parsing of invoices and expenses (OpenAI, Anthropic, Google Document AI)'

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
        cls = PROVIDERS.get(provider)
        configured = bool(key) if provider in ('openai', 'anthropic') else bool(
            cfg and cfg.google_project_id and cfg.google_processor_id)
        return {'provider': provider,
                'display_name': cls.display_name if cls else provider,
                'api_key': key, 'model': model, 'configured': configured}

    def _get_provider(self):
        """Get configured AI provider instance."""
        cfg = self._get_config()
        provider_cls = PROVIDERS.get(cfg.provider)
        if not provider_cls:
            return None
        config_dict = {
            'openai_api_key': cfg.openai_api_key,
            'openai_model': cfg.openai_model,
            'anthropic_api_key': cfg.anthropic_api_key,
            'anthropic_model': cfg.anthropic_model,
            'google_project_id': cfg.google_project_id,
            'google_processor_id': cfg.google_processor_id,
            'google_location': cfg.google_location,
        }
        provider = provider_cls(config_dict)
        if not provider.is_configured():
            return None
        return provider

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
                return jsonify({'error': 'AI provider not configured. Go to Settings → AI Parser.'}), 400

            try:
                file_data = file.read()
                result = provider.parse_document(file_data, file.filename, doc_type)
                module.core.log_activity(
                    'ai_parse_success', 'ai_parser',
                    f'Parsed {doc_type}: {file.filename} via {provider.display_name}')
                module.logger.info('Parsed %s: %s via %s', doc_type, file.filename, provider.name)
                return jsonify(result)
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
        api_key = api_key.strip() or getattr(cfg, f'{provider}_api_key')
        if not api_key:
            return {'error': 'Enter the API key first.'}, 400
        try:
            models = catalog.list_models(provider, api_key)
        except Exception as e:
            self.logger.warning('Model listing failed for %s: %s', provider, type(e).__name__)
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
        from modules.api.index import ApiError
        file = request.files.get('file')
        if not file or not file.filename:
            raise ApiError(400, 'bad_request',
                           'Multipart "file" field is required')
        doc_type = request.form.get('doc_type', 'invoice')
        provider = self._get_provider()
        if not provider:
            raise ApiError(400, 'bad_request',
                           'AI provider not configured (Settings → AI Parser)')
        try:
            result = provider.parse_document(file.read(), file.filename, doc_type)
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
                if 'models_cache' not in cols:
                    conn.execute(text("ALTER TABLE ai_parser_config ADD COLUMN models_cache TEXT DEFAULT ''"))
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
                  for p in catalog.LISTABLE}
        providers_info = [
            {'id': 'openai', 'name': OpenAIProvider.display_name, 'configured': bool(cfg.openai_api_key)},
            {'id': 'anthropic', 'name': 'Anthropic (Claude)', 'configured': bool(cfg.anthropic_api_key)},
            {'id': 'google_docai', 'name': 'Google Document AI', 'configured': bool(cfg.google_project_id and cfg.google_processor_id)},
        ]
        return self.render_settings_template('ai_parser_settings.html',
                               cfg=cfg, providers=providers_info, models=models)

    def save_settings(self, settings, form):
        if 'ai_provider' not in form:
            return
        cfg = self._get_config()
        cfg.provider = form.get('ai_provider', cfg.provider)
        cfg.openai_api_key = secret_from_form(form, 'ai_openai_api_key', cfg.openai_api_key)
        cfg.openai_model = (form.get('ai_openai_model', cfg.openai_model)
                            or DEFAULT_MODELS['openai'])
        cfg.anthropic_api_key = secret_from_form(form, 'ai_anthropic_api_key', cfg.anthropic_api_key)
        cfg.anthropic_model = (form.get('ai_anthropic_model', cfg.anthropic_model)
                               or DEFAULT_MODELS['anthropic'])
        cfg.google_project_id = form.get('ai_google_project_id', cfg.google_project_id)
        cfg.google_processor_id = form.get('ai_google_processor_id', cfg.google_processor_id)
        cfg.google_location = form.get('ai_google_location', cfg.google_location) or 'eu'
        self._db.session.commit()
        self.logger.info('AI Parser settings saved: provider=%s', cfg.provider)

    # --- Form injection: invoice create ---

    def get_create_form_html(self):
        return '''
    <div class="form-group" style="background: var(--color-primary-tint); padding: 15px; border-radius: 8px; border: 1px solid var(--color-primary); margin-bottom: 20px;">
        <label style="font-weight: bold; color: var(--color-primary);">🤖 AI Document Parser</label>
        <div style="display: flex; gap: 10px; align-items: end; margin-top: 8px;">
            <div style="flex: 1;">
                <input type="file" id="ai_parse_file" accept=".pdf,.jpg,.jpeg,.png"
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
