from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum
from django.db.models import Q
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from datetime import date
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from accounts.permissions import role_required
from accounts.roles import ADMIN, CUSTOMER, FINANCE, SUPERADMIN
from core.audit import get_client_ip, log_event
from imports.models import ImportJob, ImportRowError
from notifications.models import EmailDeliveryLog
from notifications.services import get_invoice_reminder_history, send_invoice_email
from payments.services import (
    get_bank_transfer_details,
    get_or_create_bank_transfer_payment,
    successful_payments_queryset,
)
from payments.models import PaymentRecord
from payments.forms import BankTransferConfirmationForm, BankTransferNoticeForm
from support.models import SupportTicket

from .exports import generate_invoice_excel, generate_invoice_pdf
from .forms import (
    CustomerCreateForm,
    InvoiceCsvUploadForm,
    InvoiceForm,
    InvoiceItemFormSet,
    InvoiceTemplateSettingsForm,
)
from .models import Customer as InvoiceCustomer
from .models import Invoice
from .models import InvoiceTemplateSettings
from .services import (
    apply_overdue_status,
    generate_invoice_number,
    import_invoice_rows_from_preview,
    mark_invoice_viewed,
    parse_invoice_upload,
    recalculate_invoice_totals,
    refresh_overdue_invoices,
    transition_invoice_status,
)

CSV_IMPORT_SESSION_KEY_PREFIX = "invoice_csv_import_preview_"
BATCH_INVOICE_EMAIL_ALLOWED_STATUSES = {
    Invoice.STATUS_DRAFT,
    Invoice.STATUS_SENT,
    Invoice.STATUS_VIEWED,
    Invoice.STATUS_OVERDUE,
}
BANK_TRANSFER_PAYABLE_STATUSES = {
    Invoice.STATUS_SENT,
    Invoice.STATUS_VIEWED,
    Invoice.STATUS_OVERDUE,
}
OUTSTANDING_INVOICE_STATUSES = {
    Invoice.STATUS_SENT,
    Invoice.STATUS_VIEWED,
    Invoice.STATUS_OVERDUE,
}
INVOICE_DASHBOARD_DEFAULT_CATEGORY = "bank_transfer_to_verify"


def _invoice_dashboard_category_queryset(category_key: str):
    base_queryset = Invoice.objects.select_related("customer")
    if category_key == "outstanding":
        return base_queryset.filter(status__in=OUTSTANDING_INVOICE_STATUSES)
    if category_key == "overdue":
        return base_queryset.filter(status=Invoice.STATUS_OVERDUE)
    if category_key == "requires_follow_up":
        return base_queryset.filter(
            status__in=[
                Invoice.STATUS_DRAFT,
                Invoice.STATUS_SENT,
                Invoice.STATUS_VIEWED,
                Invoice.STATUS_OVERDUE,
            ]
        )
    if category_key == "bank_transfer_to_verify":
        return base_queryset.filter(
            payment_records__provider=PaymentRecord.PROVIDER_MANUAL,
            payment_records__status=PaymentRecord.STATUS_PENDING,
            payment_records__manual_customer_submitted_at__isnull=False,
        ).distinct()
    return base_queryset.none()


def _order_invoice_dashboard_category_queryset(queryset):
    return queryset.order_by("due_date", "-issue_date", "-created_at")


def _build_invoice_dashboard_categories(selected_key: str):
    category_definitions = [
        {
            "key": "bank_transfer_to_verify",
            "label": "Bank Transfer to Verify",
            "description": "Customer-submitted bank transfer notices waiting for Finance verification.",
        },
        {
            "key": "outstanding",
            "label": "Outstanding",
            "description": "Sent, viewed, and overdue invoices still waiting for payment.",
        },
        {
            "key": "overdue",
            "label": "Overdue",
            "description": "Invoices already past due and needing immediate collection follow-up.",
        },
        {
            "key": "requires_follow_up",
            "label": "Requires Follow Up",
            "description": "Draft, pending, viewed, and overdue invoices that still need Finance action.",
        },
    ]
    categories = []
    for category in category_definitions:
        category_queryset = _invoice_dashboard_category_queryset(category["key"])
        categories.append(
            {
                **category,
                "count": category_queryset.count(),
                "is_selected": category["key"] == selected_key,
                "url": f"{reverse('invoice-dashboard')}?category={category['key']}",
            }
        )
    return categories


def _invoice_support_ticket_action_summary():
    active_tickets = list(
        SupportTicket.objects.filter(
            category__in=[
                SupportTicket.CATEGORY_INVOICE,
                SupportTicket.CATEGORY_PAYMENT,
            ],
            status__in=[
                SupportTicket.STATUS_OPEN,
                SupportTicket.STATUS_IN_PROGRESS,
            ],
        ).only("id", "status", "created_at")
    )
    open_count = sum(1 for ticket in active_tickets if ticket.status == SupportTicket.STATUS_OPEN)
    in_progress_count = sum(
        1 for ticket in active_tickets if ticket.status == SupportTicket.STATUS_IN_PROGRESS
    )
    return {
        "open_count": open_count,
        "in_progress_count": in_progress_count,
        "overdue_count": sum(1 for ticket in active_tickets if ticket.is_sla_breached),
        "active_count": len(active_tickets),
        "list_url": reverse("finance-support-ticket-list"),
    }


def _bank_transfer_context(invoice: Invoice, initiated_by=None) -> dict:
    if invoice.status not in BANK_TRANSFER_PAYABLE_STATUSES:
        return {
            "bank_transfer_details": None,
            "bank_transfer_payment": None,
        }
    bank_transfer_details = get_bank_transfer_details()
    if bank_transfer_details is None:
        return {
            "bank_transfer_details": None,
            "bank_transfer_payment": None,
        }
    bank_transfer_payment = get_or_create_bank_transfer_payment(
        invoice=invoice,
        initiated_by=initiated_by,
    )
    return {
        "bank_transfer_details": bank_transfer_details,
        "bank_transfer_payment": bank_transfer_payment,
        "bank_transfer_confirmation_form": BankTransferConfirmationForm(
            payment_record=bank_transfer_payment
        ),
        "bank_transfer_notice_form": BankTransferNoticeForm(payment_record=bank_transfer_payment),
    }


def _invoice_reminder_context(invoice: Invoice) -> dict:
    reminder_history = list(get_invoice_reminder_history(invoice))
    sent_reminder_history = [log for log in reminder_history if log.status == EmailDeliveryLog.STATUS_SENT]
    last_reminder_sent_log = sent_reminder_history[0] if sent_reminder_history else None
    return {
        "reminder_history": reminder_history,
        "reminders_sent_count": len(sent_reminder_history),
        "last_reminder_sent_at": (
            last_reminder_sent_log.sent_at or last_reminder_sent_log.attempted_at
            if last_reminder_sent_log
            else None
        ),
    }


def _get_linked_customer_for_user(user):
    email = (user.email or "").strip()
    if not email:
        return None
    return InvoiceCustomer.objects.filter(email__iexact=email).first()


def _get_customer_invoice_queryset(user):
    linked_customer = _get_linked_customer_for_user(user)
    if linked_customer is None:
        return None, Invoice.objects.none()
    invoices = Invoice.objects.select_related("customer").filter(customer=linked_customer)
    return linked_customer, invoices


def _csv_import_session_key(import_token: str) -> str:
    return f"{CSV_IMPORT_SESSION_KEY_PREFIX}{import_token}"


def _clear_invoice_import_preview_sessions(request) -> None:
    for key in list(request.session.keys()):
        if key.startswith(CSV_IMPORT_SESSION_KEY_PREFIX):
            request.session.pop(key, None)


def _resolve_next_url(request, next_url: str, fallback: str) -> str:
    candidate = (next_url or "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def _append_customer_query(url: str, customer_id: int) -> str:
    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query["customer"] = str(customer_id)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _build_chart_summary(labels, values):
    numeric_values = [float(value or 0) for value in values]
    total_value = sum(numeric_values)
    has_data = any(value > 0 for value in numeric_values)
    peak_label = ""
    peak_value = 0.0
    if has_data and labels and numeric_values:
        peak_index = max(range(len(numeric_values)), key=numeric_values.__getitem__)
        peak_label = labels[peak_index]
        peak_value = numeric_values[peak_index]
    return {
        "has_data": has_data,
        "six_month_total": total_value,
        "peak_label": peak_label,
        "peak_value": peak_value,
    }


def _recent_month_starts(today, total_months=6):
    month_start = today.replace(day=1)
    month_starts = []
    for offset in range(total_months - 1, -1, -1):
        year = month_start.year
        month = month_start.month - offset
        while month <= 0:
            month += 12
            year -= 1
        month_starts.append(month_start.replace(year=year, month=month, day=1))
    return month_starts


def _get_batch_invoice_email_block_reason(invoice: Invoice) -> str:
    if invoice.status not in BATCH_INVOICE_EMAIL_ALLOWED_STATUSES:
        if invoice.status == Invoice.STATUS_PAID:
            return "Paid invoices are excluded from batch email sending."
        if invoice.status == Invoice.STATUS_REFUNDED:
            return "Refunded invoices are excluded from batch email sending."
        return f"{invoice.get_status_display()} invoices are excluded from batch email sending."
    return ""


def _send_invoice_email_with_audit(request, invoice: Invoice) -> tuple[bool, EmailDeliveryLog]:
    public_invoice_url = request.build_absolute_uri(
        reverse("invoice-public-view", args=[invoice.public_view_token])
    )
    success, delivery_log = send_invoice_email(
        invoice=invoice,
        public_invoice_url=public_invoice_url,
        triggered_by=request.user,
    )

    if success:
        log_event(
            action="invoice.email.sent",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "delivery_log_id": delivery_log.id,
                "recipient_email": invoice.customer.email,
            },
            ip_address=get_client_ip(request),
        )
    else:
        log_event(
            action="invoice.email.failed",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={
                "invoice_number": invoice.invoice_number,
                "delivery_log_id": delivery_log.id,
                "recipient_email": invoice.customer.email,
                "error_message": delivery_log.error_message,
            },
            ip_address=get_client_ip(request),
        )
    return success, delivery_log


def _format_invoice_batch_feedback(entries: list[str], *, max_items: int = 5) -> str:
    if not entries:
        return ""
    visible_entries = entries[:max_items]
    suffix = ""
    if len(entries) > max_items:
        suffix = f" and {len(entries) - max_items} more"
    return "; ".join(visible_entries) + suffix


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_csv_upload(request):
    form = InvoiceCsvUploadForm()
    preview_payload = None

    if request.method == "POST":
        action = request.POST.get("action", "preview")

        if action == "confirm":
            import_token = request.POST.get("import_token", "").strip()
            session_key = _csv_import_session_key(import_token)
            preview_payload = request.session.get(session_key)
            if not preview_payload:
                messages.error(request, "Import preview expired. Please upload the file again.")
                return redirect("invoice-csv-upload")

            import_job = get_object_or_404(ImportJob, pk=preview_payload["import_job_id"])
            valid_rows = preview_payload["valid_rows"]
            all_rows = preview_payload["all_rows"]

            import_job.status = ImportJob.STATUS_PROCESSING
            import_job.started_at = timezone.now()
            import_job.save(update_fields=["status", "started_at", "updated_at"])

            if not valid_rows:
                import_job.status = ImportJob.STATUS_COMPLETED_WITH_ERRORS
                import_job.completed_at = timezone.now()
                import_job.saved_rows = 0
                import_job.save(update_fields=["status", "completed_at", "saved_rows", "updated_at"])
                request.session.pop(session_key, None)
                messages.error(request, "No valid rows to import. Please fix the file errors and retry.")
                return redirect("invoice-csv-upload")

            summary = import_invoice_rows_from_preview(
                valid_rows=valid_rows,
                all_rows=all_rows,
                source_file_name=preview_payload["source_file_name"],
                initiated_by=request.user,
            )

            import_job.status = (
                ImportJob.STATUS_COMPLETED
                if import_job.invalid_rows == 0
                else ImportJob.STATUS_COMPLETED_WITH_ERRORS
            )
            import_job.completed_at = timezone.now()
            import_job.saved_rows = summary["saved_rows"]
            import_job.save(update_fields=["status", "completed_at", "saved_rows", "updated_at"])

            request.session.pop(session_key, None)
            log_event(
                action="invoice.csv_import.confirmed",
                user=request.user,
                metadata={
                    "path": request.path,
                    "import_job_id": import_job.id,
                    "saved_rows": summary["saved_rows"],
                    "created_invoices": summary["created_invoices"],
                },
                ip_address=get_client_ip(request),
            )
            messages.success(
                request,
                (
                    f"Invoice import completed. Invoices created: {summary['created_invoices']}, "
                    f"items created: {summary['created_items']}, source rows stored: {summary['stored_source_rows']}."
                ),
            )
            return redirect("invoice-list")

        form = InvoiceCsvUploadForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = form.cleaned_data["csv_file"]
            try:
                parsed = parse_invoice_upload(csv_file)
            except ValueError as exc:
                messages.error(request, str(exc))
                return render(
                    request,
                    "invoicing/invoice_csv_upload_preview.html",
                    {"form": form, "preview": None},
                )

            import_job = ImportJob.objects.create(
                module=ImportJob.MODULE_INVOICING,
                source_file_name=csv_file.name,
                status=ImportJob.STATUS_PENDING,
                total_rows=parsed["total_rows"],
                valid_rows=len(parsed["valid_rows"]),
                invalid_rows=len(parsed["invalid_rows"]),
                initiated_by=request.user,
            )

            row_errors = []
            for row in parsed["invalid_rows"]:
                for error_message in row["errors"]:
                    row_errors.append(
                        ImportRowError(
                            import_job=import_job,
                            row_number=row["row_number"],
                            field_name="",
                            error_message=error_message,
                            raw_data=row["source"],
                        )
                    )
            if row_errors:
                ImportRowError.objects.bulk_create(row_errors)

            import_token = uuid.uuid4().hex
            session_key = _csv_import_session_key(import_token)
            _clear_invoice_import_preview_sessions(request)
            preview_payload = {
                "import_token": import_token,
                "import_job_id": import_job.id,
                "source_file_name": csv_file.name,
                "total_rows": parsed["total_rows"],
                "valid_rows": parsed["valid_rows"],
                "invalid_rows": parsed["invalid_rows"],
                "all_rows": parsed["all_rows"],
                "preview_groups": parsed["preview_groups"],
            }
            request.session[session_key] = preview_payload
            log_event(
                action="invoice.uploaded",
                user=request.user,
                metadata={
                    "path": request.path,
                    "source_file_name": csv_file.name,
                    "total_rows": parsed["total_rows"],
                    "valid_rows": len(parsed["valid_rows"]),
                    "invalid_rows": len(parsed["invalid_rows"]),
                },
                ip_address=get_client_ip(request),
            )
            log_event(
                action="invoice.csv_import.previewed",
                user=request.user,
                metadata={
                    "path": request.path,
                    "import_job_id": import_job.id,
                    "total_rows": parsed["total_rows"],
                    "valid_rows": len(parsed["valid_rows"]),
                    "invalid_rows": len(parsed["invalid_rows"]),
                },
                ip_address=get_client_ip(request),
            )
    return render(
        request,
        "invoicing/invoice_csv_upload_preview.html",
        {
            "form": form,
            "preview": preview_payload,
            "invalid_preview_rows": (preview_payload or {}).get("invalid_rows", [])[:20],
            "group_preview_rows": (preview_payload or {}).get("preview_groups", []),
        },
    )


@login_required
@role_required(CUSTOMER)
def customer_invoice_dashboard(request):
    refresh_overdue_invoices()
    linked_customer, scoped_invoices = _get_customer_invoice_queryset(request.user)

    action_statuses = [Invoice.STATUS_SENT, Invoice.STATUS_VIEWED, Invoice.STATUS_OVERDUE]
    action_required_invoices = scoped_invoices.filter(status__in=action_statuses).order_by("due_date", "-issue_date")
    paid_invoices = scoped_invoices.filter(status=Invoice.STATUS_PAID).order_by("-issue_date", "-created_at")

    outstanding_amount = (
        action_required_invoices.aggregate(total=Sum("total_amount"))["total"]
        or 0
    )
    pending_payment_count = action_required_invoices.filter(status=Invoice.STATUS_SENT).count()
    overdue_count = action_required_invoices.filter(status=Invoice.STATUS_OVERDUE).count()

    return render(
        request,
        "invoicing/customer_invoice_dashboard.html",
        {
            "linked_customer": linked_customer,
            "action_required_invoices": action_required_invoices,
            "paid_invoices": paid_invoices,
            "outstanding_amount": outstanding_amount,
            "pending_payment_count": pending_payment_count,
            "overdue_count": overdue_count,
        },
    )


@login_required
@role_required(CUSTOMER)
def customer_invoice_detail(request, pk):
    linked_customer, scoped_invoices = _get_customer_invoice_queryset(request.user)
    if linked_customer is None:
        raise Http404()
    invoice = get_object_or_404(scoped_invoices, pk=pk)
    apply_overdue_status(invoice)
    invoice.refresh_from_db()
    reminder_context = _invoice_reminder_context(invoice)

    return render(
        request,
        "invoicing/customer_invoice_detail.html",
        {
            "invoice": invoice,
            "items": invoice.items.all(),
            **_bank_transfer_context(invoice, initiated_by=request.user),
            "bank_transfer_notice_action": reverse("payment-bank-transfer-notice-customer", args=[invoice.pk]),
            **reminder_context,
        },
    )


@login_required
@role_required(CUSTOMER)
def customer_invoice_download_pdf(request, pk):
    linked_customer, scoped_invoices = _get_customer_invoice_queryset(request.user)
    if linked_customer is None:
        raise Http404()
    invoice = get_object_or_404(scoped_invoices, pk=pk)
    pdf_bytes = generate_invoice_pdf(invoice)

    log_event(
        action="invoice.customer.pdf.downloaded",
        user=request.user,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "customer_id": linked_customer.id,
        },
        ip_address=get_client_ip(request),
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.pdf"'
    return response


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_list(request):
    updated_count = refresh_overdue_invoices()
    selected_filter = request.GET.get("status", "").strip().lower()
    search_query = request.GET.get("q", "").strip()
    issue_date_from_raw = request.GET.get("issue_date_from", "").strip()
    issue_date_to_raw = request.GET.get("issue_date_to", "").strip()

    def _parse_issue_date(raw_value: str, label: str) -> tuple[date | None, str]:
        value = (raw_value or "").strip()
        if not value:
            return None, ""
        try:
            parsed_value = parse_date(value)
        except ValueError:
            parsed_value = None
        if parsed_value is None:
            return None, f'{label} "{value}" is invalid. Use YYYY-MM-DD.'
        return parsed_value, ""

    issue_date_from, issue_date_from_error = _parse_issue_date(issue_date_from_raw, "From date")
    issue_date_to, issue_date_to_error = _parse_issue_date(issue_date_to_raw, "To date")
    issue_date_error = issue_date_from_error or issue_date_to_error
    issue_date_from_filter = issue_date_from
    issue_date_to_filter = issue_date_to
    if issue_date_error:
        if issue_date_from_error:
            issue_date_from_filter = None
        if issue_date_to_error:
            issue_date_to_filter = None
    elif issue_date_from and issue_date_to and issue_date_from > issue_date_to:
        issue_date_error = "From date cannot be later than To date."
        issue_date_from_filter = None
        issue_date_to_filter = None

    invoices = Invoice.objects.select_related("customer").all()

    filter_map = {
        "draft": [Invoice.STATUS_DRAFT],
        "unsent": [Invoice.STATUS_DRAFT],
        "sent": [Invoice.STATUS_SENT],
        "pending_payment": [Invoice.STATUS_SENT],
        "viewed": [Invoice.STATUS_VIEWED],
        "paid": [Invoice.STATUS_PAID],
        "refunded": [Invoice.STATUS_REFUNDED],
        "overdue": [Invoice.STATUS_OVERDUE],
        "outstanding": [
            Invoice.STATUS_DRAFT,
            Invoice.STATUS_SENT,
            Invoice.STATUS_VIEWED,
            Invoice.STATUS_OVERDUE,
        ],
    }
    active_statuses = filter_map.get(selected_filter)
    if active_statuses:
        invoices = invoices.filter(status__in=active_statuses)

    if issue_date_from_filter:
        invoices = invoices.filter(issue_date__gte=issue_date_from_filter)
    if issue_date_to_filter:
        invoices = invoices.filter(issue_date__lte=issue_date_to_filter)

    if search_query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search_query)
            | Q(customer__name__icontains=search_query)
            | Q(customer__email__icontains=search_query)
        )

    status_summary = {
        "total": Invoice.objects.count(),
        "draft": Invoice.objects.filter(status=Invoice.STATUS_DRAFT).count(),
        "sent": Invoice.objects.filter(status=Invoice.STATUS_SENT).count(),
        "viewed": Invoice.objects.filter(status=Invoice.STATUS_VIEWED).count(),
        "paid": Invoice.objects.filter(status=Invoice.STATUS_PAID).count(),
        "refunded": Invoice.objects.filter(status=Invoice.STATUS_REFUNDED).count(),
        "overdue": Invoice.objects.filter(status=Invoice.STATUS_OVERDUE).count(),
    }

    filter_label_map = {
        "draft": "Draft invoices",
        "unsent": "Draft invoices",
        "sent": "Pending payment invoices",
        "pending_payment": "Pending payment invoices",
        "viewed": "Viewed invoices",
        "paid": "Paid invoices",
        "refunded": "Refunded invoices",
        "overdue": "Overdue invoices",
        "outstanding": "Outstanding invoices",
    }
    active_filter_label = filter_label_map.get(selected_filter, "All invoices")

    invoice_rows = list(invoices)
    for invoice in invoice_rows:
        invoice.batch_email_block_reason = _get_batch_invoice_email_block_reason(invoice)
        invoice.can_batch_send_email = not invoice.batch_email_block_reason

    return render(
        request,
        "invoicing/invoice_list.html",
        {
            "invoices": invoice_rows,
            "selected_filter": selected_filter,
            "search_query": search_query,
            "active_filter_label": active_filter_label,
            "status_summary": status_summary,
            "result_count": len(invoice_rows),
            "issue_date_from": issue_date_from_raw,
            "issue_date_to": issue_date_to_raw,
            "issue_date_error": issue_date_error,
            "batch_email_next_url": request.get_full_path(),
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_dashboard(request):
    refresh_overdue_invoices()
    requested_category = request.GET.get("category", "").strip().lower()
    allowed_category_keys = {
        "bank_transfer_to_verify",
        "outstanding",
        "overdue",
        "requires_follow_up",
    }
    selected_invoice_category_key = (
        requested_category
        if requested_category in allowed_category_keys
        else INVOICE_DASHBOARD_DEFAULT_CATEGORY
    )
    today = timezone.localdate()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    report_generated_at = timezone.now()
    reporting_period_label = month_start.strftime("%B %Y")
    open_statuses = [
        Invoice.STATUS_DRAFT,
        Invoice.STATUS_SENT,
        Invoice.STATUS_VIEWED,
        Invoice.STATUS_OVERDUE,
    ]

    status_counts = Invoice.objects.values("status").annotate(total_count=Count("id"))
    counts_by_status = {
        row["status"]: row["total_count"] for row in status_counts if row["status"] is not None
    }

    total_invoices = sum(counts_by_status.values())
    draft_count = counts_by_status.get(Invoice.STATUS_DRAFT, 0)
    sent_count = counts_by_status.get(Invoice.STATUS_SENT, 0)
    pending_payment_count = sent_count
    viewed_count = counts_by_status.get(Invoice.STATUS_VIEWED, 0)
    paid_count = counts_by_status.get(Invoice.STATUS_PAID, 0)
    refunded_count = counts_by_status.get(Invoice.STATUS_REFUNDED, 0)
    overdue_count = counts_by_status.get(Invoice.STATUS_OVERDUE, 0)
    invoices_requiring_follow_up_count = draft_count + pending_payment_count + viewed_count + overdue_count

    outstanding_amount = (
        Invoice.objects.filter(
            status__in=OUTSTANDING_INVOICE_STATUSES
        ).aggregate(total=Sum("total_amount"))["total"]
        or 0
    )
    overdue_amount = (
        Invoice.objects.filter(status=Invoice.STATUS_OVERDUE).aggregate(total=Sum("total_amount"))["total"]
        or 0
    )

    successful_payments = successful_payments_queryset()
    collected_month = (
        successful_payments.filter(
            paid_at__date__gte=month_start,
            paid_at__date__lte=today,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    collected_year = (
        successful_payments.filter(
            paid_at__date__gte=year_start,
            paid_at__date__lte=today,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )

    month_to_date_invoice_count = Invoice.objects.filter(
        issue_date__gte=month_start,
        issue_date__lte=today,
    ).count()
    year_to_date_invoice_count = Invoice.objects.filter(
        issue_date__gte=year_start,
        issue_date__lte=today,
    ).count()

    month_starts = _recent_month_starts(today, total_months=6)
    collection_trend_labels = [month.strftime("%b %Y") for month in month_starts]
    month_keys = [month.strftime("%Y-%m") for month in month_starts]
    collection_rows = list(
        successful_payments.filter(
            paid_at__date__gte=month_starts[0],
            paid_at__date__lte=today,
        )
        .annotate(month=TruncMonth("paid_at"))
        .values("month")
        .annotate(total_amount=Sum("amount"))
        .order_by("month")
    )
    collection_map = {}
    for row in collection_rows:
        month_value = row.get("month")
        if not month_value:
            continue
        collection_map[month_value.strftime("%Y-%m")] = float(row.get("total_amount") or 0)
    monthly_collection_values = [collection_map.get(month_key, 0.0) for month_key in month_keys]
    collection_chart_summary = _build_chart_summary(collection_trend_labels, monthly_collection_values)

    invoice_status_labels = [
        "Draft",
        "Pending Payment",
        "Viewed",
        "Paid",
        "Overdue",
        "Refunded",
    ]
    invoice_status_values = [
        draft_count,
        pending_payment_count,
        viewed_count,
        paid_count,
        overdue_count,
        refunded_count,
    ]
    status_chart_summary = [
        {"label": "Draft", "value": str(draft_count)},
        {"label": "Pending Payment", "value": str(pending_payment_count)},
        {"label": "Viewed", "value": str(viewed_count)},
        {"label": "Paid", "value": str(paid_count)},
        {"label": "Overdue", "value": str(overdue_count)},
        {"label": "Refunded", "value": str(refunded_count)},
    ]

    failed_invoice_email_ids = {
        int(log["related_object_id"])
        for log in EmailDeliveryLog.objects.filter(
            related_object_type="invoice",
            template_key="invoice_email_v1",
            status=EmailDeliveryLog.STATUS_FAILED,
        ).values("related_object_id")
        if str(log["related_object_id"]).isdigit()
    }
    actionable_failed_invoice_email_ids = set(
        Invoice.objects.filter(id__in=failed_invoice_email_ids, status__in=open_statuses).values_list("id", flat=True)
    )
    failed_invoice_email_count = len(actionable_failed_invoice_email_ids)

    latest_import_job_with_issues = (
        ImportJob.objects.filter(
            module=ImportJob.MODULE_INVOICING,
            invalid_rows__gt=0,
        )
        .order_by("-created_at")
        .first()
    )
    import_validation_issue_count = latest_import_job_with_issues.invalid_rows if latest_import_job_with_issues else 0
    submitted_bank_transfer_count = _invoice_dashboard_category_queryset(
        "bank_transfer_to_verify"
    ).count()

    attention_items = []
    if submitted_bank_transfer_count > 0:
        attention_items.append(
            {
                "priority_label": "Verify",
                "priority_class": "status-warning",
                "issue": "Bank transfers awaiting verification",
                "count": submitted_bank_transfer_count,
                "scope": "Customer-submitted transfer notices",
                "detail": "Customers reported bank transfers that Finance needs to match against the bank account.",
                "action_label": "Review transfers",
                "action_url": reverse("payment-stripe-report"),
            }
        )
    if overdue_count > 0:
        attention_items.append(
            {
                "priority_label": "High",
                "priority_class": "status-danger",
                "issue": "Overdue invoices",
                "count": overdue_count,
                "scope": reporting_period_label,
                "detail": f"S${overdue_amount:,.2f} is already overdue and needs collection follow-up.",
                "action_label": "View invoices",
                "action_url": f"{reverse('invoice-list')}?status=overdue",
            }
        )
    if draft_count > 0:
        attention_items.append(
            {
                "priority_label": "Review",
                "priority_class": "status-warning",
                "issue": "Draft invoices not sent",
                "count": draft_count,
                "scope": "Current invoice pipeline",
                "detail": "Draft invoices still need review and sending before collection can begin.",
                "action_label": "View drafts",
                "action_url": f"{reverse('invoice-list')}?status=draft",
            }
        )
    if failed_invoice_email_count > 0:
        attention_items.append(
            {
                "priority_label": "Review",
                "priority_class": "status-warning",
                "issue": "Failed invoice email deliveries",
                "count": failed_invoice_email_count,
                "scope": "Open invoices",
                "detail": "Invoice email delivery failures were logged for invoices still awaiting payment.",
                "action_label": "View invoices",
                "action_url": f"{reverse('invoice-list')}?status=outstanding",
            }
        )
    if import_validation_issue_count > 0:
        attention_items.append(
            {
                "priority_label": "Review",
                "priority_class": "status-neutral",
                "issue": "Import validation issues",
                "count": import_validation_issue_count,
                "scope": latest_import_job_with_issues.source_file_name,
                "detail": "The latest invoice upload preview stored invalid rows that still need correction.",
                "action_label": "Upload invoices",
                "action_url": reverse("invoice-csv-upload"),
            }
        )

    secondary_summary_items = [
        {
            "label": "Draft Invoices",
            "value": str(draft_count),
            "note": "Invoices still being prepared before they can be sent to customers.",
        },
        {
            "label": "Pending Payment",
            "value": str(pending_payment_count),
            "note": "Invoices sent and still waiting for payment.",
        },
        {
            "label": "Viewed Invoices",
            "value": str(viewed_count),
            "note": "Customers opened these invoices, but payment is still outstanding.",
        },
        {
            "label": "Total Invoices",
            "value": str(total_invoices),
            "note": "All invoice records currently tracked across every status.",
        },
    ]

    invoice_dashboard_categories = _build_invoice_dashboard_categories(selected_invoice_category_key)
    selected_invoice_category = next(
        category for category in invoice_dashboard_categories if category["is_selected"]
    )
    category_invoices = _order_invoice_dashboard_category_queryset(
        _invoice_dashboard_category_queryset(selected_invoice_category_key)
    )
    invoice_support_ticket_summary = _invoice_support_ticket_action_summary()

    return render(
        request,
        "invoicing/invoice_dashboard.html",
        {
            "report_generated_at": report_generated_at,
            "reporting_period_label": reporting_period_label,
            "total_invoices": total_invoices,
            "month_to_date_invoice_count": month_to_date_invoice_count,
            "year_to_date_invoice_count": year_to_date_invoice_count,
            "invoices_requiring_follow_up_count": invoices_requiring_follow_up_count,
            "draft_count": draft_count,
            "sent_count": sent_count,
            "pending_payment_count": pending_payment_count,
            "viewed_count": viewed_count,
            "paid_count": paid_count,
            "refunded_count": refunded_count,
            "overdue_count": overdue_count,
            "submitted_bank_transfer_count": submitted_bank_transfer_count,
            "outstanding_amount": outstanding_amount,
            "overdue_amount": overdue_amount,
            "collected_month": collected_month,
            "collected_year": collected_year,
            "collection_trend_labels": collection_trend_labels,
            "monthly_collection_values": monthly_collection_values,
            "collection_chart_summary": collection_chart_summary,
            "invoice_status_labels": invoice_status_labels,
            "invoice_status_values": invoice_status_values,
            "status_chart_summary": status_chart_summary,
            "secondary_summary_items": secondary_summary_items,
            "attention_items": attention_items,
            "failed_invoice_email_count": failed_invoice_email_count,
            "import_validation_issue_count": import_validation_issue_count,
            "invoice_dashboard_categories": invoice_dashboard_categories,
            "selected_invoice_category_key": selected_invoice_category_key,
            "selected_invoice_category": selected_invoice_category,
            "category_invoices": category_invoices,
            "invoice_support_ticket_summary": invoice_support_ticket_summary,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_template_settings(request):
    template_settings = InvoiceTemplateSettings.load()
    if request.method != "POST":
        return render(
            request,
            "invoicing/invoice_template_settings.html",
            {
                "form": InvoiceTemplateSettingsForm(instance=template_settings),
                "template_settings": template_settings,
            },
        )

    form = InvoiceTemplateSettingsForm(request.POST, request.FILES, instance=template_settings)
    if form.is_valid():
        changed_fields = list(form.changed_data)
        if getattr(form, "stale_logo_missing", False) and "logo" not in changed_fields:
            changed_fields.append("logo")
        if changed_fields:
            settings_obj = form.save(commit=False)
            settings_obj.updated_by = request.user
            settings_obj.save()
            log_event(
                action="invoice.template_settings.updated",
                user=request.user,
                target_type="invoice_template_settings",
                target_id=str(settings_obj.id),
                metadata={"changed_fields": changed_fields},
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Invoice template settings updated.")
        else:
            messages.info(request, "No invoice template setting changes to save.")
        return redirect("invoice-template-settings")

    return render(
        request,
        "invoicing/invoice_template_settings.html",
        {
            "form": form,
            "template_settings": template_settings,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_create(request):
    if request.method == "POST":
        form = InvoiceForm(request.POST)
        formset = InvoiceItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                invoice = form.save(commit=False)
                invoice.invoice_number = generate_invoice_number()
                invoice.status = Invoice.STATUS_DRAFT
                invoice.created_by = request.user
                invoice.save()

                formset.instance = invoice
                items = formset.save(commit=False)
                for item in items:
                    item.invoice = invoice
                    item.save()
                for deleted in formset.deleted_objects:
                    deleted.delete()
                recalculate_invoice_totals(invoice)

            log_event(
                action="invoice.created",
                user=request.user,
                target_type="invoice",
                target_id=str(invoice.id),
                metadata={"invoice_number": invoice.invoice_number},
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Invoice created as Draft.")
            return redirect("invoice-detail", pk=invoice.pk)
    else:
        initial = {}
        selected_customer_id = request.GET.get("customer")
        if selected_customer_id and InvoiceCustomer.objects.filter(pk=selected_customer_id).exists():
            initial["customer"] = selected_customer_id
        form = InvoiceForm(initial=initial)
        formset = InvoiceItemFormSet(prefix="items")

    return render(
        request,
        "invoicing/invoice_form.html",
        {
            "form": form,
            "formset": formset,
            "is_edit": False,
            "invoice": None,
            "customer_create_next": request.get_full_path(),
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_customer_create(request):
    fallback_next = reverse("invoice-create")
    if request.method == "POST":
        next_url = _resolve_next_url(request, request.POST.get("next", ""), fallback_next)
        form = CustomerCreateForm(request.POST)
        if form.is_valid():
            customer = form.save(commit=False)
            customer.created_by = request.user
            customer.save()
            log_event(
                action="invoice.customer.created",
                user=request.user,
                target_type="customer",
                target_id=str(customer.id),
                metadata={
                    "name": customer.name,
                    "email": customer.email,
                },
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Customer created. You can now create the invoice.")
            return redirect(_append_customer_query(next_url, customer.id))
    else:
        next_url = _resolve_next_url(request, request.GET.get("next", ""), fallback_next)
        form = CustomerCreateForm()

    return render(
        request,
        "invoicing/customer_form.html",
        {
            "form": form,
            "next_url": next_url,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_edit(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status != Invoice.STATUS_DRAFT:
        messages.error(request, "Only Draft invoices can be edited.")
        return redirect("invoice-detail", pk=invoice.pk)

    if request.method == "POST":
        form = InvoiceForm(request.POST, instance=invoice)
        formset = InvoiceItemFormSet(request.POST, instance=invoice, prefix="items")
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                form.save()
                formset.save()
                recalculate_invoice_totals(invoice)

            log_event(
                action="invoice.edited",
                user=request.user,
                target_type="invoice",
                target_id=str(invoice.id),
                metadata={"invoice_number": invoice.invoice_number},
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Invoice updated.")
            return redirect("invoice-detail", pk=invoice.pk)
    else:
        form = InvoiceForm(instance=invoice)
        formset = InvoiceItemFormSet(instance=invoice, prefix="items")

    return render(
        request,
        "invoicing/invoice_form.html",
        {
            "form": form,
            "formset": formset,
            "is_edit": True,
            "invoice": invoice,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_delete_draft(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    if invoice.status != Invoice.STATUS_DRAFT:
        messages.error(request, "Only Draft invoices can be deleted.")
        return redirect("invoice-detail", pk=invoice.pk)

    next_url = _resolve_next_url(request, request.GET.get("next", "") or request.POST.get("next", ""), "")
    cancel_url = next_url or reverse("invoice-detail", args=[invoice.pk])

    if request.method == "POST":
        invoice_id = invoice.id
        invoice_number = invoice.invoice_number
        customer_id = invoice.customer_id
        customer_name = invoice.customer.name
        invoice.delete()
        log_event(
            action="invoice.deleted",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice_id),
            metadata={
                "invoice_number": invoice_number,
                "customer_id": customer_id,
                "customer_name": customer_name,
            },
            ip_address=get_client_ip(request),
        )
        messages.success(request, f"Draft invoice {invoice_number} deleted.")
        return redirect(next_url or reverse("invoice-list"))

    return render(
        request,
        "invoicing/invoice_confirm_delete.html",
        {
            "invoice": invoice,
            "cancel_url": cancel_url,
            "next_url": next_url,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    apply_overdue_status(invoice)
    invoice.refresh_from_db()
    reminder_context = _invoice_reminder_context(invoice)
    last_invoice_email_log = (
        EmailDeliveryLog.objects.filter(
            related_object_type="invoice",
            related_object_id=str(invoice.id),
            template_key="invoice_email_v1",
            status=EmailDeliveryLog.STATUS_SENT,
        )
        .order_by("-attempted_at")
        .first()
    )
    return render(
        request,
        "invoicing/invoice_detail.html",
        {
            "invoice": invoice,
            "items": invoice.items.all(),
            "status_choices": Invoice.STATUS_CHOICES,
            **_bank_transfer_context(invoice, initiated_by=request.user),
            "last_invoice_email_sent_at": (
                (last_invoice_email_log.sent_at or last_invoice_email_log.attempted_at)
                if last_invoice_email_log
                else None
            ),
            **reminder_context,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_status_update(request, pk):
    if request.method != "POST":
        raise Http404()

    invoice = get_object_or_404(Invoice, pk=pk)
    new_status = request.POST.get("status", "").strip()
    success, message = transition_invoice_status(invoice, new_status)
    if success:
        log_event(
            action="invoice.status.changed",
            user=request.user,
            target_type="invoice",
            target_id=str(invoice.id),
            metadata={"invoice_number": invoice.invoice_number, "new_status": new_status},
            ip_address=get_client_ip(request),
        )
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect("invoice-detail", pk=invoice.pk)


def invoice_public_view(request, token):
    invoice = (
        Invoice.objects.select_related("customer")
        .filter(public_view_token=token)
        .order_by("id")
        .first()
    )
    if invoice is None:
        raise Http404()
    previous_status = invoice.status
    mark_invoice_viewed(invoice)
    invoice.refresh_from_db()
    log_event(
        action="invoice.public_viewed",
        user=None,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={
            "invoice_number": invoice.invoice_number,
            "status_before": previous_status,
            "status_after": invoice.status,
        },
        ip_address=get_client_ip(request),
    )
    return render(
        request,
        "invoicing/invoice_public_view.html",
        {
            "invoice": invoice,
            "items": invoice.items.all(),
            **_bank_transfer_context(invoice),
            "bank_transfer_notice_action": reverse(
                "payment-bank-transfer-notice-public",
                args=[invoice.public_view_token],
            ),
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_download_pdf(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    pdf_bytes = generate_invoice_pdf(invoice)
    log_event(
        action="invoice.pdf.downloaded",
        user=request.user,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={"invoice_number": invoice.invoice_number},
        ip_address=get_client_ip(request),
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.pdf"'
    return response


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_download_excel(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    excel_bytes = generate_invoice_excel(invoice)
    log_event(
        action="invoice.excel.downloaded",
        user=request.user,
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={"invoice_number": invoice.invoice_number},
        ip_address=get_client_ip(request),
    )
    response = HttpResponse(
        excel_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.xlsx"'
    return response


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_send_email(request, pk):
    if request.method != "POST":
        raise Http404()

    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    success, delivery_log = _send_invoice_email_with_audit(request, invoice)

    if success:
        messages.success(request, f"Invoice emailed to {invoice.customer.email}.")
    else:
        messages.error(
            request,
            "Failed to send invoice email. Invoice status remains unchanged.",
        )
    return redirect("invoice-detail", pk=invoice.pk)


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_send_email_batch(request):
    if request.method != "POST":
        raise Http404()

    next_url = _resolve_next_url(request, request.POST.get("next", ""), reverse("invoice-list"))
    selected_invoice_ids = [value for value in request.POST.getlist("selected_invoice_ids") if value.isdigit()]
    if not selected_invoice_ids:
        messages.warning(request, "Select at least one invoice to send.")
        return redirect(next_url)

    selected_ids = [int(value) for value in selected_invoice_ids]
    invoices = {
        invoice.id: invoice
        for invoice in Invoice.objects.select_related("customer").filter(id__in=selected_ids)
    }

    sent_count = 0
    failed_count = 0
    skipped_reasons = []
    failed_reasons = []

    for invoice_id in selected_ids:
        invoice = invoices.get(invoice_id)
        if invoice is None:
            skipped_reasons.append(f"Invoice #{invoice_id} (record not found)")
            continue

        block_reason = _get_batch_invoice_email_block_reason(invoice)
        if block_reason:
            skipped_reasons.append(f"{invoice.invoice_number} ({block_reason})")
            continue

        success, delivery_log = _send_invoice_email_with_audit(request, invoice)
        if success:
            sent_count += 1
        else:
            failed_count += 1
            failure_reason = delivery_log.error_message or "Email delivery failed."
            failed_reasons.append(f"{invoice.invoice_number} ({failure_reason})")

    if sent_count:
        messages.success(request, f"Invoice emails sent: {sent_count}.")
    if skipped_reasons:
        messages.warning(
            request,
            f"Skipped {len(skipped_reasons)} invoice(s): {_format_invoice_batch_feedback(skipped_reasons)}.",
        )
    if failed_reasons:
        messages.error(
            request,
            f"Failed to send {failed_count} invoice email(s): {_format_invoice_batch_feedback(failed_reasons)}.",
        )
    if not sent_count and not skipped_reasons and not failed_reasons:
        messages.warning(request, "No invoice emails were sent.")

    return redirect(next_url)
