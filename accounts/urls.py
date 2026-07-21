from django.contrib.auth import views as auth_views
from django.urls import path

from .views import (
    UserLoginView,
    UserLogoutView,
    admin_dashboard,
    create_admin_account,
    email_delivery_log_list,
    login_security_policy_update,
    managed_account_create,
    managed_account_delete,
    managed_account_password_update,
    managed_account_resend_verification,
    managed_account_role_update,
    managed_account_suspend,
    managed_account_unsuspend,
    managed_account_verify,
    mass_email_send,
    payment_reminder_run_check,
    payment_reminder_settings_update,
    register,
    suspicious_activity_list,
    verify_email,
)

# Each path connects an account-related URL to a view function/class.
urlpatterns = [
    # Login and logout pages.
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            email_template_name="accounts/password_reset_email.txt",
            subject_template_name="accounts/password_reset_subject.txt",
            success_url="/accounts/password-reset/done/",
        ),
        name="password-reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password-reset-done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url="/accounts/password-reset/complete/",
        ),
        name="password-reset-confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password-reset-complete",
    ),
    # Public account registration and email verification.
    path("register/", register, name="register"),
    path("verify-email/<str:token>/", verify_email, name="verify-email"),
    # Admin account and admin dashboard pages.
    path("admin-create/", create_admin_account, name="create-admin-account"),
    path("admin-dashboard/", admin_dashboard, name="admin-dashboard"),
    # Admin-managed account creation.
    path("admin-dashboard/accounts/create/", managed_account_create, name="managed-account-create"),
    # Admin-managed role change.
    path(
        "admin-dashboard/accounts/<int:user_id>/role/",
        managed_account_role_update,
        name="managed-account-role-update",
    ),
    # Admin-managed password change.
    path(
        "admin-dashboard/accounts/<int:user_id>/password/",
        managed_account_password_update,
        name="managed-account-password-update",
    ),
    # Admin-managed account deletion.
    path(
        "admin-dashboard/accounts/<int:user_id>/delete/",
        managed_account_delete,
        name="managed-account-delete",
    ),
    # Admin-managed account suspension.
    path(
        "admin-dashboard/accounts/<int:user_id>/suspend/",
        managed_account_suspend,
        name="managed-account-suspend",
    ),
    # Admin-managed account unsuspension.
    path(
        "admin-dashboard/accounts/<int:user_id>/unsuspend/",
        managed_account_unsuspend,
        name="managed-account-unsuspend",
    ),
    # Manually mark an unverified account as verified for admin/testing.
    path(
        "admin-dashboard/accounts/<int:user_id>/verify/",
        managed_account_verify,
        name="managed-account-verify",
    ),
    # Resend a verification email for an account.
    path(
        "admin-dashboard/accounts/<int:user_id>/resend-verification/",
        managed_account_resend_verification,
        name="managed-account-resend-verification",
    ),
    # Update failed-login lockout settings.
    path(
        "admin-dashboard/login-security/",
        login_security_policy_update,
        name="login-security-policy-update",
    ),
    # Older route kept for compatibility.
    path(
        "admin-dashboard/login-security/<str:role>/",
        login_security_policy_update,
        name="login-security-policy-update-legacy",
    ),
    # Update payment reminder email settings.
    path(
        "admin-dashboard/reminders/",
        payment_reminder_settings_update,
        name="payment-reminder-settings-update",
    ),
    # Run a manual payment reminder check.
    path(
        "admin-dashboard/reminders/run-check/",
        payment_reminder_run_check,
        name="payment-reminder-run-check",
    ),
    # Send mass email and review email/security logs.
    path("admin-dashboard/mass-email/", mass_email_send, name="mass-email-send"),
    path("admin-dashboard/delivery-logs/", email_delivery_log_list, name="email-delivery-log-list"),
    path("admin-dashboard/suspicious-activity/", suspicious_activity_list, name="suspicious-activity-list"),
]
