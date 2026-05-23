from django.urls import path

from .views import (
    UserLoginView,
    UserLogoutView,
    admin_dashboard,
    create_admin_account,
    login_security_policy_update,
    managed_account_create,
    managed_account_delete,
    managed_account_password_update,
    managed_account_role_update,
    managed_account_suspend,
    managed_account_unsuspend,
    mass_email_send,
    payment_reminder_run_check,
    payment_reminder_settings_update,
    register,
    suspicious_activity_list,
    verify_email,
)

urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("register/", register, name="register"),
    path("verify-email/<str:token>/", verify_email, name="verify-email"),
    path("admin-create/", create_admin_account, name="create-admin-account"),
    path("admin-dashboard/", admin_dashboard, name="admin-dashboard"),
    path("admin-dashboard/accounts/create/", managed_account_create, name="managed-account-create"),
    path(
        "admin-dashboard/accounts/<int:user_id>/role/",
        managed_account_role_update,
        name="managed-account-role-update",
    ),
    path(
        "admin-dashboard/accounts/<int:user_id>/password/",
        managed_account_password_update,
        name="managed-account-password-update",
    ),
    path(
        "admin-dashboard/accounts/<int:user_id>/delete/",
        managed_account_delete,
        name="managed-account-delete",
    ),
    path(
        "admin-dashboard/accounts/<int:user_id>/suspend/",
        managed_account_suspend,
        name="managed-account-suspend",
    ),
    path(
        "admin-dashboard/accounts/<int:user_id>/unsuspend/",
        managed_account_unsuspend,
        name="managed-account-unsuspend",
    ),
    path(
        "admin-dashboard/login-security/",
        login_security_policy_update,
        name="login-security-policy-update",
    ),
    path(
        "admin-dashboard/login-security/<str:role>/",
        login_security_policy_update,
        name="login-security-policy-update-legacy",
    ),
    path(
        "admin-dashboard/reminders/",
        payment_reminder_settings_update,
        name="payment-reminder-settings-update",
    ),
    path(
        "admin-dashboard/reminders/run-check/",
        payment_reminder_run_check,
        name="payment-reminder-run-check",
    ),
    path("admin-dashboard/mass-email/", mass_email_send, name="mass-email-send"),
    path("admin-dashboard/suspicious-activity/", suspicious_activity_list, name="suspicious-activity-list"),
]
