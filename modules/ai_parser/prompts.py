"""
Extraction prompts shared by every prompt-based provider (OpenAI, Anthropic,
CLI agents). The field names are the contract with the forms that consume
the result: templates/create.html (invoice widget in index.py) and
modules/expenses/templates/expense_form.html (prefillFromData).
"""

# Same names as the expenses module's seed categories (SEED_CATEGORY_DEFAULTS),
# so the parsed category picks up its VAT and deductibility defaults.
EXPENSE_CATEGORIES = (
    'Office Supplies', 'Software', 'Equipment', 'Services', 'Professional Services',
    'Telecommunications', 'Utilities', 'Travel', 'Meals', 'Vehicle', 'Insurance',
    'Social Security', 'Other',
)

NOT_A_DOCUMENT = 'not_a_document'

_COMMON_RULES = f"""Rules:
- The document can be a photo, a scan or a PDF with several pages; read every page.
- Dates as YYYY-MM-DD. Numbers as JSON numbers with a dot as the decimal
  separator and no currency symbol or thousands separator (1.234,56 € -> 1234.56).
- Use null for anything the document does not show. Never guess or compute a
  value the document does not state, except where a rule below says so.
- If the file is not an invoice, receipt or ticket, return {{"error": "{NOT_A_DOCUMENT}"}}.
- Return ONLY the JSON object: no markdown fences, no comments."""

INVOICE_PROMPT = f"""Extract the data of this invoice as a JSON object with exactly these fields:
{{
  "invoice_number": "string",
  "invoice_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD or null",
  "client_name": "string",
  "client_vat": "string or null",
  "client_address": "string or null",
  "currency": "ISO 4217 code (EUR, USD, ...)",
  "items": [
    {{"description": "string", "quantity": number, "unit_price": number}}
  ],
  "total": number,
  "notes": "string or null"
}}
The client is the customer the invoice is addressed to, not the issuer.
"client_vat" is the client's tax ID (NIF/CIF/VAT number) as printed.
"unit_price" is the price per unit before VAT. "total" is the amount due,
VAT included. Payment terms or other remarks printed on the invoice go to "notes".
{_COMMON_RULES}"""

EXPENSE_PROMPT = f"""Extract the data of this receipt or supplier invoice as a JSON object with exactly these fields:
{{
  "invoice_number": "string or null",
  "expense_date": "YYYY-MM-DD",
  "contractor_name": "string (the vendor or supplier)",
  "contractor_vat": "string or null (the supplier's tax ID: NIF/CIF/VAT number)",
  "contractor_country": "ISO 3166-1 alpha-2 code of the supplier's address, or null",
  "buyer_tax_id": "string or null (the tax ID - NIF, CIF or VAT number - of the buyer the document is issued to)",
  "amount": number,
  "currency": "ISO 4217 code (EUR, USD, ...)",
  "category": "one of: {', '.join(EXPENSE_CATEGORIES)}",
  "description": "brief description of what was purchased",
  "net_amount": "number or null (taxable base, amount excluding VAT/IVA)",
  "vat_rate": "number or null (VAT/IVA percentage, e.g. 21)",
  "vat_amount": "number or null (VAT/IVA amount)",
  "reverse_charge": "'eu', 'non_eu' or null"
}}
"amount" is the gross total, VAT included. If the document shows no VAT
breakdown, set net_amount, vat_rate and vat_amount to null; never guess VAT
data. With several VAT rates, give the totals in net_amount and vat_amount and
set vat_rate to null.
"reverse_charge" is "eu" or "non_eu" (by the supplier's country) only when the
document says the buyer accounts for the VAT ("reverse charge", "inversión del
sujeto pasivo", "Article 196 of Directive 2006/112/EC"); otherwise null.
"buyer_tax_id" is the buyer's or recipient's tax ID as printed on the document,
never the supplier's; null when the document shows none (most tickets).
Restaurants and catering are "Meals"; fuel, parking, tolls and car costs are
"Vehicle"; Spanish Social Security (RETA) receipts are "Social Security".
{_COMMON_RULES}"""


def prompt_for(doc_type):
    return INVOICE_PROMPT if doc_type == 'invoice' else EXPENSE_PROMPT
