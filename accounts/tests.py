from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import AuditLog

from .models import LoginSecurityPolicy
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

    def test_public_user_can_register_and_gets_staff_role(self):
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
        self.assertEqual(response.headers["Location"], reverse("dashboard"))
        user = User.objects.get(username="newstaff")
        self.assertEqual(user.role_profile.role, STAFF)
        self.assertTrue(
            AuditLog.objects.filter(
                action="auth.registered",
                target_type="user",
                target_id=str(user.id),
                user=user,
            ).exists()
        )

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
        self.assertNotContains(response, reverse("admin:index"))

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

    def test_django_admin_is_only_visible_to_superadmin(self):
        superadmin = User.objects.create_user(username="django_admin_super", password="TempPass123!")
        superadmin.role_profile.role = SUPERADMIN
        superadmin.role_profile.save()

        self.client.login(username="django_admin_super", password="TempPass123!")
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Admin Console")
        self.assertContains(response, reverse("admin:index"))

    def test_django_admin_rejects_regular_admin(self):
        admin = User.objects.create_user(username="django_admin_regular", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save()

        self.client.login(username="django_admin_regular", password="TempPass123!")
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 302)

    def test_django_admin_allows_superadmin(self):
        superadmin = User.objects.create_user(username="django_admin_allowed", password="TempPass123!")
        superadmin.role_profile.role = SUPERADMIN
        superadmin.role_profile.save()

        self.client.login(username="django_admin_allowed", password="TempPass123!")
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)

    def test_django_admin_user_change_hides_password_hash_details(self):
        superadmin = User.objects.create_user(username="django_admin_password_viewer", password="TempPass123!")
        superadmin.role_profile.role = SUPERADMIN
        superadmin.role_profile.save()
        target_user = User.objects.create_user(username="password_hash_target", password="TempPass123!")

        self.client.login(username="django_admin_password_viewer", password="TempPass123!")
        response = self.client.get(reverse("admin:auth_user_change", args=[target_user.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reset password")
        self.assertNotContains(response, "algorithm:")
        self.assertNotContains(response, "iterations:")
        self.assertNotContains(response, "salt:")
        self.assertNotContains(response, "hash:")

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
