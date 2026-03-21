# Report Templates

This folder contains templates for generating financial reports.

## How to Use

1. Each template is a Python file that exports a `generate_report()` function
2. The function receives three parameters:
   - `buffer`: BytesIO buffer to write the PDF to
   - `report_data`: Dictionary containing report data
   - `settings`: Application settings object

## Creating a Custom Template

1. Copy `official_template.py` to a new file (e.g., `my_template.py`)
2. Modify the PDF generation logic as needed
3. The template name (without .py extension) will appear in Settings -> General Settings
4. Select your template from the dropdown

## Report Data Structure

```python
report_data = {
    'report_type': 'Income Report' | 'Expenses Report' | 'Financial Report',
    'period_text': 'Q1 2026' | 'Q1, Q2 2026' | 'Full Year 2026',
    'show_income': True/False,
    'show_expenses': True/False,
    'show_summary': True/False,
    'income_data': [
        {
            'invoice_number': '2026-1',
            'invoice_date': '30/01/2026',
            'client_name': 'Client Name',
            'amount_eur': 6730.0,
            'status': 'paid'
        },
        ...
    ],
    'expenses_data': [
        {
            'expense_date': '06/01/2026',
            'contractor_name': 'Contractor Name',
            'category': 'Services',
            'description': 'Description',
            'amount_eur': 242.0
        },
        ...
    ]
}
```

## Settings Object

The settings object contains business information:
- `business_name`
- `owner_name`
- `vat_number`
- `nie_number`
- `address`, `city`, `postal_code`, `country`
- `email`, `phone`

## Example Template Structure

```python
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, Paragraph
import io

def generate_report(buffer, report_data, settings):
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    story = []

    # Add your content here
    # ...

    doc.build(story)
    buffer.seek(0)
    return buffer
```

## Tips

- Use ReportLab library for PDF generation
- Keep the official template style for consistency
- Test your template thoroughly before using in production
- Make sure to handle missing data gracefully
