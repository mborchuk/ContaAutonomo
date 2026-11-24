# Invoice PDF Templates

This folder contains templates for generating invoice PDFs. You can create custom templates by copying the default template and modifying it.

## Available Templates

- `default_template.py` - Default professional invoice template with grey accents
- `modern_blue_template.py` - Modern minimalist design with blue accents

## Creating a Custom Template

1. Copy `default_template.py` to a new file (e.g., `my_template.py`)
2. Modify the `generate_invoice_pdf()` function to customize the layout
3. The function must accept three parameters:
   - `invoice`: Invoice object
   - `customer`: Customer object (can be None)
   - `settings`: Settings object
4. The function must return a BytesIO buffer containing the PDF
5. Select your template in Settings -> General Settings

## Template Function Signature

```python
def generate_invoice_pdf(invoice, customer, settings):
    """
    Generate invoice PDF

    Args:
        invoice: Invoice object with fields like invoice_number, invoice_date, amount_usd, etc.
        customer: Customer object with fields like name, vat_number, address, etc.
        settings: Settings object with business information

    Returns:
        BytesIO buffer containing the PDF
    """
    # Your template code here
    return buffer
```

## Invoice Object Fields

- `invoice_number`: Invoice number (e.g., "2026-1")
- `invoice_date`: Date object
- `amount_usd`: Amount in USD
- `amount_eur`: Amount in EUR
- `currency`: Invoice currency code
- `items`: List of InvoiceItem objects
- `notes`: Invoice notes
- `bank`: Bank object (can be None)
- `customer`: Customer object (can be None)

## Customer Object Fields

- `name`: Customer name
- `vat_number`: VAT number
- `address`: Address
- `city`: City
- `postal_code`: Postal code
- `country`: Country
- `tax_type`: Tax type (non_eu, eu_b2b, standard)

## Settings Object Fields

- `business_name`: Business name
- `owner_name`: Owner name
- `vat_number`: VAT number
- `nie_number`: NIE number (or local equivalent — overridable by country modules via `get_field_labels()`)
- `address`: Address
- `city`: City
- `postal_code`: Postal code
- `country`: Country
- `phone`: Phone number
- `email`: Email
