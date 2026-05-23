from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.permissions import get_user_role, role_required
from accounts.roles import ADMIN, FINANCE, HR, ROLE_CHOICES, SUPERADMIN
from imports.models import ImportJob, ImportRowError
from invoicing.models import Customer, Invoice
from notifications.models import EmailDeliveryLog
from payroll.models import Employee, PayrollBatch, PayrollEntry, PayrollRecord

from .audit import get_client_ip, log_event
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
    "report.payment_stripe.viewed": "Payment report viewed",
    "admin.dashboard.viewed": "Admin dashboard viewed",
    "admin.account.created": "Account created",
    "admin.account.role_changed": "User role changed",
    "admin.account.password_updated": "Password updated",
    "admin.account.deleted": "Account deleted",
    "admin.payment_reminders.updated": "Reminder settings updated",
    "admin.payment_reminders.run_check": "Reminder check executed",
    "admin.mass_email.sent": "Mass email sent",
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
        "report.payment_stripe.viewed": "An authorized user opened the Payment and Stripe report.",
        "admin.dashboard.viewed": "An admin opened the admin dashboard.",
        "admin.account.created": "An admin created a user account.",
        "admin.account.role_changed": "An admin changed a user's role.",
        "admin.account.password_updated": "An admin reset a user's password.",
        "admin.account.deleted": "An admin deleted a user account.",
        "admin.payment_reminders.updated": "An admin updated reminder settings.",
        "admin.payment_reminders.run_check": "An admin ran the reminder check job.",
        "admin.mass_email.sent": "An admin sent a mass email.",
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


def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return redirect("login")


@login_required
def dashboard(request):
    role = get_user_role(request.user)
    can_view_admin_stats = role in {SUPERADMIN, ADMIN}
    can_view_invoice_stats = role in {SUPERADMIN, ADMIN, FINANCE}
    can_view_payroll_stats = role in {SUPERADMIN, ADMIN, HR}

    recent_since = timezone.now() - timezone.timedelta(days=7)
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
    recent_import_errors = _safe_list(ImportRowError.objects.select_related("import_job").order_by("-created_at")[:6])

    email_status_counts = _safe_group_counts(EmailDeliveryLog.objects.all(), "status")

    log_event(
        action="core.dashboard.viewed",
        user=request.user,
        metadata={"path": request.path},
        ip_address=get_client_ip(request),
    )
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
            "import_failed_count": import_status_counts.get(ImportJob.STATUS_FAILED, 0),
            "import_with_errors_count": import_status_counts.get(ImportJob.STATUS_COMPLETED_WITH_ERRORS, 0),
            "import_error_total": _safe_count(ImportRowError.objects.all()),
            "recent_import_errors": recent_import_errors,
            "email_sent_count": email_status_counts.get(EmailDeliveryLog.STATUS_SENT, 0),
            "email_failed_count": email_status_counts.get(EmailDeliveryLog.STATUS_FAILED, 0),
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def audit_log_list(request):
    selected_role = request.GET.get("role", "").strip()
    selected_action = request.GET.get("action", "").strip()
    search_query = request.GET.get("q", "").strip()

    logs = AuditLog.objects.select_related("user", "user__role_profile").order_by("-created_at")
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
                    "description": explain_audit_action(log.action),
                    "target_type": log.target_type,
                    "target_id": log.target_id,
                    "ip_address": log.ip_address,
                }
                for log in _safe_list(logs[:200])
            ],
            "role_choices": ROLE_CHOICES,
            "selected_role": selected_role,
            "selected_action": selected_action,
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
            "errors": _safe_list(errors[:200]),
            "module_choices": ImportJob.MODULE_CHOICES,
            "selected_module": selected_module,
            "search_query": search_query,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def finance_console(request):
    log_event(
        action="core.finance_console.viewed",
        user=request.user,
        metadata={"path": request.path},
        ip_address=get_client_ip(request),
    )
    return render(request, "core/finance_console.html")
