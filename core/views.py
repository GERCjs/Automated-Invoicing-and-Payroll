from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import get_role_landing_route_name, get_user_role, role_required
from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, ROLE_CHOICES, SUPERADMIN
from imports.models import ImportJob, ImportRowError
from invoicing.models import Customer, Invoice
from notifications.models import EmailDeliveryLog
from payments.models import PaymentRecord
from payments.services import successful_payments_queryset
from payroll.models import Employee, PayrollBatch, PayrollEntry, PayrollRecord

from .models import AuditLog


AUDIT_ACTION_LABELS = {
    "auth.login": "User logged in",
    "auth.logout": "User logged out",
    "auth.registered": "User registered",
    "auth.email_verified": "Email verified",
    "auth.permission_denied": "Access denied",
    "core.dashboard.viewed": "Management dashboard viewed",
    "core.finance_console.viewed": "Payroll area opened",
    "invoice.dashboard.viewed": "Invoice dashboard viewed",
    "invoice.list.viewed": "Invoice list viewed",
    "invoice.detail.viewed": "Invoice details viewed",
    "invoice.created": "Invoice created",
    "invoice.edited": "Invoice updated",
    "invoice.deleted": "Draft invoice deleted",
    "invoice.status.changed": "Invoice status changed",
    "invoice.email.sent": "Invoice email sent",
    "invoice.email.failed": "Invoice email failed",
    "payment.checkout.started": "Stripe checkout started",
    "payment.checkout.cancelled": "Stripe checkout cancelled",
    "payment.stripe.succeeded": "Stripe payment succeeded",
    "payment.stripe.failed": "Stripe payment failed",
    "payment.stripe.cancelled": "Stripe payment cancelled/expired",
    "payment.stripe.redirect_confirmed": "Stripe success redirect confirmed",
    "payment.invoice.marked_paid": "Invoice marked as paid from payment",
    "payment.refund.requested": "Stripe refund requested",
    "payment.refund.succeeded": "Stripe refund succeeded",
    "payment.refund.failed": "Stripe refund failed",
    "report.payment_stripe.viewed": "Payment report viewed",
    "report.admin_security.viewed": "Admin and security report viewed",
    "admin.dashboard.viewed": "Admin dashboard viewed",
    "admin.account.created": "Account created",
    "admin.account.role_changed": "User role changed",
    "admin.account.password_updated": "Password updated",
    "admin.account.deleted": "Account deleted",
    "admin.payment_reminders.updated": "Reminder settings updated",
    "admin.payment_reminders.run_check": "Reminder check executed",
    "admin.mass_email.sent": "Mass email sent",
    "support.ticket.created": "Support ticket created",
    "support.ticket.updated": "Support ticket updated",

}


NOISY_AUDIT_ACTIONS = {
    "admin.dashboard.viewed",
    "core.dashboard.viewed",
    "core.finance_console.viewed",
    "invoice.customer.dashboard.viewed",
    "invoice.customer.detail.viewed",
    "invoice.list.viewed",
    "invoice.dashboard.viewed",
    "invoice.detail.viewed",
    "payroll.dashboard.viewed",
    "payroll.list.viewed",
    "payroll.record.viewed",
    "payroll.my_payslips.viewed",
    "report.invoice_customer.viewed",
    "report.admin_security.viewed",
    "report.payment_stripe.viewed",
    "report.payroll.viewed",
}


def describe_audit_action(action):
    if action in AUDIT_ACTION_LABELS:
        return AUDIT_ACTION_LABELS[action]
    return action.replace(".", " ").replace("_", " ").title()


def explain_audit_action(action):
    descriptions = {
        "auth.login": "A user signed in successfully.",
        "auth.logout": "A user signed out.",
        "auth.registered": "A new account registration was submitted.",
        "auth.email_verified": "A user verified their email and activated the account.",
        "auth.permission_denied": "A user tried to open a page without the required role.",
        "core.dashboard.viewed": "A user opened the management dashboard.",
        "core.finance_console.viewed": "A user opened the payroll workspace.",
        "invoice.dashboard.viewed": "A user opened the invoice dashboard.",
        "invoice.list.viewed": "A user viewed the invoice list.",
        "invoice.detail.viewed": "A user opened an invoice record.",
        "invoice.created": "A new invoice was created.",
        "invoice.edited": "An invoice was updated.",
        "invoice.deleted": "A draft invoice was deleted.",
        "invoice.status.changed": "An invoice status was changed.",
        "invoice.email.sent": "An invoice email was sent successfully.",
        "invoice.email.failed": "An invoice email failed to send.",
        "payment.checkout.started": "A Stripe Checkout session was created for an invoice.",
        "payment.checkout.cancelled": "Checkout was cancelled before payment completion.",
        "payment.stripe.succeeded": "Stripe confirmed payment success for an invoice.",
        "payment.stripe.failed": "Stripe reported an asynchronous payment failure.",
        "payment.stripe.cancelled": "Stripe checkout expired or was cancelled.",
        "payment.stripe.redirect_confirmed": "Success redirect confirmed payment in sandbox fallback mode.",
        "payment.invoice.marked_paid": "Invoice status was updated to paid from payment processing.",
        "payment.refund.requested": "A Stripe refund request was created or is pending completion.",
        "payment.refund.succeeded": "Stripe confirmed a successful refund for a prior payment.",
        "payment.refund.failed": "Stripe refund request failed or could not be completed.",
        "report.payment_stripe.viewed": "An authorized user opened the Payment and Stripe report.",
        "report.admin_security.viewed": "An authorized admin opened the Admin and Security report.",
        "admin.dashboard.viewed": "An admin opened the admin dashboard.",
        "admin.account.created": "An admin created a user account.",
        "admin.account.role_changed": "An admin changed a user's role.",
        "admin.account.password_updated": "An admin reset a user's password.",
        "admin.account.deleted": "An admin deleted a user account.",
        "admin.payment_reminders.updated": "An admin updated reminder settings.",
        "admin.payment_reminders.run_check": "An admin ran the reminder check job.",
        "admin.mass_email.sent": "An admin sent a mass email.",
        "support.ticket.created": "A user submitted a support ticket.",
        "support.ticket.updated": "An authorized user updated a support ticket.",

    }
    return descriptions.get(action, describe_audit_action(action))


def _safe_count(queryset):
    try:
        return queryset.count()
    except DatabaseError:
        return 0


def _safe_sum(queryset, field_name):
    try:
        return queryset.aggregate(total=Sum(field_name))["total"] or 0
    except DatabaseError:
        return 0


def _safe_group_counts(queryset, field_name):
    try:
        return {
            row[field_name]: row["total"]
            for row in queryset.values(field_name).annotate(total=Count("id"))
        }
    except DatabaseError:
        return {}


def _safe_list(queryset):
    try:
        return list(queryset)
    except DatabaseError:
        return []


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


def _currency_string(value):
    return f"S${float(value or 0):,.2f}"


def _build_chart_summary(month_labels, values):
    numeric_values = [float(value or 0) for value in values]
    total_value = sum(numeric_values)
    has_data = any(value > 0 for value in numeric_values)
    peak_label = ""
    peak_value = 0.0
    if has_data and month_labels and numeric_values:
        peak_index = max(range(len(numeric_values)), key=numeric_values.__getitem__)
        peak_label = month_labels[peak_index]
        peak_value = numeric_values[peak_index]
    return {
        "has_data": has_data,
        "six_month_total": total_value,
        "peak_label": peak_label,
        "peak_value": peak_value,
    }


def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return redirect("login")


def customer_entry(request):
    return render(request, "core/customer_entry.html")


@login_required
def dashboard(request):
    role = get_user_role(request.user)
    if role not in {SUPERADMIN, ADMIN}:
        landing_route_name = get_role_landing_route_name(request.user)
        if landing_route_name == "dashboard":
            raise PermissionDenied("You do not have permission to access this page.")
        return redirect(landing_route_name)

    can_view_admin_stats = role in {SUPERADMIN, ADMIN}
    can_view_invoice_stats = role in {SUPERADMIN, ADMIN, FINANCE}
    can_view_payroll_stats = role in {SUPERADMIN, ADMIN, HR}

    recent_since = timezone.now() - timezone.timedelta(days=7)
    today = timezone.localdate()
    current_month_start = today.replace(day=1)
    report_generated_at = timezone.localtime()
    reporting_period_label = current_month_start.strftime("%B %Y")

    audit_logs = AuditLog.objects.select_related("user", "user__role_profile")
    audit_action_stats = _safe_list(
        audit_logs.values("action").annotate(total=Count("id")).order_by("-total", "action")[:6]
    )
    max_audit_action_count = max([row["total"] for row in audit_action_stats] or [1])
    audit_action_chart = [
        {
            "action": row["action"],
            "label": describe_audit_action(row["action"]),
            "total": row["total"],
            "percent": int((row["total"] / max_audit_action_count) * 100),
        }
        for row in audit_action_stats
    ]

    invoice_status_counts = _safe_group_counts(Invoice.objects.all(), "status")
    invoice_outstanding = _safe_sum(
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

    payroll_status_counts = _safe_group_counts(PayrollBatch.objects.all(), "status")
    payroll_pending_entries = _safe_count(PayrollEntry.objects.filter(status=PayrollEntry.STATUS_PENDING))
    payroll_total_net = _safe_sum(PayrollRecord.objects.all(), "net_salary")

    import_status_counts = _safe_group_counts(ImportJob.objects.all(), "status")
    import_failed_count = import_status_counts.get(ImportJob.STATUS_FAILED, 0)
    import_with_errors_count = import_status_counts.get(ImportJob.STATUS_COMPLETED_WITH_ERRORS, 0)
    recent_import_errors = _safe_list(ImportRowError.objects.select_related("import_job").order_by("-created_at")[:6])

    email_status_counts = _safe_group_counts(EmailDeliveryLog.objects.all(), "status")

    succeeded_payments = successful_payments_queryset()
    collected_total = _safe_sum(succeeded_payments, "amount")
    refunded_total = _safe_sum(
        PaymentRecord.objects.filter(status=PaymentRecord.STATUS_REFUNDED),
        "amount",
    )
    collected_this_month = _safe_sum(
        succeeded_payments.filter(paid_at__date__gte=current_month_start, paid_at__date__lte=today),
        "amount",
    )
    successful_payment_count_this_month = _safe_count(
        succeeded_payments.filter(paid_at__date__gte=current_month_start, paid_at__date__lte=today)
    )

    month_starts = _recent_month_starts(today, total_months=6)
    month_labels = [month.strftime("%b %Y") for month in month_starts]
    month_keys = [month.strftime("%Y-%m") for month in month_starts]

    payment_month_rows = _safe_list(
        succeeded_payments.annotate(month=TruncMonth("paid_at"))
        .values("month")
        .annotate(total=Sum("amount"))
        .order_by("month")
    )
    collected_by_month = {}
    for row in payment_month_rows:
        month_value = row.get("month")
        if not month_value:
            continue
        collected_by_month[month_value.strftime("%Y-%m")] = _to_float(row.get("total"))
    monthly_collection_values = [collected_by_month.get(month_key, 0.0) for month_key in month_keys]

    payroll_month_rows = _safe_list(
        PayrollRecord.objects.annotate(month=TruncMonth("payment_date"))
        .values("month")
        .annotate(
            total_basic=Sum("basic_salary"),
            total_allowances=Sum("allowances"),
        )
        .order_by("month")
    )
    payroll_by_month = {}
    for row in payroll_month_rows:
        month_value = row.get("month")
        if not month_value:
            continue
        payroll_by_month[month_value.strftime("%Y-%m")] = _to_float(row.get("total_basic")) + _to_float(
            row.get("total_allowances")
        )
    monthly_payroll_values = [payroll_by_month.get(month_key, 0.0) for month_key in month_keys]

    payroll_cost_this_month = _safe_sum(
        PayrollRecord.objects.filter(payment_date__gte=current_month_start, payment_date__lte=today),
        "net_salary",
    )
    payroll_records_this_month = PayrollRecord.objects.filter(
        payment_date__gte=current_month_start,
        payment_date__lte=today,
    )
    payroll_paid_employee_count = _safe_count(
        payroll_records_this_month.values("employee_id").distinct()
    )
    overdue_invoice_amount = _safe_sum(
        Invoice.objects.filter(status=Invoice.STATUS_OVERDUE),
        "total_amount",
    )

    suspicious_activity_count = _safe_count(
        AuditLog.objects.filter(
            created_at__gte=current_month_start,
            action__in=["auth.permission_denied", "auth.login.failed"],
        )
    )
    failed_email_count = email_status_counts.get(EmailDeliveryLog.STATUS_FAILED, 0)
    security_alert_total = suspicious_activity_count + failed_email_count
    import_issue_total = import_failed_count + import_with_errors_count

    detail_report_links = [
        {
            "label": "Finance Report",
            "description": "Review invoice collections and customer balances.",
            "url": reverse("invoice-customer-report"),
        },
        {
            "label": "Payment Report",
            "description": "Review payment activity, failed attempts and refunds.",
            "url": reverse("payment-stripe-report"),
        },
        {
            "label": "Payroll Report",
            "description": "Review payroll cost and employee payment records.",
            "url": reverse("payroll-report"),
        },
        {
            "label": "Security Report",
            "description": "Review suspicious activity and failed email delivery issues.",
            "url": reverse("admin-security-report"),
        },
    ]
    secondary_summary_items = [
        {
            "label": "Overdue Amount",
            "value": _currency_string(overdue_invoice_amount),
            "note": f"{invoice_status_counts.get(Invoice.STATUS_OVERDUE, 0)} overdue invoice(s)",
            "link_label": "Review overdue invoices",
            "link_url": f"{reverse('invoice-list')}?status=overdue",
        },
        {
            "label": "Refunded Amount",
            "value": _currency_string(refunded_total),
            "note": "Refunded payments remain excluded from net collection.",
            "link_label": "Review refund activity",
            "link_url": reverse("payment-stripe-report"),
        },
        {
            "label": "Failed Email Deliveries",
            "value": str(failed_email_count),
            "note": "Invoice, reminder or payslip messages may require follow-up.",
            "link_label": "Review failed emails",
            "link_url": f"{reverse('email-delivery-log-list')}?status=failed",
        },
        {
            "label": "Import Issues",
            "value": str(import_issue_total),
            "note": "Failed and completed-with-errors import jobs needing review.",
            "link_label": "Review import errors",
            "link_url": reverse("dashboard-validation-errors"),
        },
    ]
    management_attention_items = []
    if overdue_invoice_amount or invoice_status_counts.get(Invoice.STATUS_OVERDUE, 0):
        management_attention_items.append(
            {
                "priority": "High",
                "priority_class": "status-danger",
                "area": "Finance",
                "finding": f"{invoice_status_counts.get(Invoice.STATUS_OVERDUE, 0)} overdue invoice(s)",
                "impact": f"{_currency_string(overdue_invoice_amount)} remains unpaid.",
                "link_label": "Review overdue invoices",
                "link_url": f"{reverse('invoice-list')}?status=overdue",
            }
        )
    if suspicious_activity_count:
        management_attention_items.append(
            {
                "priority": "High",
                "priority_class": "status-warning",
                "area": "Security",
                "finding": f"{suspicious_activity_count} suspicious event(s)",
                "impact": "Accounts or access attempts may require investigation.",
                "link_label": "Review security issues",
                "link_url": reverse("admin-security-report"),
            }
        )
    if failed_email_count:
        management_attention_items.append(
            {
                "priority": "Medium",
                "priority_class": "status-warning",
                "area": "Email",
                "finding": f"{failed_email_count} failed delivery log(s)",
                "impact": "Some users may not receive invoices, reminders or payroll documents.",
                "link_label": "Review failed emails",
                "link_url": f"{reverse('email-delivery-log-list')}?status=failed",
            }
        )
    if import_failed_count or import_with_errors_count:
        management_attention_items.append(
            {
                "priority": "Low",
                "priority_class": "status-neutral",
                "area": "Imports",
                "finding": (
                    f"{import_failed_count} failed import(s) and "
                    f"{import_with_errors_count} import(s) completed with errors."
                ),
                "impact": "Some imported records may need correction before follow-up reporting.",
                "link_label": "Review import errors",
                "link_url": reverse("dashboard-validation-errors"),
            }
        )

    collection_chart_summary = _build_chart_summary(month_labels, monthly_collection_values)
    payroll_chart_summary = _build_chart_summary(month_labels, monthly_payroll_values)

    return render(
        request,
        "core/dashboard.html",
        {
            "can_view_admin_stats": can_view_admin_stats,
            "can_view_invoice_stats": can_view_invoice_stats,
            "can_view_payroll_stats": can_view_payroll_stats,
            "total_audit_logs": _safe_count(AuditLog.objects.all()),
            "audit_action_chart": audit_action_chart,
            "invoice_total": _safe_count(Invoice.objects.all()),
            "invoice_customer_total": _safe_count(Customer.objects.all()),
            "invoice_draft_count": invoice_status_counts.get(Invoice.STATUS_DRAFT, 0),
            "invoice_paid_count": invoice_status_counts.get(Invoice.STATUS_PAID, 0),
            "invoice_overdue_count": invoice_status_counts.get(Invoice.STATUS_OVERDUE, 0),
            "invoice_outstanding": invoice_outstanding,
            "employee_total": _safe_count(Employee.objects.all()),
            "active_employee_total": _safe_count(Employee.objects.filter(status=Employee.STATUS_ACTIVE)),
            "payroll_batch_total": _safe_count(PayrollBatch.objects.all()),
            "payroll_processed_count": payroll_status_counts.get(PayrollBatch.STATUS_PROCESSED, 0),
            "payroll_failed_count": payroll_status_counts.get(PayrollBatch.STATUS_FAILED, 0),
            "payroll_pending_entries": payroll_pending_entries,
            "payroll_total_net": payroll_total_net,
            "import_total": _safe_count(ImportJob.objects.all()),
            "import_failed_count": import_failed_count,
            "import_with_errors_count": import_with_errors_count,
            "import_error_total": _safe_count(ImportRowError.objects.all()),
            "recent_import_errors": recent_import_errors,
            "email_sent_count": email_status_counts.get(EmailDeliveryLog.STATUS_SENT, 0),
            "email_failed_count": failed_email_count,
            "collection_trend_labels": month_labels,
            "collection_trend_values": monthly_collection_values,
            "outstanding_vs_collected_labels": ["Collected", "Outstanding", "Refunded"],
            "outstanding_vs_collected_values": [
                _to_float(collected_total),
                _to_float(invoice_outstanding),
                _to_float(refunded_total),
            ],
            "payroll_trend_labels": month_labels,
            "payroll_trend_values": monthly_payroll_values,
            "collected_this_month": collected_this_month,
            "successful_payment_count_this_month": successful_payment_count_this_month,
            "payroll_cost_this_month": payroll_cost_this_month,
            "payroll_paid_employee_count": payroll_paid_employee_count,
            "overdue_invoice_amount": overdue_invoice_amount,
            "suspicious_activity_count": suspicious_activity_count,
            "security_alert_total": security_alert_total,
            "refunded_total": refunded_total,
            "import_issue_total": import_issue_total,
            "reporting_period_label": reporting_period_label,
            "report_generated_at": report_generated_at,
            "detail_report_links": detail_report_links,
            "secondary_summary_items": secondary_summary_items,
            "management_attention_items": management_attention_items,
            "collection_chart_summary": collection_chart_summary,
            "payroll_chart_summary": payroll_chart_summary,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def audit_log_list(request):
    selected_role = request.GET.get("role", "").strip()
    selected_action = request.GET.get("action", "").strip()
    search_query = request.GET.get("q", "").strip()
    selected_action_display = "" if selected_action in NOISY_AUDIT_ACTIONS else selected_action

    logs = (
        AuditLog.objects.select_related("user", "user__role_profile")
        .exclude(action__in=NOISY_AUDIT_ACTIONS)
        .order_by("-created_at")
    )
    if selected_role:
        logs = logs.filter(user__role_profile__role=selected_role)
    if selected_action:
        logs = logs.filter(action__icontains=selected_action)
    if search_query:
        logs = logs.filter(
            Q(user__username__icontains=search_query)
            | Q(target_type__icontains=search_query)
            | Q(target_id__icontains=search_query)
            | Q(metadata__icontains=search_query)
        )

    return render(
        request,
        "core/audit_log_list.html",
        {
            "logs": [
                {
                    "created_at": log.created_at,
                    "user": log.user,
                    "action": log.action,
                    "action_label": describe_audit_action(log.action),
                    "description": explain_audit_action(log.action),
                    "target_type": log.target_type,
                    "target_id": log.target_id,
                    "ip_address": log.ip_address,
                }
                for log in _safe_list(logs[:500])
            ],
            "role_choices": ROLE_CHOICES,
            "selected_role": selected_role,
            "selected_action": selected_action_display,
            "search_query": search_query,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def validation_error_list(request):
    selected_module = request.GET.get("module", "").strip()
    search_query = request.GET.get("q", "").strip()

    errors = ImportRowError.objects.select_related("import_job").order_by("-created_at", "-id")
    if selected_module:
        errors = errors.filter(import_job__module=selected_module)
    if search_query:
        errors = errors.filter(
            Q(import_job__source_file_name__icontains=search_query)
            | Q(field_name__icontains=search_query)
            | Q(error_message__icontains=search_query)
        )

    return render(
        request,
        "core/validation_error_list.html",
        {
            "errors": _safe_list(errors[:500]),
            "module_choices": ImportJob.MODULE_CHOICES,
            "selected_module": selected_module,
            "search_query": search_query,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def finance_console(request):
    return redirect("payroll-dashboard")
