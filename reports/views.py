from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from accounts.permissions import role_required
from accounts.roles import ADMIN, FINANCE, HR, ROLE_CHOICES, STAFF, SUPERADMIN
from core.audit import get_client_ip, log_event
from core.models import AuditLog
from invoicing.models import Invoice
from notifications.models import EmailDeliveryLog
from notifications.models import PaymentReminderSettings
from payments.models import PaymentRecord
from payroll.models import Employee, PayrollRecord
from payroll.services import cpf_for_2026


def _safe_sum(queryset, field_name):
    return queryset.aggregate(total=Sum(field_name))["total"] or 0


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


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def invoice_customer_report(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    invoice_queryset = Invoice.objects.select_related("customer")
    paid_invoices = invoice_queryset.filter(status=Invoice.STATUS_PAID)
    outstanding_invoices = invoice_queryset.filter(
        status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_SENT, Invoice.STATUS_VIEWED, Invoice.STATUS_OVERDUE]
    )

    total_amount_collected_month = _safe_sum(
        paid_invoices.filter(updated_at__date__gte=month_start, updated_at__date__lte=today),
        "total_amount",
    )
    total_amount_collected_year = _safe_sum(
        paid_invoices.filter(updated_at__date__gte=year_start, updated_at__date__lte=today),
        "total_amount",
    )
    outstanding_amount = _safe_sum(outstanding_invoices, "total_amount")

    draft_count = invoice_queryset.filter(status=Invoice.STATUS_DRAFT).count()
    pending_payment_count = invoice_queryset.filter(status=Invoice.STATUS_SENT).count()
    viewed_count = invoice_queryset.filter(status=Invoice.STATUS_VIEWED).count()
    overdue_count = invoice_queryset.filter(status=Invoice.STATUS_OVERDUE).count()
    paid_count = invoice_queryset.filter(status=Invoice.STATUS_PAID).count()

    status_summary = [
        {"label": "Draft", "count": draft_count},
        {"label": "Pending Payment", "count": pending_payment_count},
        {"label": "Viewed", "count": viewed_count},
        {"label": "Overdue", "count": overdue_count},
        {"label": "Paid", "count": paid_count},
    ]

    total_customers_with_invoices = (
        invoice_queryset.values("customer_id").distinct().count()
    )
    top_customers_by_total = list(
        invoice_queryset.values("customer__name", "customer__email")
        .annotate(
            invoice_count=Count("id"),
            total_amount=Sum("total_amount"),
        )
        .order_by("-total_amount", "customer__name")[:8]
    )
    customers_with_overdue = list(
        invoice_queryset.filter(status=Invoice.STATUS_OVERDUE)
        .values("customer__name", "customer__email")
        .annotate(
            overdue_invoice_count=Count("id"),
            overdue_amount=Sum("total_amount"),
        )
        .order_by("-overdue_invoice_count", "-overdue_amount", "customer__name")[:8]
    )

    recent_invoices_created = invoice_queryset.order_by("-created_at")[:10]
    recent_invoices_paid = paid_invoices.order_by("-updated_at")[:10]
    recent_invoice_emails_sent = EmailDeliveryLog.objects.filter(
        template_key="invoice_email_v1"
    ).order_by("-attempted_at")[:10]

    log_event(
        action="report.invoice_customer.viewed",
        user=request.user,
        metadata={"path": request.path},
        ip_address=get_client_ip(request),
    )

    return render(
        request,
        "reports/invoice_customer_report.html",
        {
            "today": today,
            "month_start": month_start,
            "year_start": year_start,
            "total_amount_collected_month": total_amount_collected_month,
            "total_amount_collected_year": total_amount_collected_year,
            "outstanding_amount": outstanding_amount,
            "overdue_count": overdue_count,
            "pending_payment_count": pending_payment_count,
            "paid_count": paid_count,
            "draft_count": draft_count,
            "status_summary": status_summary,
            "total_customers_with_invoices": total_customers_with_invoices,
            "top_customers_by_total": top_customers_by_total,
            "customers_with_overdue": customers_with_overdue,
            "recent_invoices_created": recent_invoices_created,
            "recent_invoices_paid": recent_invoices_paid,
            "recent_invoice_emails_sent": recent_invoice_emails_sent,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def admin_security_report(request):
    user_model = get_user_model()
    today = timezone.localdate()
    month_start = today.replace(day=1)

    users = user_model.objects.select_related("role_profile")
    total_users = users.count()
    users_by_role = [
        {"role": role, "label": label, "count": users.filter(role_profile__role=role).count()}
        for role, label in ROLE_CHOICES
    ]
    new_users_this_month = users.filter(date_joined__date__gte=month_start, date_joined__date__lte=today).count()
    active_users_count = users.filter(is_active=True, role_profile__suspended_at__isnull=True).count()
    suspended_or_inactive_users_count = users.filter(
        Q(is_active=False) | Q(role_profile__suspended_at__isnull=False)
    ).distinct().count()
    suspended_accounts_count = users.filter(role_profile__suspended_at__isnull=False).count()

    failed_login_attempts_count = AuditLog.objects.filter(action="auth.login.failed").count()
    suspicious_activity_count = AuditLog.objects.filter(
        Q(action="auth.permission_denied") | Q(action="auth.login.failed")
    ).count()
    recent_suspicious_activities = (
        AuditLog.objects.select_related("user", "user__role_profile")
        .filter(Q(action="auth.permission_denied") | Q(action="auth.login.failed"))
        .order_by("-created_at")[:10]
    )
    recent_login_related_logs = (
        AuditLog.objects.select_related("user", "user__role_profile")
        .filter(action__startswith="auth.login")
        .order_by("-created_at")[:10]
    )

    recent_account_creations = (
        AuditLog.objects.select_related("user", "user__role_profile")
        .filter(action__in=["admin.account.created", "auth.admin_account.created"])
        .order_by("-created_at")[:10]
    )
    recent_role_changes = (
        AuditLog.objects.select_related("user", "user__role_profile")
        .filter(action="admin.account.role_changed")
        .order_by("-created_at")[:10]
    )
    recent_password_changes = (
        AuditLog.objects.select_related("user", "user__role_profile")
        .filter(action="admin.account.password_updated")
        .order_by("-created_at")[:10]
    )
    recent_admin_actions = (
        AuditLog.objects.select_related("user", "user__role_profile")
        .filter(action__startswith="admin.")
        .order_by("-created_at")[:12]
    )

    reminder_settings = PaymentReminderSettings.load()
    reminder_email_logs = EmailDeliveryLog.objects.filter(template_key__startswith="payment_reminder_")
    reminder_emails_sent_count = reminder_email_logs.filter(status=EmailDeliveryLog.STATUS_SENT).count()
    recent_reminder_email_logs = reminder_email_logs.order_by("-attempted_at")[:10]

    log_event(
        action="report.admin_security.viewed",
        user=request.user,
        metadata={"path": request.path},
        ip_address=get_client_ip(request),
    )

    return render(
        request,
        "reports/admin_security_report.html",
        {
            "today": today,
            "month_start": month_start,
            "total_users": total_users,
            "users_by_role": users_by_role,
            "new_users_this_month": new_users_this_month,
            "active_users_count": active_users_count,
            "suspended_or_inactive_users_count": suspended_or_inactive_users_count,
            "suspended_accounts_count": suspended_accounts_count,
            "failed_login_attempts_count": failed_login_attempts_count,
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
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def payment_stripe_report(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    succeeded_payments = PaymentRecord.objects.filter(status=PaymentRecord.STATUS_SUCCEEDED)
    failed_cancelled_payments = PaymentRecord.objects.filter(
        status__in=[PaymentRecord.STATUS_FAILED, PaymentRecord.STATUS_CANCELLED]
    )
    refunded_payments = PaymentRecord.objects.filter(status=PaymentRecord.STATUS_REFUNDED)

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

    stripe_payments = PaymentRecord.objects.filter(provider=PaymentRecord.PROVIDER_STRIPE)
    stripe_status_summary = list(
        stripe_payments.values("status").annotate(total=Count("id")).order_by("status")
    )
    recent_stripe_transactions = stripe_payments.select_related("invoice", "invoice__customer").order_by(
        "-created_at"
    )[:8]

    recent_payments = (
        PaymentRecord.objects.select_related("invoice", "invoice__customer").order_by("-created_at")[:20]
    )

    stripe_total = stripe_payments.count()
    manual_total = PaymentRecord.objects.filter(provider=PaymentRecord.PROVIDER_MANUAL).count()

    payment_method_summary = [
        {
            "method": "Stripe",
            "available": True,
            "has_count": True,
            "count": stripe_total,
            "note": "Current integrated prototype method.",
        },
        {
            "method": "PayNow",
            "available": False,
            "has_count": False,
            "count": None,
            "note": "Processed within Stripe Checkout but not stored separately yet.",
        },
        {
            "method": "Credit card",
            "available": False,
            "has_count": False,
            "count": None,
            "note": "Processed within Stripe Checkout but not stored separately yet.",
        },
        {
            "method": "Bank transfer",
            "available": manual_total > 0,
            "has_count": True,
            "count": manual_total,
            "note": "Represented by provider=manual records.",
        },
    ]

    log_event(
        action="report.payment_stripe.viewed",
        user=request.user,
        metadata={"path": request.path},
        ip_address=get_client_ip(request),
    )

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
            "failed_cancelled_count": failed_cancelled_payments.count(),
            "refunded_count": refunded_payments.count(),
            "outstanding_amount": outstanding_amount,
            "stripe_total": stripe_total,
            "stripe_status_summary": stripe_status_summary,
            "recent_stripe_transactions": recent_stripe_transactions,
            "payment_method_summary": payment_method_summary,
            "recent_payments": recent_payments,
            "is_stripe_only_prototype": stripe_total > 0 and manual_total == 0,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, HR)
def payroll_report(request):
    today = timezone.localdate()
    selected_month = (request.GET.get("month") or "").strip()
    selected_month, month_start, month_end = _month_bounds(selected_month, today)
    year_start = today.replace(month=1, day=1)

    month_records = PayrollRecord.objects.filter(payment_date__gte=month_start, payment_date__lte=month_end)
    year_records = PayrollRecord.objects.filter(payment_date__gte=year_start, payment_date__lte=today)

    total_payroll_amount_month = _safe_sum(month_records, "basic_salary") + _safe_sum(month_records, "allowances")
    total_payroll_amount_year = _safe_sum(year_records, "basic_salary") + _safe_sum(year_records, "allowances")
    total_net_pay_month = _safe_sum(month_records, "net_salary")
    total_allowances_month = _safe_sum(month_records, "allowances")
    total_deductions_month = _safe_sum(month_records, "deductions")
    employee_cpf_total_month = _safe_sum(month_records, "cpf_contribution")
    employees_paid_month = month_records.values("employee_id").distinct().count()

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

    recent_payslips = PayrollRecord.objects.order_by("-created_at")[:10]
    pending_email_or_download_count = sum(1 for row in records_with_status if row["status"] == "Pending")
    staff_employee_codes = list(
        Employee.objects.filter(user__role_profile__role=STAFF).values_list("employee_code", flat=True)
    )
    staff_payslip_records_count = PayrollRecord.objects.filter(employee_id__in=staff_employee_codes).count()

    log_event(
        action="report.payroll.viewed",
        user=request.user,
        metadata={"path": request.path, "selected_month": selected_month},
        ip_address=get_client_ip(request),
    )

    return render(
        request,
        "reports/payroll_report.html",
        {
            "today": today,
            "selected_month": selected_month,
            "month_start": month_start,
            "month_end": month_end,
            "year_start": year_start,
            "total_payroll_amount_month": total_payroll_amount_month,
            "total_payroll_amount_year": total_payroll_amount_year,
            "employees_paid_month": employees_paid_month,
            "total_net_pay_month": total_net_pay_month,
            "total_allowances_month": total_allowances_month,
            "total_deductions_month": total_deductions_month,
            "employee_cpf_total_month": employee_cpf_total_month,
            "employer_cpf_total_month": employer_cpf_total_month,
            "total_cpf_month": employee_cpf_total_month + employer_cpf_total_month,
            "records_with_status": records_with_status,
            "recent_payslips": recent_payslips,
            "pending_email_or_download_count": pending_email_or_download_count,
            "staff_payslip_records_count": staff_payslip_records_count,
        },
    )
