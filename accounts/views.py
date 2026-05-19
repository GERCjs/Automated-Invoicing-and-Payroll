from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone

from core.audit import get_client_ip, log_event
from core.models import AuditLog
from notifications.models import EmailDeliveryLog, PaymentReminderSettings

from .forms import (
    AdminAccountCreationForm,
    LoginForm,
    ManagedAccountCreationForm,
    ManagedPasswordUpdateForm,
    ManagedRoleUpdateForm,
    MassEmailForm,
    PaymentReminderSettingsForm,
    RegistrationForm,
)
from .permissions import get_user_role, role_required
from .roles import ADMIN, CUSTOMER, ROLE_CHOICES, STAFF, SUPERADMIN

User = get_user_model()


class UserLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class UserLogoutView(LogoutView):
    next_page = reverse_lazy("login")


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.role_profile.role = STAFF
            user.role_profile.save(update_fields=["role", "updated_at"])
            log_event(
                action="auth.registered",
                user=user,
                target_type="user",
                target_id=str(user.id),
                metadata={"username": user.username, "role": user.role_profile.role},
                ip_address=get_client_ip(request),
            )
            login(request, user)
            messages.success(request, "Registration successful.")
            return redirect("dashboard")
    else:
        form = RegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


def _get_role_counts():
    rows = User.objects.values("role_profile__role").annotate(total=Count("id"))
    counts = {row["role_profile__role"]: row["total"] for row in rows}
    return {role: counts.get(role, 0) for role, _label in ROLE_CHOICES}


def _can_manage_target(actor, target_user):
    if actor == target_user:
        return False
    target_role = get_user_role(target_user)
    if target_role == CUSTOMER:
        return False
    if target_role == SUPERADMIN:
        return False
    if get_user_role(actor) == ADMIN and target_role == ADMIN:
        return False
    return True


def _dashboard_context(request, account_form=None, reminder_form=None, mass_email_form=None):
    selected_role = request.GET.get("role", "").strip()
    selected_action = request.GET.get("action", "").strip()
    search_query = request.GET.get("q", "").strip()

    users = User.objects.select_related("role_profile").order_by("username")
    if selected_role:
        users = users.filter(role_profile__role=selected_role)
    if search_query:
        users = users.filter(
            Q(username__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
        )

    audit_logs = AuditLog.objects.select_related("user", "user__role_profile")
    if selected_role:
        audit_logs = audit_logs.filter(user__role_profile__role=selected_role)
    if selected_action:
        audit_logs = audit_logs.filter(action__icontains=selected_action)

    role_counts = _get_role_counts()
    role_count_cards = [
        {"role": role, "label": label, "count": role_counts.get(role, 0)}
        for role, label in ROLE_CHOICES
    ]
    last_seen = {
        row["user_id"]: row["last_activity"]
        for row in AuditLog.objects.exclude(user_id=None)
        .values("user_id")
        .annotate(last_activity=Max("created_at"))
    }
    users_with_activity = []
    for managed_user in users:
        managed_user.last_activity_at = last_seen.get(managed_user.id)
        managed_user.role_update_form = ManagedRoleUpdateForm(
            actor=request.user,
            target_user=managed_user,
            initial={"role": get_user_role(managed_user)},
        )
        managed_user.can_be_managed = _can_manage_target(request.user, managed_user)
        users_with_activity.append(managed_user)

    suspicious_since = timezone.now() - timezone.timedelta(days=7)
    suspicious_users = (
        User.objects.filter(
            Q(audit_logs__action="auth.permission_denied")
            | Q(audit_logs__action__icontains="failed"),
            audit_logs__created_at__gte=suspicious_since,
        )
        .select_related("role_profile")
        .annotate(flag_count=Count("audit_logs"))
        .order_by("-flag_count", "username")[:8]
    )

    reminder_settings = PaymentReminderSettings.load()
    return {
        "users": users_with_activity,
        "role_choices": ROLE_CHOICES,
        "role_counts": role_counts,
        "role_count_cards": role_count_cards,
        "total_users": User.objects.count(),
        "admin_count": role_counts.get(ADMIN, 0),
        "superadmin_count": role_counts.get(SUPERADMIN, 0),
        "suspicious_count": suspicious_users.count(),
        "email_count": EmailDeliveryLog.objects.count(),
        "suspicious_users": suspicious_users,
        "selected_role": selected_role,
        "selected_action": selected_action,
        "search_query": search_query,
        "account_form": account_form or ManagedAccountCreationForm(actor=request.user),
        "reminder_form": reminder_form or PaymentReminderSettingsForm(instance=reminder_settings),
        "mass_email_form": mass_email_form or MassEmailForm(role_counts=role_counts),
        "reminder_settings": reminder_settings,
    }


@login_required
@role_required(SUPERADMIN, ADMIN)
def admin_dashboard(request):
    log_event(
        action="admin.dashboard.viewed",
        user=request.user,
        metadata={"path": request.path},
        ip_address=get_client_ip(request),
    )
    return render(request, "accounts/admin_dashboard.html", _dashboard_context(request))


@login_required
@role_required(SUPERADMIN, ADMIN)
def managed_account_create(request):
    if request.method != "POST":
        return render(
            request,
            "accounts/managed_account_create.html",
            {"form": ManagedAccountCreationForm(actor=request.user)},
        )
    form = ManagedAccountCreationForm(request.POST, actor=request.user)
    if form.is_valid():
        user = form.save()
        log_event(
            action="admin.account.created",
            user=request.user,
            target_type="user",
            target_id=str(user.id),
            metadata={"username": user.username, "role": get_user_role(user)},
            ip_address=get_client_ip(request),
        )
        messages.success(request, f"Account {user.username} created.")
        return redirect("admin-dashboard")
    return render(request, "accounts/managed_account_create.html", {"form": form})


@login_required
@role_required(SUPERADMIN, ADMIN)
def managed_account_role_update(request, user_id):
    if request.method != "POST":
        return redirect("admin-dashboard")
    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    if not _can_manage_target(request.user, target_user):
        messages.error(request, "You cannot change this account's role.")
        return redirect("admin-dashboard")
    previous_role = get_user_role(target_user)
    form = ManagedRoleUpdateForm(request.POST, actor=request.user, target_user=target_user)
    if form.is_valid():
        target_user.role_profile.role = form.cleaned_data["role"]
        target_user.role_profile.save(update_fields=["role", "updated_at"])
        log_event(
            action="admin.account.role_changed",
            user=request.user,
            target_type="user",
            target_id=str(target_user.id),
            metadata={
                "username": target_user.username,
                "previous_role": previous_role,
                "new_role": form.cleaned_data["role"],
            },
            ip_address=get_client_ip(request),
        )
        messages.success(request, f"Updated role for {target_user.username}.")
    else:
        messages.error(request, "Role update failed. Please check the selected role.")
    return redirect("admin-dashboard")


@login_required
@role_required(SUPERADMIN, ADMIN)
def managed_account_password_update(request, user_id):
    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    if not _can_manage_target(request.user, target_user):
        messages.error(request, "You cannot update this account's password.")
        return redirect("admin-dashboard")
    if request.method == "POST":
        form = ManagedPasswordUpdateForm(target_user, request.POST)
        if form.is_valid():
            form.save()
            log_event(
                action="admin.account.password_updated",
                user=request.user,
                target_type="user",
                target_id=str(target_user.id),
                metadata={"username": target_user.username},
                ip_address=get_client_ip(request),
            )
            messages.success(request, f"Updated password for {target_user.username}.")
            return redirect("admin-dashboard")
    else:
        form = ManagedPasswordUpdateForm(target_user)
    return render(
        request,
        "accounts/admin_password_update.html",
        {"form": form, "target_user": target_user},
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def managed_account_delete(request, user_id):
    if request.method != "POST":
        return redirect("admin-dashboard")
    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    if not _can_manage_target(request.user, target_user):
        messages.error(request, "You cannot delete this account.")
        return redirect("admin-dashboard")
    username = target_user.username
    target_role = get_user_role(target_user)
    target_user.delete()
    log_event(
        action="admin.account.deleted",
        user=request.user,
        target_type="user",
        target_id=str(user_id),
        metadata={"username": username, "role": target_role},
        ip_address=get_client_ip(request),
    )
    messages.success(request, f"Deleted account {username}.")
    return redirect("admin-dashboard")


@login_required
@role_required(SUPERADMIN, ADMIN)
def payment_reminder_settings_update(request):
    reminder_settings = PaymentReminderSettings.load()
    if request.method != "POST":
        return render(
            request,
            "accounts/payment_reminder_settings.html",
            {"form": PaymentReminderSettingsForm(instance=reminder_settings)},
        )
    form = PaymentReminderSettingsForm(request.POST, instance=reminder_settings)
    if form.is_valid():
        settings_obj = form.save(commit=False)
        settings_obj.updated_by = request.user
        settings_obj.save()
        log_event(
            action="admin.payment_reminders.updated",
            user=request.user,
            target_type="payment_reminder_settings",
            target_id=str(settings_obj.id),
            metadata={
                "days_before_due": settings_obj.reminder_days_before_due,
                "overdue_enabled": settings_obj.overdue_reminders_enabled,
                "overdue_repeat_days": settings_obj.overdue_repeat_days,
                "mass_email_enabled": settings_obj.mass_email_enabled,
            },
            ip_address=get_client_ip(request),
        )
        messages.success(request, "Payment reminder settings updated.")
        return redirect("admin-dashboard")
    return render(request, "accounts/payment_reminder_settings.html", {"form": form})


@login_required
@role_required(SUPERADMIN, ADMIN)
def mass_email_send(request):
    role_counts = _get_role_counts()
    if request.method != "POST":
        return render(
            request,
            "accounts/mass_email.html",
            {"form": MassEmailForm(role_counts=role_counts)},
        )
    form = MassEmailForm(request.POST, role_counts=role_counts)
    reminder_settings = PaymentReminderSettings.load()
    if not reminder_settings.mass_email_enabled:
        messages.error(request, "Mass email sending is disabled in reminder settings.")
        return redirect("admin-dashboard")
    if form.is_valid():
        recipients = User.objects.filter(
            role_profile__role__in=form.cleaned_data["recipients"],
            email__isnull=False,
        ).exclude(email="")
        recipient_emails = list(recipients.values_list("email", flat=True))
        if not recipient_emails:
            messages.error(request, "No users with email addresses matched the selected roles.")
            return redirect("admin-dashboard")
        sent_count = send_mail(
            subject=form.cleaned_data["subject"],
            message=form.cleaned_data["message"],
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipient_emails,
            fail_silently=False,
        )
        EmailDeliveryLog.objects.create(
            recipient_email=settings.DEFAULT_FROM_EMAIL,
            subject=form.cleaned_data["subject"],
            template_key="admin_mass_email",
            status=EmailDeliveryLog.STATUS_SENT,
            related_object_type="user_role_group",
            related_object_id=",".join(form.cleaned_data["recipients"]),
            triggered_by=request.user,
            sent_at=timezone.now(),
            metadata={"recipient_count": len(recipient_emails), "sent_count": sent_count},
        )
        log_event(
            action="admin.mass_email.sent",
            user=request.user,
            metadata={
                "roles": form.cleaned_data["recipients"],
                "recipient_count": len(recipient_emails),
                "sent_count": sent_count,
            },
            ip_address=get_client_ip(request),
        )
        messages.success(request, f"Mass email queued for {len(recipient_emails)} recipient(s).")
        return redirect("admin-dashboard")
    return render(request, "accounts/mass_email.html", {"form": form})


@login_required
@role_required(SUPERADMIN, ADMIN)
def suspicious_activity_list(request):
    selected_role = request.GET.get("role", "").strip()
    search_query = request.GET.get("q", "").strip()
    selected_reason = request.GET.get("reason", "").strip()
    recent_since = timezone.now() - timezone.timedelta(days=7)

    logs = AuditLog.objects.select_related("user", "user__role_profile").filter(
        Q(action="auth.permission_denied") | Q(action__icontains="failed"),
        created_at__gte=recent_since,
    )
    if selected_role:
        logs = logs.filter(user__role_profile__role=selected_role)
    if selected_reason:
        logs = logs.filter(action__icontains=selected_reason)
    if search_query:
        logs = logs.filter(
            Q(user__username__icontains=search_query)
            | Q(user__email__icontains=search_query)
            | Q(target_type__icontains=search_query)
            | Q(target_id__icontains=search_query)
        )

    events = logs.order_by("-created_at")[:200]
    return render(
        request,
        "accounts/suspicious_activity_list.html",
        {
            "events": events,
            "role_choices": ROLE_CHOICES,
            "selected_role": selected_role,
            "selected_reason": selected_reason,
            "search_query": search_query,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def create_admin_account(request):
    if request.method == "POST":
        form = AdminAccountCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            log_event(
                action="auth.admin_account.created",
                user=request.user,
                target_type="user",
                target_id=str(user.id),
                metadata={"username": user.username, "role": user.role_profile.role},
                ip_address=get_client_ip(request),
            )
            messages.success(request, f"Admin account {user.username} created.")
            return redirect("create-admin-account")
    else:
        form = AdminAccountCreationForm()

    return render(request, "accounts/create_admin_account.html", {"form": form})
