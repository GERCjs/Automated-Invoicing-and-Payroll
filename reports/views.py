from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.shortcuts import render
from django.utils import timezone

from accounts.permissions import role_required
from accounts.roles import ADMIN, FINANCE, SUPERADMIN
from core.audit import get_client_ip, log_event
from invoicing.models import Invoice
from payments.models import PaymentRecord


def _safe_sum(queryset, field_name):
    return queryset.aggregate(total=Sum(field_name))["total"] or 0


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
