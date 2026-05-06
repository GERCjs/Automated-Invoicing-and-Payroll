from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import AuditLog

from .roles import ADMIN, FINANCE, STAFF

User = get_user_model()


class AccountsPhaseOneTests(TestCase):
    def test_user_role_profile_is_created_automatically(self):
        user = User.objects.create_user(username="rolecheck", password="TempPass123!")
        self.assertTrue(hasattr(user, "role_profile"))

    def test_finance_user_can_open_finance_console(self):
        user = User.objects.create_user(username="finance1", password="TempPass123!")
        profile = user.role_profile
        profile.role = FINANCE
        profile.save()

        self.client.login(username="finance1", password="TempPass123!")
        response = self.client.get(reverse("finance-console"))

        self.assertEqual(response.status_code, 200)

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
                "email": "newstaff@example.com",
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
                "email": "admin_created@example.com",
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

    def test_staff_enabled_user_sees_admin_console_link(self):
        admin = User.objects.create_user(username="staffadmin", password="TempPass123!", is_staff=True)
        admin.role_profile.role = ADMIN
        admin.role_profile.save()

        self.client.login(username="staffadmin", password="TempPass123!")
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Admin Console")
        self.assertContains(response, reverse("admin:index"))

    def test_non_staff_user_does_not_see_admin_console_link(self):
        user = User.objects.create_user(username="notstaff", password="TempPass123!")
        user.role_profile.role = ADMIN
        user.role_profile.save()

        self.client.login(username="notstaff", password="TempPass123!")
        response = self.client.get(reverse("dashboard"))

        self.assertNotContains(response, "Admin Console")
