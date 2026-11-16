"""
AI Parser providers. Each one turns an uploaded document (PDF or image) into
the field dict described in prompts.py. SDKs are imported lazily so a missing
optional package only affects its own provider.
"""
import json
from datetime import datetime

from . import media
from .catalog import DEFAULT_MODELS
from .prompts import NOT_A_DOCUMENT, prompt_for


class NotADocument(ValueError):
    """The provider says the file is not an invoice or receipt."""


def describe_error(exc):
    """A message the user can act on for a provider HTTP error, else None."""
    status = getattr(exc, 'status_code', None)
    if status == 404:
        return ('The selected model is not available for this API key. Choose another '
                'model in Settings → AI Parser ("Refresh list" shows the current ones).')
    if status in (401, 403):
        return 'The provider rejected the API key. Check it in Settings → AI Parser.'
    if status == 413:
        return 'The file is too large for the provider. Try a smaller file or fewer pages.'
    if status == 429:
        return 'The provider rate limit or quota was reached. Try again later.'
    return None


def parse_reply(text):
    """The JSON object in a model reply. Tolerates markdown fences and text
    around the object; raises ValueError when there is no object."""
    text = (text or '').strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = _first_object(text)
    if not isinstance(data, dict):
        raise ValueError('The provider reply is not a JSON object')
    if data.get('error') == NOT_A_DOCUMENT:
        raise NotADocument('The file does not look like an invoice or receipt.')
    return data


def _first_object(text):
    """The first JSON object in `text` (fences, prose around it), else ValueError."""
    decoder = json.JSONDecoder()
    start = text.find('{')
    while start >= 0:
        try:
            data, _ = decoder.raw_decode(text, start)
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        start = text.find('{', start + 1)
    raise ValueError('The provider reply contains no JSON object')


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

    def is_configured(self):
        return bool(self.config.get('openai_api_key'))

    @staticmethod
    def file_part(file_data, filename, mime):
        data_url = f'data:{mime};base64,{media.b64(file_data)}'
        if media.is_pdf(mime):
            return {'type': 'file', 'file': {'filename': filename or 'document.pdf',
                                             'file_data': data_url}}
        return {'type': 'image_url', 'image_url': {'url': data_url}}

    def parse_document(self, file_data, filename, doc_type='invoice'):
        mime = media.detect_mime(file_data)
        import openai
        client = openai.OpenAI(api_key=self.config['openai_api_key'])
        response = client.chat.completions.create(
            model=self.config.get('openai_model') or DEFAULT_MODELS['openai'],
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt_for(doc_type)},
                    self.file_part(file_data, filename, mime),
                ]
            }],
            # Current models reject max_tokens and a non-default temperature;
            # reasoning tokens count toward this limit.
            max_completion_tokens=8000,
        )
        return parse_reply(response.choices[0].message.content)


class AnthropicProvider(BaseAIProvider):
    name = 'anthropic'
    display_name = 'Anthropic (Claude)'

    def is_configured(self):
        return bool(self.config.get('anthropic_api_key'))

    @staticmethod
    def file_block(file_data, mime):
        kind = 'document' if media.is_pdf(mime) else 'image'
        return {'type': kind, 'source': {'type': 'base64', 'media_type': mime,
                                         'data': media.b64(file_data)}}

    def parse_document(self, file_data, filename, doc_type='invoice'):
        mime = media.detect_mime(file_data)
        import anthropic
        client = anthropic.Anthropic(api_key=self.config['anthropic_api_key'])
        response = client.messages.create(
            model=self.config.get('anthropic_model') or DEFAULT_MODELS['anthropic'],
            max_tokens=4096,
            messages=[{
                'role': 'user',
                'content': [
                    self.file_block(file_data, mime),
                    {'type': 'text', 'text': prompt_for(doc_type)},
                ]
            }]
        )
        text = ''.join(getattr(block, 'text', '') or '' for block in response.content)
        return parse_reply(text)


class GoogleDocAIProvider(BaseAIProvider):
    name = 'google_docai'
    display_name = 'Google Document AI'

    def is_configured(self):
        return bool(self.config.get('google_project_id') and
                    self.config.get('google_processor_id'))

    def parse_document(self, file_data, filename, doc_type='invoice'):
        mime = media.detect_mime(file_data)
        from google.cloud import documentai_v1 as documentai

        project_id = self.config['google_project_id']
        location = self.config.get('google_location', 'eu')
        processor_id = self.config['google_processor_id']

        client = documentai.DocumentProcessorServiceClient()
        resource_name = client.processor_path(project_id, location, processor_id)

        raw_document = documentai.RawDocument(content=file_data, mime_type=mime)
        req = documentai.ProcessRequest(name=resource_name, raw_document=raw_document)
        result = client.process_document(request=req)
        document = result.document

        # Extract entities from Document AI response
        parsed = {}
        for entity in document.entities:
            parsed[entity.type_] = entity.mention_text

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
        total = self._parse_amount(parsed.get('total_amount', '0'))
        vat = self._parse_amount(parsed.get('total_tax_amount', '0'))
        return {
            'invoice_number': parsed.get('invoice_id', ''),
            'expense_date': self._parse_date(parsed.get('invoice_date')),
            'contractor_name': parsed.get('supplier_name', ''),
            'contractor_vat': parsed.get('supplier_tax_id') or None,
            'contractor_country': None,
            'amount': total,
            'currency': parsed.get('currency', 'EUR'),
            'category': 'Other',
            'description': parsed.get('supplier_name', ''),
            # F11-D2 — VAT split parity with the prompt-based providers.
            'vat_amount': vat or None,
            'net_amount': round(total - vat, 2) if total and vat else None,
            'vat_rate': None,  # Document AI gives amounts, not the rate
            'reverse_charge': None,
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


PROVIDERS = {
    'openai': OpenAIProvider,
    'anthropic': AnthropicProvider,
    'google_docai': GoogleDocAIProvider,
}
