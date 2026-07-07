from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum
from django.db.models import Q
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

from .exports import generate_invoice_excel, generate_invoice_pdf
from .forms import CustomerCreateForm, InvoiceCsvUploadForm, InvoiceForm, InvoiceItemFormSet
from .models import Customer as InvoiceCustomer
from .models import Invoice
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
    return {
        "bank_transfer_details": bank_transfer_details,
        "bank_transfer_payment": get_or_create_bank_transfer_payment(
            invoice=invoice,
            initiated_by=initiated_by,
        ),
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
    updated_count = refresh_overdue_invoices()
    today = timezone.localdate()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

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
    overdue_count = counts_by_status.get(Invoice.STATUS_OVERDUE, 0)
    unsent_count = draft_count

    outstanding_amount = (
        Invoice.objects.filter(
            status__in=[
                Invoice.STATUS_DRAFT,
                Invoice.STATUS_SENT,
                Invoice.STATUS_VIEWED,
                Invoice.STATUS_OVERDUE,
            ]
        ).aggregate(total=Sum("total_amount"))["total"]
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

    recent_action_invoices = (
        Invoice.objects.select_related("customer")
        .filter(
            status__in=[
                Invoice.STATUS_DRAFT,
                Invoice.STATUS_SENT,
                Invoice.STATUS_VIEWED,
                Invoice.STATUS_OVERDUE,
            ]
        )
        .order_by("due_date", "-issue_date", "-created_at")[:10]
    )

    return render(
        request,
        "invoicing/invoice_dashboard.html",
        {
            "total_invoices": total_invoices,
            "month_to_date_invoice_count": month_to_date_invoice_count,
            "year_to_date_invoice_count": year_to_date_invoice_count,
            "draft_count": draft_count,
            "sent_count": sent_count,
            "pending_payment_count": pending_payment_count,
            "viewed_count": viewed_count,
            "paid_count": paid_count,
            "overdue_count": overdue_count,
            "unsent_count": unsent_count,
            "outstanding_amount": outstanding_amount,
            "collected_month": collected_month,
            "collected_year": collected_year,
            "recent_action_invoices": recent_action_invoices,
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
