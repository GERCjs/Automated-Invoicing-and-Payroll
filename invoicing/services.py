from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Invoice, InvoiceItem

ZERO = Decimal("0.00")
TWOPLACES = Decimal("0.01")


def _to_money(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def generate_invoice_number() -> str:
    year = timezone.localdate().year
    prefix = f"INV-{year}-"
    last_invoice = (
        Invoice.objects.filter(invoice_number__startswith=prefix)
        .order_by("-invoice_number")
        .only("invoice_number")
        .first()
    )
    if not last_invoice:
        return f"{prefix}0001"

    try:
        last_seq = int(last_invoice.invoice_number.split("-")[-1])
    except (ValueError, IndexError):
        last_seq = Invoice.objects.filter(invoice_number__startswith=prefix).count()
    return f"{prefix}{last_seq + 1:04d}"


def calculate_item_amounts(item: InvoiceItem) -> tuple[Decimal, Decimal, Decimal]:
    base_amount = _to_money(Decimal(item.quantity) * Decimal(item.unit_price))
    tax_amount = _to_money(base_amount * (Decimal(item.tax_rate) / Decimal("100")))
    line_total = _to_money(base_amount + tax_amount)
    return base_amount, tax_amount, line_total


def recalculate_invoice_totals(invoice: Invoice) -> Invoice:
    subtotal = ZERO
    tax_total = ZERO
    total = ZERO
    for item in invoice.items.all():
        base_amount, tax_amount, line_total = calculate_item_amounts(item)
        if item.line_total != line_total:
            item.line_total = line_total
            item.save(update_fields=["line_total", "updated_at"])
        subtotal += base_amount
        tax_total += tax_amount
        total += line_total

    invoice.subtotal = _to_money(subtotal)
    invoice.tax_amount = _to_money(tax_total)
    invoice.total_amount = _to_money(total)
    invoice.save(update_fields=["subtotal", "tax_amount", "total_amount", "updated_at"])
    return invoice


def apply_overdue_status(invoice: Invoice, today: date | None = None) -> bool:
    today = today or timezone.localdate()
    if invoice.status == Invoice.STATUS_PAID:
        return False
    if invoice.due_date < today and invoice.status in {
        Invoice.STATUS_DRAFT,
        Invoice.STATUS_SENT,
        Invoice.STATUS_VIEWED,
    }:
        invoice.status = Invoice.STATUS_OVERDUE
        invoice.save(update_fields=["status", "updated_at"])
        return True
    return False


def refresh_overdue_invoices(today: date | None = None) -> int:
    today = today or timezone.localdate()
    return Invoice.objects.filter(
        due_date__lt=today,
        status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_SENT, Invoice.STATUS_VIEWED],
    ).update(status=Invoice.STATUS_OVERDUE, updated_at=timezone.now())


def transition_invoice_status(invoice: Invoice, new_status: str) -> tuple[bool, str]:
    allowed_transitions = {
        Invoice.STATUS_DRAFT: {Invoice.STATUS_SENT, Invoice.STATUS_PAID},
        Invoice.STATUS_SENT: {Invoice.STATUS_VIEWED, Invoice.STATUS_PAID, Invoice.STATUS_OVERDUE},
        Invoice.STATUS_VIEWED: {Invoice.STATUS_PAID, Invoice.STATUS_OVERDUE},
        Invoice.STATUS_OVERDUE: {Invoice.STATUS_PAID},
        Invoice.STATUS_PAID: set(),
    }
    if new_status not in dict(Invoice.STATUS_CHOICES):
        return False, "Invalid status."
    if new_status == invoice.status:
        return False, "Invoice is already in the selected status."

    allowed = allowed_transitions.get(invoice.status, set())
    if new_status not in allowed:
        return False, f"Cannot move status from {invoice.status} to {new_status}."

    if new_status == Invoice.STATUS_OVERDUE and invoice.due_date >= timezone.localdate():
        return False, "Invoice cannot be marked overdue before its due date."

    invoice.status = new_status
    invoice.save(update_fields=["status", "updated_at"])
    return True, "Status updated."


@transaction.atomic
def mark_invoice_viewed(invoice: Invoice) -> Invoice:
    Invoice.objects.filter(pk=invoice.pk).update(view_count=F("view_count") + 1)
    invoice.refresh_from_db()
    if invoice.viewed_at is None:
        invoice.viewed_at = timezone.now()
    if invoice.status == Invoice.STATUS_SENT:
        invoice.status = Invoice.STATUS_VIEWED
    invoice.save(update_fields=["viewed_at", "status", "updated_at"])
    apply_overdue_status(invoice)
    return invoice
