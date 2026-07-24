from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.permissions import get_user_role, role_required
from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF, SUPERADMIN
from core.audit import get_client_ip, log_event

from .forms import (
    CustomerInvoiceSupportTicketForm,
    StaffPayslipSupportTicketForm,
    SupportTicketCreateForm,
    SupportTicketSettingsForm,
    SupportTicketUpdateForm,
)
from .models import SupportTicket, SupportTicketSettings, get_support_ticket_response_target_days
from .services import send_support_ticket_resolved_email


TICKET_HANDLER_ROLES = {SUPERADMIN, ADMIN, FINANCE, HR}
FINANCE_TICKET_ROLES = {SUPERADMIN, ADMIN, FINANCE}
INVOICE_PAYMENT_CATEGORIES = {
    SupportTicket.CATEGORY_INVOICE,
    SupportTicket.CATEGORY_PAYMENT,
}
SUPPORT_REQUEST_WRONG_ACCOUNT_MESSAGE = (
    "This support request belongs to another account. "
    "Please log out and sign in using the account that received the email."
)


def _safe_next_url(request):
    next_url = (request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ""


def _ticket_base_queryset():
    return SupportTicket.objects.select_related("created_by", "created_by__role_profile", "assigned_to")


def _default_assigned_role_for_category(category):
    if category in INVOICE_PAYMENT_CATEGORIES:
        return SupportTicket.ASSIGNED_ROLE_FINANCE
    if category == SupportTicket.CATEGORY_PAYROLL:
        return SupportTicket.ASSIGNED_ROLE_PAYROLL
    return ""


def _apply_assigned_status(ticket):
    if ticket.assigned_role and ticket.status == SupportTicket.STATUS_OPEN:
        ticket.status = SupportTicket.STATUS_IN_PROGRESS


def _is_response_target_breached(ticket, response_target_days):
    return not ticket.is_resolved and ticket.unresolved_age_days >= response_target_days


def _ticket_queryset_for(user):
    role = get_user_role(user)
    tickets = _ticket_base_queryset()
    if role in {SUPERADMIN, ADMIN}:
        return tickets
    if role == FINANCE:
        return tickets.filter(
            Q(category__in=INVOICE_PAYMENT_CATEGORIES)
            | Q(assigned_role=SupportTicket.ASSIGNED_ROLE_FINANCE)
            | Q(assigned_to=user)
            | Q(created_by=user)
        )
    if role == HR:
        return tickets.filter(
            Q(category=SupportTicket.CATEGORY_PAYROLL)
            | Q(assigned_role=SupportTicket.ASSIGNED_ROLE_PAYROLL)
            | Q(assigned_to=user)
            | Q(created_by=user)
        )
    return tickets.filter(created_by=user)


def _finance_ticket_queryset_for(user):
    role = get_user_role(user)
    if role == FINANCE:
        return _ticket_queryset_for(user)
    if role in {SUPERADMIN, ADMIN}:
        return _ticket_base_queryset().filter(
            Q(category__in=INVOICE_PAYMENT_CATEGORIES)
            | Q(assigned_role=SupportTicket.ASSIGNED_ROLE_FINANCE)
        )
    return _ticket_base_queryset().none()


def _customer_ticket_queryset_for(user):
    return _ticket_base_queryset().filter(created_by=user)


def _filter_ticket_queryset(request, tickets):
    selected_status = request.GET.get("status", "").strip()
    selected_category = request.GET.get("category", "").strip()
    selected_priority = request.GET.get("priority", "").strip()
    search_query = request.GET.get("q", "").strip()

    if selected_status:
        tickets = tickets.filter(status=selected_status)
    if selected_category:
        tickets = tickets.filter(category=selected_category)
    if selected_priority:
        tickets = tickets.filter(priority=selected_priority)
    if search_query:
        tickets = tickets.filter(
            Q(subject__icontains=search_query)
            | Q(message__icontains=search_query)
            | Q(related_reference__icontains=search_query)
            | Q(created_by__username__icontains=search_query)
            | Q(created_by__email__icontains=search_query)
            | Q(created_by__first_name__icontains=search_query)
            | Q(created_by__last_name__icontains=search_query)
        )

    return tickets, {
        "selected_status": selected_status,
        "selected_category": selected_category,
        "selected_priority": selected_priority,
        "search_query": search_query,
    }


def _build_ticket_list_context(
    request,
    tickets,
    *,
    page_title,
    page_subtitle,
    detail_url_name,
    show_requester_details,
    show_assignment,
):
    filtered_tickets, selected_filters = _filter_ticket_queryset(request, tickets)
    ticket_list = list(filtered_tickets.order_by("-created_at")[:500])
    response_target_days = get_support_ticket_response_target_days()
    for ticket in ticket_list:
        ticket.response_target_breached = _is_response_target_breached(ticket, response_target_days)
    sla_breached_count = sum(1 for ticket in ticket_list if ticket.response_target_breached)
    role = get_user_role(request.user)
    return_url = _safe_next_url(request)
    return {
        "tickets": ticket_list,
        "can_handle_tickets": role in TICKET_HANDLER_ROLES,
        "can_edit_support_settings": role in {SUPERADMIN, ADMIN},
        "support_ticket_sla_days": response_target_days,
        "sla_breached_count": sla_breached_count,
        "status_choices": SupportTicket.STATUS_CHOICES,
        "category_choices": SupportTicket.CATEGORY_CHOICES,
        "priority_choices": SupportTicket.PRIORITY_CHOICES,
        "detail_url_name": detail_url_name,
        "page_title": page_title,
        "page_subtitle": page_subtitle,
        "show_requester_details": show_requester_details,
        "show_assignment": show_assignment,
        "back_url": return_url or _ticket_list_back_url_for(request.user),
        "return_url": return_url,
        **selected_filters,
    }


def _customer_invoice_queryset_for(user):
    from invoicing.models import Invoice

    user_email = (user.email or "").strip()
    if not user_email:
        return Invoice.objects.none()
    return Invoice.objects.filter(customer__email__iexact=user_email)


def _get_customer_invoice_or_404(user, invoice_id):
    return get_object_or_404(_customer_invoice_queryset_for(user), pk=invoice_id)


def _staff_payslip_queryset_for(user):
    from payroll.models import Employee, PayrollRecord

    employee = getattr(user, "employee_profile", None)
    if employee is None:
        user_email = (user.email or "").strip()
        email_matches = Employee.objects.filter(email__iexact=user_email) if user_email else Employee.objects.none()
        employee = email_matches.first() if email_matches.count() == 1 else None
    if employee is None:
        return PayrollRecord.objects.none()
    return PayrollRecord.objects.filter(employee_id=employee.employee_code)


def _get_staff_payslip_or_404(user, payslip_id):
    return get_object_or_404(_staff_payslip_queryset_for(user), pk=payslip_id)


def _validated_customer_invoice_reference(user, category, raw_invoice_id="", raw_reference=""):
    related_reference = (raw_reference or "").strip()[:100]
    if category not in INVOICE_PAYMENT_CATEGORIES:
        return related_reference

    invoice_queryset = _customer_invoice_queryset_for(user).only("id", "invoice_number")
    invoice = None
    if str(raw_invoice_id).isdigit():
        invoice = invoice_queryset.filter(pk=int(raw_invoice_id)).first()
    elif related_reference:
        invoice = invoice_queryset.filter(invoice_number=related_reference).first()
    if invoice is None:
        return ""
    return invoice.invoice_number


def _validated_staff_payslip_reference(user, category, raw_payslip_id="", raw_reference=""):
    related_reference = (raw_reference or "").strip()[:100]
    if category != SupportTicket.CATEGORY_PAYROLL:
        return related_reference

    payslip_queryset = _staff_payslip_queryset_for(user).only("id", "employee_id", "payment_date")
    payslip = None
    if str(raw_payslip_id).isdigit():
        payslip = payslip_queryset.filter(pk=int(raw_payslip_id)).first()
    elif related_reference:
        employee_id, _, payment_date = related_reference.partition(" / ")
        if employee_id.strip() and payment_date.strip():
            try:
                parsed_payment_date = date.fromisoformat(payment_date.strip())
            except ValueError:
                parsed_payment_date = None
            if parsed_payment_date is not None:
                payslip = payslip_queryset.filter(
                    employee_id=employee_id.strip(),
                    payment_date=parsed_payment_date,
                ).first()
    if payslip is None:
        return ""
    return f"{payslip.employee_id} / {payslip.payment_date:%Y-%m-%d}"


def _invoice_for_ticket_reference(ticket):
    if ticket.category not in INVOICE_PAYMENT_CATEGORIES:
        return None
    related_reference = (ticket.related_reference or "").strip()
    if not related_reference:
        return None

    from invoicing.models import Invoice

    return (
        Invoice.objects.select_related("customer")
        .filter(invoice_number=related_reference)
        .first()
    )


def _related_reference_url_for(user, ticket):
    invoice = _invoice_for_ticket_reference(ticket)
    if invoice is None:
        return ""

    role = get_user_role(user)
    if role in {SUPERADMIN, ADMIN, FINANCE}:
        return reverse("invoice-detail", args=[invoice.pk])
    if role == CUSTOMER:
        user_email = (user.email or "").strip()
        if user_email and invoice.customer.email.lower() == user_email.lower():
            return reverse("customer-invoice-detail", args=[invoice.pk])
    return ""


def _payslip_reference_url_for(user, ticket):
    if ticket.category != SupportTicket.CATEGORY_PAYROLL:
        return ""
    related_reference = (ticket.related_reference or "").strip()
    if not related_reference:
        return ""

    from payroll.models import PayrollRecord

    employee_id, _, payment_date = related_reference.partition(" / ")
    if not employee_id.strip() or not payment_date.strip():
        return ""
    try:
        parsed_payment_date = date.fromisoformat(payment_date.strip())
    except ValueError:
        return ""
    payslip = PayrollRecord.objects.filter(employee_id=employee_id.strip(), payment_date=parsed_payment_date).first()
    if payslip is None:
        return ""

    role = get_user_role(user)
    if role in {SUPERADMIN, ADMIN, HR}:
        return reverse("payroll-detail", args=[payslip.pk])
    if role == STAFF and _staff_payslip_queryset_for(user).filter(pk=payslip.pk).exists():
        return reverse("payslip-pdf-download", args=[payslip.pk])
    return ""


def _reference_url_for(user, ticket):
    return _related_reference_url_for(user, ticket) or _payslip_reference_url_for(user, ticket)


def _can_manage_ticket(user, ticket):
    role = get_user_role(user)
    if role in {SUPERADMIN, ADMIN}:
        return True
    if ticket.assigned_to_id == user.id:
        return True
    if role == FINANCE and ticket.assigned_role == SupportTicket.ASSIGNED_ROLE_FINANCE:
        return True
    if role == HR and ticket.assigned_role == SupportTicket.ASSIGNED_ROLE_PAYROLL:
        return True
    if role == FINANCE and ticket.category in INVOICE_PAYMENT_CATEGORIES:
        return True
    if role == HR and ticket.category == SupportTicket.CATEGORY_PAYROLL:
        return True
    return False


def _wrong_support_request_account_response(request, ticket_id):
    log_event(
        action="auth.permission_denied",
        user=request.user if request.user.is_authenticated else None,
        target_type="support_ticket",
        target_id=str(ticket_id),
        metadata={
            "path": request.path,
            "reason": "support_ticket_wrong_requester_account",
        },
        ip_address=get_client_ip(request),
    )
    return render(
        request,
        "403.html",
        {"permission_message": SUPPORT_REQUEST_WRONG_ACCOUNT_MESSAGE},
        status=403,
    )


def _internal_ticket_back_url_for(user):
    if get_user_role(user) == FINANCE:
        return reverse("finance-support-ticket-list")
    return reverse("support-ticket-list")


def _ticket_list_back_url_for(user):
    role = get_user_role(user)
    if role in {SUPERADMIN, ADMIN}:
        return reverse("admin-dashboard")
    if role == FINANCE:
        return reverse("invoice-dashboard")
    if role == HR:
        return reverse("payroll-dashboard")
    if role == CUSTOMER:
        return reverse("customer-invoice-dashboard")
    if role == STAFF:
        return reverse("my-payslips")
    return ""


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
def support_ticket_list(request):
    response_target_days = get_support_ticket_response_target_days()
    return render(
        request,
        "support/ticket_list.html",
        _build_ticket_list_context(
            request,
            _ticket_queryset_for(request.user),
            page_title="Support Tickets",
            page_subtitle=(
                "Track invoice, payment, payroll, and account support requests. "
                f"Tickets open for {response_target_days} days are highlighted."
            ),
            detail_url_name="support-ticket-detail",
            show_requester_details=True,
            show_assignment=True,
        ),
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE)
def finance_support_ticket_list(request):
    return render(
        request,
        "support/ticket_list.html",
        _build_ticket_list_context(
            request,
            _finance_ticket_queryset_for(request.user),
            page_title="Support Tickets",
            page_subtitle=(
                "Finance can track invoice and payment support requests with requester details and response targets."
            ),
            detail_url_name="support-ticket-detail",
            show_requester_details=True,
            show_assignment=True,
        ),
    )


@login_required
@role_required(CUSTOMER, STAFF)
def customer_support_ticket_list(request):
    return render(
        request,
        "support/ticket_list.html",
        _build_ticket_list_context(
            request,
            _customer_ticket_queryset_for(request.user),
            page_title="My Support Requests",
            page_subtitle="Review the support requests you submitted and any resolution notes from the support team.",
            detail_url_name="customer-support-ticket-detail",
            show_requester_details=False,
            show_assignment=False,
        ),
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
def support_ticket_create(request):
    role = get_user_role(request.user)
    if request.method == "POST":
        form = SupportTicketCreateForm(request.POST, actor_role=role)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.created_by = request.user
            ticket.assigned_role = _default_assigned_role_for_category(ticket.category)
            _apply_assigned_status(ticket)
            ticket.save()
            log_event(
                action="support.ticket.created",
                user=request.user,
                target_type="support_ticket",
                target_id=str(ticket.id),
                metadata={
                    "category": ticket.category,
                    "priority": ticket.priority,
                    "status": ticket.status,
                    "related_reference": ticket.related_reference,
                    "assigned_role": ticket.assigned_role,
                },
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Support request submitted.")
            return redirect("support-ticket-detail", ticket_id=ticket.id)
    else:
        form = SupportTicketCreateForm(actor_role=role)
    return render(request, "support/ticket_form.html", {"form": form})


@login_required
@role_required(CUSTOMER)
def customer_invoice_support_ticket_create(request, invoice_id):
    invoice = _get_customer_invoice_or_404(request.user, invoice_id)

    if request.method == "POST":
        form = CustomerInvoiceSupportTicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.category = SupportTicket.CATEGORY_INVOICE
            ticket.related_reference = invoice.invoice_number
            ticket.created_by = request.user
            ticket.assigned_role = SupportTicket.ASSIGNED_ROLE_FINANCE
            _apply_assigned_status(ticket)
            ticket.save()
            log_event(
                action="support.ticket.created",
                user=request.user,
                target_type="support_ticket",
                target_id=str(ticket.id),
                metadata={
                    "category": ticket.category,
                    "priority": ticket.priority,
                    "status": ticket.status,
                    "related_reference": ticket.related_reference,
                    "assigned_role": ticket.assigned_role,
                    "source": "customer_invoice_detail",
                },
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Support request submitted.")
            return redirect("customer-support-ticket-detail", ticket_id=ticket.id)
    else:
        form = CustomerInvoiceSupportTicketForm(
            initial={"subject": f"Question about invoice {invoice.invoice_number}"}
        )

    return render(
        request,
        "support/customer_invoice_ticket_form.html",
        {
            "form": form,
            "invoice": invoice,
        },
    )


@login_required
@role_required(STAFF)
def staff_payslip_support_ticket_create(request, payslip_id):
    payslip = _get_staff_payslip_or_404(request.user, payslip_id)
    related_reference = f"{payslip.employee_id} / {payslip.payment_date:%Y-%m-%d}"

    if request.method == "POST":
        form = StaffPayslipSupportTicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.category = SupportTicket.CATEGORY_PAYROLL
            ticket.related_reference = related_reference
            ticket.created_by = request.user
            ticket.assigned_role = SupportTicket.ASSIGNED_ROLE_PAYROLL
            _apply_assigned_status(ticket)
            ticket.save()
            log_event(
                action="support.ticket.created",
                user=request.user,
                target_type="support_ticket",
                target_id=str(ticket.id),
                metadata={
                    "category": ticket.category,
                    "priority": ticket.priority,
                    "status": ticket.status,
                    "related_reference": ticket.related_reference,
                    "assigned_role": ticket.assigned_role,
                    "source": "staff_payslip_list",
                },
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Support request submitted.")
            return redirect("customer-support-ticket-detail", ticket_id=ticket.id)
    else:
        form = StaffPayslipSupportTicketForm(
            initial={"subject": f"Question about payslip {related_reference}"}
        )

    return render(
        request,
        "support/staff_payslip_ticket_form.html",
        {
            "form": form,
            "payslip": payslip,
            "related_reference": related_reference,
        },
    )


@login_required
@require_POST
@role_required(STAFF, CUSTOMER)
def support_ticket_chat_create(request):
    role = get_user_role(request.user)
    message = request.POST.get("message", "").strip()
    if not message:
        return JsonResponse({"ok": False, "errors": {"message": ["Please enter a message."]}}, status=400)

    selected_category = request.POST.get("category", "").strip()
    if selected_category:
        allowed_categories = _allowed_chat_categories_for(role)
        if selected_category not in allowed_categories:
            return JsonResponse({"ok": False, "errors": {"category": ["Invalid support category."]}}, status=400)
        category = selected_category
    else:
        category = _chat_category_for(role, message)

    issue_label = request.POST.get("issue_label", "").strip()[:80]
    related_reference = request.POST.get("related_reference", "").strip()[:100]
    if role == CUSTOMER:
        related_reference = _validated_customer_invoice_reference(
            request.user,
            category,
            raw_invoice_id=request.POST.get("invoice_id", ""),
            raw_reference=related_reference,
        )
        if category in INVOICE_PAYMENT_CATEGORIES and not related_reference:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "related_reference": ["Select one of your invoices before sending this request."],
                    },
                },
                status=400,
            )
    elif role == STAFF:
        related_reference = _validated_staff_payslip_reference(
            request.user,
            category,
            raw_payslip_id=request.POST.get("invoice_id", ""),
            raw_reference=related_reference,
        )
        if category == SupportTicket.CATEGORY_PAYROLL and not related_reference:
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {
                        "related_reference": ["Select one of your payslips before sending this request."],
                    },
                },
                status=400,
            )

    subject = _chat_subject_from(message, issue_label, related_reference)
    priority = _chat_priority_for(category, issue_label, message)
    ticket = SupportTicket.objects.create(
        category=category,
        subject=subject,
        message=message,
        priority=priority,
        related_reference=related_reference,
        created_by=request.user,
        assigned_role=_default_assigned_role_for_category(category),
    )
    _apply_assigned_status(ticket)
    if ticket.status == SupportTicket.STATUS_IN_PROGRESS:
        ticket.save(update_fields=["status", "updated_at"])
    log_event(
        action="support.ticket.created",
        user=request.user,
        target_type="support_ticket",
        target_id=str(ticket.id),
        metadata={
            "category": ticket.category,
            "priority": ticket.priority,
            "status": ticket.status,
            "related_reference": ticket.related_reference,
            "assigned_role": ticket.assigned_role,
            "issue_label": issue_label,
            "source": "chat_widget",
        },
        ip_address=get_client_ip(request),
    )
    return JsonResponse(
        {
            "ok": True,
            "ticket_id": ticket.id,
            "message": "Your support request has been sent. Our team will follow up from the ticket.",
        }
    )


def _allowed_chat_categories_for(role):
    if role == STAFF:
        return {
            SupportTicket.CATEGORY_PAYROLL,
            SupportTicket.CATEGORY_ACCOUNT,
            SupportTicket.CATEGORY_OTHER,
        }
    return {
        SupportTicket.CATEGORY_INVOICE,
        SupportTicket.CATEGORY_PAYMENT,
        SupportTicket.CATEGORY_ACCOUNT,
        SupportTicket.CATEGORY_OTHER,
    }


def _chat_category_for(role, message):
    normalized = message.lower()
    if role == STAFF:
        if any(keyword in normalized for keyword in ["account", "login", "password", "profile"]):
            return SupportTicket.CATEGORY_ACCOUNT
        return SupportTicket.CATEGORY_PAYROLL
    if any(keyword in normalized for keyword in ["payment", "paid", "pay", "card", "stripe", "receipt"]):
        return SupportTicket.CATEGORY_PAYMENT
    if any(keyword in normalized for keyword in ["account", "login", "password", "profile"]):
        return SupportTicket.CATEGORY_ACCOUNT
    if any(keyword in normalized for keyword in ["invoice", "bill", "amount", "overdue"]):
        return SupportTicket.CATEGORY_INVOICE
    return SupportTicket.CATEGORY_INVOICE


def _chat_subject_from(message, issue_label="", related_reference=""):
    if issue_label and related_reference:
        subject = f"{issue_label} - {related_reference}"
        return subject[:255]
    if issue_label:
        return issue_label[:255]
    first_line = message.splitlines()[0].strip()
    if len(first_line) <= 72:
        return first_line or "Support chat message"
    return f"{first_line[:69].rstrip()}..."


def _chat_priority_for(category, issue_label, message):
    normalized = f"{issue_label} {message}".lower()
    high_priority_keywords = [
        "did not receive my pay",
        "payment issue",
        "payment failed",
        "failed payment",
        "cannot login",
        "can't login",
        "unable to login",
        "password",
        "refund",
    ]
    if any(keyword in normalized for keyword in high_priority_keywords):
        return SupportTicket.PRIORITY_HIGH
    if category == SupportTicket.CATEGORY_PAYMENT:
        return SupportTicket.PRIORITY_HIGH
    return SupportTicket.PRIORITY_MEDIUM


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
def support_ticket_detail(request, ticket_id):
    ticket = get_object_or_404(_ticket_queryset_for(request.user), pk=ticket_id)
    role = get_user_role(request.user)
    can_manage = _can_manage_ticket(request.user, ticket)

    if request.method == "POST":
        if not can_manage:
            messages.error(request, "You cannot update this support ticket.")
            return redirect("support-ticket-detail", ticket_id=ticket.id)
        previous_status = ticket.status
        form = SupportTicketUpdateForm(request.POST, instance=ticket, actor_role=role)
        if form.is_valid():
            updated_ticket = form.save(commit=False)
            if updated_ticket.assigned_role:
                updated_ticket.assigned_to = None
            _apply_assigned_status(updated_ticket)
            updated_ticket.mark_resolution_timestamp()
            updated_ticket.save()
            resolution_email_status = ""
            resolution_email_log_id = ""
            requester_role = get_user_role(updated_ticket.created_by) if updated_ticket.created_by_id else None
            if (
                previous_status != SupportTicket.STATUS_RESOLVED
                and updated_ticket.status == SupportTicket.STATUS_RESOLVED
                and requester_role in {CUSTOMER, STAFF}
            ):
                ticket_url = request.build_absolute_uri(
                    reverse("customer-support-ticket-detail", args=[updated_ticket.id])
                )
                email_sent, email_log = send_support_ticket_resolved_email(
                    ticket=updated_ticket,
                    ticket_url=ticket_url,
                    triggered_by=request.user,
                )
                resolution_email_status = "sent" if email_sent else "failed"
                resolution_email_log_id = str(email_log.id)
            log_event(
                action="support.ticket.updated",
                user=request.user,
                target_type="support_ticket",
                target_id=str(ticket.id),
                metadata={
                    "previous_status": previous_status,
                    "new_status": updated_ticket.status,
                    "assigned_to_id": updated_ticket.assigned_to_id,
                    "assigned_role": updated_ticket.assigned_role,
                    "priority": updated_ticket.priority,
                    "resolution_email_status": resolution_email_status,
                    "resolution_email_log_id": resolution_email_log_id,
                },
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Support ticket updated.")
            return redirect("support-ticket-detail", ticket_id=ticket.id)
    else:
        form = SupportTicketUpdateForm(instance=ticket, actor_role=role) if can_manage else None

    response_target_days = get_support_ticket_response_target_days()
    ticket.response_target_breached = _is_response_target_breached(ticket, response_target_days)
    return render(
        request,
        "support/ticket_detail.html",
        {
            "ticket": ticket,
            "form": form,
            "can_manage": can_manage,
            "detail_page_title": ticket.subject,
            "support_ticket_sla_days": response_target_days,
            "related_reference_url": _reference_url_for(request.user, ticket),
            "back_url": _safe_next_url(request) or _internal_ticket_back_url_for(request.user),
            "back_label": "Back",
            "show_requester_details": True,
        },
    )


@login_required
def customer_support_ticket_detail(request, ticket_id):
    role = get_user_role(request.user)
    if role not in {CUSTOMER, STAFF}:
        return _wrong_support_request_account_response(request, ticket_id)

    ticket = _ticket_base_queryset().filter(pk=ticket_id).first()
    if ticket is None or ticket.created_by_id != request.user.id:
        return _wrong_support_request_account_response(request, ticket_id)

    response_target_days = get_support_ticket_response_target_days()
    ticket.response_target_breached = _is_response_target_breached(ticket, response_target_days)
    return render(
        request,
        "support/ticket_detail.html",
        {
            "ticket": ticket,
            "form": None,
            "can_manage": False,
            "detail_page_title": "Support Request",
            "support_ticket_sla_days": response_target_days,
            "related_reference_url": _reference_url_for(request.user, ticket),
            "back_url": _safe_next_url(request) or reverse("customer-support-ticket-list"),
            "back_label": "Back",
            "show_requester_details": False,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def support_ticket_settings_update(request):
    support_settings = SupportTicketSettings.load()
    back_url = reverse("support-ticket-list")
    if request.method == "POST":
        form = SupportTicketSettingsForm(request.POST, instance=support_settings)
        if form.is_valid():
            settings_obj = form.save(commit=False)
            settings_obj.updated_by = request.user
            settings_obj.save()
            log_event(
                action="support.ticket_settings.updated",
                user=request.user,
                target_type="support_ticket_settings",
                target_id=str(settings_obj.id),
                metadata={"response_target_days": settings_obj.response_target_days},
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Support ticket settings updated.")
            return redirect(back_url)
    else:
        form = SupportTicketSettingsForm(instance=support_settings)

    return render(
        request,
        "support/settings.html",
        {
            "form": form,
            "back_url": back_url,
        },
    )
