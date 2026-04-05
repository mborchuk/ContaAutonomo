#!/usr/bin/env python3
"""
AI Parser Module
Pluggable AI-powered document parsing for invoices and expenses.
Supports multiple backends: OpenAI (GPT-4o), Google Document AI, Anthropic Claude.
"""

from module_manager import BaseModule
from flask import Blueprint, request, jsonify, render_template
import json
import base64
from datetime import datetime


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
    display_name = 'OpenAI (GPT-4o)'

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
  "vat_amount": number or null
}
Return ONLY valid JSON, no markdown fences."""

    def is_configured(self):
        return bool(self.config.get('openai_api_key'))

    def parse_document(self, file_data, filename, doc_type='invoice'):
        import openai
        client = openai.OpenAI(api_key=self.config['openai_api_key'])
        model = self.config.get('openai_model', 'gpt-4o')

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
            max_tokens=2000,
            temperature=0
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
        model = self.config.get('anthropic_model', 'claude-sonnet-4-20250514')

        b64 = base64.b64encode(file_data).decode('utf-8')
        mime = 'application/pdf' if filename.lower().endswith('.pdf') else 'image/jpeg'
        if filename.lower().endswith('.png'):
            mime = 'image/png'

        prompt = self.INVOICE_PROMPT if doc_type == 'invoice' else self.EXPENSE_PROMPT

        response = client.messages.create(
            model=model,
            max_tokens=2000,
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
            return {
                'invoice_number': parsed.get('invoice_id', ''),
                'expense_date': self._parse_date(parsed.get('invoice_date')),
                'contractor_name': parsed.get('supplier_name', ''),
                'amount': self._parse_amount(parsed.get('total_amount', '0')),
                'currency': parsed.get('currency', 'EUR'),
                'category': 'Other',
                'description': parsed.get('supplier_name', ''),
                'vat_amount': self._parse_amount(parsed.get('total_tax_amount', '0'))
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
            openai_api_key = db.Column(db.String(500), default='')
            openai_model = db.Column(db.String(100), default='gpt-4o')
            anthropic_api_key = db.Column(db.String(500), default='')
            anthropic_model = db.Column(db.String(100), default='claude-sonnet-4-20250514')
            google_project_id = db.Column(db.String(200), default='')
            google_processor_id = db.Column(db.String(200), default='')
            google_location = db.Column(db.String(50), default='eu')

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

        @bp.route('/')
        @login_required
        def parse_page():
            """Standalone parse page."""
            return render_template('parse.html')

        @bp.route('/parse', methods=['POST'])
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
                return jsonify({'error': 'Parsing failed. Check server logs.'}), 500

        app.register_blueprint(bp)

    # --- Settings integration ---

    @property
    def settings_tab(self):
        return {'id': 'ai_parser', 'label': 'AI Parser'}

    def get_settings_html(self, settings):
        cfg = self._get_config()
        providers_info = [
            {'id': 'openai', 'name': 'OpenAI (GPT-4o)', 'configured': bool(cfg.openai_api_key)},
            {'id': 'anthropic', 'name': 'Anthropic (Claude)', 'configured': bool(cfg.anthropic_api_key)},
            {'id': 'google_docai', 'name': 'Google Document AI', 'configured': bool(cfg.google_project_id and cfg.google_processor_id)},
        ]
        return render_template('ai_parser_settings.html',
                               cfg=cfg, providers=providers_info)

    def save_settings(self, settings, form):
        if 'ai_provider' not in form:
            return
        cfg = self._get_config()
        cfg.provider = form.get('ai_provider', cfg.provider)
        cfg.openai_api_key = form.get('ai_openai_api_key', cfg.openai_api_key)
        cfg.openai_model = form.get('ai_openai_model', cfg.openai_model) or 'gpt-4o'
        cfg.anthropic_api_key = form.get('ai_anthropic_api_key', cfg.anthropic_api_key)
        cfg.anthropic_model = form.get('ai_anthropic_model', cfg.anthropic_model) or 'claude-sonnet-4-20250514'
        cfg.google_project_id = form.get('ai_google_project_id', cfg.google_project_id)
        cfg.google_processor_id = form.get('ai_google_processor_id', cfg.google_processor_id)
        cfg.google_location = form.get('ai_google_location', cfg.google_location) or 'eu'
        self._db.session.commit()
        self.logger.info('AI Parser settings saved: provider=%s', cfg.provider)

    # --- Form injection: invoice create ---

    def get_create_form_html(self):
        return '''
    <div class="form-group" style="background: #f0f7ff; padding: 15px; border-radius: 8px; border: 1px solid #b3d4fc; margin-bottom: 20px;">
        <label style="font-weight: bold; color: #1565c0;">🤖 AI Document Parser</label>
        <div style="display: flex; gap: 10px; align-items: end; margin-top: 8px;">
            <div style="flex: 1;">
                <input type="file" id="ai_parse_file" accept=".pdf,.jpg,.jpeg,.png"
                       style="padding: 8px; border: 1px dashed #90caf9; border-radius: 5px; background: white; width: 100%;">
            </div>
            <button type="button" onclick="aiParseDocument('invoice')" class="btn btn-primary"
                    id="ai_parse_btn" style="white-space: nowrap;">
                🔍 Parse with AI
            </button>
        </div>
        <small style="display: block; margin-top: 5px; color: #666;">
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
            statusDiv.innerHTML = '<span style="color: #d32f2f;">Please select a file first.</span>';
            return;
        }

        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('doc_type', docType);

        btn.disabled = true;
        btn.textContent = '⏳ Parsing...';
        statusDiv.style.display = 'block';
        statusDiv.innerHTML = '<span style="color: #1565c0;">Sending to AI...</span>';

        fetch('/ai-parser/parse', { method: 'POST', body: formData })
            .then(r => r.json())
            .then(data => {
                btn.disabled = false;
                btn.textContent = '🔍 Parse with AI';
                if (data.error) {
                    statusDiv.innerHTML = '<span style="color: #d32f2f;">Error: ' + data.error + '</span>';
                    return;
                }
                statusDiv.innerHTML = '<span style="color: #2e7d32;">✓ Parsed successfully! Fields filled.</span>';
                aiFillInvoiceForm(data);
            })
            .catch(err => {
                btn.disabled = false;
                btn.textContent = '🔍 Parse with AI';
                statusDiv.innerHTML = '<span style="color: #d32f2f;">Request failed: ' + err + '</span>';
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
