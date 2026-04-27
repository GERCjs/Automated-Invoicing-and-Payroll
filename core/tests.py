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

    def test_login_logout_and_dashboard_audit(self):
        user = User.objects.create_user(username="coreuser", password="TempPass123!")

        login_response = self.client.post(
            reverse("login"),
            data={"username": "coreuser", "password": "TempPass123!"},
        )
        self.assertEqual(login_response.status_code, 302)

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(action="core.dashboard.viewed", user=user).exists()
        )

        logout_response = self.client.post(reverse("logout"))
        self.assertEqual(logout_response.status_code, 302)
