from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import AuditLog

User = get_user_model()


class CorePhaseOneTests(TestCase):
    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_login_logout_and_dashboard_flow(self):
        user = User.objects.create_user(username="coreuser", password="TempPass123!")

        login_response = self.client.post(
            reverse("login"),
            data={"username": "coreuser", "password": "TempPass123!"},
        )
        self.assertEqual(login_response.status_code, 302)

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertFalse(AuditLog.objects.filter(action="core.dashboard.viewed", user=user).exists())

        logout_response = self.client.post(reverse("logout"))
        self.assertEqual(logout_response.status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="auth.login", user=user).exists())
        self.assertTrue(AuditLog.objects.filter(action="auth.logout", user=user).exists())

    def test_audit_log_page_hides_noisy_actions_without_deleting_rows(self):
        admin = User.objects.create_superuser(
            username="auditadmin",
            email="auditadmin@example.com",
            password="TempPass123!",
        )
        noisy_log = AuditLog.objects.create(action="invoice.list.viewed", user=admin)
        important_log = AuditLog.objects.create(action="payment.invoice.marked_paid", user=admin)

        self.client.login(username="auditadmin", password="TempPass123!")

        response = self.client.get(reverse("dashboard-audit-logs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invoice marked as paid from payment")
        self.assertNotContains(response, "payment.invoice.marked_paid")
        self.assertNotContains(response, "invoice.list.viewed")

        filtered_response = self.client.get(
            reverse("dashboard-audit-logs"),
            data={"action": "invoice.list.viewed"},
        )
        self.assertEqual(filtered_response.status_code, 200)
        self.assertNotContains(filtered_response, "invoice.list.viewed")

        self.assertTrue(AuditLog.objects.filter(pk=noisy_log.pk).exists())
        self.assertTrue(AuditLog.objects.filter(pk=important_log.pk).exists())

    def test_audit_log_page_supports_page_size_options(self):
        admin = User.objects.create_superuser(
            username="auditpager",
            email="auditpager@example.com",
            password="TempPass123!",
        )
        self.client.login(username="auditpager", password="TempPass123!")

        for index in range(15):
            AuditLog.objects.create(
                action="invoice.created",
                user=admin,
                target_type="invoice",
                target_id=str(index + 1),
                metadata={"invoice_number": f"INV-PAGE-{index + 1:04d}"},
            )

        response = self.client.get(
            reverse("dashboard-audit-logs"),
            data={"action": "invoice.created", "per_page": "10"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_per_page"], 10)
        self.assertEqual(len(response.context["logs"]), 10)
        self.assertContains(response, "Showing 1-10 of 15")

        second_page = self.client.get(
            reverse("dashboard-audit-logs"),
            data={"action": "invoice.created", "per_page": "10", "page": "2"},
        )

        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(second_page.context["logs"]), 5)
        self.assertContains(second_page, "Showing 11-15 of 15")

    def test_audit_log_page_shows_friendly_label_for_refund_actions(self):
        admin = User.objects.create_superuser(
            username="auditrefund",
            email="auditrefund@example.com",
            password="TempPass123!",
        )
        AuditLog.objects.create(
            action="payment.refund.succeeded",
            user=admin,
            target_type="invoice",
            target_id="1",
        )

        self.client.login(username="auditrefund", password="TempPass123!")
        response = self.client.get(reverse("dashboard-audit-logs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stripe refund succeeded")
        self.assertNotContains(response, "<td>payment.refund.succeeded</td>", html=True)
