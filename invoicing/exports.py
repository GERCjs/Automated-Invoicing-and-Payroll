from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from django.conf import settings
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from payments.services import get_bank_transfer_details

from .models import Invoice


def build_export_context(invoice: Invoice) -> dict:
    items = list(invoice.items.all())
    return {
        "company_name": getattr(settings, "COMPANY_NAME", "Vaniday Singapore Pte Ltd"),
        "company_email": settings.COMPANY_EMAIL,
        "company_phone": settings.COMPANY_PHONE,
        "company_address": settings.COMPANY_ADDRESS,
        "company_reg_no": settings.COMPANY_REG_NO,
        "registered_office_text": settings.REGISTERED_OFFICE_TEXT,
        "invoice_payment_term_days": settings.INVOICE_PAYMENT_TERM_DAYS,
        "bank_transfer_details": get_bank_transfer_details(),
        "invoice_payment_notes": settings.INVOICE_PAYMENT_NOTES,
        "invoice_attention_email": getattr(settings, "COMPANY_EMAIL", "finance@vaniday.com"),
        "invoice": invoice,
        "items": items,
    }


def generate_invoice_pdf(invoice: Invoice) -> bytes:
    context = build_export_context(invoice)
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "InvoiceBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
    )
    small = ParagraphStyle(
        "InvoiceSmall",
        parent=body,
        fontSize=8,
        leading=10,
    )
    small_right = ParagraphStyle(
        "InvoiceSmallRight",
        parent=small,
        alignment=2,
    )
    heading = ParagraphStyle(
        "InvoiceHeading",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
    )
    invoice_title = ParagraphStyle(
        "InvoiceTitle",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=24,
    )
    right = ParagraphStyle(
        "InvoiceRight",
        parent=body,
        alignment=2,
    )
    right_bold = ParagraphStyle(
        "InvoiceRightBold",
        parent=right,
        fontName="Helvetica-Bold",
    )
    right_title = ParagraphStyle(
        "InvoiceRightTitle",
        parent=right_bold,
        fontSize=22,
        leading=24,
    )

    customer_display = invoice.customer.name
    if invoice.customer.tax_number:
        customer_display = f"{customer_display} ({invoice.customer.tax_number})"

    registered_office = context["registered_office_text"]
    attention_email = context["invoice_attention_email"]
    office_line = f"Registered Office: {registered_office}"
    attention_line = f"Attention: {attention_email}"
    header_left = [
        [Paragraph(f"<b>{context['company_name']}</b>", heading)],
        [Paragraph(f"Reg No. {context['company_reg_no']}", body)],
        [Paragraph(context["company_address"].replace("\n", "<br/>"), body)],
        [Paragraph(f"Email: {context['company_email']}", body)],
        [Paragraph(f"Phone: {context['company_phone']}", body)],
    ]
    left_header_table = Table(header_left, colWidths=[78 * mm])
    left_header_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )

    right_header_data = [
        [Paragraph("INVOICE", right_title)],
        [Paragraph(customer_display, right_bold)],
        [Paragraph("<b>Invoice Date</b>", right)],
        [Paragraph(str(invoice.issue_date.strftime("%d %b %Y")), right)],
        [Paragraph("<b>Invoice Number</b>", right)],
        [Paragraph(invoice.invoice_number, right)],
    ]
    right_header_table = Table(right_header_data, colWidths=[82 * mm])
    right_header_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )

    story = [
        Paragraph(office_line, small_right),
        Paragraph(attention_line, small_right),
        Spacer(1, 6),
        Table([[left_header_table, right_header_table]], colWidths=[78 * mm, 82 * mm]),
        Spacer(1, 10),
    ]

    amount_header = f"Amount {invoice.currency}"
    table_data = [["Description", "Quantity", "Unit Price", amount_header]]
    for item in context["items"]:
        description = (item.description or "").replace("\n", "<br/>")
        unit_value = f"{item.unit_price:.2f}"
        line_value = f"{item.line_total:.2f}"
        table_data.append(
            [
                Paragraph(description, body),
                str(item.quantity),
                unit_value,
                line_value,
            ]
        )
    if len(table_data) == 1:
        table_data.append(["No items", "-", "-", "-"])

    table = Table(table_data, colWidths=[95 * mm, 20 * mm, 22 * mm, 23 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.black),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, -1), (-1, -1), 0.8, colors.black),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 10))

    amount_paid = Decimal("0.00")
    if invoice.status == Invoice.STATUS_PAID:
        amount_paid = invoice.total_amount
    amount_due = invoice.total_amount - amount_paid
    totals = [
        ["Subtotal", f"{invoice.subtotal:.2f}"],
        ["GST", f"{invoice.tax_amount:.2f}"],
        [f"TOTAL {invoice.currency}", f"{invoice.total_amount:.2f}"],
        ["Less Amount Paid", f"{amount_paid:.2f}"],
        [f"AMOUNT DUE {invoice.currency}", f"{amount_due:.2f}"],
    ]
    totals_table = Table(totals, colWidths=[124 * mm, 36 * mm])
    totals_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(totals_table)

    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Due Date: {invoice.due_date.strftime('%d %b %Y')}", body))
    story.append(Paragraph(f"Payment Term: {context['invoice_payment_term_days']} Days", body))
    bank_transfer_details = context["bank_transfer_details"]
    if bank_transfer_details:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Bank Transfer Details</b>", body))
        story.append(Paragraph("Please arrange payment via bank transfer to:", body))
        detail_lines = [
            ("Account Name", bank_transfer_details.get("account_name", "")),
            ("Bank", bank_transfer_details.get("bank_name", "")),
            ("Account Number", bank_transfer_details.get("account_number", "")),
            ("PayNow ID", bank_transfer_details.get("paynow_id", "")),
            ("BIC/SWIFT", bank_transfer_details.get("bic", "")),
        ]
        for label, value in detail_lines:
            if value:
                story.append(Paragraph(f"<b>{label}:</b> {value}", body))
        for line in bank_transfer_details.get("instructions", "").split("\n"):
            if line.strip():
                story.append(Paragraph(line, body))

    story.append(Spacer(1, 4))
    for line in context["invoice_payment_notes"].split("\n"):
        if line.strip():
            story.append(Paragraph(line, small))

    if invoice.notes:
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"Notes: {invoice.notes}", body))

    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "This is a computer generated invoice and therefore no signature is required.",
            small,
        )
    )

    doc.build(story)
    return buffer.getvalue()


def generate_invoice_excel(invoice: Invoice) -> bytes:
    context = build_export_context(invoice)
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"

    header_fill = PatternFill(fill_type="solid", start_color="0D6EFD", end_color="0D6EFD")
    header_font = Font(color="FFFFFF", bold=True)

    ws["A1"] = context["company_name"]
    ws["A2"] = context["company_address"]
    ws["A3"] = f"Email: {context['company_email']}"
    ws["A4"] = f"Phone: {context['company_phone']}"

    ws["E1"] = "Invoice #"
    ws["F1"] = invoice.invoice_number
    ws["E2"] = "Issue Date"
    ws["F2"] = str(invoice.issue_date)
    ws["E3"] = "Due Date"
    ws["F3"] = str(invoice.due_date)
    ws["E4"] = "Status"
    ws["F4"] = invoice.get_status_display()

    ws["A6"] = "Bill To"
    ws["A7"] = invoice.customer.name
    ws["A8"] = invoice.customer.billing_address or "-"
    ws["A9"] = invoice.customer.email

    headers = ["Description", "Qty", "Unit Price", "GST %", "Line Total"]
    row = 11
    for idx, title in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=idx, value=title)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    row += 1
    for item in context["items"]:
        ws.cell(row=row, column=1, value=item.description)
        ws.cell(row=row, column=2, value=float(item.quantity))
        ws.cell(row=row, column=3, value=float(item.unit_price))
        ws.cell(row=row, column=4, value=float(item.tax_rate))
        ws.cell(row=row, column=5, value=float(item.line_total))
        row += 1

    if row == 12:
        ws.cell(row=row, column=1, value="No items")
        row += 1

    row += 1
    ws.cell(row=row, column=4, value="Subtotal").font = Font(bold=True)
    ws.cell(row=row, column=5, value=float(invoice.subtotal))
    row += 1
    ws.cell(row=row, column=4, value="GST").font = Font(bold=True)
    ws.cell(row=row, column=5, value=float(invoice.tax_amount))
    row += 1
    ws.cell(row=row, column=4, value="Total").font = Font(bold=True)
    ws.cell(row=row, column=5, value=float(invoice.total_amount)).font = Font(bold=True)

    if invoice.notes:
        row += 2
        ws.cell(row=row, column=1, value="Notes").font = Font(bold=True)
        row += 1
        ws.cell(row=row, column=1, value=invoice.notes)

    widths = {"A": 45, "B": 10, "C": 15, "D": 10, "E": 15, "F": 25}
    for column, width in widths.items():
        ws.column_dimensions[column].width = width

    output = BytesIO()
    wb.save(output)
    return output.getvalue()
