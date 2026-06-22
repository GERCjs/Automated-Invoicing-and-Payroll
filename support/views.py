from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import get_user_role, role_required
from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF, SUPERADMIN
from core.audit import get_client_ip, log_event

from .forms import SupportTicketCreateForm, SupportTicketUpdateForm
from .models import SupportTicket


TICKET_HANDLER_ROLES = {SUPERADMIN, ADMIN, FINANCE, HR}


def _ticket_queryset_for(user):
    role = get_user_role(user)
    tickets = SupportTicket.objects.select_related("created_by", "assigned_to")
    if role in {SUPERADMIN, ADMIN}:
        return tickets
    if role == FINANCE:
        return tickets.filter(
            Q(category__in=[SupportTicket.CATEGORY_INVOICE, SupportTicket.CATEGORY_PAYMENT])
            | Q(assigned_to=user)
            | Q(created_by=user)
        )
    if role == HR:
        return tickets.filter(
            Q(category=SupportTicket.CATEGORY_PAYROLL)
            | Q(assigned_to=user)
            | Q(created_by=user)
        )
    return tickets.filter(created_by=user)


def _can_manage_ticket(user, ticket):
    role = get_user_role(user)
    if role in {SUPERADMIN, ADMIN}:
        return True
    if ticket.assigned_to_id == user.id:
        return True
    if role == FINANCE and ticket.category in {SupportTicket.CATEGORY_INVOICE, SupportTicket.CATEGORY_PAYMENT}:
        return True
    if role == HR and ticket.category == SupportTicket.CATEGORY_PAYROLL:
        return True
    return False


@login_required
def support_ticket_list(request):
    role = get_user_role(request.user)
    selected_status = request.GET.get("status", "").strip()
    selected_category = request.GET.get("category", "").strip()
    selected_priority = request.GET.get("priority", "").strip()
    search_query = request.GET.get("q", "").strip()

    tickets = _ticket_queryset_for(request.user)
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
        )

    return render(
        request,
        "support/ticket_list.html",
        {
            "tickets": tickets.order_by("-created_at")[:500],
            "can_handle_tickets": role in TICKET_HANDLER_ROLES,
            "selected_status": selected_status,
            "selected_category": selected_category,
            "selected_priority": selected_priority,
            "search_query": search_query,
            "status_choices": SupportTicket.STATUS_CHOICES,
            "category_choices": SupportTicket.CATEGORY_CHOICES,
            "priority_choices": SupportTicket.PRIORITY_CHOICES,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN, FINANCE, HR, STAFF, CUSTOMER)
def support_ticket_create(request):
    role = get_user_role(request.user)
    if request.method == "POST":
        form = SupportTicketCreateForm(request.POST, actor_role=role)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.created_by = request.user
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
                },
                ip_address=get_client_ip(request),
            )
            messages.success(request, "Support request submitted.")
            return redirect("support-ticket-detail", ticket_id=ticket.id)
    else:
        form = SupportTicketCreateForm(actor_role=role)
    return render(request, "support/ticket_form.html", {"form": form})


@login_required
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
        form = SupportTicketUpdateForm(request.POST, instance=ticket, actor_role=role)
        if form.is_valid():
            updated_ticket = form.save(commit=False)
            if role not in {SUPERADMIN, ADMIN}:
                updated_ticket.assigned_to_id = previous_assignee_id
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
        },
    )
