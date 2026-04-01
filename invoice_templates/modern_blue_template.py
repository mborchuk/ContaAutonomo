#!/usr/bin/env python3
"""
Modern Blue Invoice PDF Template
A clean, modern template with blue accents and minimalist design
"""

import io
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from currency_converter import get_currency_symbol
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer


def generate_invoice_pdf(invoice, customer, settings):
    """
    Generate invoice PDF using the modern blue template

    Args:
        invoice: Invoice object
        customer: Customer object (can be None)
        settings: Settings object

    Returns:
        BytesIO buffer containing the PDF
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                          rightMargin=50, leftMargin=50,
                          topMargin=40, bottomMargin=50)
    elements = []
    styles = getSampleStyleSheet()

    # Define colors
    blue_color = colors.HexColor('#5B6FD8')  # Main blue color
    dark_gray = colors.HexColor('#4A4A4A')
    light_gray = colors.HexColor('#F5F5F5')

    # Custom styles
    sender_name_style = ParagraphStyle(
        'sender_name',
        parent=styles['Normal'],
        fontSize=20,
        textColor=blue_color,
        fontName='Helvetica-Bold',
        spaceAfter=8
    )

    sender_info_style = ParagraphStyle(
        'sender_info',
        parent=styles['Normal'],
        fontSize=10,
        textColor=dark_gray,
        fontName='Helvetica',
        leading=12,
        spaceAfter=8
    )

    bank_header_style = ParagraphStyle(
        'bank_header',
        parent=styles['Normal'],
        fontSize=10,
        textColor=dark_gray,
        fontName='Helvetica-Bold',
        spaceAfter=8
    )

    bank_info_style = ParagraphStyle(
        'bank_info',
        parent=styles['Normal'],
        fontSize=10,
        textColor=dark_gray,
        fontName='Helvetica',
        leading=11,
        spaceAfter=8
    )

    invoice_title_style = ParagraphStyle(
        'invoice_title',
        parent=styles['Normal'],
        fontSize=34,
        textColor=blue_color,
        fontName='Helvetica-Bold',
        spaceAfter=20
    )

    section_header_style = ParagraphStyle(
        'section_header',
        parent=styles['Normal'],
        fontSize=10,
        textColor=dark_gray,
        fontName='Helvetica-Bold',
        spaceAfter=4
    )

    normal_text_style = ParagraphStyle(
        'normal_text',
        parent=styles['Normal'],
        fontSize=10,
        textColor=dark_gray,
        fontName='Helvetica',
        leading=12
    )

    # Blue line at top
    blue_line_data = [['']]
    blue_line_table = Table(blue_line_data, colWidths=[512])
    blue_line_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), blue_color),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(blue_line_table)
    elements.append(Spacer(1, 20))

    # Header with sender info and bank details
    if settings:
        sender_name = settings.owner_name or settings.business_name or 'Your Name'
        sender_nie = settings.nie_number or ''
        sender_vat = settings.vat_number or ''
        sender_address = settings.address or ''
        sender_city = settings.city or ''
        sender_postal = settings.postal_code or ''
        sender_country = settings.country or ''
        sender_phone = settings.phone or ''
    else:
        sender_name = 'Your Name'
        sender_nie = ''
        sender_vat = ''
        sender_address = ''
        sender_city = ''
        sender_postal = ''
        sender_country = ''
        sender_phone = ''

    # Format sender address
    sender_location = f"{sender_postal} {sender_city}, {sender_country}".strip(', ')

    sender_info_text = f'''<b>NIE: {sender_nie}</b>     <b>VAT: {sender_vat}</b><br/>
{sender_address}<br/>
{sender_location}<br/>
{sender_phone}'''

    # Bank details
    bank_info_text = ''
    if invoice.bank:
        bank_info_text = f'''<b>Bank name:</b> {invoice.bank.bank_name or 'N/A'}<br/>
<b>SWIFT:</b> {invoice.bank.swift or 'N/A'}<br/>
<b>Beneficiar:</b> {sender_name}<br/>
<b>IBAN:</b> {invoice.bank.iban}'''
    else:
        bank_info_text = '''<b>Bank name:</b> [Your Bank]<br/>
<b>SWIFT:</b> [Your SWIFT]<br/>
<b>Beneficiar:</b> [Your Name]<br/>
<b>IBAN:</b> [Your IBAN]'''

    header_data = [
        [
            Paragraph('', sender_name_style),
            Paragraph(f'<b>{sender_name}</b>', sender_name_style),
            Paragraph('<b>Bank details:</b>', bank_header_style)
        ],
        [
            Paragraph('', sender_name_style),
            Paragraph(sender_info_text, sender_info_style),
            Paragraph(bank_info_text, bank_info_style)
        ]
    ]

    header_table = Table(header_data, colWidths=[50, 231, 231], rowHeights=[25, 42])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (1, 0), 'TOP'),
        ('VALIGN', (2, 0), (-1, 0), 'BOTTOM'),
        ('VALIGN', (0, 1), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 30))

    # Invoice title
    elements.append(Paragraph('<b>Invoice</b>', invoice_title_style))
    elements.append(Spacer(1, 10))

    # Invoice details section
    customer_name = customer.name if customer else invoice.client_name
    customer_vat = customer.vat_number if customer and customer.vat_number else ''

    # Build customer address
    customer_address_parts = []
    if customer and customer.address:
        customer_address_parts.append(customer.address)
    if customer and (customer.postal_code or customer.city):
        city_line = f"{customer.postal_code or ''} {customer.city or ''}".strip()
        if city_line:
            customer_address_parts.append(city_line)
    if customer and customer.country:
        customer_address_parts.append(customer.country)

    customer_address = '<br/>'.join(customer_address_parts) if customer_address_parts else ''

    customer_info = f'''<b>{customer_name}</b><br/>
<b>VAT: {customer_vat}</b><br/>
{customer_address}''' if customer_vat else f'''<b>{customer_name}</b><br/>
{customer_address}'''

    # Calculate payment terms
    payment_terms = 'Due with in 30d'
    if invoice.due_date and invoice.invoice_date:
        days_diff = (invoice.due_date - invoice.invoice_date).days
        payment_terms = f'Due with in {days_diff}d'

    invoice_details_data = [
        [
            Paragraph('<b>Invoice for</b>', section_header_style),
            '',
            Paragraph('<b>Invoice #</b>', section_header_style),
            '',
            Paragraph('<b>Terms</b>', section_header_style)
        ],
        [
            Paragraph(customer_info, normal_text_style),
            '',
            Paragraph(f'<b>{invoice.invoice_number}</b>', normal_text_style),
            '',
            Paragraph(payment_terms, normal_text_style)
        ],
        ['', '', '', '', ''],
        [
            '',
            '',
            Paragraph('<b>Issue Date</b>', section_header_style),
            '',
            Paragraph('<b>Due date</b>', section_header_style)
        ],
        [
            '',
            '',
            Paragraph(invoice.invoice_date.strftime('%d.%m.%Y'), normal_text_style),
            '',
            Paragraph(invoice.due_date.strftime('%d.%m.%Y') if invoice.due_date else invoice.invoice_date.strftime('%d.%m.%Y'), normal_text_style)
        ]
    ]

    invoice_details_table = Table(invoice_details_data, colWidths=[180, 20, 100, 20, 192])
    invoice_details_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LINEBELOW', (0, 4), (-1, 4), 1, colors.HexColor('#CCCCCC')),
    ]))
    elements.append(invoice_details_table)
    elements.append(Spacer(1, 20))

    # Items table
    # Currency handling
    invoice_currency = invoice.currency or 'USD'
    currency_symbol = get_currency_symbol(invoice_currency)

    if invoice_currency == 'EUR':
        invoice_amount = invoice.amount_eur
    elif invoice_currency == 'USD':
        invoice_amount = invoice.amount_usd
    else:
        invoice_amount = invoice.amount_usd

    # Table header
    items_header_style = ParagraphStyle(
        'items_header',
        parent=styles['Normal'],
        fontSize=10,
        textColor=blue_color,
        fontName='Helvetica-Bold'
    )

    items_text_style = ParagraphStyle(
        'items_text',
        parent=styles['Normal'],
        fontSize=9,
        textColor=dark_gray,
        fontName='Helvetica'
    )

    service_data = [
        [
            Paragraph('<b>Description</b>', items_header_style),
            Paragraph('<b>Qty</b>', items_header_style),
            Paragraph('<b>Unit price</b>', items_header_style),
            Paragraph('<b>Total price</b>', items_header_style)
        ]
    ]

    # Add items
    if invoice.items and len(invoice.items) > 0:
        for item in invoice.items:
            if invoice_currency == 'EUR':
                unit_price = item.unit_price_usd * (invoice.amount_eur / invoice.amount_usd) if invoice.amount_usd else item.unit_price_usd
                subtotal = item.subtotal_usd * (invoice.amount_eur / invoice.amount_usd) if invoice.amount_usd else item.subtotal_usd
            else:
                unit_price = item.unit_price_usd
                subtotal = item.subtotal_usd

            service_data.append([
                Paragraph(item.description or '', items_text_style),
                Paragraph(f'{int(item.quantity)}', items_text_style),
                Paragraph(f'{currency_symbol}{unit_price:,.2f}', items_text_style),
                Paragraph(f'{currency_symbol}{subtotal:,.2f}', items_text_style)
            ])
    else:
        unit_price = invoice.unit_price_usd if invoice.unit_price_usd else invoice_amount
        if invoice_currency == 'EUR' and invoice.amount_usd:
            unit_price = unit_price * (invoice.amount_eur / invoice.amount_usd)

        service_data.append([
            Paragraph(invoice.description or '', items_text_style),
            Paragraph(f'{int(invoice.quantity) if invoice.quantity else 1}', items_text_style),
            Paragraph(f'{currency_symbol}{unit_price:,.2f}', items_text_style),
            Paragraph(f'{currency_symbol}{invoice_amount:,.2f}', items_text_style)
        ])

    # Add empty rows for spacing
    for _ in range(3):
        service_data.append(['', '', '', ''])

    service_table = Table(service_data, colWidths=[280, 60, 86, 86])
    service_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#CCCCCC')),
        ('BACKGROUND', (0, 1), (-1, -4), colors.white),
        ('BACKGROUND', (0, -3), (-1, -1), light_gray),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(service_table)
    elements.append(Spacer(1, 10))

    # Calculate tax
    vat_pct = settings.default_vat_rate if settings and settings.default_vat_rate is not None else 21.0
    tax_rate = 0.0
    tax_label = 'REVERSE CHARGE'

    if customer and customer.tax_type:
        if customer.tax_type == 'non_eu':
            tax_rate = 0.0
            tax_label = 'REVERSE CHARGE'
        elif customer.tax_type == 'eu_b2b':
            tax_rate = 0.0
            tax_label = 'REVERSE CHARGE'
        elif customer.tax_type == 'standard':
            tax_rate = vat_pct / 100.0
            tax_label = f'IVA {vat_pct:g}%'
    else:
        tax_rate = 0.0
        tax_label = 'REVERSE CHARGE'

    tax_amount = invoice_amount * tax_rate
    total_with_tax = invoice_amount + tax_amount

    # Notes and totals section
    notes_text = ''
    if invoice.notes and invoice.notes.strip() and invoice.notes != 'None':
        notes_text = invoice.notes.replace('\n', '<br/>')

    notes_style = ParagraphStyle(
        'notes',
        parent=styles['Normal'],
        fontSize=9,
        textColor=dark_gray,
        fontName='Helvetica'
    )

    totals_label_style = ParagraphStyle(
        'totals_label',
        parent=styles['Normal'],
        fontSize=10,
        textColor=blue_color,
        fontName='Helvetica-Bold',
        alignment=2  # Right align
    )

    totals_value_style = ParagraphStyle(
        'totals_value',
        parent=styles['Normal'],
        fontSize=10,
        textColor=dark_gray,
        fontName='Helvetica-Bold',
        alignment=2  # Right align
    )

    totals_data = [
        [
            Paragraph(f'<b>Notes:</b>     {notes_text}' if notes_text else '', notes_style),
            '',
            Paragraph('<b>AMOUNT DUE</b>', totals_label_style),
            Paragraph(f'<b>{currency_symbol}{invoice_amount:,.2f}</b>', totals_value_style)
        ],
        [
            '',
            '',
            Paragraph(f'<b>{tax_label}</b>', totals_label_style),
            Paragraph(f'<b>{currency_symbol}{tax_amount:,.2f}</b>', totals_value_style)
        ],
        [
            '',
            '',
            '',
            ''
        ],
        [
            '',
            '',
            Paragraph('<b>TOTAL FACTURA</b>', totals_label_style),
            Paragraph(f'<b>{currency_symbol}{total_with_tax:,.2f}</b>', totals_value_style)
        ]
    ]

    totals_table = Table(totals_data, colWidths=[280, 60, 86, 86])
    totals_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEABOVE', (2, 0), (-1, 0), 1, colors.HexColor('#CCCCCC')),
        ('LINEABOVE', (2, 3), (-1, 3), 1, colors.HexColor('#CCCCCC')),
    ]))
    elements.append(totals_table)

    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer
