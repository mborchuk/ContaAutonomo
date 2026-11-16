# AI Parser (`ai_parser`)

Reads an invoice or a receipt (PDF or photo) with an AI provider and returns
the fields that prefill the invoice and expense forms. Nothing is saved: the
user reviews the form and saves it.

## Files

| File | Contents |
|------|----------|
| `index.py` | `AIParserModule`: config table, routes, settings tab, invoice-form widget |
| `providers.py` | One class per provider, `PROVIDERS`, reply parsing, error messages |
| `prompts.py` | `INVOICE_PROMPT`, `EXPENSE_PROMPT`, `EXPENSE_CATEGORIES` |
| `media.py` | File type from the leading bytes; what each provider is sent |
| `catalog.py` | Default, fallback and retired models; live listing via `GET /v1/models` |
| `templates/` | Parse page and settings tab |

## Flow

1. `POST /ai-parser/parse` (or `POST /api/v1/m/ai_parser/parse`) with `file`
   and `doc_type` (`invoice` or `expense`).
2. `media.detect_mime` reads the type from the bytes. Anything other than
   PDF, JPEG, PNG, GIF or WebP returns 422 without calling a provider.
3. The configured provider sends the file and the prompt for `doc_type`.
4. `providers.parse_reply` takes the JSON object out of the reply. A reply of
   `{"error": "not_a_document"}` returns 422.

## Result fields

The field names are the contract with the forms; change them together.

- Invoice (`templates/create.html` through the widget in `index.py`):
  `invoice_number`, `invoice_date`, `due_date`, `client_name`, `client_vat`,
  `client_address`, `currency`, `items[]` (`description`, `quantity`,
  `unit_price`), `total`, `notes`.
- Expense (`modules/expenses/templates/expense_form.html`, `prefillFromData`):
  `invoice_number`, `expense_date`, `contractor_name`, `contractor_vat`,
  `contractor_country`, `amount` (gross), `currency`, `category`,
  `description`, `net_amount`, `vat_rate`, `vat_amount`, `reverse_charge`.
  VAT fields are `null` when the document shows no breakdown.

## Models

Defaults are in `catalog.DEFAULT_MODELS`. "Refresh list" in Settings → AI
Parser calls `POST /ai-parser/models/<openai|anthropic>/refresh`, which asks
the vendor which models the key can use and stores the list in
`ai_parser_config.models_cache`. When a vendor retires a model that earlier
versions offered, add it to `catalog.RETIRED_MODELS`: stored settings on it
move to the default when the module loads.

## Tests

`tests/test_ai_parser.py`. Every SDK is replaced by a stub; tests never call
a provider.
