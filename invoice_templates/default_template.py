#!/usr/bin/env python3
"""
Default Invoice PDF Template
This template generates a professional invoice PDF with sender/recipient info and itemized services
"""

import io
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from currency_converter import get_currency_symbol
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer


def generate_invoice_pdf(invoice, customer, settings, Bank):
    """
    Generate invoice PDF using the default template

    Args:
        invoice: Invoice object
        customer: Customer object (can be None)
        settings: Settings object
        Bank: Bank model class for querying default bank

    Returns:
        BytesIO buffer containing the PDF
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                          rightMargin=50, leftMargin=50,
                          topMargin=50, bottomMargin=50)
    elements = []
    styles = getSampleStyleSheet()

    # Helvetica is built-in to ReportLab
    font_name = 'Helvetica'
    font_bold = 'Helvetica-Bold'

    # Custom styles
    title_style = styles['Normal'].clone('title_style')
    title_style.fontSize = 18
    title_style.fontName = font_bold
    title_style.alignment = 2  # Right align

    header_style = styles['Normal'].clone('header_style')
    header_style.fontSize = 10
    header_style.fontName = font_name
    header_style.textColor = colors.grey
    header_style.alignment = 2  # Right align

    header_style_title = styles['Normal'].clone('header_style')
    header_style_title.fontSize = 8
    header_style_title.fontName = font_name
    header_style_title.textColor = colors.grey
    header_style_title.alignment = 2  # Right align

    normal_style = styles['Normal'].clone('normal_style')
    normal_style.fontSize = 9
    normal_style.fontName = font_name

    # Header - All right-aligned: Invoice title, then number|date, then labels
    # First row: Invoice title
    header_data_1 = [
        ['', '', Paragraph('<b>Invoice</b>', title_style)],
    ]
    header_table_1 = Table(header_data_1, colWidths=[252, 125, 135])
    header_table_1.setStyle(TableStyle([
        ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(header_table_1)
    elements.append(Spacer(1, 10))  # Space between Invoice and number/date

    # Second row: Number and Date
    header_data_2 = [
        ['', Paragraph(f'<b>{invoice.invoice_number}</b>', header_style), Paragraph(f'<b>{invoice.invoice_date.strftime("%d/%m/%Y")}</b>', header_style)],
        ['', Paragraph('Number', header_style_title), Paragraph('Date', header_style_title)],
    ]
    header_table_2 = Table(header_data_2, colWidths=[360, 80, 65], rowHeights=[15, 15])
    header_table_2.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, 0), 'TOP'),
        ('VALIGN', (0, 1), (-1, -1), 'BOTTOM'),
        ('LINEAFTER', (1, 0), (1, 1), 1, colors.silver),  # Vertical line separator
    ]))
    elements.append(header_table_2)
    elements.append(Spacer(1, 30))

    # Sender and Recipient info side by side with grey background
    # Use settings data if available, otherwise use defaults
    if settings:
        sender_name = settings.owner_name or settings.business_name or 'Your Name'
        sender_vat = settings.vat_number or ''
        sender_nie = settings.nie_number or ''
        sender_address = settings.address or ''
        sender_city_postal = f"{settings.postal_code or ''} {settings.city or ''}".strip()
        sender_country = settings.country or ''
        sender_phone = settings.phone or ''
    else:
        sender_name = 'Your Name'
        sender_vat = ''
        sender_nie = ''
        sender_address = ''
        sender_city_postal = ''
        sender_country = ''
        sender_phone = ''

    # Create larger font style for sender/recipient info
    info_style = styles['Normal'].clone('info_style')
    info_style.fontSize = 10
    info_style.fontName = font_name
    info_style.alignment = 0  # Left align

    # Sender info - left side, no background, with NIE and VAT on same line
    sender_info = f'''<b>{sender_name}</b><br/><b>NIE: {sender_nie}, VAT: {sender_vat}</b><br/>{sender_address}<br/>{sender_city_postal}, {sender_country}<br/>{sender_phone}'''

    # Recipient info - right side, grey background
    customer_name = customer.name if customer else invoice.client_name
    customer_vat = customer.vat_number if customer and customer.vat_number else ''

    # Build recipient info with bold name and VAT if exists
    recipient_lines = [f'<b>{customer_name}</b>']
    if customer_vat:
        recipient_lines.append(f'<b>VAT: {customer_vat}</b>')
    if customer and customer.address:
        recipient_lines.append(customer.address)

    city_postal = ''
    if customer and customer.city:
        city_postal = customer.city
    if customer and customer.postal_code:
        city_postal = f"{customer.postal_code} {city_postal}".strip()
    if city_postal:
        recipient_lines.append(city_postal)
    if customer and customer.country:
        recipient_lines.append(customer.country)

    recipient_info = '<br/>'.join(recipient_lines)

    info_data = [[Paragraph(sender_info, info_style), Paragraph(recipient_info, info_style)]]
    info_table = Table(info_data, colWidths=[256, 256])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (1, 0), (1, 0), colors.HexColor('#f5f5f5')),  # Only right column has background
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),  # No left padding for sender
        ('LEFTPADDING', (1, 0), (1, 0), 10),  # Left padding for recipient
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 30))

    # Service table - use items if available, otherwise fall back to old fields
    service_header = ['DESCRIPTION', 'QUANTITY', 'UNIT PRICE', 'SUBTOTAL']
    service_data = [service_header]

    # Determine currency symbol and amount to use
    invoice_currency = invoice.currency or 'USD'
    currency_symbol = get_currency_symbol(invoice_currency)

    # Get the amount in the invoice's original currency
    if invoice_currency == 'EUR':
        invoice_amount = invoice.amount_eur
    elif invoice_currency == 'USD':
        invoice_amount = invoice.amount_usd
    else:
        # For other currencies, use USD as fallback
        invoice_amount = invoice.amount_usd

    # Get items from invoice
    if invoice.items and len(invoice.items) > 0:
        # Use new items structure
        for item in invoice.items:
            # Calculate item amounts in invoice currency
            if invoice_currency == 'EUR':
                unit_price = item.unit_price_usd * (invoice.amount_eur / invoice.amount_usd) if invoice.amount_usd else item.unit_price_usd
                subtotal = item.subtotal_usd * (invoice.amount_eur / invoice.amount_usd) if invoice.amount_usd else item.subtotal_usd
            else:
                unit_price = item.unit_price_usd
                subtotal = item.subtotal_usd

            service_data.append([
                item.description or '',
                f'{item.quantity}',
                f'{unit_price:,.2f} {currency_symbol}',
                f'{subtotal:,.2f} {currency_symbol}'
            ])
    else:
        # Fall back to old single-item structure
        unit_price = invoice.unit_price_usd if invoice.unit_price_usd else invoice_amount
        if invoice_currency == 'EUR' and invoice.amount_usd:
            unit_price = unit_price * (invoice.amount_eur / invoice.amount_usd)

        service_row = [
            invoice.description or '',
            f'{invoice.quantity}' if invoice.quantity else '1',
            f'{unit_price:,.2f} {currency_symbol}',
            f'{invoice_amount:,.2f} {currency_symbol}'
        ]
        service_data.append(service_row)

    service_table = Table(service_data, colWidths=[252, 80, 90, 90])
    service_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), colors.white),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.silver),
        ('LINEBELOW', (0, 1), (-1, -2), 0.5, colors.lightgrey),
        ('LINEBELOW', (0, -1), (-1, -1), 1, colors.silver),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(service_table)
    elements.append(Spacer(1, 30))

    # Calculate tax
    vat_pct = settings.default_vat_rate if settings and settings.default_vat_rate is not None else 21.0
    tax_rate = 0.0
    tax_label = 'IVA 0%'

    if customer and customer.tax_type:
        if customer.tax_type == 'non_eu':
            tax_rate = 0.0
            tax_label = 'IVA 0%'
        elif customer.tax_type == 'eu_b2b':
            tax_rate = 0.0
            tax_label = f'IVA {vat_pct:g}%'
        elif customer.tax_type == 'standard':
            tax_rate = vat_pct / 100.0
            tax_label = f'IVA {vat_pct:g}%'
    else:
        tax_rate = 0.0
        tax_label = f'IVA {vat_pct:g}%'

    tax_amount = invoice_amount * tax_rate
    total_with_tax = invoice_amount + tax_amount

    # Totals styles
    total_style_text = styles['Normal'].clone('total_style')
    total_style_text.fontSize = 14
    total_style_text.alignment = 0
    total_style_text.fontName = font_bold

    total_style_sum = styles['Normal'].clone('total_style')
    total_style_sum.fontSize = 14
    total_style_sum.alignment = 2
    total_style_sum.fontName = font_bold

    normal_style_sum = styles['Normal'].clone('total_style')
    normal_style_sum.fontSize = 9
    normal_style_sum.alignment = 2
    normal_style_sum.fontName = font_bold

    normal_style_text = styles['Normal'].clone('normal_style_text')
    normal_style_text.fontSize = 9
    normal_style_text.alignment = 0
    normal_style_text.fontName = font_bold

    notes_style = styles['Normal'].clone('notes_style')
    notes_style.fontSize = 9
    notes_style.fontName = font_name
    notes_style.textColor = colors.HexColor('#666666')
    notes_style.alignment = 0

    # Notes
    notes_content = ''
    if invoice.notes and invoice.notes.strip() and invoice.notes != 'None':
        notes_text = invoice.notes.replace('\n', '<br/>')
        notes_content = f'<b>Notes:</b><br/>{notes_text}'

    notes_paragraph = Paragraph(notes_content, notes_style) if notes_content else Paragraph('', notes_style)

    # Totals table
    totals_data = [
        [notes_paragraph, '', Paragraph('Subtotal', normal_style_text),
         Paragraph(f'{invoice_amount:,.2f} {currency_symbol}', normal_style_sum), ''],
        ['', '', Paragraph(tax_label, normal_style_text),
         Paragraph(f'{tax_amount:,.2f} {currency_symbol}', normal_style_sum), ''],
        ['', '', Paragraph('<b>Total</b>', total_style_text),
         Paragraph(f'<b>{total_with_tax:,.2f} {currency_symbol}</b>', total_style_sum), ''],
    ]

    totals_table = Table(totals_data, colWidths=[256, 10, 146, 90, 10])
    totals_table.setStyle(TableStyle([
        ('BACKGROUND', (1, 0), (-1, -1), colors.HexColor('#f5f5f5')),
        ('SPAN', (0, 0), (0, 2)),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('VALIGN', (0, 0), (0, -1), 'TOP'),
        ('ALIGN', (2, 0), (2, -1), 'LEFT'),
        ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (1, 0), (-1, -1), 0),
        ('RIGHTPADDING', (1, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 2), (-1, -1), 5),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
        ('LINEABOVE', (2, 2), (-2, 2), 1, colors.HexColor('#E9E9E9')),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 30))

    # Payment details - use bank from invoice if available, otherwise use default bank
    # Format: label on left (bold), value on right
    bank_data = []
    if invoice.bank:
        # Use bank from invoice
        bank_data = [['<b>IBAN:</b>', invoice.bank.iban]]
        if invoice.bank.swift:
            bank_data.append(['<b>SWIFT/BIC:</b>', invoice.bank.swift])
        if invoice.bank.bank_name:
            bank_data.append(['<b>Bank name:</b>', invoice.bank.bank_name])
    else:
        # Try to use default bank
        default_bank = Bank.query.filter_by(is_default=True).first()
        if default_bank:
            bank_data = [['<b>IBAN:</b>', default_bank.iban]]
            if default_bank.swift:
                bank_data.append(['<b>SWIFT/BIC:</b>', default_bank.swift])
            if default_bank.bank_name:
                bank_data.append(['<b>Bank name:</b>', default_bank.bank_name])
        else:
            # No bank configured
            bank_data = [
                ['<b>IBAN:</b>', '[Your IBAN]'],
                ['<b>SWIFT/BIC:</b>', '[Your SWIFT]'],
            ]

    payment_method = getattr(invoice, 'payment_method', None) or 'Bank Transfer'

    # Create bold style for headers (must be defined before use)
    header_bold_style = styles['Normal'].clone('header_bold_style')
    header_bold_style.fontSize = 9
    header_bold_style.alignment = 0  # Left align
    header_bold_style.fontName = font_bold

    bank_data_values = styles['Normal'].clone('bank_data_values')
    bank_data_values.fontSize = 9
    bank_data_values.alignment = 2  # Right align

    # Create three columns: Due Date, Payment Method, Bank Account
    due_date_text = f'''{invoice.due_date.strftime("%d/%m/%Y") if invoice.due_date else invoice.invoice_date.strftime("%d/%m/%Y")}'''

    # Build bank info as nested table with two columns
    bank_info_table_data = []
    for label, value in bank_data:
        bank_info_table_data.append([
            Paragraph(label, header_bold_style),  # Bold label on left
            Paragraph(value, bank_data_values)  # Normal value on right
        ])

    # Create nested table for bank info
    bank_info_nested_table = Table(bank_info_table_data, colWidths=[60, 152])
    bank_info_nested_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # Labels left aligned
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),  # Values right aligned
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))

    payment_header = [
        Paragraph('<b>Due Date</b>', header_bold_style),
        Paragraph('<b>Payment Method</b>', header_bold_style),
        Paragraph('<b>Bank Account</b>', header_bold_style)
    ]

    payment_row = [
        Paragraph(due_date_text, normal_style),
        Paragraph(payment_method, normal_style),
        bank_info_nested_table  # Use nested table instead of text
    ]

    payment_data = ['', payment_header, payment_row]

    payment_table = Table(payment_data, colWidths=[150, 150, 212], rowHeights=[6, 15, 45])
    payment_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), font_bold),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEABOVE', (0, 0), (-1, 0), 1, colors.silver),  # Grey horizontal line above header
        ('LINEAFTER', (0, 1), (1, -1), 1, colors.silver),  # Grey vertical lines between columns
        ('TOPPADDING', (0, 1), (2, 1), 0),  # first line
        ('BOTTOMPADDING', (0, -1), (-1, -1), 0),  # last line
    ]))
    elements.append(payment_table)

    # Add page numbers to footer using onPage callback
    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        page_num = canvas.getPageNumber()
        text = f"Page {page_num}"
        canvas.drawRightString(letter[0] - 50, 30, text)
        canvas.restoreState()

    doc.build(elements, onFirstPage=add_page_number, onLaterPages=add_page_number)
    buffer.seek(0)
    return buffer
