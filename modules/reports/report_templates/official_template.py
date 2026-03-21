#!/usr/bin/env python3
"""
Official Report Template for Tax/Bank Reports
This template generates professional-looking reports similar to official documents
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from datetime import datetime
from currency_converter import get_currency_symbol


def generate_report(buffer, report_data, settings):
    """
    Generate a professional report PDF

    Args:
        buffer: BytesIO buffer to write PDF to
        report_data: Dictionary containing report data
        settings: Application settings object
    """
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm
    )

    story = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#1a1a1a'),
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )

    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#333333'),
        spaceAfter=12,
        spaceBefore=12,
        fontName='Helvetica-Bold'
    )

    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#333333'),
        alignment=TA_LEFT
    )

    # Header - Business Information
    if settings:
        business_info = f"""
        <b>{settings.business_name or 'Business Name'}</b><br/>
        <b>{settings.owner_name or ''}</b><br/>
        VAT: {settings.vat_number or 'N/A'} | NIE: {settings.nie_number or 'N/A'}<br/>
        {settings.address or ''},<br/>
        {settings.postal_code or ''}, {settings.city or ''}<br/>
        {settings.email or ''} | {settings.phone or ''}
        """
        story.append(Paragraph(business_info, normal_style))
        story.append(Spacer(1, 0.5*cm))

    # Horizontal line
    story.append(Spacer(1, 0.3*cm))

    # Report Title
    report_type = report_data.get('report_type', 'Financial Report')
    title_text = f"{report_type}"
    story.append(Paragraph(title_text, title_style))

    # Report Period
    period_text = f"<b>Period:</b> {report_data.get('period_text', 'N/A')}"
    story.append(Paragraph(period_text, normal_style))
    story.append(Spacer(1, 0.5*cm))

    # Report Date
    date_text = f"<b>Report Generated:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    story.append(Paragraph(date_text, normal_style))
    story.append(Spacer(1, 1*cm))

    # === INCOME SECTION ===
    if report_data.get('show_income', True):
        story.append(Paragraph("INCOME SUMMARY", heading_style))
        story.append(Spacer(1, 0.3*cm))

        income_data = report_data.get('income_data', [])
        currency_mode = report_data.get('currency_mode', 'base')
        base_currency = report_data.get('base_currency', 'EUR')

        if income_data:
            total_by_currency = {}
            if currency_mode == 'original':
                # Original currency mode — show Currency column
                income_table_data = [
                    ['Invoice #', 'Date', 'Client', 'Currency', 'Amount', 'Status']
                ]
                total_by_currency = {}
                for invoice in income_data:
                    client_name = invoice.get('client_name', '')
                    if len(client_name) > 25:
                        client_name = client_name[:22] + '...'
                    cur = invoice.get('currency', base_currency)
                    sym = get_currency_symbol(cur) + ' '
                    amt = invoice.get('amount', 0)
                    total_by_currency[cur] = total_by_currency.get(cur, 0) + amt
                    income_table_data.append([
                        invoice.get('invoice_number', ''),
                        invoice.get('invoice_date', ''),
                        client_name,
                        cur,
                        f"{sym} {amt:.2f}",
                        invoice.get('status', '').upper()
                    ])
                # Total rows per currency
                for cur, total in sorted(total_by_currency.items()):
                    sym = get_currency_symbol(cur) + ' '
                    income_table_data.append([
                        '', '', '', f'TOTAL {cur}:', f"{sym} {total:.2f}", ''
                    ])
                col_widths = [2.5*cm, 2.3*cm, 5.5*cm, 2*cm, 2.5*cm, 2*cm]
            else:
                # Base currency mode — single currency column
                sym = get_currency_symbol(base_currency) + ' '
                income_table_data = [
                    ['Invoice #', 'Date', 'Client', f'Amount\n({base_currency})', 'Status']
                ]
                total_income = 0
                for invoice in income_data:
                    client_name = invoice.get('client_name', '')
                    if len(client_name) > 30:
                        client_name = client_name[:27] + '...'
                    amt = invoice.get('amount', 0)
                    total_income += amt
                    income_table_data.append([
                        invoice.get('invoice_number', ''),
                        invoice.get('invoice_date', ''),
                        client_name,
                        f"{sym} {amt:.2f}",
                        invoice.get('status', '').upper()
                    ])
                income_table_data.append([
                    '', '', 'TOTAL:', f"{sym} {total_income:.2f}", ''
                ])
                col_widths = [2.5*cm, 2.3*cm, 7*cm, 2.7*cm, 2.3*cm]

            num_total_rows = len(total_by_currency) if total_by_currency else 1

            income_table = Table(income_table_data, colWidths=col_widths)
            income_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5568')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (-3, -num_total_rows), (-3, -1), 'RIGHT'),
                ('ALIGN', (-2, 0), (-2, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 1), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
                ('BACKGROUND', (0, 1), (-1, -(num_total_rows + 1)), colors.white),
                ('GRID', (0, 0), (-1, -(num_total_rows + 1)), 0.5, colors.grey),
                ('BACKGROUND', (0, -num_total_rows), (-1, -1), colors.HexColor('#e8f5e9')),
                ('FONTNAME', (0, -num_total_rows), (-1, -1), 'Helvetica-Bold'),
                ('LINEABOVE', (0, -num_total_rows), (-1, -num_total_rows), 2, colors.HexColor('#4caf50')),
            ]))

            story.append(income_table)
            story.append(Spacer(1, 0.5*cm))

            # Income summary text
            if currency_mode == 'original' and len(total_by_currency) > 1:
                parts = [f"{get_currency_symbol(c)} {t:.2f}" for c, t in sorted(total_by_currency.items())]
                summary_text = f"<b>Total Income:</b> {' | '.join(parts)}"
            else:
                total_income = sum(inv.get('amount', 0) for inv in income_data)
                sym = get_currency_symbol(base_currency) + ' '
                summary_text = f"<b>Total Income:</b> {sym}{total_income:.2f}"
            story.append(Paragraph(summary_text, normal_style))
            story.append(Spacer(1, 1*cm))
        else:
            story.append(Paragraph("No income data for this period.", normal_style))
            story.append(Spacer(1, 1*cm))

    # === EXPENSES SECTION ===
    if report_data.get('show_expenses', True):
        story.append(Paragraph("EXPENSES SUMMARY", heading_style))
        story.append(Spacer(1, 0.3*cm))

        expenses_data = report_data.get('expenses_data', [])
        if expenses_data:
            # Custom style for table cells with word wrap
            cell_style = ParagraphStyle(
                'CellStyle',
                parent=styles['Normal'],
                fontSize=9,
                textColor=colors.HexColor('#333333'),
                alignment=TA_LEFT,
                leading=11
            )

            # Expenses table
            expenses_table_data = [
                ['Invoice #', 'Date', 'Contractor', 'Category', 'Description', 'Amount\n(EUR)']
            ]

            total_expenses = 0
            for expense in expenses_data:
                # Use Paragraph for contractor name to enable word wrap
                contractor_name = expense.get('contractor_name', 'N/A')
                contractor_para = Paragraph(contractor_name, cell_style)

                category = expense.get('category', '')
                category_para = Paragraph(category, cell_style)

                # Use Paragraph for description to enable word wrap
                description = expense.get('description', '')
                description_para = Paragraph(description, cell_style)

                invoice_number = expense.get('invoice_number', '')
                invoice_para = Paragraph(invoice_number, cell_style)

                expenses_table_data.append([
                    invoice_para,
                    expense.get('expense_date', ''),
                    contractor_para,
                    category_para,
                    description_para,
                    f"€ {expense.get('amount_eur', 0):.2f}"
                ])
                total_expenses += expense.get('amount_eur', 0)

            # Add total row
            expenses_table_data.append([
                '', '', '', '', 'TOTAL:', f"€ {total_expenses:.2f}"
            ])

            expenses_table = Table(expenses_table_data, colWidths=[2.5*cm, 2*cm, 3*cm, 2.5*cm, 4.5*cm, 2.8*cm])
            expenses_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5568')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),  # Align content to top for wrapped text
                ('ALIGN', (4, -1), (4, -1), 'RIGHT'),  # Align TOTAL: to right
                ('ALIGN', (5, 0), (5, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTSIZE', (0, 1), (-1, -1), 9),  # Smaller font for data rows
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 1), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                ('GRID', (0, 0), (-1, -2), 0.5, colors.grey),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#ffebee')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('LINEABOVE', (0, -1), (-1, -1), 2, colors.HexColor('#f44336')),
            ]))

            story.append(expenses_table)
            story.append(Spacer(1, 0.5*cm))

            # Expenses summary
            summary_text = f"<b>Total Expenses:</b> € {total_expenses:.2f}"
            story.append(Paragraph(summary_text, normal_style))
            story.append(Spacer(1, 1*cm))
        else:
            story.append(Paragraph("No expenses data for this period.", normal_style))
            story.append(Spacer(1, 1*cm))

    # === SOCIAL SECURITY SECTION ===
    ss_data = report_data.get('ss_data', [])
    total_ss = sum(p.get('amount', 0) for p in ss_data)

    if ss_data:
        story.append(Paragraph("SOCIAL SECURITY PAYMENTS", heading_style))
        story.append(Spacer(1, 0.3*cm))

        ss_cell_style = ParagraphStyle(
            'SSCellStyle',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#333333'),
            alignment=TA_LEFT,
            leading=11
        )

        ss_table_data = [
            ['Date', 'Description', 'Amount (EUR)']
        ]

        for p in ss_data:
            desc = p.get('description', '')
            ss_table_data.append([
                p.get('payment_date', ''),
                Paragraph(desc, ss_cell_style) if desc else '',
                f"€ {p.get('amount', 0):.2f}"
            ])

        ss_table_data.append(['', 'TOTAL:', f"€ {total_ss:.2f}"])

        ss_table = Table(ss_table_data, colWidths=[3*cm, 11*cm, 3*cm])
        ss_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5568')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (1, -1), (1, -1), 'RIGHT'),
            ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
            ('BACKGROUND', (0, 1), (-1, -2), colors.white),
            ('GRID', (0, 0), (-1, -2), 0.5, colors.grey),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e8f5e9')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('LINEABOVE', (0, -1), (-1, -1), 2, colors.HexColor('#4caf50')),
        ]))

        story.append(ss_table)
        story.append(Spacer(1, 0.5*cm))

        summary_text = f"<b>Total Social Security:</b> € {total_ss:.2f}"
        story.append(Paragraph(summary_text, normal_style))
        story.append(Spacer(1, 1*cm))

    # === EXTRA SECTIONS (from any module) ===
    for extra in report_data.get('extra_sections', []):
        title = extra.get('title', 'Additional Data').upper()
        story.append(Paragraph(title, heading_style))
        story.append(Spacer(1, 0.3*cm))

        rows = extra.get('data', [])
        if not rows:
            continue

        extra_cell_style = ParagraphStyle(
            f'ExtraCell_{extra.get("id", "")}',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#333333'),
            alignment=TA_LEFT,
            leading=11,
        )

        # Determine columns: explicit or auto-detect from first row keys
        col_defs = extra.get('columns')  # [{'key': 'date', 'label': 'Date', 'width': 3}, ...]
        if not col_defs:
            col_defs = [{'key': k, 'label': k.replace('_', ' ').title()} for k in rows[0].keys()]

        # Header row
        header = [c['label'] for c in col_defs]
        table_data = [header]

        total_field = extra.get('total_field')
        total_val = 0

        for row in rows:
            table_row = []
            for c in col_defs:
                val = row.get(c['key'], '')
                if c['key'] == total_field and isinstance(val, (int, float)):
                    total_val += val
                    table_row.append(f"€ {val:.2f}")
                elif isinstance(val, (int, float)):
                    table_row.append(f"{val:.2f}" if isinstance(val, float) else str(val))
                else:
                    table_row.append(Paragraph(str(val), extra_cell_style) if len(str(val)) > 20 else str(val))
            table_data.append(table_row)

        # Total row
        if total_field:
            total_row = [''] * len(col_defs)
            total_row[-2] = 'TOTAL:'
            total_row[-1] = f"€ {total_val:.2f}"
            table_data.append(total_row)

        # Column widths: use explicit or distribute evenly
        available = 17 * cm
        col_widths = []
        for c in col_defs:
            w = c.get('width')
            col_widths.append(w * cm if w else available / len(col_defs))

        extra_table = Table(table_data, colWidths=col_widths)
        style_cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5568')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
            ('BACKGROUND', (0, 1), (-1, -2 if total_field else -1), colors.white),
            ('GRID', (0, 0), (-1, -2 if total_field else -1), 0.5, colors.grey),
        ]
        if total_field:
            style_cmds.extend([
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e3f2fd')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('LINEABOVE', (0, -1), (-1, -1), 2, colors.HexColor('#2196f3')),
                ('ALIGN', (-2, -1), (-2, -1), 'RIGHT'),
                ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
            ])
        extra_table.setStyle(TableStyle(style_cmds))

        story.append(extra_table)
        story.append(Spacer(1, 1*cm))

    # === FINAL SUMMARY ===
    if report_data.get('show_summary', True):
        story.append(Paragraph("FINANCIAL SUMMARY", heading_style))
        story.append(Spacer(1, 0.3*cm))

        base_currency = report_data.get('base_currency', 'EUR')
        sym = get_currency_symbol(base_currency) + ' '

        # Summary always uses amount_eur (base currency) for consistency
        total_income = sum(inv.get('amount_eur', 0) for inv in report_data.get('income_data', []))
        total_expenses = sum(exp.get('amount_eur', 0) for exp in report_data.get('expenses_data', []))
        net_profit = total_income - total_expenses - total_ss

        summary_table_data = [
            ['Description', f'Amount ({base_currency})'],
            ['Total Income', f"{sym} {total_income:.2f}"],
            ['Total Expenses', f"{sym} {total_expenses:.2f}"],
            ['Social Security', f"{sym} {total_ss:.2f}"],
            ['Net Profit/Loss', f"{sym} {net_profit:.2f}"]
        ]

        summary_table = Table(summary_table_data, colWidths=[10*cm, 6*cm])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5568')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e3f2fd') if net_profit >= 0 else colors.HexColor('#ffebee')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, -1), (-1, -1), 12),
            ('LINEABOVE', (0, -1), (-1, -1), 2, colors.HexColor('#2196f3') if net_profit >= 0 else colors.HexColor('#f44336')),
        ]))

        story.append(summary_table)
        story.append(Spacer(1, 1*cm))

    # Footer
    story.append(Spacer(1, 2*cm))
    footer_text = f"""
    <i>This report was automatically generated by the Invoice Management System.<br/>
    For any questions, please contact {settings.email if settings else 'N/A'}.</i>
    """
    story.append(Paragraph(footer_text, normal_style))

    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer
