from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import NoReverseMatch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog
from invoicing.models import Customer, Invoice
from notifications.models import EmailDeliveryLog, PaymentReminderSettings

from .models import EmailVerificationToken, LoginSecurityPolicy
from .signals import ADMIN_CONSOLE_GROUP_NAME
from .roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF, SUPERADMIN

User = get_user_model()


class AccountsPhaseOneTests(TestCase):
    def test_user_role_profile_is_created_automatically(self):
        user = User.objects.create_user(username="rolecheck", password="TempPass123!")
        self.assertTrue(hasattr(user, "role_profile"))
        self.assertTrue(user.role_profile.code_id.startswith("STF-"))

    def test_hr_user_can_open_finance_console(self):
        user = User.objects.create_user(username="hr1", password="TempPass123!")
        profile = user.role_profile
        profile.role = HR
        profile.save()

        self.client.login(username="hr1", password="TempPass123!")
        response = self.client.get(reverse("finance-console"))

        self.assertEqual(response.status_code, 200)

    def test_finance_user_is_forbidden_from_finance_console(self):
        user = User.objects.create_user(username="finance1", password="TempPass123!")
        profile = user.role_profile
        profile.role = FINANCE
        profile.save()

        self.client.login(username="finance1", password="TempPass123!")
        response = self.client.get(reverse("finance-console"))

        self.assertEqual(response.status_code, 403)

    def test_staff_user_is_forbidden_and_denial_is_audited(self):
        user = User.objects.create_user(username="staff1", password="TempPass123!")
        profile = user.role_profile
        profile.role = STAFF
        profile.save()

        self.client.login(username="staff1", password="TempPass123!")
        response = self.client.get(reverse("finance-console"))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            AuditLog.objects.filter(
                action="auth.permission_denied",
                target_type="view",
                target_id="finance_console",
                user=user,
            ).exists()
        )

    def test_public_user_can_register_staff_with_company_email_and_requires_verification(self):
        response = self.client.post(
            reverse("register"),
            data={
                "username": "newstaff",
                "email": "newstaff@vaniday.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("login"))
        user = User.objects.get(username="newstaff")
        self.assertEqual(user.role_profile.role, STAFF)
        self.assertFalse(user.is_active)
        self.assertTrue(EmailVerificationToken.objects.filter(user=user, used_at__isnull=True).exists())
        self.assertGreaterEqual(len(mail.outbox), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                action="auth.registered",
                target_type="user",
                target_id=str(user.id),
                user=user,
            ).exists()
        )

    def test_public_user_can_register_customer_with_invoice_email_and_requires_verification(self):
        Customer.objects.create(name="Cust One", email="cust1@gmail.com")
        response = self.client.post(
            reverse("register"),
            data={
                "username": "cust_user",
                "email": "cust1@gmail.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("login"))
        user = User.objects.get(username="cust_user")
        self.assertEqual(user.role_profile.role, CUSTOMER)
        self.assertFalse(user.is_active)

    def test_customer_registration_rejects_email_without_invoice_link(self):
        response = self.client.post(
            reverse("register"),
            data={
                "username": "cust_missing",
                "email": "not_linked_customer@yahoo.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Customer registration requires an email linked to an existing invoice customer record.",
        )

    def test_email_verification_activates_account(self):
        user = User.objects.create_user(username="verify_me", email="verify_me@vaniday.com", password="TempPass123!")
        user.is_active = False
        user.save(update_fields=["is_active"])
        token = EmailVerificationToken.issue_for_user(user)

        response = self.client.get(reverse("verify-email", args=[token.token]))
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        token.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(token.used_at)

    def test_login_supports_email_identifier(self):
        user = User.objects.create_user(
            username="email_login_user",
            email="email_login_user@vaniday.com",
            password="TempPass123!",
        )
        response = self.client.post(
            reverse("login"),
            data={"username": "email_login_user@vaniday.com", "password": "TempPass123!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard"), response.headers["Location"])
        self.assertTrue(
            AuditLog.objects.filter(action="auth.login", user=user).exists()
        )

    def test_unverified_account_login_shows_verification_message(self):
        user = User.objects.create_user(
            username="not_verified_user",
            email="not_verified_user@vaniday.com",
            password="TempPass123!",
        )
        user.is_active = False
        user.save(update_fields=["is_active"])
        EmailVerificationToken.issue_for_user(user)

        response = self.client.post(
            reverse("login"),
            data={"username": "not_verified_user@vaniday.com", "password": "TempPass123!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Your account is not verified. Please check your email for the verification link.",
        )

    def test_admin_dashboard_shows_unverified_status_for_unverified_account(self):
        admin = User.objects.create_user(username="verify_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])
        target = User.objects.create_user(
            username="unverified_dashboard_user",
            email="unverified_dashboard_user@vaniday.com",
            password="TempPass123!",
        )
        target.is_active = False
        target.save(update_fields=["is_active"])
        EmailVerificationToken.issue_for_user(target)

        self.client.login(username="verify_admin", password="TempPass123!")
        response = self.client.get(reverse("admin-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unverified")
        self.assertContains(response, "Verify")
        self.assertContains(response, "Resend Verification")

    def test_admin_dashboard_can_filter_unverified_accounts(self):
        admin = User.objects.create_user(username="unverified_filter_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])
        target = User.objects.create_user(
            username="unverified_filter_user",
            email="unverified_filter_user@vaniday.com",
            password="TempPass123!",
        )
        target.is_active = False
        target.save(update_fields=["is_active"])
        EmailVerificationToken.issue_for_user(target)
        verified_user = User.objects.create_user(
            username="active_filter_user",
            email="active_filter_user@vaniday.com",
            password="TempPass123!",
        )

        self.client.login(username="unverified_filter_admin", password="TempPass123!")
        response = self.client.get(reverse("admin-dashboard"), data={"role": "unverified"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unverified_filter_user")
        self.assertContains(response, "Unverified")
        self.assertNotContains(response, verified_user.username)

    def test_admin_can_manually_verify_unverified_account(self):
        admin = User.objects.create_user(username="manual_verify_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])
        target = User.objects.create_user(
            username="manual_verify_target",
            email="manual_verify_target@vaniday.com",
            password="TempPass123!",
        )
        target.role_profile.role = CUSTOMER
        target.role_profile.save(update_fields=["role", "updated_at"])
        target.is_active = False
        target.save(update_fields=["is_active"])
        token = EmailVerificationToken.issue_for_user(target)

        self.client.login(username="manual_verify_admin", password="TempPass123!")
        response = self.client.post(reverse("managed-account-verify", args=[target.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("admin-dashboard"))
        target.refresh_from_db()
        token.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertIsNotNone(token.used_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="admin.account.manually_verified",
                user=admin,
                target_type="user",
                target_id=str(target.id),
            ).exists()
        )

    def test_admin_can_resend_verification_email(self):
        admin = User.objects.create_user(username="resend_verify_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])
        target = User.objects.create_user(
            username="resend_verify_target",
            email="resend_verify_target@vaniday.com",
            password="TempPass123!",
        )
        target.is_active = False
        target.save(update_fields=["is_active"])
        old_token = EmailVerificationToken.issue_for_user(target)

        self.client.login(username="resend_verify_admin", password="TempPass123!")
        response = self.client.post(reverse("managed-account-resend-verification", args=[target.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("admin-dashboard"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Verify your account", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [target.email])
        old_token.refresh_from_db()
        self.assertIsNotNone(old_token.used_at)
        new_token = EmailVerificationToken.objects.filter(user=target, used_at__isnull=True).latest("created_at")
        self.assertIn(new_token.token, mail.outbox[0].body)
        email_log = EmailDeliveryLog.objects.latest("attempted_at")
        self.assertEqual(email_log.template_key, "account_verification_email_v1")
        self.assertEqual(email_log.status, EmailDeliveryLog.STATUS_SENT)
        self.assertEqual(email_log.triggered_by, admin)
        self.assertTrue(
            AuditLog.objects.filter(
                action="admin.account.verification_email_resent",
                user=admin,
                target_type="user",
                target_id=str(target.id),
            ).exists()
        )

    def test_admin_can_resend_verification_for_active_account_with_pending_token(self):
        admin = User.objects.create_user(username="active_resend_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])
        target = User.objects.create_user(
            username="active_pending_verify_target",
            email="geraldcjs@gmail.com",
            password="TempPass123!",
        )
        old_token = EmailVerificationToken.issue_for_user(target)

        self.client.login(username="active_resend_admin", password="TempPass123!")
        response = self.client.post(reverse("managed-account-resend-verification", args=[target.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [target.email])
        old_token.refresh_from_db()
        self.assertIsNotNone(old_token.used_at)

    def test_staff_user_cannot_access_admin_account_creation_page(self):
        staff = User.objects.create_user(username="plainstaff", password="TempPass123!")
        staff.role_profile.role = STAFF
        staff.role_profile.save()

        self.client.login(username="plainstaff", password="TempPass123!")
        response = self.client.get(reverse("create-admin-account"))

        self.assertEqual(response.status_code, 403)

    def test_admin_user_can_create_admin_account(self):
        admin = User.objects.create_user(username="projectadmin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save()

        self.client.login(username="projectadmin", password="TempPass123!")
        response = self.client.post(
            reverse("create-admin-account"),
            data={
                "username": "admin_created",
                "email": "admin_created@vaniday.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = User.objects.get(username="admin_created")
        self.assertEqual(created.role_profile.role, ADMIN)
        self.assertTrue(created.is_staff)
        self.assertTrue(
            AuditLog.objects.filter(
                action="auth.admin_account.created",
                target_type="user",
                target_id=str(created.id),
                user=admin,
            ).exists()
        )

    def test_superadmin_user_can_open_finance_console(self):
        superadmin = User.objects.create_superuser(
            username="rootsa",
            email="rootsa@example.com",
            password="TempPass123!",
        )

        self.client.login(username="rootsa", password="TempPass123!")
        response = self.client.get(reverse("finance-console"))

        self.assertEqual(response.status_code, 200)
        superadmin.refresh_from_db()
        self.assertEqual(superadmin.role_profile.role, SUPERADMIN)

    def test_staff_enabled_user_sees_admin_console_link(self):
        admin = User.objects.create_user(username="staffadmin", password="TempPass123!", is_staff=True)
        admin.role_profile.role = ADMIN
        admin.role_profile.save()

        self.client.login(username="staffadmin", password="TempPass123!")
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Admin Console")
        self.assertNotContains(response, "Django Admin")

    def test_admin_role_auto_sets_staff_and_shows_admin_console_link(self):
        user = User.objects.create_user(username="notstaff", password="TempPass123!")
        user.role_profile.role = ADMIN
        user.role_profile.save()
        user.refresh_from_db()

        self.client.login(username="notstaff", password="TempPass123!")
        response = self.client.get(reverse("dashboard"))

        self.assertTrue(user.is_staff)
        self.assertContains(response, "Admin Console")

    def test_finance_user_does_not_see_admin_console_link(self):
        user = User.objects.create_user(username="finance_no_admin_link", password="TempPass123!")
        user.role_profile.role = FINANCE
        user.role_profile.save()

        self.client.login(username="finance_no_admin_link", password="TempPass123!")
        response = self.client.get(reverse("dashboard"))

        self.assertNotContains(response, "Admin Console")

    def test_customer_is_redirected_from_management_dashboard_to_my_invoices(self):
        user = User.objects.create_user(
            username="customer_dashboard_redirect",
            email="customer_dashboard_redirect@example.com",
            password="TempPass123!",
        )
        user.role_profile.role = CUSTOMER
        user.role_profile.save()
        Customer.objects.create(name="Dashboard Customer", email="customer_dashboard_redirect@example.com")

        self.client.login(username="customer_dashboard_redirect", password="TempPass123!")
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("customer-invoice-dashboard"))

    def test_admin_role_is_not_superuser_but_has_admin_permissions(self):
        user = User.objects.create_user(username="role_admin_perms", password="TempPass123!")
        user.role_profile.role = ADMIN
        user.role_profile.save()
        user.refresh_from_db()

        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.has_perm("accounts.view_userrole"))
        self.assertTrue(user.groups.filter(name=ADMIN_CONSOLE_GROUP_NAME).exists())

    def test_superadmin_role_is_superuser_and_not_forced_into_admin_group(self):
        user = User.objects.create_user(username="role_superadmin_perms", password="TempPass123!")
        user.role_profile.role = SUPERADMIN
        user.role_profile.save()
        user.refresh_from_db()

        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertFalse(user.groups.filter(name=ADMIN_CONSOLE_GROUP_NAME).exists())

    def test_django_admin_is_not_visible_to_superadmin(self):
        superadmin = User.objects.create_user(username="django_admin_super", password="TempPass123!")
        superadmin.role_profile.role = SUPERADMIN
        superadmin.role_profile.save()

        self.client.login(username="django_admin_super", password="TempPass123!")
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Admin Console")
        self.assertNotContains(response, "Django Admin")

    def test_django_admin_route_is_not_exposed(self):
        with self.assertRaises(NoReverseMatch):
            reverse("admin:index")


    def test_customer_role_cannot_be_changed_from_admin_dashboard(self):
        admin = User.objects.create_user(username="customer_guard_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save()

        customer = User.objects.create_user(username="protected_customer", password="TempPass123!")
        customer.role_profile.role = CUSTOMER
        customer.role_profile.save()

        self.client.login(username="customer_guard_admin", password="TempPass123!")
        response = self.client.post(
            reverse("managed-account-role-update", args=[customer.id]),
            data={"role": ADMIN},
        )

        self.assertEqual(response.status_code, 302)
        customer.role_profile.refresh_from_db()
        self.assertEqual(customer.role_profile.role, CUSTOMER)

    def test_managed_account_creation_auto_generates_code_id(self):
        superadmin = User.objects.create_user(username="code_creator", password="TempPass123!")
        superadmin.role_profile.role = SUPERADMIN
        superadmin.role_profile.save()

        self.client.login(username="code_creator", password="TempPass123!")
        response = self.client.post(
            reverse("managed-account-create"),
            data={
                "username": "finance_code_user",
                "email": "finance_code_user@vaniday.com",
                "code_id": "",
                "role": FINANCE,
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = User.objects.get(username="finance_code_user")
        self.assertTrue(created.role_profile.code_id.startswith("FIN-"))

    def test_managed_account_creation_accepts_manual_code_id(self):
        superadmin = User.objects.create_user(username="manual_code_creator", password="TempPass123!")
        superadmin.role_profile.role = SUPERADMIN
        superadmin.role_profile.save()

        self.client.login(username="manual_code_creator", password="TempPass123!")
        response = self.client.post(
            reverse("managed-account-create"),
            data={
                "username": "manual_code_user",
                "email": "manual_code_user@vaniday.com",
                "code_id": "hr-special-001",
                "role": HR,
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = User.objects.get(username="manual_code_user")
        self.assertEqual(created.role_profile.code_id, "HR-SPECIAL-001")

    def test_managed_account_creation_rejects_duplicate_code_id(self):
        superadmin = User.objects.create_user(username="duplicate_code_creator", password="TempPass123!")
        superadmin.role_profile.role = SUPERADMIN
        superadmin.role_profile.save()

        existing = User.objects.create_user(username="existing_code_user", password="TempPass123!")
        existing.role_profile.code_id = "DUP-001"
        existing.role_profile.save(update_fields=["code_id", "updated_at"])

        self.client.login(username="duplicate_code_creator", password="TempPass123!")
        response = self.client.post(
            reverse("managed-account-create"),
            data={
                "username": "duplicate_code_user",
                "email": "duplicate_code_user@vaniday.com",
                "code_id": "dup-001",
                "role": STAFF,
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="duplicate_code_user").exists())
        self.assertContains(response, "This Code ID is already in use.")

    def test_admin_can_suspend_and_unsuspend_staff_account(self):
        admin = User.objects.create_user(username="suspend_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save()

        target = User.objects.create_user(username="suspend_target", password="TempPass123!")
        target.role_profile.role = STAFF
        target.role_profile.save()

        self.client.login(username="suspend_admin", password="TempPass123!")
        suspend_response = self.client.post(reverse("managed-account-suspend", args=[target.id]))
        self.assertEqual(suspend_response.status_code, 302)

        target.refresh_from_db()
        target.role_profile.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(target.role_profile.is_suspended)
        self.assertTrue(
            AuditLog.objects.filter(
                action="admin.account.suspended",
                user=admin,
                target_type="user",
                target_id=str(target.id),
            ).exists()
        )

        unsuspend_response = self.client.post(reverse("managed-account-unsuspend", args=[target.id]))
        self.assertEqual(unsuspend_response.status_code, 302)
        target.refresh_from_db()
        target.role_profile.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertFalse(target.role_profile.is_suspended)
        self.assertEqual(target.role_profile.failed_login_attempts, 0)
        self.assertTrue(
            AuditLog.objects.filter(
                action="admin.account.unsuspended",
                user=admin,
                target_type="user",
                target_id=str(target.id),
            ).exists()
        )

    def test_admin_can_suspend_customer_account(self):
        admin = User.objects.create_user(username="customer_suspend_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save()

        customer = User.objects.create_user(username="customer_suspend_target", password="TempPass123!")
        customer.role_profile.role = CUSTOMER
        customer.role_profile.save()

        self.client.login(username="customer_suspend_admin", password="TempPass123!")
        response = self.client.post(reverse("managed-account-suspend", args=[customer.id]))
        self.assertEqual(response.status_code, 302)

        customer.refresh_from_db()
        customer.role_profile.refresh_from_db()
        self.assertFalse(customer.is_active)
        self.assertTrue(customer.role_profile.is_suspended)

    def test_admin_can_update_login_security_policy(self):
        admin = User.objects.create_user(username="policy_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save()

        self.client.login(username="policy_admin", password="TempPass123!")
        response = self.client.post(
            reverse("login-security-policy-update"),
            data={
                "policy_admin-role": ADMIN,
                "policy_admin-max_failed_login_attempts": 5,
                "policy_finance-role": FINANCE,
                "policy_finance-max_failed_login_attempts": 5,
                "policy_hr-role": HR,
                "policy_hr-max_failed_login_attempts": 5,
                "policy_staff-role": STAFF,
                "policy_staff-max_failed_login_attempts": 7,
                "policy_customer-role": CUSTOMER,
                "policy_customer-max_failed_login_attempts": 5,
            },
        )
        self.assertEqual(response.status_code, 302)

        policy = LoginSecurityPolicy.objects.get(role=STAFF)
        self.assertEqual(policy.max_failed_login_attempts, 7)
        self.assertEqual(policy.updated_by, admin)
        self.assertTrue(
            AuditLog.objects.filter(
                action="admin.login_security_policy.updated.bulk",
                user=admin,
                target_type="login_security_policy",
            ).exists()
        )

    def test_failed_login_attempts_auto_suspend_at_role_threshold(self):
        user = User.objects.create_user(username="lock_me", password="TempPass123!")
        user.role_profile.role = STAFF
        user.role_profile.save(update_fields=["role", "updated_at"])
        LoginSecurityPolicy.objects.create(role=STAFF, max_failed_login_attempts=2)

        first_attempt = self.client.post(
            reverse("login"),
            data={"username": "lock_me", "password": "WrongPass123!"},
        )
        self.assertEqual(first_attempt.status_code, 200)
        user.refresh_from_db()
        user.role_profile.refresh_from_db()
        self.assertEqual(user.role_profile.failed_login_attempts, 1)
        self.assertTrue(user.is_active)
        self.assertFalse(user.role_profile.is_suspended)

        second_attempt = self.client.post(
            reverse("login"),
            data={"username": "lock_me", "password": "WrongPass123!"},
        )
        self.assertEqual(second_attempt.status_code, 200)
        user.refresh_from_db()
        user.role_profile.refresh_from_db()
        self.assertEqual(user.role_profile.failed_login_attempts, 2)
        self.assertFalse(user.is_active)
        self.assertTrue(user.role_profile.is_suspended)
        self.assertTrue(
            AuditLog.objects.filter(
                action="auth.login.auto_suspended",
                user=user,
                target_type="user",
                target_id=str(user.id),
            ).exists()
        )

    def test_successful_login_resets_failed_attempt_counter(self):
        user = User.objects.create_user(username="reset_counter_u", password="TempPass123!")
        user.role_profile.role = STAFF
        user.role_profile.failed_login_attempts = 3
        user.role_profile.save(update_fields=["role", "failed_login_attempts", "updated_at"])

        response = self.client.post(
            reverse("login"),
            data={"username": "reset_counter_u", "password": "TempPass123!"},
        )
        self.assertEqual(response.status_code, 302)
        user.role_profile.refresh_from_db()
        self.assertEqual(user.role_profile.failed_login_attempts, 0)

    def test_suspended_user_login_shows_suspended_message(self):
        user = User.objects.create_user(username="already_suspended", password="TempPass123!")
        user.role_profile.role = STAFF
        user.role_profile.save(update_fields=["role", "updated_at"])
        user.role_profile.suspend(reason="Manual suspension for test")

        response = self.client.post(
            reverse("login"),
            data={"username": "already_suspended", "password": "TempPass123!"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your account is suspended. Please contact an administrator.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_failed_login_auto_suspend_defaults_to_five_attempts(self):
        user = User.objects.create_user(username="lock_at_five", password="TempPass123!")
        user.role_profile.role = STAFF
        user.role_profile.save(update_fields=["role", "updated_at"])

        for _ in range(4):
            response = self.client.post(
                reverse("login"),
                data={"username": "lock_at_five", "password": "WrongPass123!"},
            )
            self.assertEqual(response.status_code, 200)

        user.refresh_from_db()
        user.role_profile.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(user.role_profile.is_suspended)
        self.assertEqual(user.role_profile.failed_login_attempts, 4)

        fifth_response = self.client.post(
            reverse("login"),
            data={"username": "lock_at_five", "password": "WrongPass123!"},
        )
        self.assertEqual(fifth_response.status_code, 200)
        user.refresh_from_db()
        user.role_profile.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertTrue(user.role_profile.is_suspended)
        self.assertEqual(user.role_profile.failed_login_attempts, 5)

    def test_suspicious_activity_page_shows_failed_login_triage_details(self):
        admin = User.objects.create_user(username="susp_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])

        target = User.objects.create_user(
            username="flagged_user",
            email="flagged_user@vaniday.com",
            password="TempPass123!",
        )
        target.role_profile.role = STAFF
        target.role_profile.save(update_fields=["role", "updated_at"])

        for _ in range(2):
            self.client.post(
                reverse("login"),
                data={"username": "flagged_user", "password": "WrongPass123!"},
            )

        self.client.login(username="susp_admin", password="TempPass123!")
        response = self.client.get(reverse("suspicious-activity-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Flagged Accounts")
        self.assertContains(response, "flagged_user")
        self.assertContains(response, "flagged_user@vaniday.com")
        self.assertContains(response, "2 / 5")
        self.assertContains(response, "Suspend for Review")

    def test_admin_can_update_extended_payment_reminder_settings(self):
        admin = User.objects.create_user(username="reminder_settings_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])

        self.client.login(username="reminder_settings_admin", password="TempPass123!")
        response = self.client.post(
            reverse("payment-reminder-settings-update"),
            data={
                "before_due_reminders_enabled": "on",
                "reminder_days_before_due": 3,
                "due_date_reminders_enabled": "on",
                "after_due_reminders_enabled": "on",
                "after_due_days": 2,
                "overdue_repeat_enabled": "on",
                "overdue_repeat_days": 4,
                "mass_email_enabled": "on",
            },
        )
        self.assertEqual(response.status_code, 302)

        reminder_settings = PaymentReminderSettings.load()
        self.assertTrue(reminder_settings.before_due_reminders_enabled)
        self.assertEqual(reminder_settings.reminder_days_before_due, 3)
        self.assertTrue(reminder_settings.due_date_reminders_enabled)
        self.assertTrue(reminder_settings.after_due_reminders_enabled)
        self.assertEqual(reminder_settings.after_due_days, 2)
        self.assertTrue(reminder_settings.overdue_repeat_enabled)
        self.assertEqual(reminder_settings.overdue_repeat_days, 4)

    def test_run_reminder_check_simulation_creates_pending_reminder_log(self):
        admin = User.objects.create_user(username="reminder_sim_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])

        customer = Customer.objects.create(name="Reminder Customer", email="reminder_customer@example.com")
        invoice = Invoice.objects.create(
            invoice_number="INV-REM-0001",
            customer=customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate(),
            total_amount=100,
        )

        settings_obj = PaymentReminderSettings.load()
        settings_obj.before_due_reminders_enabled = False
        settings_obj.due_date_reminders_enabled = True
        settings_obj.after_due_reminders_enabled = False
        settings_obj.overdue_repeat_enabled = False
        settings_obj.save()

        self.client.login(username="reminder_sim_admin", password="TempPass123!")
        response = self.client.post(reverse("payment-reminder-run-check"), data={"mode": "simulate"})
        self.assertEqual(response.status_code, 302)

        log = EmailDeliveryLog.objects.filter(
            related_object_type="invoice",
            related_object_id=str(invoice.id),
            template_key="payment_reminder_due_date",
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_PENDING)
        self.assertTrue(log.metadata.get("simulate"))

    def test_run_reminder_check_send_marks_log_sent(self):
        admin = User.objects.create_user(username="reminder_send_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])

        customer = Customer.objects.create(name="Send Reminder Customer", email="send_reminder@example.com")
        invoice = Invoice.objects.create(
            invoice_number="INV-REM-0002",
            customer=customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate(),
            total_amount=200,
        )

        settings_obj = PaymentReminderSettings.load()
        settings_obj.before_due_reminders_enabled = False
        settings_obj.due_date_reminders_enabled = True
        settings_obj.after_due_reminders_enabled = False
        settings_obj.overdue_repeat_enabled = False
        settings_obj.save()

        self.client.login(username="reminder_send_admin", password="TempPass123!")
        response = self.client.post(reverse("payment-reminder-run-check"), data={"mode": "send"})
        self.assertEqual(response.status_code, 302)

        log = EmailDeliveryLog.objects.filter(
            related_object_type="invoice",
            related_object_id=str(invoice.id),
            template_key="payment_reminder_due_date",
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_SENT)

    def test_run_reminder_check_send_after_simulation_marks_log_sent(self):
        admin = User.objects.create_user(username="reminder_sim_then_send_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])

        customer = Customer.objects.create(name="Sim Then Send Customer", email="sim_then_send@example.com")
        invoice = Invoice.objects.create(
            invoice_number="INV-REM-0003",
            customer=customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate(),
            total_amount=300,
        )

        settings_obj = PaymentReminderSettings.load()
        settings_obj.before_due_reminders_enabled = False
        settings_obj.due_date_reminders_enabled = True
        settings_obj.after_due_reminders_enabled = False
        settings_obj.overdue_repeat_enabled = False
        settings_obj.save()

        self.client.login(username="reminder_sim_then_send_admin", password="TempPass123!")
        simulate_response = self.client.post(reverse("payment-reminder-run-check"), data={"mode": "simulate"})
        self.assertEqual(simulate_response.status_code, 302)
        send_response = self.client.post(reverse("payment-reminder-run-check"), data={"mode": "send"})
        self.assertEqual(send_response.status_code, 302)

        logs = EmailDeliveryLog.objects.filter(
            related_object_type="invoice",
            related_object_id=str(invoice.id),
            template_key="payment_reminder_due_date",
        ).order_by("attempted_at")
        self.assertEqual(logs.count(), 2)
        self.assertEqual(logs[0].status, EmailDeliveryLog.STATUS_PENDING)
        self.assertTrue(logs[0].metadata.get("simulate"))
        self.assertEqual(logs[1].status, EmailDeliveryLog.STATUS_SENT)
        self.assertFalse(logs[1].metadata.get("simulate"))


class AnnouncementEmailTests(TestCase):
    def setUp(self):
        self.password = "TempPass123!"
        self.url = reverse("mass-email-send")
        self.admin = self._create_user("announcement_admin", ADMIN, "announcement_admin@vaniday.com")
        self.customer = self._create_user("announcement_customer", CUSTOMER, "customer@example.com")
        self.staff = self._create_user("announcement_staff", STAFF, "staff@example.com")

    def _create_user(self, username, role, email):
        user = User.objects.create_user(username=username, email=email, password=self.password)
        user.role_profile.role = role
        user.role_profile.save(update_fields=["role", "updated_at"])
        return user

    def test_admin_can_open_announcement_email_page(self):
        self.client.login(username=self.admin.username, password=self.password)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Announcement Email")
        self.assertContains(response, "Send Announcement")

    def test_customer_cannot_open_announcement_email_page(self):
        self.client.login(username=self.customer.username, password=self.password)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_open_announcement_email_page(self):
        self.client.login(username=self.staff.username, password=self.password)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_superadmin_can_open_announcement_email_page(self):
        superadmin = User.objects.create_superuser(
            username="announcement_superadmin",
            email="announcement_superadmin@example.com",
            password=self.password,
        )
        self.client.login(username=superadmin.username, password=self.password)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Announcement Email")

    def test_announcement_email_send_deduplicates_recipients_and_logs_each_delivery(self):
        self._create_user("announcement_staff_dup", STAFF, "STAFF@example.com")
        self._create_user("announcement_staff_blank", STAFF, "")
        self._create_user("announcement_finance", FINANCE, "finance@example.com")

        self.client.login(username=self.admin.username, password=self.password)
        response = self.client.post(
            self.url,
            data={
                "recipients": [CUSTOMER, STAFF],
                "subject": "Portal update",
                "message": "Please review the new schedule.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Announcement email results: sent 2, failed 0, skipped 2.")
        self.assertEqual(len(mail.outbox), 2)

        delivered_to = {message.to[0] for message in mail.outbox}
        self.assertEqual(delivered_to, {"customer@example.com", "staff@example.com"})
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)
            self.assertFalse(message.cc)
            self.assertFalse(message.bcc)
            self.assertEqual(message.subject, "Portal update")
            self.assertEqual(message.body, "Please review the new schedule.")

        logs = EmailDeliveryLog.objects.filter(template_key="admin_mass_email").order_by("recipient_email")
        self.assertEqual(logs.count(), 2)
        self.assertEqual({log.recipient_email for log in logs}, delivered_to)
        for log in logs:
            self.assertEqual(log.status, EmailDeliveryLog.STATUS_SENT)
            self.assertEqual(log.triggered_by, self.admin)
            self.assertIsNotNone(log.sent_at)
            self.assertEqual(set(log.metadata.get("selected_roles", [])), {CUSTOMER, STAFF})
            self.assertNotEqual(log.recipient_email, settings.DEFAULT_FROM_EMAIL)

        audit_log = AuditLog.objects.filter(action="admin.mass_email.sent", user=self.admin).latest("created_at")
        self.assertEqual(set(audit_log.metadata.get("roles", [])), {CUSTOMER, STAFF})
        self.assertEqual(audit_log.metadata.get("attempted_count"), 2)
        self.assertEqual(audit_log.metadata.get("sent_count"), 2)
        self.assertEqual(audit_log.metadata.get("failed_count"), 0)
        self.assertEqual(audit_log.metadata.get("skipped_count"), 2)
        self.assertNotIn("subject", audit_log.metadata)
        self.assertNotIn("message", audit_log.metadata)
        self.assertNotIn("Please review the new schedule.", str(audit_log.metadata))

    def test_active_user_in_selected_role_receives_announcement(self):
        self.client.login(username=self.admin.username, password=self.password)

        response = self.client.post(
            self.url,
            data={
                "recipients": [CUSTOMER],
                "subject": "Portal update",
                "message": "Please review the new schedule.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Announcement email results: sent 1, failed 0, skipped 0.")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["customer@example.com"])

        logs = EmailDeliveryLog.objects.filter(template_key="admin_mass_email")
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.get().recipient_email, "customer@example.com")

    def test_inactive_user_does_not_receive_announcement(self):
        self.customer.is_active = False
        self.customer.save(update_fields=["is_active"])
        self.client.login(username=self.admin.username, password=self.password)

        response = self.client.post(
            self.url,
            data={
                "recipients": [CUSTOMER],
                "subject": "Portal update",
                "message": "Please review the new schedule.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No users with usable email addresses matched the selected roles.")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailDeliveryLog.objects.filter(template_key="admin_mass_email").exists())

    def test_suspended_user_does_not_receive_announcement(self):
        self.customer.role_profile.suspend(by=self.admin, reason="Suspended for test")
        self.client.login(username=self.admin.username, password=self.password)

        response = self.client.post(
            self.url,
            data={
                "recipients": [CUSTOMER],
                "subject": "Portal update",
                "message": "Please review the new schedule.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No users with usable email addresses matched the selected roles.")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailDeliveryLog.objects.filter(template_key="admin_mass_email").exists())

    def test_technically_active_suspended_user_does_not_receive_announcement(self):
        self.customer.role_profile.suspended_at = timezone.now()
        self.customer.role_profile.suspended_reason = "Manual state test"
        self.customer.role_profile.save(update_fields=["suspended_at", "suspended_reason", "updated_at"])
        self.customer.is_active = True
        self.customer.save(update_fields=["is_active"])
        self.client.login(username=self.admin.username, password=self.password)

        response = self.client.post(
            self.url,
            data={
                "recipients": [CUSTOMER],
                "subject": "Portal update",
                "message": "Please review the new schedule.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No users with usable email addresses matched the selected roles.")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailDeliveryLog.objects.filter(template_key="admin_mass_email").exists())

    def test_inactive_but_not_suspended_user_does_not_receive_announcement(self):
        self.customer.is_active = False
        self.customer.save(update_fields=["is_active"])
        self.customer.role_profile.suspended_at = None
        self.customer.role_profile.suspended_reason = ""
        self.customer.role_profile.save(update_fields=["suspended_at", "suspended_reason", "updated_at"])
        self.client.login(username=self.admin.username, password=self.password)

        response = self.client.post(
            self.url,
            data={
                "recipients": [CUSTOMER],
                "subject": "Portal update",
                "message": "Please review the new schedule.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No users with usable email addresses matched the selected roles.")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailDeliveryLog.objects.filter(template_key="admin_mass_email").exists())

    def test_active_and_suspended_accounts_sharing_email_send_once_to_active_account_only(self):
        suspended_customer = self._create_user(
            "announcement_customer_suspended",
            CUSTOMER,
            "customer@example.com",
        )
        suspended_customer.role_profile.suspend(by=self.admin, reason="Suspended for test")
        self.client.login(username=self.admin.username, password=self.password)

        response = self.client.post(
            self.url,
            data={
                "recipients": [CUSTOMER],
                "subject": "Portal update",
                "message": "Please review the new schedule.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Announcement email results: sent 1, failed 0, skipped 1.")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["customer@example.com"])

        logs = EmailDeliveryLog.objects.filter(template_key="admin_mass_email")
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.get().recipient_email, "customer@example.com")

    def test_empty_recipient_selection_is_rejected_clearly(self):
        self.client.login(username=self.admin.username, password=self.password)

        response = self.client.post(
            self.url,
            data={"subject": "Portal update", "message": "Please review the new schedule."},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select at least one recipient role.")
        self.assertEqual(len(mail.outbox), 0)

    def test_selected_role_with_no_usable_email_addresses_is_handled_safely(self):
        self._create_user("announcement_hr_blank", HR, "")

        self.client.login(username=self.admin.username, password=self.password)
        response = self.client.post(
            self.url,
            data={
                "recipients": [HR],
                "subject": "Portal update",
                "message": "Please review the new schedule.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No users with usable email addresses matched the selected roles.")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailDeliveryLog.objects.filter(template_key="admin_mass_email").exists())

    def test_skipped_summary_counts_inactive_suspended_blank_and_duplicate_recipients(self):
        self._create_user("announcement_customer_duplicate", CUSTOMER, "CUSTOMER@example.com")
        blank_customer = self._create_user("announcement_customer_blank", CUSTOMER, "")
        blank_customer.is_active = True
        blank_customer.save(update_fields=["is_active"])

        inactive_customer = self._create_user("announcement_customer_inactive", CUSTOMER, "inactive@example.com")
        inactive_customer.is_active = False
        inactive_customer.save(update_fields=["is_active"])

        suspended_customer = self._create_user(
            "announcement_customer_suspended_count",
            CUSTOMER,
            "suspended@example.com",
        )
        suspended_customer.role_profile.suspend(by=self.admin, reason="Suspended for test")

        self.client.login(username=self.admin.username, password=self.password)
        response = self.client.post(
            self.url,
            data={
                "recipients": [CUSTOMER],
                "subject": "Portal update",
                "message": "Please review the new schedule.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Announcement email results: sent 1, failed 0, skipped 4.")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["customer@example.com"])

        logs = EmailDeliveryLog.objects.filter(template_key="admin_mass_email")
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.get().recipient_email, "customer@example.com")

        audit_log = AuditLog.objects.filter(action="admin.mass_email.sent", user=self.admin).latest("created_at")
        self.assertEqual(audit_log.metadata.get("attempted_count"), 1)
        self.assertEqual(audit_log.metadata.get("sent_count"), 1)
        self.assertEqual(audit_log.metadata.get("failed_count"), 0)
        self.assertEqual(audit_log.metadata.get("skipped_count"), 4)
        self.assertEqual(set(audit_log.metadata.get("roles", [])), {CUSTOMER})
        self.assertNotIn("recipient_emails", audit_log.metadata)
        self.assertNotIn("inactive@example.com", str(audit_log.metadata))
        self.assertNotIn("suspended@example.com", str(audit_log.metadata))

    def test_announcement_email_smtp_failure_is_logged_without_server_error(self):
        self.client.login(username=self.admin.username, password=self.password)

        with patch("accounts.views.send_mail", side_effect=RuntimeError("SMTP backend unavailable")):
            response = self.client.post(
                self.url,
                data={
                    "recipients": [CUSTOMER],
                    "subject": "Portal update",
                    "message": "Please review the new schedule.",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Announcement email results: sent 0, failed 1, skipped 0.")
        self.assertEqual(len(mail.outbox), 0)

        email_log = EmailDeliveryLog.objects.get(template_key="admin_mass_email")
        self.assertEqual(email_log.recipient_email, "customer@example.com")
        self.assertEqual(email_log.status, EmailDeliveryLog.STATUS_FAILED)
        self.assertIn("SMTP backend unavailable", email_log.error_message)
        self.assertIsNone(email_log.sent_at)

        audit_log = AuditLog.objects.filter(action="admin.mass_email.sent", user=self.admin).latest("created_at")
        self.assertEqual(audit_log.metadata.get("attempted_count"), 1)
        self.assertEqual(audit_log.metadata.get("sent_count"), 0)
        self.assertEqual(audit_log.metadata.get("failed_count"), 1)
        self.assertEqual(audit_log.metadata.get("skipped_count"), 0)
        self.assertNotIn("subject", audit_log.metadata)
        self.assertNotIn("message", audit_log.metadata)

    def test_announcement_email_tests_use_in_memory_backend(self):
        self.assertEqual(settings.EMAIL_BACKEND, "django.core.mail.backends.locmem.EmailBackend")
