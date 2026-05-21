from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.roles import ADMIN, CUSTOMER, FINANCE, STAFF, SUPERADMIN
from invoicing.models import Customer, Invoice
from payments.models import PaymentRecord


User = get_user_model()


class PaymentStripeReportAccessTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Report Customer", email="report@example.com")
        self.invoice = Invoice.objects.create(
            invoice_number="INV-REPORT-1001",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
        )
        PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REPORT-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("109.00"),
            currency="SGD",
            paid_at=timezone.now(),
            stripe_checkout_session_id="cs_test_report_001",
        )

    def _make_user(self, username, role):
        user = User.objects.create_user(username=username, password="TempPass123!")
        user.role_profile.role = role
        user.role_profile.save(update_fields=["role", "updated_at"])
        return user

    def test_superadmin_can_access_report(self):
        user = self._make_user("report_super", SUPERADMIN)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment & Stripe Report")

    def test_admin_can_access_report(self):
        user = self._make_user("report_admin", ADMIN)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recent Payments")

    def test_finance_can_access_report(self):
        user = self._make_user("report_finance", FINANCE)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recent Stripe Transactions")

    def test_customer_cannot_access_report(self):
        user = self._make_user("report_customer", CUSTOMER)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_access_report(self):
        user = self._make_user("report_staff", STAFF)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 403)
