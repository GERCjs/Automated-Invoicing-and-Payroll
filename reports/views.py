from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.db.models.functions import TruncDate, TruncMonth
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailVerificationToken
from accounts.permissions import role_required
from accounts.roles import ADMIN, FINANCE, HR, ROLE_CHOICES, STAFF, SUPERADMIN
from core.models import AuditLog
from invoicing.models import Invoice
from invoicing.services import refresh_overdue_invoices
from notifications.models import EmailDeliveryLog
from notifications.models import PaymentReminderSettings
from payments.models import PaymentRecord
from payments.services import _import_stripe, _require_stripe_secret_key, successful_payments_queryset
from payroll.models import Employee, PayrollRecord
from payroll.services import cpf_for_2026


def _safe_sum(queryset, field_name):
    return queryset.aggregate(total=Sum(field_name))["total"] or 0


def _to_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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


def _next_month_start(month_start):
    if month_start.month == 12:
        return month_start.replace(year=month_start.year + 1, month=1, day=1)
    return month_start.replace(month=month_start.month + 1, day=1)


def _parse_month_filter(raw_value: str):
    value = (raw_value or "").strip()
    if not value:
        return "", None, None
    try:
        month_start = timezone.datetime.strptime(f"{value}-01", "%Y-%m-%d").date()
    except ValueError:
        return "", None, None
    month_end = _next_month_start(month_start) - timezone.timedelta(days=1)
    return value, month_start, month_end


def _parse_iso_date(raw_value: str):
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        return timezone.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_date_range(raw_date_from: str, raw_date_to: str):
    date_filter_error = ""
    date_from = None
    date_to = None

    if raw_date_from:
        date_from = _parse_iso_date(raw_date_from)
        if date_from is None:
            date_filter_error = "From date is invalid. Use YYYY-MM-DD."

    if raw_date_to and not date_filter_error:
        date_to = _parse_iso_date(raw_date_to)
        if date_to is None:
            date_filter_error = "To date is invalid. Use YYYY-MM-DD."

    if date_from and date_to and date_from > date_to:
        date_filter_error = "From date cannot be later than To date."

    filter_date_from = None if date_filter_error else date_from
    filter_date_to = None if date_filter_error else date_to
    return date_from, date_to, filter_date_from, filter_date_to, date_filter_error


def _resolve_quick_date_range(selected_quick_range: str, today):
    current_month_start = today.replace(day=1)
    if selected_quick_range == "today":
        return today, today
    if selected_quick_range == "last_7_days":
        return today - timezone.timedelta(days=6), today
    if selected_quick_range == "last_30_days":
        return today - timezone.timedelta(days=29), today
    if selected_quick_range == "this_month":
        return current_month_start, today
    if selected_quick_range == "previous_month":
        previous_month_end = current_month_start - timezone.timedelta(days=1)
        return previous_month_end.replace(day=1), previous_month_end
    return None, None


def _apply_date_bounds(queryset, field_name: str, date_from, date_to):
    if date_from:
        queryset = queryset.filter(**{f"{field_name}__gte": date_from})
    if date_to:
        queryset = queryset.filter(**{f"{field_name}__lte": date_to})
    return queryset


def _month_bounds(selected_month: str, today):
    if selected_month:
        try:
            month_start = timezone.datetime.strptime(f"{selected_month}-01", "%Y-%m-%d").date()
        except ValueError:
            month_start = today.replace(day=1)
            selected_month = month_start.strftime("%Y-%m")
    else:
        month_start = today.replace(day=1)
        selected_month = month_start.strftime("%Y-%m")

    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1, day=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1, day=1)
    month_end = next_month - timezone.timedelta(days=1)
    return selected_month, month_start, month_end


def _query_string(params: dict) -> str:
    cleaned = {}
    for key, value in params.items():
        if value in {"", None}:
            continue
        cleaned[key] = value
    if not cleaned:
        return ""
    return f"?{urlencode(cleaned)}"


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_customer_report(request):
    refresh_overdue_invoices()
    today = timezone.localdate()
    current_month_start = today.replace(day=1)
    current_year_start = today.replace(month=1, day=1)
    search_query = (request.GET.get("q") or "").strip()
    status_filter_options = [{"value": "", "label": "All"}] + [
        {"value": value, "label": label} for value, label in Invoice.STATUS_CHOICES
    ]
    valid_status_values = {option["value"] for option in status_filter_options if option["value"]}
    selected_status = (request.GET.get("status") or "").strip().lower()
    if selected_status not in valid_status_values:
        selected_status = ""

    all_invoices = Invoice.objects.select_related("customer")
    customer_filter_options = list(
        all_invoices.values("customer_id", "customer__name", "customer__email")
        .annotate(invoice_count=Count("id"))
        .order_by("customer__name", "customer__email")
    )
    selected_customer_raw = (request.GET.get("customer") or "").strip()
    selected_customer_id = ""
    selected_customer_option = None
    if selected_customer_raw.isdigit():
        for option in customer_filter_options:
            if option["customer_id"] == int(selected_customer_raw):
                selected_customer_id = selected_customer_raw
                selected_customer_option = option
                break

    selected_month, filter_month_start, filter_month_end = _parse_month_filter(request.GET.get("month", ""))
    date_type_options = [
        {"value": "", "label": "Select date type"},
        {"value": "issue_date", "label": "Issue Date"},
        {"value": "due_date", "label": "Due Date"},
        {"value": "payment_date", "label": "Payment Date"},
    ]
    quick_range_options = [
        {"value": "", "label": "Custom Range"},
        {"value": "today", "label": "Today"},
        {"value": "last_7_days", "label": "Last 7 Days"},
        {"value": "last_30_days", "label": "Last 30 Days"},
        {"value": "this_month", "label": "This Month"},
        {"value": "previous_month", "label": "Previous Month"},
    ]
    ageing_filter_options = [
        {"value": "", "label": "All invoices"},
        {"value": "all_overdue", "label": "All overdue"},
        {"value": "days_1_7", "label": "1-7 days overdue"},
        {"value": "days_8_30", "label": "8-30 days overdue"},
        {"value": "days_31_60", "label": "31-60 days overdue"},
        {"value": "days_over_60", "label": "More than 60 days overdue"},
    ]
    valid_date_type_values = {option["value"] for option in date_type_options if option["value"]}
    selected_date_type = (request.GET.get("date_type") or "").strip().lower()
    if selected_date_type not in valid_date_type_values:
        selected_date_type = ""
    valid_quick_range_values = {option["value"] for option in quick_range_options if option["value"]}
    selected_quick_range = (request.GET.get("quick_range") or "").strip().lower()
    if selected_quick_range not in valid_quick_range_values:
        selected_quick_range = ""
    valid_ageing_values = {option["value"] for option in ageing_filter_options if option["value"]}
    selected_ageing = (request.GET.get("ageing") or "").strip().lower()
    if selected_ageing not in valid_ageing_values:
        selected_ageing = ""

    raw_date_from = (request.GET.get("date_from") or "").strip()
    raw_date_to = (request.GET.get("date_to") or "").strip()
    if selected_quick_range:
        date_from, date_to = _resolve_quick_date_range(selected_quick_range, today)
        filter_date_from = date_from
        filter_date_to = date_to
        date_filter_error = ""
    else:
        date_from, date_to, filter_date_from, filter_date_to, date_filter_error = _parse_date_range(
            raw_date_from,
            raw_date_to,
        )
    if (date_from or date_to or selected_quick_range) and not selected_date_type and not date_filter_error:
        date_filter_error = "Select a Date Type before using the date filters."
        filter_date_from = None
        filter_date_to = None

    month_label_map = {}
    invoice_month_rows = list(
        all_invoices.annotate(month=TruncMonth("issue_date"))
        .values_list("month", flat=True)
        .distinct()
    )
    payment_month_rows = list(
        PaymentRecord.objects.filter(invoice__isnull=False, paid_at__isnull=False)
        .annotate(month=TruncMonth("paid_at"))
        .values_list("month", flat=True)
        .distinct()
    )
    for month_value in invoice_month_rows + payment_month_rows:
        if not month_value:
            continue
        month_key = month_value.strftime("%Y-%m")
        month_label_map[month_key] = month_value.strftime("%b %Y")
    if selected_month and selected_month not in month_label_map and filter_month_start:
        month_label_map[selected_month] = filter_month_start.strftime("%b %Y")
    month_filter_options = [
        {"value": month_key, "label": month_label_map[month_key]}
        for month_key in sorted(month_label_map.keys(), reverse=True)
    ]

    invoice_queryset = all_invoices
    if selected_status:
        invoice_queryset = invoice_queryset.filter(status=selected_status)
    if selected_customer_id:
        invoice_queryset = invoice_queryset.filter(customer_id=int(selected_customer_id))
    if search_query:
        invoice_queryset = invoice_queryset.filter(
            Q(invoice_number__icontains=search_query)
            | Q(customer__name__icontains=search_query)
            | Q(customer__email__icontains=search_query)
        )

    use_legacy_month_filter = bool(
        selected_month
        and not selected_date_type
        and not filter_date_from
        and not filter_date_to
        and not selected_quick_range
    )
    if use_legacy_month_filter and filter_month_start and filter_month_end:
        invoice_queryset = invoice_queryset.filter(
            issue_date__gte=filter_month_start,
            issue_date__lte=filter_month_end,
        )

    collection_payments = successful_payments_queryset().filter(invoice__isnull=False)
    if selected_status:
        collection_payments = collection_payments.filter(invoice__status=selected_status)
    if selected_customer_id:
        collection_payments = collection_payments.filter(invoice__customer_id=int(selected_customer_id))
    if search_query:
        collection_payments = collection_payments.filter(
            Q(invoice__invoice_number__icontains=search_query)
            | Q(invoice__customer__name__icontains=search_query)
            | Q(invoice__customer__email__icontains=search_query)
        )

    if not date_filter_error:
        if selected_date_type == "issue_date":
            invoice_queryset = _apply_date_bounds(invoice_queryset, "issue_date", filter_date_from, filter_date_to)
            collection_payments = _apply_date_bounds(
                collection_payments,
                "invoice__issue_date",
                filter_date_from,
                filter_date_to,
            )
        elif selected_date_type == "due_date":
            invoice_queryset = _apply_date_bounds(invoice_queryset, "due_date", filter_date_from, filter_date_to)
            collection_payments = _apply_date_bounds(
                collection_payments,
                "invoice__due_date",
                filter_date_from,
                filter_date_to,
            )
        elif selected_date_type == "payment_date":
            collection_payments = _apply_date_bounds(
                collection_payments,
                "paid_at__date",
                filter_date_from,
                filter_date_to,
            )
            if filter_date_from or filter_date_to:
                invoice_queryset = invoice_queryset.filter(
                    id__in=collection_payments.values_list("invoice_id", flat=True).distinct()
                )

    if selected_ageing:
        overdue_queryset = invoice_queryset.filter(
            status=Invoice.STATUS_OVERDUE,
            due_date__lt=today,
        )
        if selected_ageing == "days_1_7":
            overdue_queryset = overdue_queryset.filter(
                due_date__gte=today - timezone.timedelta(days=7),
                due_date__lte=today - timezone.timedelta(days=1),
            )
        elif selected_ageing == "days_8_30":
            overdue_queryset = overdue_queryset.filter(
                due_date__gte=today - timezone.timedelta(days=30),
                due_date__lte=today - timezone.timedelta(days=8),
            )
        elif selected_ageing == "days_31_60":
            overdue_queryset = overdue_queryset.filter(
                due_date__gte=today - timezone.timedelta(days=60),
                due_date__lte=today - timezone.timedelta(days=31),
            )
        elif selected_ageing == "days_over_60":
            overdue_queryset = overdue_queryset.filter(
                due_date__lt=today - timezone.timedelta(days=60),
            )
        invoice_queryset = overdue_queryset
        collection_payments = collection_payments.filter(
            invoice_id__in=invoice_queryset.values_list("id", flat=True)
        )

    outstanding_invoices = invoice_queryset.filter(
        status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_SENT, Invoice.STATUS_VIEWED, Invoice.STATUS_OVERDUE]
    )
    filtered_invoice_ids = list(invoice_queryset.values_list("id", flat=True))

    collection_month_start = filter_date_from or filter_month_start or current_month_start
    collection_month_end = filter_date_to or filter_month_end or today
    collection_year_start = (
        collection_month_start.replace(month=1, day=1)
        if (filter_date_from or filter_month_start)
        else current_year_start
    )
    collection_year_end = collection_month_end

    total_amount_collected_month = _safe_sum(
        collection_payments.filter(
            paid_at__date__gte=collection_month_start,
            paid_at__date__lte=collection_month_end,
        ),
        "amount",
    )
    total_amount_collected_year = _safe_sum(
        collection_payments.filter(
            paid_at__date__gte=collection_year_start,
            paid_at__date__lte=collection_year_end,
        ),
        "amount",
    )
    outstanding_amount = _safe_sum(outstanding_invoices, "total_amount")

    draft_count = invoice_queryset.filter(status=Invoice.STATUS_DRAFT).count()
    pending_payment_count = invoice_queryset.filter(status=Invoice.STATUS_SENT).count()
    viewed_count = invoice_queryset.filter(status=Invoice.STATUS_VIEWED).count()
    overdue_count = invoice_queryset.filter(status=Invoice.STATUS_OVERDUE).count()
    paid_count = invoice_queryset.filter(status=Invoice.STATUS_PAID).count()
    refunded_count = invoice_queryset.filter(status=Invoice.STATUS_REFUNDED).count()

    status_summary = [
        {"label": "Draft", "count": draft_count},
        {"label": "Pending Payment", "count": pending_payment_count},
        {"label": "Viewed", "count": viewed_count},
        {"label": "Overdue", "count": overdue_count},
        {"label": "Paid", "count": paid_count},
        {"label": "Refunded", "count": refunded_count},
    ]

    total_customers_with_invoices = (
        invoice_queryset.values("customer_id").distinct().count()
    )
    top_customers_by_total = list(
        invoice_queryset.values("customer_id", "customer__name", "customer__email")
        .annotate(
            invoice_count=Count("id"),
            total_amount=Sum("total_amount"),
        )
        .order_by("-total_amount", "customer__name")[:8]
    )
    customers_with_overdue = list(
        invoice_queryset.filter(status=Invoice.STATUS_OVERDUE)
        .values("customer_id", "customer__name", "customer__email")
        .annotate(
            overdue_invoice_count=Count("id"),
            overdue_amount=Sum("total_amount"),
        )
        .order_by("-overdue_invoice_count", "-overdue_amount", "customer__name")[:8]
    )
    top_customers_by_outstanding = list(
        outstanding_invoices.values("customer_id", "customer__name", "customer__email")
        .annotate(
            outstanding_invoice_count=Count("id"),
            outstanding_amount=Sum("total_amount"),
        )
        .order_by("-outstanding_amount", "customer__name")[:8]
    )

    if filter_month_start:
        chart_month_starts = [filter_month_start]
    else:
        chart_month_starts = _recent_month_starts(today, total_months=6)
    monthly_collection_labels = [month.strftime("%b %Y") for month in chart_month_starts]
    month_keys = [month.strftime("%Y-%m") for month in chart_month_starts]

    if filter_month_start and filter_month_end:
        chart_collection_payments = collection_payments.filter(
            paid_at__date__gte=filter_month_start,
            paid_at__date__lte=filter_month_end,
        )
    else:
        chart_collection_payments = collection_payments
    collection_rows = list(
        chart_collection_payments.annotate(month=TruncMonth("paid_at"))
        .values("month")
        .annotate(total=Sum("amount"))
        .order_by("month")
    )

    monthly_collection_map = {}
    for row in collection_rows:
        month_value = row.get("month")
        if not month_value:
            continue
        monthly_collection_map[month_value.strftime("%Y-%m")] = _to_float(row.get("total"))
    monthly_collection_values = [monthly_collection_map.get(month_key, 0.0) for month_key in month_keys]

    overdue_ageing_labels = ["1-7 days overdue", "8-30 days overdue", "31-60 days overdue", "More than 60 days overdue"]
    overdue_ageing_values = [0.0, 0.0, 0.0, 0.0]
    overdue_rows = invoice_queryset.filter(
        status=Invoice.STATUS_OVERDUE,
        due_date__lt=today,
    ).values("due_date", "total_amount")
    for row in overdue_rows:
        due_date = row.get("due_date")
        if not due_date:
            continue
        days_overdue = (today - due_date).days
        amount = _to_float(row.get("total_amount"))
        if 1 <= days_overdue <= 7:
            overdue_ageing_values[0] += amount
        elif 8 <= days_overdue <= 30:
            overdue_ageing_values[1] += amount
        elif 31 <= days_overdue <= 60:
            overdue_ageing_values[2] += amount
        elif days_overdue > 60:
            overdue_ageing_values[3] += amount

    action_rank = Case(
        When(status=Invoice.STATUS_OVERDUE, then=Value(0)),
        When(status=Invoice.STATUS_VIEWED, then=Value(1)),
        When(status=Invoice.STATUS_SENT, then=Value(2)),
        When(status=Invoice.STATUS_DRAFT, then=Value(3)),
        When(status=Invoice.STATUS_REFUNDED, then=Value(4)),
        default=Value(5),
        output_field=IntegerField(),
    )
    detailed_invoice_queryset = invoice_queryset.annotate(action_rank=action_rank).order_by(
        "action_rank",
        "due_date",
        "-issue_date",
        "-created_at",
    )
    follow_up_invoices = detailed_invoice_queryset.filter(
        status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_SENT, Invoice.STATUS_VIEWED, Invoice.STATUS_OVERDUE]
    )[:10]
    detailed_invoices = detailed_invoice_queryset[:25]
    recent_invoices_created = invoice_queryset.order_by("-created_at")[:10]
    recent_payments_received = collection_payments
    if use_legacy_month_filter and filter_month_start and filter_month_end:
        recent_payments_received = recent_payments_received.filter(
            paid_at__date__gte=filter_month_start,
            paid_at__date__lte=filter_month_end,
        )
    recent_payments_received = recent_payments_received.select_related("invoice", "invoice__customer").order_by(
        "-paid_at",
        "-created_at",
    )[:10]
    recent_invoice_email_logs = EmailDeliveryLog.objects.filter(
        template_key="invoice_email_v1",
        related_object_type="invoice",
        related_object_id__in=[str(invoice_id) for invoice_id in filtered_invoice_ids],
    ).order_by("-attempted_at")[:10]
    recent_failed_invoice_email_logs = EmailDeliveryLog.objects.filter(
        template_key="invoice_email_v1",
        related_object_type="invoice",
        related_object_id__in=[str(invoice_id) for invoice_id in filtered_invoice_ids],
        status=EmailDeliveryLog.STATUS_FAILED,
    ).order_by("-attempted_at")[:10]
    failed_invoice_email_count = EmailDeliveryLog.objects.filter(
        template_key="invoice_email_v1",
        related_object_type="invoice",
        related_object_id__in=[str(invoice_id) for invoice_id in filtered_invoice_ids],
        status=EmailDeliveryLog.STATUS_FAILED,
    ).count()

    selected_status_label = next(
        (option["label"] for option in status_filter_options if option["value"] == selected_status),
        "All",
    )
    selected_customer_label = (
        f'{selected_customer_option["customer__name"]} ({selected_customer_option["customer__email"]})'
        if selected_customer_option
        else "All customers"
    )
    selected_month_label = month_label_map.get(selected_month, "All months") if selected_month else "All months"
    selected_date_type_label = next(
        (option["label"] for option in date_type_options if option["value"] == selected_date_type),
        "",
    )
    selected_quick_range_label = next(
        (option["label"] for option in quick_range_options if option["value"] == selected_quick_range),
        "",
    )
    selected_ageing_label = next(
        (option["label"] for option in ageing_filter_options if option["value"] == selected_ageing),
        "",
    )
    active_filter_badges = []
    if search_query:
        active_filter_badges.append(f"Search: {search_query}")
    if selected_status:
        active_filter_badges.append(f"Status: {selected_status_label}")
    if selected_customer_id:
        active_filter_badges.append(f"Customer: {selected_customer_label}")
    if filter_date_from or filter_date_to:
        date_range_start = filter_date_from.strftime("%d %b %Y") if filter_date_from else "Start"
        date_range_end = filter_date_to.strftime("%d %b %Y") if filter_date_to else "Today"
        if selected_date_type_label:
            active_filter_badges.append(f"{selected_date_type_label}: {date_range_start} to {date_range_end}")
        else:
            active_filter_badges.append(f"{date_range_start} to {date_range_end}")
    if selected_quick_range_label:
        active_filter_badges.append(f"Quick Range: {selected_quick_range_label}")
    if selected_ageing_label:
        active_filter_badges.append(f"Ageing: {selected_ageing_label}")
    if use_legacy_month_filter and selected_month:
        active_filter_badges.append(f"Month: {selected_month_label}")
    has_active_filters = bool(
        search_query
        or selected_status
        or selected_customer_id
        or selected_date_type
        or raw_date_from
        or raw_date_to
        or selected_quick_range
        or selected_ageing
        or selected_month
    )
    filtered_invoice_count = invoice_queryset.count()
    has_report_data = bool(
        filtered_invoice_count
        or total_amount_collected_month
        or total_amount_collected_year
        or outstanding_amount
        or refunded_count
    )
    has_custom_date_range = bool(selected_date_type and (filter_date_from or filter_date_to))
    collection_month_label = "Collected in Filtered Period" if has_custom_date_range else (
        "Collected in Selected Month" if use_legacy_month_filter else "Collected This Month"
    )
    collection_year_label = "Collected in Filtered Year" if has_custom_date_range else (
        "Collected in Selected Year" if use_legacy_month_filter else "Collected This Year"
    )
    drill_down_search = search_query or (
        selected_customer_option["customer__name"] if selected_customer_option else ""
    )
    issue_date_from_value = (
        filter_date_from.isoformat() if selected_date_type == "issue_date" and filter_date_from else
        filter_month_start.isoformat() if use_legacy_month_filter and filter_month_start else ""
    )
    issue_date_to_value = (
        filter_date_to.isoformat() if selected_date_type == "issue_date" and filter_date_to else
        filter_month_end.isoformat() if use_legacy_month_filter and filter_month_end else ""
    )
    overdue_invoice_list_query = _query_string(
        {
            "status": "overdue",
            "q": drill_down_search,
            "issue_date_from": issue_date_from_value,
            "issue_date_to": issue_date_to_value,
        }
    )
    outstanding_invoice_list_query = _query_string(
        {
            "status": "outstanding",
            "q": drill_down_search,
            "issue_date_from": issue_date_from_value,
            "issue_date_to": issue_date_to_value,
        }
    )
    pending_invoice_list_query = _query_string(
        {
            "status": "pending_payment",
            "q": drill_down_search,
            "issue_date_from": issue_date_from_value,
            "issue_date_to": issue_date_to_value,
        }
    )
    draft_invoice_list_query = _query_string(
        {
            "status": "draft",
            "q": drill_down_search,
            "issue_date_from": issue_date_from_value,
            "issue_date_to": issue_date_to_value,
        }
    )
    refunded_invoice_list_query = _query_string(
        {
            "status": "refunded",
            "q": drill_down_search,
            "issue_date_from": issue_date_from_value,
            "issue_date_to": issue_date_to_value,
        }
    )
    customer_history_query = _query_string(
        {
            "q": search_query,
            "customer": selected_customer_id,
            "month": selected_month,
            "date_type": selected_date_type,
            "date_from": filter_date_from.isoformat() if filter_date_from else "",
            "date_to": filter_date_to.isoformat() if filter_date_to else "",
            "quick_range": selected_quick_range,
            "ageing": selected_ageing,
        }
    )
    for row in top_customers_by_total:
        row["report_query"] = _query_string(
            {
                "q": search_query,
                "status": selected_status,
                "customer": row["customer_id"],
                "month": selected_month if use_legacy_month_filter else "",
                "date_type": selected_date_type,
                "date_from": filter_date_from.isoformat() if filter_date_from else "",
                "date_to": filter_date_to.isoformat() if filter_date_to else "",
                "quick_range": selected_quick_range,
                "ageing": selected_ageing,
            }
        )
    for row in customers_with_overdue:
        row["report_query"] = _query_string(
            {
                "q": search_query,
                "status": selected_status,
                "customer": row["customer_id"],
                "month": selected_month if use_legacy_month_filter else "",
                "date_type": selected_date_type,
                "date_from": filter_date_from.isoformat() if filter_date_from else "",
                "date_to": filter_date_to.isoformat() if filter_date_to else "",
                "quick_range": selected_quick_range,
                "ageing": selected_ageing,
            }
        )
    for row in top_customers_by_outstanding:
        row["report_query"] = _query_string(
            {
                "q": search_query,
                "status": selected_status,
                "customer": row["customer_id"],
                "month": selected_month if use_legacy_month_filter else "",
                "date_type": selected_date_type,
                "date_from": filter_date_from.isoformat() if filter_date_from else "",
                "date_to": filter_date_to.isoformat() if filter_date_to else "",
                "quick_range": selected_quick_range,
                "ageing": selected_ageing,
            }
        )

    return render(
        request,
        "reports/invoice_customer_report.html",
        {
            "today": today,
            "month_start": current_month_start,
            "year_start": current_year_start,
            "total_amount_collected_month": total_amount_collected_month,
            "total_amount_collected_year": total_amount_collected_year,
            "collection_month_label": collection_month_label,
            "collection_year_label": collection_year_label,
            "outstanding_amount": outstanding_amount,
            "overdue_count": overdue_count,
            "pending_payment_count": pending_payment_count,
            "viewed_count": viewed_count,
            "paid_count": paid_count,
            "draft_count": draft_count,
            "refunded_count": refunded_count,
            "status_summary": status_summary,
            "total_customers_with_invoices": total_customers_with_invoices,
            "top_customers_by_total": top_customers_by_total,
            "top_customers_by_outstanding": top_customers_by_outstanding,
            "customers_with_overdue": customers_with_overdue,
            "monthly_collection_labels": monthly_collection_labels,
            "monthly_collection_values": monthly_collection_values,
            "overdue_ageing_labels": overdue_ageing_labels,
            "overdue_ageing_values": overdue_ageing_values,
            "status_filter_options": status_filter_options,
            "customer_filter_options": customer_filter_options,
            "month_filter_options": month_filter_options,
            "date_type_options": date_type_options,
            "quick_range_options": quick_range_options,
            "ageing_filter_options": ageing_filter_options,
            "search_query": search_query,
            "selected_status": selected_status,
            "selected_customer_id": selected_customer_id,
            "selected_month": selected_month,
            "selected_date_type": selected_date_type,
            "selected_quick_range": selected_quick_range,
            "selected_ageing": selected_ageing,
            "date_from": date_from.isoformat() if date_from else raw_date_from,
            "date_to": date_to.isoformat() if date_to else raw_date_to,
            "filter_month_start": filter_month_start,
            "filter_month_end": filter_month_end,
            "date_filter_error": date_filter_error,
            "has_active_filters": has_active_filters,
            "active_filter_badges": active_filter_badges,
            "filtered_invoice_count": filtered_invoice_count,
            "has_report_data": has_report_data,
            "detailed_invoices": detailed_invoices,
            "follow_up_invoices": follow_up_invoices,
            "recent_invoices_created": recent_invoices_created,
            "recent_payments_received": recent_payments_received,
            "recent_invoice_email_logs": recent_invoice_email_logs,
            "recent_failed_invoice_email_logs": recent_failed_invoice_email_logs,
            "failed_invoice_email_count": failed_invoice_email_count,
            "overdue_invoice_list_query": overdue_invoice_list_query,
            "outstanding_invoice_list_query": outstanding_invoice_list_query,
            "pending_invoice_list_query": pending_invoice_list_query,
            "draft_invoice_list_query": draft_invoice_list_query,
            "refunded_invoice_list_query": refunded_invoice_list_query,
            "customer_history_query": customer_history_query,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def admin_security_report(request):
    user_model = get_user_model()
    today = timezone.localdate()
    month_start = today.replace(day=1)
    raw_date_from = (request.GET.get("date_from") or "").strip()
    raw_date_to = (request.GET.get("date_to") or "").strip()
    date_from, date_to, filter_date_from, filter_date_to, date_filter_error = _parse_date_range(
        raw_date_from,
        raw_date_to,
    )

    filtered_audit_logs = AuditLog.objects.all()
    filtered_email_logs = EmailDeliveryLog.objects.all()
    if not date_filter_error:
        filtered_audit_logs = _apply_date_bounds(
            filtered_audit_logs,
            "created_at__date",
            filter_date_from,
            filter_date_to,
        )
        filtered_email_logs = _apply_date_bounds(
            filtered_email_logs,
            "attempted_at__date",
            filter_date_from,
            filter_date_to,
        )

    users = user_model.objects.select_related("role_profile")
    total_users = users.count()
    users_by_role = [
        {"role": role, "label": label, "count": users.filter(role_profile__role=role).count()}
        for role, label in ROLE_CHOICES
    ]
    users_by_role_chart = [
        {
            "role": row["role"],
            "label": "HR / Payroll" if row["role"] == HR else row["label"].replace(" Officer", ""),
            "count": row["count"],
        }
        for row in users_by_role
    ]
    new_users_this_month = users.filter(date_joined__date__gte=month_start, date_joined__date__lte=today).count()
    active_users_count = users.filter(is_active=True, role_profile__suspended_at__isnull=True).count()
    suspended_or_inactive_users_count = users.filter(
        Q(is_active=False) | Q(role_profile__suspended_at__isnull=False)
    ).distinct().count()
    suspended_accounts_count = users.filter(role_profile__suspended_at__isnull=False).count()
    unverified_users_count = EmailVerificationToken.objects.filter(
        used_at__isnull=True,
        user__is_active=False,
    ).values("user_id").distinct().count()
    suspended_or_inactive_only_count = max(suspended_or_inactive_users_count - unverified_users_count, 0)

    failed_login_attempts_count = filtered_audit_logs.filter(action="auth.login.failed").count()
    permission_denied_count = filtered_audit_logs.filter(action="auth.permission_denied").count()
    suspicious_activity_count = filtered_audit_logs.filter(
        Q(action="auth.permission_denied") | Q(action="auth.login.failed")
    ).count()
    recent_suspicious_activities = (
        filtered_audit_logs.select_related("user", "user__role_profile")
        .filter(Q(action="auth.permission_denied") | Q(action="auth.login.failed"))
        .order_by("-created_at")[:10]
    )
    recent_login_related_logs = (
        filtered_audit_logs.select_related("user", "user__role_profile")
        .filter(action__startswith="auth.login")
        .order_by("-created_at")[:10]
    )

    recent_account_creations = (
        filtered_audit_logs.select_related("user", "user__role_profile")
        .filter(action__in=["admin.account.created", "auth.admin_account.created"])
        .order_by("-created_at")[:10]
    )
    recent_role_changes = (
        filtered_audit_logs.select_related("user", "user__role_profile")
        .filter(action="admin.account.role_changed")
        .order_by("-created_at")[:10]
    )
    recent_password_changes = (
        filtered_audit_logs.select_related("user", "user__role_profile")
        .filter(action="admin.account.password_updated")
        .order_by("-created_at")[:10]
    )
    recent_admin_actions = (
        filtered_audit_logs.select_related("user", "user__role_profile")
        .filter(action__startswith="admin.")
        .order_by("-created_at")[:12]
    )
    if filter_date_from and filter_date_to:
        failed_login_trend_start = filter_date_from
        failed_login_trend_end = filter_date_to
    else:
        failed_login_trend_end = filter_date_to or today
        failed_login_trend_start = filter_date_from or (failed_login_trend_end - timezone.timedelta(days=6))
    failed_login_rows = list(
        filtered_audit_logs.filter(
            action="auth.login.failed",
            created_at__date__gte=failed_login_trend_start,
            created_at__date__lte=failed_login_trend_end,
        )
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )
    failed_login_map = {}
    for row in failed_login_rows:
        day_value = row.get("day")
        if not day_value:
            continue
        failed_login_map[day_value] = int(row.get("total") or 0)
    failed_login_trend_labels = []
    failed_login_trend_values = []
    total_days = (failed_login_trend_end - failed_login_trend_start).days + 1
    for day_offset in range(total_days):
        day = failed_login_trend_start + timezone.timedelta(days=day_offset)
        failed_login_trend_labels.append(day.strftime("%d %b"))
        failed_login_trend_values.append(failed_login_map.get(day, 0))

    admin_action_summary = [
        {
            "label": "Account Created",
            "count": filtered_audit_logs.filter(
                action__in=["admin.account.created", "auth.admin_account.created"]
            ).count(),
        },
        {
            "label": "Role Changed",
            "count": filtered_audit_logs.filter(action="admin.account.role_changed").count(),
        },
        {
            "label": "Password Updated",
            "count": filtered_audit_logs.filter(action="admin.account.password_updated").count(),
        },
        {
            "label": "Account Suspended",
            "count": filtered_audit_logs.filter(action="admin.account.suspended").count(),
        },
        {
            "label": "Account Unsuspended",
            "count": filtered_audit_logs.filter(action="admin.account.unsuspended").count(),
        },
    ]

    reminder_settings = PaymentReminderSettings.load()
    reminder_email_logs = filtered_email_logs.filter(template_key__startswith="payment_reminder_")
    reminder_emails_sent_count = reminder_email_logs.filter(status=EmailDeliveryLog.STATUS_SENT).count()
    recent_reminder_email_logs = reminder_email_logs.order_by("-attempted_at")[:10]
    failed_email_deliveries_count = filtered_email_logs.filter(
        status=EmailDeliveryLog.STATUS_FAILED
    ).count()
    recent_failed_email_logs = filtered_email_logs.filter(
        status=EmailDeliveryLog.STATUS_FAILED
    ).order_by("-attempted_at")[:10]
    requires_investigation_count = (
        suspicious_activity_count
        + failed_email_deliveries_count
        + suspended_accounts_count
    )
    active_filter_badges = []
    if filter_date_from or filter_date_to:
        date_range_start = filter_date_from.strftime("%d %b %Y") if filter_date_from else "Start"
        date_range_end = filter_date_to.strftime("%d %b %Y") if filter_date_to else "Today"
        active_filter_badges.append(f"Event Date: {date_range_start} to {date_range_end}")
    has_active_filters = bool(raw_date_from or raw_date_to)

    return render(
        request,
        "reports/admin_security_report.html",
        {
            "today": today,
            "month_start": month_start,
            "total_users": total_users,
            "users_by_role": users_by_role,
            "users_by_role_chart": users_by_role_chart,
            "new_users_this_month": new_users_this_month,
            "active_users_count": active_users_count,
            "suspended_or_inactive_users_count": suspended_or_inactive_users_count,
            "suspended_accounts_count": suspended_accounts_count,
            "unverified_users_count": unverified_users_count,
            "suspended_or_inactive_only_count": suspended_or_inactive_only_count,
            "failed_login_attempts_count": failed_login_attempts_count,
            "permission_denied_count": permission_denied_count,
            "failed_login_trend_labels": failed_login_trend_labels,
            "failed_login_trend_values": failed_login_trend_values,
            "admin_action_summary": admin_action_summary,
            "suspicious_activity_count": suspicious_activity_count,
            "recent_suspicious_activities": recent_suspicious_activities,
            "recent_login_related_logs": recent_login_related_logs,
            "recent_account_creations": recent_account_creations,
            "recent_role_changes": recent_role_changes,
            "recent_password_changes": recent_password_changes,
            "recent_admin_actions": recent_admin_actions,
            "reminder_settings": reminder_settings,
            "reminder_emails_sent_count": reminder_emails_sent_count,
            "recent_reminder_email_logs": recent_reminder_email_logs,
            "failed_email_deliveries_count": failed_email_deliveries_count,
            "recent_failed_email_logs": recent_failed_email_logs,
            "requires_investigation_count": requires_investigation_count,
            "date_from": date_from.isoformat() if date_from else raw_date_from,
            "date_to": date_to.isoformat() if date_to else raw_date_to,
            "date_filter_error": date_filter_error,
            "has_active_filters": has_active_filters,
            "active_filter_badges": active_filter_badges,
            "suspicious_activity_url": reverse("suspicious-activity-list"),
            "suspicious_failed_url": reverse("suspicious-activity-list") + _query_string({"reason": "failed"}),
            "permission_denied_audit_url": reverse("dashboard-audit-logs")
            + _query_string({"action": "auth.permission_denied"}),
            "failed_email_logs_url": reverse("email-delivery-log-list") + _query_string({"status": "failed"}),
            "audit_log_url": reverse("dashboard-audit-logs"),
            "reminder_settings_url": reverse("payment-reminder-settings-update"),
            "announcement_email_url": reverse("mass-email-send"),
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def payment_stripe_report(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    month_starts = _recent_month_starts(today, total_months=6)
    payment_trend_labels = [month.strftime("%b %Y") for month in month_starts]
    month_keys = [month.strftime("%Y-%m") for month in month_starts]
    raw_date_from = (request.GET.get("date_from") or "").strip()
    raw_date_to = (request.GET.get("date_to") or "").strip()
    date_from, date_to, filter_date_from, filter_date_to, date_filter_error = _parse_date_range(
        raw_date_from,
        raw_date_to,
    )

    succeeded_payments = successful_payments_queryset()
    payment_records = PaymentRecord.objects.all()
    if not date_filter_error:
        succeeded_payments = _apply_date_bounds(
            succeeded_payments,
            "paid_at__date",
            filter_date_from,
            filter_date_to,
        )
        payment_records = _apply_date_bounds(
            payment_records,
            "paid_at__date",
            filter_date_from,
            filter_date_to,
        )
    failed_cancelled_payments = payment_records.filter(
        status__in=[PaymentRecord.STATUS_FAILED, PaymentRecord.STATUS_CANCELLED]
    )
    refunded_payments = payment_records.filter(status=PaymentRecord.STATUS_REFUNDED)
    pending_manual_payments = payment_records.filter(
        provider=PaymentRecord.PROVIDER_MANUAL,
        status=PaymentRecord.STATUS_PENDING,
    )

    successful_month_amount = _safe_sum(
        succeeded_payments.filter(paid_at__date__gte=month_start, paid_at__date__lte=today),
        "amount",
    )
    successful_year_amount = _safe_sum(
        succeeded_payments.filter(paid_at__date__gte=year_start, paid_at__date__lte=today),
        "amount",
    )

    outstanding_amount = _safe_sum(
        Invoice.objects.filter(
            status__in=[
                Invoice.STATUS_DRAFT,
                Invoice.STATUS_SENT,
                Invoice.STATUS_VIEWED,
                Invoice.STATUS_OVERDUE,
            ]
        ),
        "total_amount",
    )

    stripe_payments = payment_records.filter(provider=PaymentRecord.PROVIDER_STRIPE)
    status_count_map = {
        row["status"]: row["total"]
        for row in payment_records.values("status").annotate(total=Count("id"))
    }
    payment_status_summary = [
        {"status": "succeeded", "label": "Successful", "total": status_count_map.get(PaymentRecord.STATUS_SUCCEEDED, 0)},
        {"status": "failed", "label": "Failed", "total": status_count_map.get(PaymentRecord.STATUS_FAILED, 0)},
        {"status": "cancelled", "label": "Cancelled", "total": status_count_map.get(PaymentRecord.STATUS_CANCELLED, 0)},
        {"status": "refunded", "label": "Refunded", "total": status_count_map.get(PaymentRecord.STATUS_REFUNDED, 0)},
    ]
    recent_stripe_transactions = stripe_payments.select_related("invoice", "invoice__customer").order_by(
        "-created_at"
    )[:8]

    recent_payments = (
        payment_records.select_related("invoice", "invoice__customer").order_by("-created_at")[:20]
    )

    stripe_total = stripe_payments.count()
    manual_total = payment_records.filter(provider=PaymentRecord.PROVIDER_MANUAL).count()

    payment_method_summary = [
        {
            "method": "Stripe",
            "available": stripe_total > 0,
            "has_count": True,
            "count": stripe_total,
            "note": (
                "Current integrated prototype method."
                if stripe_total > 0 and manual_total == 0
                else "Stripe payment records."
            ),
        },
    ]
    if manual_total > 0:
        payment_method_summary.append(
            {
                "method": "Manual / Bank transfer",
                "available": True,
                "has_count": True,
                "count": manual_total,
                "note": "Stored as provider=manual records.",
            }
        )

    stripe_succeeded_payments = succeeded_payments.filter(provider=PaymentRecord.PROVIDER_STRIPE)

    stripe_card_brand_totals = {}
    stripe_paynow_total = 0
    stripe_unknown_total = 0
    stripe_records = list(
        stripe_succeeded_payments.exclude(external_transaction_id__exact="")
        .only("id", "external_transaction_id")
        .order_by("id")
    )

    try:
        stripe = _import_stripe()
        stripe.api_key = _require_stripe_secret_key()
    except Exception:
        stripe_unknown_total = stripe_succeeded_payments.count()
    else:
        for record in stripe_records:
            method_type = ""
            card_brand = ""
            try:
                payment_intent = stripe.PaymentIntent.retrieve(
                    record.external_transaction_id,
                    expand=["latest_charge"],
                )
                method_types = list(getattr(payment_intent, "payment_method_types", []) or [])
                if method_types:
                    method_type = str(method_types[0]).strip().lower()
                latest_charge = getattr(payment_intent, "latest_charge", None)
                method_details = getattr(latest_charge, "payment_method_details", None)
                if method_details is not None:
                    details_type_value = getattr(method_details, "type", "")
                    if not details_type_value and hasattr(method_details, "get"):
                        details_type_value = method_details.get("type")
                    details_type = (
                        str(details_type_value or "")
                        .strip()
                        .lower()
                    )
                    if details_type:
                        method_type = details_type
                    if details_type == "card":
                        card_details = getattr(method_details, "card", None)
                        if card_details is None and hasattr(method_details, "get"):
                            card_details = method_details.get("card")
                        if card_details is not None:
                            brand_value = getattr(card_details, "brand", "")
                            if not brand_value and hasattr(card_details, "get"):
                                brand_value = card_details.get("brand")
                            card_brand = (
                                str(brand_value or "")
                                .strip()
                                .lower()
                            )
            except Exception:
                method_type = ""
                card_brand = ""

            if method_type == "paynow":
                stripe_paynow_total += 1
            elif method_type == "card":
                if card_brand:
                    stripe_card_brand_totals[card_brand] = stripe_card_brand_totals.get(card_brand, 0) + 1
                else:
                    stripe_unknown_total += 1
            else:
                stripe_unknown_total += 1

    stripe_card_total = sum(stripe_card_brand_totals.values())

    # Successful Stripe records without payment_intent IDs cannot be resolved to method type.
    stripe_unknown_total += max(
        stripe_succeeded_payments.count() - (stripe_card_total + stripe_paynow_total + stripe_unknown_total),
        0,
    )

    brand_display_map = {
        "amex": "American Express",
        "mastercard": "Mastercard",
        "visa": "Visa",
        "discover": "Discover",
        "jcb": "JCB",
        "diners": "Diners Club",
        "unionpay": "UnionPay",
    }

    payment_method_table_summary = []
    for brand, total in sorted(stripe_card_brand_totals.items(), key=lambda row: (-row[1], row[0])):
        payment_method_table_summary.append(
            {
                "method": brand_display_map.get(brand, brand.replace("_", " ").title()),
                "available": total > 0,
                "has_count": True,
                "count": total,
                "note": "Successful Stripe card payments used by customers.",
            }
        )

    payment_method_table_summary.append(
        {
            "method": "PayNow",
            "available": stripe_paynow_total > 0,
            "has_count": True,
            "count": stripe_paynow_total,
            "note": "Successful Stripe PayNow payments used by customers.",
        }
    )
    if stripe_unknown_total > 0:
        payment_method_table_summary.append(
            {
                "method": "Stripe (Unknown)",
                "available": True,
                "has_count": True,
                "count": stripe_unknown_total,
                "note": "Method could not be determined from stored Stripe details. ",
            }
        )
    if manual_total > 0:
        payment_method_table_summary.append(
            {
                "method": "Manual / Bank transfer",
                "available": True,
                "has_count": True,
                "count": manual_total,
                "note": "Stored as provider=manual records.",
            }
        )

    monthly_successful_rows = list(
        succeeded_payments.filter(paid_at__isnull=False)
        .annotate(month=TruncMonth("paid_at"))
        .values("month")
        .annotate(total_amount=Sum("amount"), payment_count=Count("id"))
        .order_by("month")
    )
    monthly_successful_amount_map = {}
    monthly_successful_count_map = {}
    for row in monthly_successful_rows:
        month_value = row.get("month")
        if not month_value:
            continue
        month_key = month_value.strftime("%Y-%m")
        monthly_successful_amount_map[month_key] = _to_float(row.get("total_amount"))
        monthly_successful_count_map[month_key] = int(row.get("payment_count") or 0)

    monthly_payment_amount_values = [monthly_successful_amount_map.get(month_key, 0.0) for month_key in month_keys]
    monthly_successful_count_values = [monthly_successful_count_map.get(month_key, 0) for month_key in month_keys]
    attention_payments = payment_records.select_related("invoice", "invoice__customer").filter(
        status__in=[
            PaymentRecord.STATUS_PENDING,
            PaymentRecord.STATUS_FAILED,
            PaymentRecord.STATUS_CANCELLED,
            PaymentRecord.STATUS_REFUNDED,
        ]
    ).order_by("-created_at")[:10]
    refunded_amount = _safe_sum(refunded_payments, "amount")
    active_filter_badges = []
    if filter_date_from or filter_date_to:
        date_range_start = filter_date_from.strftime("%d %b %Y") if filter_date_from else "Start"
        date_range_end = filter_date_to.strftime("%d %b %Y") if filter_date_to else "Today"
        active_filter_badges.append(f"Payment Date: {date_range_start} to {date_range_end}")
    has_active_filters = bool(raw_date_from or raw_date_to)

    return render(
        request,
        "reports/payment_stripe_report.html",
        {
            "today": today,
            "month_start": month_start,
            "year_start": year_start,
            "successful_month_amount": successful_month_amount,
            "successful_year_amount": successful_year_amount,
            "successful_payment_count": succeeded_payments.count(),
            "failed_payment_count": PaymentRecord.objects.filter(status=PaymentRecord.STATUS_FAILED).count(),
            "cancelled_payment_count": PaymentRecord.objects.filter(status=PaymentRecord.STATUS_CANCELLED).count(),
            "failed_cancelled_count": failed_cancelled_payments.count(),
            "refunded_count": refunded_payments.count(),
            "refunded_amount": refunded_amount,
            "pending_manual_payment_count": pending_manual_payments.count(),
            "outstanding_amount": outstanding_amount,
            "stripe_total": stripe_total,
            "payment_status_summary": payment_status_summary,
            "recent_stripe_transactions": recent_stripe_transactions,
            "payment_method_summary": payment_method_summary,
            "payment_method_table_summary": payment_method_table_summary,
            "recent_payments": recent_payments,
            "attention_payments": attention_payments,
            "is_stripe_only_prototype": stripe_total > 0 and manual_total == 0,
            "payment_trend_labels": payment_trend_labels,
            "monthly_payment_amount_values": monthly_payment_amount_values,
            "monthly_successful_count_values": monthly_successful_count_values,
            "date_from": date_from.isoformat() if date_from else raw_date_from,
            "date_to": date_to.isoformat() if date_to else raw_date_to,
            "date_filter_error": date_filter_error,
            "has_active_filters": has_active_filters,
            "active_filter_badges": active_filter_badges,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_report(request):
    today = timezone.localdate()
    selected_month = (request.GET.get("month") or "").strip()
    selected_employee = (request.GET.get("employee") or "").strip()
    selected_month, month_start, month_end = _month_bounds(selected_month, today)
    year_start = today.replace(month=1, day=1)

    month_records = PayrollRecord.objects.filter(payment_date__gte=month_start, payment_date__lte=month_end)
    year_records = PayrollRecord.objects.filter(payment_date__gte=year_start, payment_date__lte=today)
    if selected_employee:
        employee_filter = Q(employee_name__icontains=selected_employee) | Q(employee_id__icontains=selected_employee)
        month_records = month_records.filter(employee_filter)
        year_records = year_records.filter(employee_filter)

    total_payroll_amount_month = _safe_sum(month_records, "basic_salary") + _safe_sum(month_records, "allowances")
    total_payroll_amount_year = _safe_sum(year_records, "basic_salary") + _safe_sum(year_records, "allowances")
    total_net_pay_month = _safe_sum(month_records, "net_salary")
    total_allowances_month = _safe_sum(month_records, "allowances")
    total_deductions_month = _safe_sum(month_records, "deductions")
    employee_cpf_total_month = _safe_sum(month_records, "cpf_contribution")
    employees_paid_month = month_records.values("employee_id").distinct().count()

    month_starts = _recent_month_starts(today, total_months=6)
    payroll_monthly_labels = [month.strftime("%b %Y") for month in month_starts]
    month_keys = [month.strftime("%Y-%m") for month in month_starts]

    payroll_monthly_queryset = PayrollRecord.objects.all()
    if selected_employee:
        payroll_monthly_queryset = payroll_monthly_queryset.filter(
            Q(employee_name__icontains=selected_employee) | Q(employee_id__icontains=selected_employee)
        )
    payroll_monthly_rows = list(
        payroll_monthly_queryset.annotate(month=TruncMonth("payment_date"))
        .values("month")
        .annotate(
            total_basic=Sum("basic_salary"),
            total_allowances=Sum("allowances"),
            employees_paid=Count("employee_id", distinct=True),
        )
        .order_by("month")
    )
    payroll_amount_map = {}
    payroll_employees_map = {}
    for row in payroll_monthly_rows:
        month_value = row.get("month")
        if not month_value:
            continue
        month_key = month_value.strftime("%Y-%m")
        payroll_amount_map[month_key] = _to_float(row.get("total_basic")) + _to_float(row.get("total_allowances"))
        payroll_employees_map[month_key] = int(row.get("employees_paid") or 0)

    payroll_monthly_cost_values = [payroll_amount_map.get(month_key, 0.0) for month_key in month_keys]
    payroll_monthly_employee_values = [payroll_employees_map.get(month_key, 0) for month_key in month_keys]

    month_rows = list(
        month_records.order_by("-payment_date", "employee_id").values(
            "id",
            "employee_name",
            "employee_id",
            "basic_salary",
            "allowances",
            "deductions",
            "net_salary",
            "payment_date",
        )
    )

    employee_codes = [row["employee_id"] for row in month_rows]
    employee_map = {
        e.employee_code: e
        for e in Employee.objects.filter(employee_code__in=employee_codes).only(
            "employee_code", "cpf_exempt", "date_of_birth"
        )
    }

    employer_cpf_total_month = 0
    for row in month_rows:
        employee = employee_map.get(row["employee_id"])
        if not employee or employee.cpf_exempt or not employee.date_of_birth:
            continue
        total_earnings = row["basic_salary"] + row["allowances"]
        age = row["payment_date"].year - employee.date_of_birth.year - (
            (row["payment_date"].month, row["payment_date"].day)
            < (employee.date_of_birth.month, employee.date_of_birth.day)
        )
        employer_cpf_total_month += cpf_for_2026(total_earnings, age).employer_amount

    month_record_id_set = {row["id"] for row in month_rows}
    email_logs = EmailDeliveryLog.objects.filter(
        related_object_type="payroll_record",
    ).values("related_object_id", "status")
    emailed_ids = {
        int(log["related_object_id"])
        for log in email_logs
        if str(log["related_object_id"]).isdigit()
        and int(log["related_object_id"]) in month_record_id_set
        and log["status"] == EmailDeliveryLog.STATUS_SENT
    }
    failed_email_ids = {
        int(log["related_object_id"])
        for log in email_logs
        if str(log["related_object_id"]).isdigit()
        and int(log["related_object_id"]) in month_record_id_set
        and log["status"] == EmailDeliveryLog.STATUS_FAILED
    }

    downloaded_logs = AuditLog.objects.filter(
        action="payroll.pdf.downloaded",
        target_type="payroll_record",
    ).values_list("target_id", flat=True)
    downloaded_ids = {
        int(target_id)
        for target_id in downloaded_logs
        if str(target_id).isdigit() and int(target_id) in month_record_id_set
    }

    records_with_status = []
    for row in month_rows:
        if row["id"] in emailed_ids:
            status = "Emailed"
        elif row["id"] in downloaded_ids:
            status = "Downloaded"
        elif row["id"] in failed_email_ids:
            status = "Email Failed"
        else:
            status = "Pending"
        gross_pay = row["basic_salary"] + row["allowances"]
        records_with_status.append(
            {
                **row,
                "gross_pay": gross_pay,
                "status": status,
            }
        )

    recent_payslips = PayrollRecord.objects.order_by("-created_at")
    if selected_employee:
        recent_payslips = recent_payslips.filter(
            Q(employee_name__icontains=selected_employee) | Q(employee_id__icontains=selected_employee)
        )
    recent_payslips = recent_payslips[:10]
    pending_email_or_download_count = sum(1 for row in records_with_status if row["status"] == "Pending")
    failed_payslip_email_count = sum(1 for row in records_with_status if row["status"] == "Email Failed")
    staff_employee_codes = list(
        Employee.objects.filter(user__role_profile__role=STAFF).values_list("employee_code", flat=True)
    )
    staff_payslip_records_count = PayrollRecord.objects.filter(employee_id__in=staff_employee_codes).count()
    has_active_filters = bool(selected_employee or (request.GET.get("month") or "").strip())
    selected_month_label = month_start.strftime("%B %Y")
    selected_filter_text = (
        f"Showing payroll records for {selected_month_label}"
        + (f" and employee search '{selected_employee}'." if selected_employee else ".")
    )

    return render(
        request,
        "reports/payroll_report.html",
        {
            "today": today,
            "selected_month": selected_month,
            "selected_employee": selected_employee,
            "month_start": month_start,
            "month_end": month_end,
            "year_start": year_start,
            "total_payroll_amount_month": total_payroll_amount_month,
            "total_payroll_amount_year": total_payroll_amount_year,
            "employees_paid_month": employees_paid_month,
            "payroll_monthly_labels": payroll_monthly_labels,
            "payroll_monthly_cost_values": payroll_monthly_cost_values,
            "payroll_monthly_employee_values": payroll_monthly_employee_values,
            "total_net_pay_month": total_net_pay_month,
            "total_allowances_month": total_allowances_month,
            "total_deductions_month": total_deductions_month,
            "employee_cpf_total_month": employee_cpf_total_month,
            "employer_cpf_total_month": employer_cpf_total_month,
            "total_cpf_month": employee_cpf_total_month + employer_cpf_total_month,
            "records_with_status": records_with_status,
            "recent_payslips": recent_payslips,
            "pending_email_or_download_count": pending_email_or_download_count,
            "failed_payslip_email_count": failed_payslip_email_count,
            "staff_payslip_records_count": staff_payslip_records_count,
            "has_active_filters": has_active_filters,
            "selected_filter_text": selected_filter_text,
            "payroll_list_query": _query_string({"month": selected_month, "q": selected_employee}),
        },
    )
