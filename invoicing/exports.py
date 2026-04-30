from __future__ import annotations

from io import BytesIO

from django.conf import settings
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Invoice


def build_export_context(invoice: Invoice) -> dict:
    items = list(invoice.items.all())
    return {
        "company_name": settings.COMPANY_NAME,
        "company_email": settings.COMPANY_EMAIL,
        "company_phone": settings.COMPANY_PHONE,
        "company_address": settings.COMPANY_ADDRESS,
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
    body = styles["BodyText"]
    title_style = styles["Heading2"]
    title_style.textColor = colors.HexColor("#0d6efd")

    story = [
        Paragraph(context["company_name"], title_style),
        Paragraph(context["company_address"], body),
        Paragraph(f"Email: {context['company_email']} | Phone: {context['company_phone']}", body),
        Spacer(1, 8),
        Paragraph(f"<b>Invoice:</b> {invoice.invoice_number}", body),
        Paragraph(f"<b>Status:</b> {invoice.get_status_display()}", body),
        Paragraph(f"<b>Issue Date:</b> {invoice.issue_date}", body),
        Paragraph(f"<b>Due Date:</b> {invoice.due_date}", body),
        Spacer(1, 8),
        Paragraph(f"<b>Bill To:</b> {invoice.customer.name}", body),
        Paragraph(invoice.customer.billing_address or "-", body),
        Paragraph(invoice.customer.email, body),
        Spacer(1, 10),
    ]

    table_data = [["Description", "Qty", "Unit Price", "Tax %", "Line Total"]]
    for item in context["items"]:
        table_data.append(
            [
                item.description,
                str(item.quantity),
                f"{invoice.currency} {item.unit_price}",
                str(item.tax_rate),
                f"{invoice.currency} {item.line_total}",
            ]
        )
    if len(table_data) == 1:
        table_data.append(["No items", "-", "-", "-", "-"])

    table = Table(table_data, colWidths=[70 * mm, 22 * mm, 30 * mm, 20 * mm, 30 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 10))

    totals = [
        ["Subtotal", f"{invoice.currency} {invoice.subtotal}"],
        ["Tax", f"{invoice.currency} {invoice.tax_amount}"],
        ["Total", f"{invoice.currency} {invoice.total_amount}"],
    ]
    totals_table = Table(totals, colWidths=[120 * mm, 52 * mm])
    totals_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
            ]
        )
    )
    story.append(totals_table)

    if invoice.notes:
        story.append(Spacer(1, 10))
        story.append(Paragraph("<b>Notes</b>", body))
        story.append(Paragraph(invoice.notes, body))

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

    headers = ["Description", "Qty", "Unit Price", "Tax %", "Line Total"]
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
    ws.cell(row=row, column=4, value="Tax").font = Font(bold=True)
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
