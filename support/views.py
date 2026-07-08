from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import get_user_role, role_required
from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF, SUPERADMIN
from core.audit import get_client_ip, log_event

from .forms import (
    CustomerInvoiceSupportTicketForm,
    SupportTicketCreateForm,
    SupportTicketUpdateForm,
)
from .models import SupportTicket


TICKET_HANDLER_ROLES = {SUPERADMIN, ADMIN, FINANCE, HR}
FINANCE_TICKET_ROLES = {SUPERADMIN, ADMIN, FINANCE}
INVOICE_PAYMENT_CATEGORIES = {
    SupportTicket.CATEGORY_INVOICE,
    SupportTicket.CATEGORY_PAYMENT,
}


def _ticket_base_queryset():
    return SupportTicket.objects.select_related("created_by", "created_by__role_profile", "assigned_to")


def _default_assigned_role_for_category(category):
    if category in INVOICE_PAYMENT_CATEGORIES:
        return SupportTicket.ASSIGNED_ROLE_FINANCE
    if category == SupportTicket.CATEGORY_PAYROLL:
        return SupportTicket.ASSIGNED_ROLE_PAYROLL
    return ""


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
    sla_breached_count = sum(1 for ticket in ticket_list if ticket.is_sla_breached)
    role = get_user_role(request.user)
    return {
        "tickets": ticket_list,
        "can_handle_tickets": role in TICKET_HANDLER_ROLES,
        "support_ticket_sla_days": settings.SUPPORT_TICKET_SLA_DAYS,
        "sla_breached_count": sla_breached_count,
        "status_choices": SupportTicket.STATUS_CHOICES,
        "category_choices": SupportTicket.CATEGORY_CHOICES,
        "priority_choices": SupportTicket.PRIORITY_CHOICES,
        "detail_url_name": detail_url_name,
        "page_title": page_title,
        "page_subtitle": page_subtitle,
        "show_requester_details": show_requester_details,
        "show_assignment": show_assignment,
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


def _internal_ticket_back_url_for(user):
    if get_user_role(user) == FINANCE:
        return reverse("finance-support-ticket-list")
    return reverse("support-ticket-list")


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR)
def support_ticket_list(request):
    return render(
        request,
        "support/ticket_list.html",
        _build_ticket_list_context(
            request,
            _ticket_queryset_for(request.user),
            page_title="Support Tickets",
            page_subtitle=(
                "Track invoice, payment, payroll, and account support requests. "
                f"Tickets open for {settings.SUPPORT_TICKET_SLA_DAYS} days are highlighted."
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
@role_required(CUSTOMER)
def customer_support_ticket_list(request):
    return render(
        request,
        "support/ticket_list.html",
        _build_ticket_list_context(
            request,
            _customer_ticket_queryset_for(request.user),
            page_title="My Support Requests",
            page_subtitle="Review the support requests you submitted and any resolution notes from Finance.",
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
        previous_assignee_id = ticket.assigned_to_id
        previous_assigned_role = ticket.assigned_role
        form = SupportTicketUpdateForm(request.POST, instance=ticket, actor_role=role)
        if form.is_valid():
            updated_ticket = form.save(commit=False)
            if role not in {SUPERADMIN, ADMIN}:
                updated_ticket.assigned_to_id = previous_assignee_id
                updated_ticket.assigned_role = previous_assigned_role
            elif updated_ticket.assigned_role:
                updated_ticket.assigned_to = None
            updated_ticket.mark_resolution_timestamp()
            updated_ticket.save()
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
                },
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Support ticket updated.")
            return redirect("support-ticket-detail", ticket_id=ticket.id)
    else:
        form = SupportTicketUpdateForm(instance=ticket, actor_role=role) if can_manage else None

    return render(
        request,
        "support/ticket_detail.html",
        {
            "ticket": ticket,
            "form": form,
            "can_manage": can_manage,
            "support_ticket_sla_days": settings.SUPPORT_TICKET_SLA_DAYS,
            "back_url": _internal_ticket_back_url_for(request.user),
            "back_label": "Back",
            "show_requester_details": True,
        },
    )


@login_required
@role_required(CUSTOMER)
def customer_support_ticket_detail(request, ticket_id):
    ticket = get_object_or_404(_customer_ticket_queryset_for(request.user), pk=ticket_id)
    return render(
        request,
        "support/ticket_detail.html",
        {
            "ticket": ticket,
            "form": None,
            "can_manage": False,
            "support_ticket_sla_days": settings.SUPPORT_TICKET_SLA_DAYS,
            "back_url": reverse("customer-support-ticket-list"),
            "back_label": "Back to My Support Requests",
            "show_requester_details": False,
        },
    )
