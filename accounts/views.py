from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from core.audit import get_client_ip, log_event
from core.models import AuditLog
from notifications.models import EmailDeliveryLog, PaymentReminderSettings
from notifications.services import run_payment_reminder_check

from .forms import (
    AdminAccountCreationForm,
    LoginSecurityPolicyForm,
    LoginForm,
    ManagedAccountCreationForm,
    ManagedPasswordUpdateForm,
    ManagedRoleUpdateForm,
    MassEmailForm,
    PaymentReminderSettingsForm,
    RegistrationForm,
)
from .models import EmailVerificationToken, LoginSecurityPolicy
from .permissions import get_user_role, role_required
from .roles import ADMIN, CUSTOMER, ROLE_CHOICES, STAFF, SUPERADMIN

User = get_user_model()
# Template key used when saving verification email logs.
VERIFICATION_EMAIL_TEMPLATE_KEY = "account_verification_email_v1"


def _send_verification_email(request, user, *, triggered_by=None):
    # Create a new verification token for this user.
    verification = EmailVerificationToken.issue_for_user(user)
    # Build the full clickable verification URL.
    verify_url = request.build_absolute_uri(reverse("verify-email", args=[verification.token]))
    subject = "Verify your account"
    body = (
        "Welcome to Automated Invoicing & Payroll.\n\n"
        "Please verify your account by clicking this link:\n"
        f"{verify_url}\n\n"
        "This link expires in 48 hours."
    )
    # Save an email log before sending so failures are still recorded.
    recipient = (user.email or "").strip().lower()
    email_log = EmailDeliveryLog.objects.create(
        recipient_email=recipient,
        subject=subject,
        template_key=VERIFICATION_EMAIL_TEMPLATE_KEY,
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="user",
        related_object_id=str(user.id),
        triggered_by=triggered_by,
        metadata={
            "username": user.username,
            "role": get_user_role(user),
            "verification_token_id": verification.id,
        },
    )

    if not recipient:
        # Cannot send email when the user has no email address.
        email_log.status = EmailDeliveryLog.STATUS_FAILED
        email_log.error_message = "User email address is missing."
        email_log.save(update_fields=["status", "error_message"])
        return False, verification, email_log

    try:
        # Send the actual verification email.
        sent_count = send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )
        if sent_count < 1:
            raise RuntimeError("Email backend returned zero deliveries.")
    except Exception as exc:
        # Record email sending failure.
        email_log.status = EmailDeliveryLog.STATUS_FAILED
        email_log.error_message = str(exc)
        email_log.save(update_fields=["status", "error_message"])
        return False, verification, email_log

    # Mark the email log as sent after successful delivery.
    email_log.status = EmailDeliveryLog.STATUS_SENT
    email_log.sent_at = timezone.now()
    email_log.save(update_fields=["status", "sent_at"])
    return True, verification, email_log


def _safe_email_error_message(exc):
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    return message[:500]


def _collect_announcement_recipient_emails(selected_roles):
    recipients = User.objects.filter(role_profile__role__in=selected_roles).order_by("id")
    recipient_emails = []
    seen = set()
    inactive_count = 0
    suspended_count = 0
    blank_count = 0
    duplicate_count = 0

    for recipient in recipients.values("email", "is_active", "role_profile__suspended_at"):
        if recipient["role_profile__suspended_at"] is not None:
            suspended_count += 1
            continue
        if not recipient["is_active"]:
            inactive_count += 1
            continue

        email = (recipient["email"] or "").strip().lower()
        if not email:
            blank_count += 1
            continue
        if email in seen:
            duplicate_count += 1
            continue

        seen.add(email)
        recipient_emails.append(email)

    skipped_count = inactive_count + suspended_count + blank_count + duplicate_count
    return recipient_emails, skipped_count


def _send_announcement_email(*, subject, body, recipient, selected_roles, triggered_by):
    email_log = EmailDeliveryLog.objects.create(
        recipient_email=recipient,
        subject=subject,
        template_key="admin_mass_email",
        status=EmailDeliveryLog.STATUS_PENDING,
        related_object_type="user_role_group",
        related_object_id=",".join(selected_roles),
        triggered_by=triggered_by,
        metadata={"selected_roles": list(selected_roles)},
    )

    try:
        sent_count = send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )
        if sent_count < 1:
            raise RuntimeError("Email backend returned zero deliveries.")
    except Exception as exc:
        email_log.status = EmailDeliveryLog.STATUS_FAILED
        email_log.error_message = _safe_email_error_message(exc)
        email_log.save(update_fields=["status", "error_message"])
        return False, email_log

    email_log.status = EmailDeliveryLog.STATUS_SENT
    email_log.sent_at = timezone.now()
    email_log.save(update_fields=["status", "sent_at"])
    return True, email_log


class UserLoginView(LoginView):
    # Login page that uses the custom LoginForm.
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    # Already-logged-in users are redirected instead of seeing the login page.
    redirect_authenticated_user = True


class UserLogoutView(LogoutView):
    # After logout, send the user back to the login page.
    next_page = reverse_lazy("login")


def register(request):
    # Logged-in users do not need the registration page.
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        # Validate submitted registration data.
        form = RegistrationForm(request.POST)
        if form.is_valid():
            # Save the inactive user, assign role, and send verification email.
            user = form.save()
            role = form.get_registration_role()
            user.role_profile.role = role
            user.role_profile.save(update_fields=["role", "updated_at"])
            verification_email_sent, _verification, email_log = _send_verification_email(
                request,
                user,
                triggered_by=user,
            )
            # Audit the registration event.
            log_event(
                action="auth.registered",
                user=user,
                target_type="user",
                target_id=str(user.id),
                metadata={
                    "username": user.username,
                    "role": user.role_profile.role,
                    "verification_required": True,
                    "verification_email_sent": verification_email_sent,
                    "verification_email_log_id": email_log.id,
                },
                ip_address=get_client_ip(request),
            )
            if verification_email_sent:
                messages.success(request, "Registration successful. Please verify your email before logging in.")
            else:
                messages.warning(
                    request,
                    "Registration saved but verification email could not be sent. Please contact an administrator.",
                )
            return redirect("login")
    else:
        # Empty form for first page load.
        form = RegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


def verify_email(request, token):
    # Look up the verification token from the URL.
    verification = (
        EmailVerificationToken.objects.select_related("user", "user__role_profile")
        .filter(token=token)
        .first()
    )
    if verification is None:
        # Unknown token.
        messages.error(request, "Invalid verification link.")
        return redirect("login")
    if not verification.is_valid:
        # Token exists but is expired or already used.
        messages.error(request, "This verification link is expired or already used.")
        return redirect("login")

    # Activate the account and mark the token as used.
    user = verification.user
    user.is_active = True
    user.save(update_fields=["is_active"])
    verification.used_at = timezone.now()
    verification.save(update_fields=["used_at"])

    # Audit successful email verification.
    log_event(
        action="auth.email_verified",
        user=user,
        target_type="user",
        target_id=str(user.id),
        metadata={"username": user.username, "role": user.role_profile.role},
        ip_address=get_client_ip(request),
    )
    messages.success(request, "Email verified. You can now log in.")
    return redirect("login")


def _get_role_counts():
    # Count how many users are in each role.
    rows = User.objects.values("role_profile__role").annotate(total=Count("id"))
    counts = {row["role_profile__role"]: row["total"] for row in rows}
    return {role: counts.get(role, 0) for role, _label in ROLE_CHOICES}


def _can_manage_target(actor, target_user):
    # Prevent users from managing themselves.
    if actor == target_user:
        return False
    target_role = get_user_role(target_user)
    # Customer and SuperAdmin accounts are protected from this management flow.
    if target_role == CUSTOMER:
        return False
    if target_role == SUPERADMIN:
        return False
    # Admins cannot manage other Admin accounts.
    if get_user_role(actor) == ADMIN and target_role == ADMIN:
        return False
    return True


def _can_suspend_target(actor, target_user):
    # Prevent users from suspending themselves.
    if actor == target_user:
        return False
    target_role = get_user_role(target_user)
    # SuperAdmin accounts cannot be suspended through this flow.
    if target_role == SUPERADMIN:
        return False
    # Admins cannot suspend other Admin accounts.
    if get_user_role(actor) == ADMIN and target_role == ADMIN:
        return False
    return True


def _can_verify_target(actor, target_user):
    # Prevent users from manually verifying themselves.
    if actor == target_user:
        return False
    target_role = get_user_role(target_user)
    # SuperAdmin accounts are protected from this management flow.
    if target_role == SUPERADMIN:
        return False
    # Admins cannot verify other Admin accounts.
    if get_user_role(actor) == ADMIN and target_role == ADMIN:
        return False
    return True


def _dashboard_context(request, account_form=None, reminder_form=None, mass_email_form=None):
    # Collect filters from the admin dashboard URL.
    selected_role = request.GET.get("role", "").strip()
    selected_action = request.GET.get("action", "").strip()
    search_query = request.GET.get("q", "").strip()

    pending_verification_user_ids = set(
        EmailVerificationToken.objects.filter(used_at__isnull=True).values_list("user_id", flat=True)
    )

    # Start with all users, then apply filters/search.
    users = User.objects.select_related("role_profile").order_by("username")
    if selected_role == "suspended":
        users = users.filter(role_profile__suspended_at__isnull=False)
    elif selected_role == "unverified":
        users = users.filter(
            is_active=False,
            role_profile__suspended_at__isnull=True,
            id__in=pending_verification_user_ids,
        )
    elif selected_role:
        users = users.filter(role_profile__role=selected_role)
    if search_query:
        users = users.filter(
            Q(username__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
        )

    # Start with all audit logs, then apply filters/search.
    audit_logs = AuditLog.objects.select_related("user", "user__role_profile")
    if selected_role and selected_role not in {"suspended", "unverified"}:
        audit_logs = audit_logs.filter(user__role_profile__role=selected_role)
    if selected_action:
        audit_logs = audit_logs.filter(action__icontains=selected_action)

    # Build the summary cards shown on the admin dashboard.
    role_counts = _get_role_counts()
    role_count_cards = [
        {"role": role, "label": label, "count": role_counts.get(role, 0)}
        for role, label in ROLE_CHOICES
    ]
    suspended_count = User.objects.filter(role_profile__suspended_at__isnull=False).count()
    role_count_cards.append({"role": "suspended", "label": "Suspended", "count": suspended_count})
    # Find each user's most recent audit activity.
    last_seen = {
        row["user_id"]: row["last_activity"]
        for row in AuditLog.objects.exclude(user_id=None)
        .values("user_id")
        .annotate(last_activity=Max("created_at"))
    }
    users_with_activity = []
    for managed_user in users:
        # Attach extra values used directly by the dashboard template.
        managed_user.last_activity_at = last_seen.get(managed_user.id)
        managed_user.role_update_form = ManagedRoleUpdateForm(
            actor=request.user,
            target_user=managed_user,
            initial={"role": get_user_role(managed_user)},
        )
        managed_user.can_be_managed = _can_manage_target(request.user, managed_user)
        managed_user.can_be_suspended = _can_suspend_target(request.user, managed_user)
        managed_user.can_be_verified = _can_verify_target(request.user, managed_user)
        managed_user.is_suspended = managed_user.role_profile.is_suspended
        managed_user.has_pending_verification = (
            not managed_user.is_suspended
            and managed_user.id in pending_verification_user_ids
        )
        managed_user.is_unverified = (
            not managed_user.is_active
            and not managed_user.is_suspended
            and managed_user.has_pending_verification
        )
        users_with_activity.append(managed_user)

    policy_rows = []
    for role, label in ROLE_CHOICES:
        if role == SUPERADMIN:
            continue
        # Show one failed-login policy form per role.
        policy = LoginSecurityPolicy.objects.filter(role=role).first() or LoginSecurityPolicy(role=role)
        policy_rows.append(
            {
                "role": role,
                "label": label,
                "policy": policy,
                "form": LoginSecurityPolicyForm(instance=policy, prefix=f"policy_{role}"),
            }
        )

    # Find recent suspicious accounts based on denied permissions or failed login logs.
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

    # Return all data needed by the admin dashboard template.
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
        "login_security_policies": policy_rows,
    }


@login_required
@role_required(SUPERADMIN, ADMIN)
def admin_dashboard(request):
    # Main admin dashboard page.
    return render(request, "accounts/admin_dashboard.html", _dashboard_context(request))


@login_required
@role_required(SUPERADMIN, ADMIN)
def managed_account_create(request):
    # Show the account creation form on GET.
    if request.method != "POST":
        return render(
            request,
            "accounts/managed_account_create.html",
            {"form": ManagedAccountCreationForm(actor=request.user)},
        )
    form = ManagedAccountCreationForm(request.POST, actor=request.user)
    if form.is_valid():
        # Save the new managed account and audit the action.
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
    # Role changes must be submitted by POST.
    if request.method != "POST":
        return redirect("admin-dashboard")
    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    if not _can_manage_target(request.user, target_user):
        messages.error(request, "You cannot change this account's role.")
        return redirect("admin-dashboard")
    previous_role = get_user_role(target_user)
    form = ManagedRoleUpdateForm(request.POST, actor=request.user, target_user=target_user)
    if form.is_valid():
        # Save the new role and audit the old/new values.
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
    # Admin password reset page for a managed user.
    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    if not _can_manage_target(request.user, target_user):
        messages.error(request, "You cannot update this account's password.")
        return redirect("admin-dashboard")
    if request.method == "POST":
        form = ManagedPasswordUpdateForm(target_user, request.POST)
        if form.is_valid():
            # Save the new password and audit the update.
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
        # Empty password form for first page load.
        form = ManagedPasswordUpdateForm(target_user)
    return render(
        request,
        "accounts/admin_password_update.html",
        {"form": form, "target_user": target_user},
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def managed_account_delete(request, user_id):
    # Deleting accounts must be submitted by POST.
    if request.method != "POST":
        return redirect("admin-dashboard")
    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    if not _can_manage_target(request.user, target_user):
        messages.error(request, "You cannot delete this account.")
        return redirect("admin-dashboard")
    username = target_user.username
    target_role = get_user_role(target_user)
    # Delete the user account, then log what was deleted.
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
def managed_account_suspend(request, user_id):
    # Suspending accounts must be submitted by POST.
    if request.method != "POST":
        return redirect("admin-dashboard")
    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    if not _can_suspend_target(request.user, target_user):
        messages.error(request, "You cannot suspend this account.")
        return redirect("admin-dashboard")
    if target_user.role_profile.is_suspended:
        messages.info(request, f"{target_user.username} is already suspended.")
        return redirect("admin-dashboard")

    reason = request.POST.get("reason", "")
    # Suspend the account, then write an audit log.
    target_user.role_profile.suspend(by=request.user, reason=reason)
    log_event(
        action="admin.account.suspended",
        user=request.user,
        target_type="user",
        target_id=str(target_user.id),
        metadata={
            "username": target_user.username,
            "role": get_user_role(target_user),
            "reason": reason.strip()[:255],
        },
        ip_address=get_client_ip(request),
    )
    messages.success(request, f"Suspended account {target_user.username}.")
    return redirect("admin-dashboard")


@login_required
@role_required(SUPERADMIN, ADMIN)
def managed_account_unsuspend(request, user_id):
    # Unsuspending accounts must be submitted by POST.
    if request.method != "POST":
        return redirect("admin-dashboard")
    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    if not _can_suspend_target(request.user, target_user):
        messages.error(request, "You cannot unsuspend this account.")
        return redirect("admin-dashboard")
    if not target_user.role_profile.is_suspended:
        messages.info(request, f"{target_user.username} is not suspended.")
        return redirect("admin-dashboard")

    # Unsuspend the account, then write an audit log.
    target_user.role_profile.unsuspend()
    log_event(
        action="admin.account.unsuspended",
        user=request.user,
        target_type="user",
        target_id=str(target_user.id),
        metadata={"username": target_user.username, "role": get_user_role(target_user)},
        ip_address=get_client_ip(request),
    )
    messages.success(request, f"Unsuspended account {target_user.username}.")
    return redirect("admin-dashboard")


@login_required
@role_required(SUPERADMIN, ADMIN)
def managed_account_verify(request, user_id):
    # Manual verification is an admin/testing override and must be submitted by POST.
    if request.method != "POST":
        return redirect("admin-dashboard")

    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    if not _can_verify_target(request.user, target_user):
        messages.error(request, "You cannot verify this account.")
        return redirect("admin-dashboard")
    if target_user.role_profile.is_suspended:
        messages.error(request, "Cannot verify a suspended account.")
        return redirect("admin-dashboard")

    pending_tokens = EmailVerificationToken.objects.filter(
        user=target_user,
        used_at__isnull=True,
    )
    if target_user.is_active and not pending_tokens.exists():
        messages.info(request, f"{target_user.username} is already verified.")
        return redirect("admin-dashboard")

    now = timezone.now()
    was_active = target_user.is_active
    token_count = pending_tokens.update(used_at=now)
    if not target_user.is_active:
        target_user.is_active = True
        target_user.save(update_fields=["is_active"])

    log_event(
        action="admin.account.manually_verified",
        user=request.user,
        target_type="user",
        target_id=str(target_user.id),
        metadata={
            "username": target_user.username,
            "role": get_user_role(target_user),
            "was_active": was_active,
            "pending_tokens_closed": token_count,
        },
        ip_address=get_client_ip(request),
    )
    messages.success(request, f"Verified account {target_user.username}.")
    return redirect("admin-dashboard")


@login_required
@role_required(SUPERADMIN, ADMIN)
def managed_account_resend_verification(request, user_id):
    # Resending verification emails must be submitted by POST.
    if request.method != "POST":
        return redirect("admin-dashboard")

    # Check whether the user still needs verification.
    target_user = get_object_or_404(User.objects.select_related("role_profile"), pk=user_id)
    has_pending_verification = EmailVerificationToken.objects.filter(
        user=target_user,
        used_at__isnull=True,
    ).exists()
    if target_user.is_active and not has_pending_verification:
        messages.info(request, f"{target_user.username} is already verified.")
        return redirect("admin-dashboard")
    if target_user.role_profile.is_suspended:
        messages.error(request, "Cannot resend verification email for a suspended account.")
        return redirect("admin-dashboard")

    # Send a new verification email and log success/failure.
    success, _verification, email_log = _send_verification_email(
        request,
        target_user,
        triggered_by=request.user,
    )
    log_event(
        action=(
            "admin.account.verification_email_resent"
            if success
            else "admin.account.verification_email_failed"
        ),
        user=request.user,
        target_type="user",
        target_id=str(target_user.id),
        metadata={
            "username": target_user.username,
            "role": get_user_role(target_user),
            "recipient_email": target_user.email,
            "email_log_id": email_log.id,
            "error_message": email_log.error_message,
        },
        ip_address=get_client_ip(request),
    )
    if success:
        messages.success(request, f"Verification email resent to {target_user.email}.")
    else:
        messages.error(
            request,
            f"Failed to resend verification email to {target_user.email}: {email_log.error_message}",
        )
    return redirect("admin-dashboard")


@login_required
@role_required(SUPERADMIN, ADMIN)
def login_security_policy_update(request, role=None):
    # Login security settings are updated as one POST from the dashboard.
    if request.method != "POST":
        return redirect("admin-dashboard")

    policy_forms = []
    invalid_roles = []
    for role, label in ROLE_CHOICES:
        if role == SUPERADMIN:
            continue
        # Validate each role's failed-login policy form.
        policy = LoginSecurityPolicy.get_for_role(role)
        form = LoginSecurityPolicyForm(request.POST, instance=policy, prefix=f"policy_{role}")
        policy_forms.append((role, form))
        if not form.is_valid():
            invalid_roles.append(label)

    if invalid_roles:
        messages.error(request, f"Failed to update policy for: {', '.join(invalid_roles)}.")
        return redirect("admin-dashboard")

    updated = []
    with transaction.atomic():
        # Save all valid policy updates together.
        for role, form in policy_forms:
            policy = form.save(commit=False)
            policy.updated_by = request.user
            policy.save()
            updated.append(
                {
                    "role": role,
                    "label": policy.get_role_display(),
                    "max_failed_login_attempts": policy.max_failed_login_attempts,
                    "policy_id": str(policy.id),
                }
            )

    # Audit the bulk update.
    log_event(
        action="admin.login_security_policy.updated.bulk",
        user=request.user,
        target_type="login_security_policy",
        metadata={"updated_policies": updated},
        ip_address=get_client_ip(request),
    )
    messages.success(request, "Login security policy updated.")
    return redirect("admin-dashboard")


@login_required
@role_required(SUPERADMIN, ADMIN)
def payment_reminder_settings_update(request):
    # Page for editing automatic payment reminder settings.
    reminder_settings = PaymentReminderSettings.load()
    if request.method != "POST":
        return render(
            request,
            "accounts/payment_reminder_settings.html",
            {"form": PaymentReminderSettingsForm(instance=reminder_settings)},
        )
    form = PaymentReminderSettingsForm(request.POST, instance=reminder_settings)
    if form.is_valid():
        # Save reminder settings and record who updated them.
        settings_obj = form.save(commit=False)
        settings_obj.updated_by = request.user
        settings_obj.save()
        log_event(
            action="admin.payment_reminders.updated",
            user=request.user,
            target_type="payment_reminder_settings",
            target_id=str(settings_obj.id),
            metadata={
                "before_due_enabled": settings_obj.before_due_reminders_enabled,
                "days_before_due": settings_obj.reminder_days_before_due,
                "due_date_enabled": settings_obj.due_date_reminders_enabled,
                "after_due_enabled": settings_obj.after_due_reminders_enabled,
                "after_due_days": settings_obj.after_due_days,
                "overdue_repeat_enabled": settings_obj.overdue_repeat_enabled,
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
def payment_reminder_run_check(request):
    # Manual reminder check must be submitted by POST.
    if request.method != "POST":
        return redirect("payment-reminder-settings-update")

    # "send" sends real emails; every other mode simulates only.
    mode = request.POST.get("mode", "simulate").strip().lower()
    simulate = mode != "send"
    summary = run_payment_reminder_check(
        triggered_by=request.user,
        base_url=request.build_absolute_uri("/").rstrip("/"),
        simulate=simulate,
    )
    # Audit the reminder run summary.
    log_event(
        action="admin.payment_reminders.run_check",
        user=request.user,
        target_type="payment_reminder_check",
        metadata=summary,
        ip_address=get_client_ip(request),
    )
    if simulate:
        messages.success(
            request,
            (
                f"Reminder check simulation complete. Matched: {summary['checked_invoices']}, "
                f"simulated logs: {summary['simulated']}, failed: {summary['failed']}, "
                f"already logged today: {summary['skipped_already_logged_today']}."
            ),
        )
    else:
        messages.success(
            request,
            (
                f"Reminder check complete. Matched: {summary['checked_invoices']}, "
                f"sent: {summary['sent']}, failed: {summary['failed']}, "
                f"already logged today: {summary['skipped_already_logged_today']}."
            ),
        )
    return redirect("payment-reminder-settings-update")


@login_required
@role_required(SUPERADMIN, ADMIN)
def mass_email_send(request):
    # Page/action for sending one announcement to selected role groups.
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
        messages.error(request, "Announcement email sending is disabled in reminder settings.")
        return redirect("admin-dashboard")
    if form.is_valid():
        selected_roles = form.cleaned_data["recipients"]
        recipient_emails, skipped_count = _collect_announcement_recipient_emails(selected_roles)
        if not recipient_emails:
            messages.error(request, "No users with usable email addresses matched the selected roles.")
            return redirect("admin-dashboard")

        sent_count = 0
        failed_count = 0
        for recipient in recipient_emails:
            email_sent, _email_log = _send_announcement_email(
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["message"],
                recipient=recipient,
                selected_roles=selected_roles,
                triggered_by=request.user,
            )
            if email_sent:
                sent_count += 1
            else:
                failed_count += 1

        log_event(
            action="admin.mass_email.sent",
            user=request.user,
            metadata={
                "roles": selected_roles,
                "attempted_count": len(recipient_emails),
                "sent_count": sent_count,
                "failed_count": failed_count,
                "skipped_count": skipped_count,
            },
            ip_address=get_client_ip(request),
        )

        summary = (
            f"Announcement email results: sent {sent_count}, "
            f"failed {failed_count}, skipped {skipped_count}."
        )
        if failed_count and sent_count:
            messages.warning(request, summary)
        elif failed_count:
            messages.error(request, summary)
        else:
            messages.success(request, summary)
        return redirect("admin-dashboard")
    return render(request, "accounts/mass_email.html", {"form": form})


@login_required
@role_required(SUPERADMIN, ADMIN)
def email_delivery_log_list(request):
    # List recent admin mass-email and payment-reminder email logs.
    selected_type = request.GET.get("type", "").strip()
    selected_status = request.GET.get("status", "").strip()
    search_query = request.GET.get("q", "").strip()

    # Only show admin mass email and payment reminder logs here.
    logs = EmailDeliveryLog.objects.select_related("triggered_by").filter(
        Q(template_key="admin_mass_email") | Q(template_key__startswith="payment_reminder_")
    )
    if selected_type == "mass":
        logs = logs.filter(template_key="admin_mass_email")
    elif selected_type == "reminder":
        logs = logs.filter(template_key__startswith="payment_reminder_")
    if selected_status:
        logs = logs.filter(status=selected_status)
    if search_query:
        # Search email logs by recipient, subject, related object, or metadata.
        logs = logs.filter(
            Q(recipient_email__icontains=search_query)
            | Q(subject__icontains=search_query)
            | Q(related_object_id__icontains=search_query)
            | Q(metadata__icontains=search_query)
        )

    return render(
        request,
        "accounts/email_delivery_log_list.html",
        {
            "logs": logs.order_by("-attempted_at")[:500],
            "selected_type": selected_type,
            "selected_status": selected_status,
            "search_query": search_query,
            "status_choices": EmailDeliveryLog.STATUS_CHOICES,
        },
    )


@login_required
@role_required(SUPERADMIN, ADMIN)
def suspicious_activity_list(request):
    # Page showing recent failed-login and permission-denied activity.
    selected_role = request.GET.get("role", "").strip()
    search_query = request.GET.get("q", "").strip()
    selected_reason = request.GET.get("reason", "").strip()
    recent_since = timezone.now() - timezone.timedelta(days=7)

    # Suspicious activity is limited to the most recent 7 days.
    suspicious_action_filter = Q(audit_logs__action="auth.permission_denied") | Q(
        audit_logs__action="auth.login.failed"
    )
    # Build one row per account with suspicious activity counts.
    account_rows = (
        User.objects.select_related("role_profile")
        .filter(suspicious_action_filter, audit_logs__created_at__gte=recent_since)
        .annotate(
            failed_attempts_7d=Count(
                "audit_logs",
                filter=Q(
                    audit_logs__action="auth.login.failed",
                    audit_logs__created_at__gte=recent_since,
                ),
                distinct=True,
            ),
            permission_denied_7d=Count(
                "audit_logs",
                filter=Q(
                    audit_logs__action="auth.permission_denied",
                    audit_logs__created_at__gte=recent_since,
                ),
                distinct=True,
            ),
            last_failed_attempt_at=Max(
                "audit_logs__created_at",
                filter=Q(
                    audit_logs__action="auth.login.failed",
                    audit_logs__created_at__gte=recent_since,
                ),
            ),
            last_flagged_at=Max(
                "audit_logs__created_at",
                filter=Q(
                    Q(audit_logs__action="auth.permission_denied")
                    | Q(audit_logs__action="auth.login.failed"),
                    audit_logs__created_at__gte=recent_since,
                ),
            ),
        )
        .distinct()
    )
    if selected_role:
        account_rows = account_rows.filter(role_profile__role=selected_role)
    if selected_reason == "failed":
        account_rows = account_rows.filter(failed_attempts_7d__gt=0)
    elif selected_reason == "permission_denied":
        account_rows = account_rows.filter(permission_denied_7d__gt=0)
    if search_query:
        # Search suspicious accounts by username, email, or code ID.
        account_rows = account_rows.filter(
            Q(username__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(role_profile__code_id__icontains=search_query)
        )
    account_rows = list(account_rows.order_by("-failed_attempts_7d", "-last_flagged_at", "username")[:200])

    # Get current failed-login limits for the roles shown.
    policy_by_role = {
        policy.role: policy.max_failed_login_attempts
        for policy in LoginSecurityPolicy.objects.filter(
            role__in={row.role_profile.role for row in account_rows}
        )
    }
    for account in account_rows:
        # Attach display fields used by the template.
        account.max_failed_login_attempts = policy_by_role.get(account.role_profile.role, 5)
        account.failed_attempts_current = account.role_profile.failed_login_attempts
        account.is_suspended = account.role_profile.is_suspended
        account.can_be_suspended = _can_suspend_target(request.user, account)
        account.status_label = "Suspended" if account.is_suspended else "Active"
        if not account.is_suspended and account.failed_attempts_current >= account.max_failed_login_attempts:
            account.status_label = "Threshold reached"

    # Build the detailed event log list for the page.
    event_logs = AuditLog.objects.select_related("user", "user__role_profile").filter(
        Q(action="auth.permission_denied") | Q(action="auth.login.failed"),
        created_at__gte=recent_since,
    )
    if selected_role:
        event_logs = event_logs.filter(user__role_profile__role=selected_role)
    if selected_reason == "failed":
        event_logs = event_logs.filter(action="auth.login.failed")
    elif selected_reason == "permission_denied":
        event_logs = event_logs.filter(action="auth.permission_denied")
    if search_query:
        # Search event logs by user and target details.
        event_logs = event_logs.filter(
            Q(user__username__icontains=search_query)
            | Q(user__email__icontains=search_query)
            | Q(target_type__icontains=search_query)
            | Q(target_id__icontains=search_query)
        )
    events = event_logs.order_by("-created_at")[:200]
    return render(
        request,
        "accounts/suspicious_activity_list.html",
        {
            "accounts": account_rows,
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
    # Older/simple page for creating Admin role accounts.
    if request.method == "POST":
        form = AdminAccountCreationForm(request.POST)
        if form.is_valid():
            # Save the Admin account and audit the creation.
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
        # Empty form for first page load.
        form = AdminAccountCreationForm()

    return render(request, "accounts/create_admin_account.html", {"form": form})
