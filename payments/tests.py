from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.roles import ADMIN, CUSTOMER, FINANCE, STAFF, SUPERADMIN
from core.models import AuditLog
from invoicing.models import Customer, Invoice
from notifications.models import EmailDeliveryLog
from notifications.services import (
    send_stripe_payment_failed_email,
    send_stripe_payment_success_email,
    send_stripe_refund_failed_email,
    send_stripe_refund_success_email,
)

from .models import PaymentBankDetails, PaymentRecord, StripeWebhookEvent
from .services import create_checkout_for_invoice, create_full_refund_for_payment

User = get_user_model()


class PaymentConfigurationSafetyTests(TestCase):
    def test_env_example_uses_placeholders_for_sensitive_values(self):
        env_example = Path(__file__).resolve().parents[1] / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        forbidden_values = [
            "RepublicPoly2026",
            "re_GnhznSjQ_BGihfGUPq7WGbMTxZ97m8Ljf",
            "sk_test_51Stps7DcQnuk3ne0",
            "pk_test_51Stps7DcQnuk3ne0",
            "anotherSuper",
            "iamAdminsuper",
        ]
        for value in forbidden_values:
            with self.subTest(value=value):
                self.assertNotIn(value, content)

        self.assertIn("STRIPE_SECRET_KEY=sk_test_your_stripe_secret_key", content)
        self.assertIn("EMAIL_HOST_PASSWORD=your_resend_api_key", content)


class StripePaymentsPhaseTests(TestCase):
    def setUp(self):
        self.customer_user = User.objects.create_user(
            username="customer_stripe",
            password="TempPass123!",
            email="billing@stripe-test.com",
        )
        self.customer_user.role_profile.role = CUSTOMER
        self.customer_user.role_profile.save()
        self.finance_user = User.objects.create_user(
            username="finance_stripe",
            password="TempPass123!",
            email="finance@stripe-test.com",
        )
        self.finance_user.role_profile.role = FINANCE
        self.finance_user.role_profile.save()
        self.staff_user = User.objects.create_user(
            username="staff_stripe",
            password="TempPass123!",
            email="staff@stripe-test.com",
        )
        self.staff_user.role_profile.role = STAFF
        self.staff_user.role_profile.save()

        self.customer = Customer.objects.create(
            name="Stripe Test Customer",
            email="billing@stripe-test.com",
        )
        self.invoice = Invoice.objects.create(
            invoice_number="INV-STRIPE-1001",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
        )
        PaymentBankDetails.objects.update_or_create(
            pk=1,
            defaults={
                "account_name": "Automated Billing Pte Ltd",
                "bank_name": "Test Receiving Bank",
                "account_number": "987-654321-0",
                "paynow_id": "201535968M",
                "bic": "TESTSGSG",
                "instructions": "Use the reference exactly as shown.",
            },
        )

    @patch("payments.views.retrieve_checkout_session")
    @patch("payments.views.create_checkout_for_invoice")
    def test_public_checkout_start_creates_session_redirect(self, create_checkout_mock, retrieve_session_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-PUBLIC-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_public_1",
        )
        create_checkout_mock.return_value = payment_record
        retrieve_session_mock.return_value = Mock(url="https://checkout.stripe.com/c/pay/test_public")

        response = self.client.post(
            reverse("payment-checkout-public", args=[self.invoice.public_view_token])
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.com/c/pay/test_public")
        create_checkout_mock.assert_called_once()

    @patch("payments.views.retrieve_checkout_session")
    @patch("payments.views.create_checkout_for_invoice")
    def test_customer_checkout_start_requires_linked_customer_email(
        self, create_checkout_mock, retrieve_session_mock
    ):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-CUST-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_customer_1",
        )
        create_checkout_mock.return_value = payment_record
        retrieve_session_mock.return_value = Mock(url="https://checkout.stripe.com/c/pay/test_customer")

        self.client.login(username="customer_stripe", password="TempPass123!")
        response = self.client.post(reverse("payment-checkout-customer", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.com/c/pay/test_customer")

    def test_customer_checkout_rejects_other_customer_invoice(self):
        other_customer = Customer.objects.create(name="Other", email="other@example.com")
        other_invoice = Invoice.objects.create(
            invoice_number="INV-STRIPE-1002",
            customer=other_customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("10.00"),
            tax_amount=Decimal("0.90"),
            total_amount=Decimal("10.90"),
        )

        self.client.login(username="customer_stripe", password="TempPass123!")
        response = self.client.post(reverse("payment-checkout-customer", args=[other_invoice.pk]))

        self.assertEqual(response.status_code, 404)

    @patch("payments.views.create_checkout_for_invoice")
    def test_public_checkout_rejects_draft_invoice(self, create_checkout_mock):
        self.invoice.status = Invoice.STATUS_DRAFT
        self.invoice.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            reverse("payment-checkout-public", args=[self.invoice.public_view_token])
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("invoice-public-view", args=[self.invoice.public_view_token]))
        create_checkout_mock.assert_not_called()

    @patch("payments.views.create_checkout_for_invoice")
    def test_customer_checkout_rejects_draft_invoice(self, create_checkout_mock):
        self.invoice.status = Invoice.STATUS_DRAFT
        self.invoice.save(update_fields=["status", "updated_at"])
        self.client.login(username="customer_stripe", password="TempPass123!")

        response = self.client.post(reverse("payment-checkout-customer", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("customer-invoice-detail", args=[self.invoice.pk]))
        create_checkout_mock.assert_not_called()

    def test_public_bank_transfer_reference_is_stable_per_invoice(self):
        first_response = self.client.get(reverse("invoice-public-view", args=[self.invoice.public_view_token]))
        payment_record = PaymentRecord.objects.get(
            invoice=self.invoice,
            provider=PaymentRecord.PROVIDER_MANUAL,
        )
        second_response = self.client.get(reverse("invoice-public-view", args=[self.invoice.public_view_token]))

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertContains(first_response, "Bank Transfer")
        self.assertContains(first_response, "Test Receiving Bank")
        self.assertContains(first_response, "987-654321-0")
        self.assertContains(first_response, "TESTSGSG")
        self.assertContains(first_response, payment_record.payment_reference)
        self.assertContains(second_response, payment_record.payment_reference)
        self.assertEqual(
            PaymentRecord.objects.filter(
                invoice=self.invoice,
                provider=PaymentRecord.PROVIDER_MANUAL,
                status=PaymentRecord.STATUS_PENDING,
            ).count(),
            1,
        )

    def test_customer_invoice_detail_shows_bank_transfer_reference(self):
        self.client.login(username="customer_stripe", password="TempPass123!")

        response = self.client.get(reverse("customer-invoice-detail", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        payment_record = PaymentRecord.objects.get(
            invoice=self.invoice,
            provider=PaymentRecord.PROVIDER_MANUAL,
        )
        self.assertContains(response, "Bank Transfer")
        self.assertContains(response, payment_record.payment_reference)

    def test_customer_can_submit_bank_transfer_notice_without_marking_invoice_paid(self):
        self.client.login(username="customer_stripe", password="TempPass123!")

        response = self.client.post(
            reverse("payment-bank-transfer-notice-customer", args=[self.invoice.pk]),
            data={
                "manual_customer_amount": str(self.invoice.total_amount),
                "manual_customer_transfer_date": timezone.localdate().isoformat(),
                "manual_customer_bank_reference": "CUSTOMER-BANK-REF-001",
                "manual_customer_notes": "Transferred from DBS.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("customer-invoice-detail", args=[self.invoice.pk]))
        self.invoice.refresh_from_db()
        payment_record = PaymentRecord.objects.get(
            invoice=self.invoice,
            provider=PaymentRecord.PROVIDER_MANUAL,
        )
        self.assertEqual(self.invoice.status, Invoice.STATUS_SENT)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)
        self.assertEqual(payment_record.manual_customer_amount, self.invoice.total_amount)
        self.assertEqual(payment_record.manual_customer_bank_reference, "CUSTOMER-BANK-REF-001")
        self.assertEqual(payment_record.manual_customer_notes, "Transferred from DBS.")
        self.assertEqual(payment_record.manual_customer_submitted_by, self.customer_user)
        self.assertIsNotNone(payment_record.manual_customer_submitted_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.bank_transfer.notice_submitted",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )

    def test_public_invoice_page_can_submit_bank_transfer_notice(self):
        response = self.client.post(
            reverse("payment-bank-transfer-notice-public", args=[self.invoice.public_view_token]),
            data={
                "manual_customer_amount": str(self.invoice.total_amount),
                "manual_customer_transfer_date": timezone.localdate().isoformat(),
                "manual_customer_bank_reference": "PUBLIC-BANK-REF-001",
                "manual_customer_notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("invoice-public-view", args=[self.invoice.public_view_token]))
        self.invoice.refresh_from_db()
        payment_record = PaymentRecord.objects.get(
            invoice=self.invoice,
            provider=PaymentRecord.PROVIDER_MANUAL,
        )
        self.assertEqual(self.invoice.status, Invoice.STATUS_SENT)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)
        self.assertEqual(payment_record.manual_customer_bank_reference, "PUBLIC-BANK-REF-001")
        self.assertIsNone(payment_record.manual_customer_submitted_by)

    def test_customer_bank_transfer_notice_rejects_invalid_amount_and_future_date(self):
        self.client.login(username="customer_stripe", password="TempPass123!")

        bad_amount_response = self.client.post(
            reverse("payment-bank-transfer-notice-customer", args=[self.invoice.pk]),
            data={
                "manual_customer_amount": "1.00",
                "manual_customer_transfer_date": timezone.localdate().isoformat(),
                "manual_customer_bank_reference": "BAD-AMOUNT",
                "manual_customer_notes": "",
            },
        )
        future_date_response = self.client.post(
            reverse("payment-bank-transfer-notice-customer", args=[self.invoice.pk]),
            data={
                "manual_customer_amount": str(self.invoice.total_amount),
                "manual_customer_transfer_date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "manual_customer_bank_reference": "FUTURE-DATE",
                "manual_customer_notes": "",
            },
        )

        self.assertEqual(bad_amount_response.status_code, 400)
        self.assertEqual(future_date_response.status_code, 400)
        self.invoice.refresh_from_db()
        payment_record = PaymentRecord.objects.get(
            invoice=self.invoice,
            provider=PaymentRecord.PROVIDER_MANUAL,
        )
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)
        self.assertIsNone(payment_record.manual_customer_submitted_at)

    def test_bank_transfer_is_hidden_when_required_bank_details_are_blank(self):
        PaymentBankDetails.objects.update_or_create(
            pk=1,
            defaults={
                "account_name": "",
                "bank_name": "",
                "account_number": "",
                "paynow_id": "",
                "bic": "",
                "instructions": "",
            },
        )

        response = self.client.get(reverse("invoice-public-view", args=[self.invoice.public_view_token]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Bank Transfer")
        self.assertNotContains(response, "001-234567-8")
        self.assertFalse(
            PaymentRecord.objects.filter(
                invoice=self.invoice,
                provider=PaymentRecord.PROVIDER_MANUAL,
            ).exists()
        )

    def test_admin_can_update_bank_transfer_details_from_webpage(self):
        admin = User.objects.create_user(username="bank_settings_admin", password="TempPass123!")
        admin.role_profile.role = ADMIN
        admin.role_profile.save(update_fields=["role", "updated_at"])
        self.client.login(username="bank_settings_admin", password="TempPass123!")

        get_response = self.client.get(reverse("payment-bank-transfer-settings"))
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Bank Transfer Details")

        response = self.client.post(
            reverse("payment-bank-transfer-settings"),
            data={
                "account_name": "Updated Billing Pte Ltd",
                "bank_name": "Updated Bank",
                "account_number": "111-222333-4",
                "paynow_id": "UEN-UPDATED",
                "bic": "UPDTSGSG",
                "instructions": "Use the invoice reference.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("payment-bank-transfer-settings"))
        details = PaymentBankDetails.load()
        self.assertEqual(details.bank_name, "Updated Bank")
        self.assertEqual(details.account_number, "111-222333-4")
        self.assertEqual(details.updated_by, admin)

        audit_log = AuditLog.objects.filter(action="payment.bank_transfer_details.updated").latest("created_at")
        self.assertEqual(audit_log.user, admin)
        self.assertIn("account_number", audit_log.metadata["changed_fields"])
        self.assertEqual(audit_log.metadata["account_number_before"], "***3210")
        self.assertEqual(audit_log.metadata["account_number_after"], "***3334")
        self.assertNotIn("987-654321-0", str(audit_log.metadata))
        self.assertNotIn("111-222333-4", str(audit_log.metadata))

    def test_superadmin_can_open_bank_transfer_settings_page(self):
        superadmin = User.objects.create_superuser(
            username="bank_settings_superadmin",
            password="TempPass123!",
            email="bank_settings_superadmin@example.com",
        )
        self.assertEqual(superadmin.role_profile.role, SUPERADMIN)
        self.client.login(username="bank_settings_superadmin", password="TempPass123!")

        response = self.client.get(reverse("payment-bank-transfer-settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Company Bank Details")

    def test_non_admin_roles_cannot_open_bank_transfer_settings_page(self):
        for user in [self.finance_user, self.customer_user, self.staff_user]:
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("payment-bank-transfer-settings"))
                self.assertEqual(response.status_code, 403)
                self.client.logout()

    def test_anonymous_user_is_redirected_from_bank_transfer_settings_page(self):
        response = self.client.get(reverse("payment-bank-transfer-settings"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_non_payable_invoice_does_not_show_bank_transfer_instructions(self):
        for status in [Invoice.STATUS_DRAFT, Invoice.STATUS_PAID, Invoice.STATUS_REFUNDED]:
            with self.subTest(status=status):
                PaymentRecord.objects.filter(invoice=self.invoice).delete()
                self.invoice.status = status
                self.invoice.save(update_fields=["status", "updated_at"])

                response = self.client.get(reverse("invoice-public-view", args=[self.invoice.public_view_token]))

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Bank Transfer")
                self.assertFalse(
                    PaymentRecord.objects.filter(
                        invoice=self.invoice,
                        provider=PaymentRecord.PROVIDER_MANUAL,
                    ).exists()
                )

    def test_internal_invoice_detail_shows_bank_transfer_confirmation_action(self):
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.get(reverse("invoice-detail", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        payment_record = PaymentRecord.objects.get(
            invoice=self.invoice,
            provider=PaymentRecord.PROVIDER_MANUAL,
        )
        self.assertContains(response, "Confirm Verified Bank Transfer")
        self.assertContains(response, payment_record.payment_reference)
        self.assertContains(response, "Test Receiving Bank")
        self.assertContains(response, "987-654321-0")
        self.assertContains(response, "Amount received")
        self.assertContains(response, "Bank reference")

    def test_internal_invoice_detail_shows_customer_submitted_bank_transfer_notice(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-SUBMIT",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            manual_customer_amount=self.invoice.total_amount,
            manual_customer_transfer_date=timezone.localdate(),
            manual_customer_bank_reference="CUSTOMER-SUBMITTED-REF",
            manual_customer_notes="Paid before lunch.",
            manual_customer_submitted_by=self.customer_user,
            manual_customer_submitted_at=timezone.now(),
        )
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.get(reverse("invoice-detail", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customer transfer notice awaiting verification")
        self.assertContains(response, payment_record.payment_reference)
        self.assertContains(response, "CUSTOMER-SUBMITTED-REF")
        self.assertContains(response, "Paid before lunch.")

    def test_finance_cannot_confirm_bank_transfer_without_proof(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-NOPROOF",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
        )
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.post(reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 400)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)

    def test_finance_cannot_confirm_bank_transfer_with_mismatched_amount(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-BADAMT",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
        )
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.post(
            reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]),
            data={
                "manual_received_amount": "1.00",
                "manual_received_date": timezone.localdate().isoformat(),
                "manual_bank_reference": "BANKREF-BAD-AMOUNT",
                "manual_confirmation_notes": "",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)

    def test_finance_cannot_confirm_bank_transfer_with_future_received_date(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-FUTURE",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
        )
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.post(
            reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]),
            data={
                "manual_received_amount": str(self.invoice.total_amount),
                "manual_received_date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "manual_bank_reference": "BANKREF-FUTURE",
                "manual_confirmation_notes": "",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)

    def test_finance_can_confirm_bank_transfer_and_mark_invoice_paid(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-ABC123",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
        )
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.post(
            reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]),
            data={
                "manual_received_amount": str(self.invoice.total_amount),
                "manual_received_date": timezone.localdate().isoformat(),
                "manual_bank_reference": "BANKREF-ABC123",
                "manual_confirmation_notes": "Matched in bank portal.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("invoice-detail", args=[self.invoice.pk]))
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertIsNotNone(payment_record.paid_at)
        self.assertEqual(payment_record.manual_received_amount, self.invoice.total_amount)
        self.assertEqual(payment_record.manual_bank_reference, "BANKREF-ABC123")
        self.assertEqual(payment_record.manual_confirmed_by, self.finance_user)
        self.assertIsNotNone(payment_record.manual_confirmed_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.bank_transfer.confirmed",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )

    def test_bank_transfer_confirmation_rejects_existing_stripe_success(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-DUP",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            manual_customer_amount=self.invoice.total_amount,
            manual_customer_transfer_date=timezone.localdate(),
            manual_customer_bank_reference="CUSTOMER-DUP-REF",
            manual_customer_submitted_by=self.customer_user,
            manual_customer_submitted_at=timezone.now(),
        )
        stripe_payment = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-STRIPE-DUP-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            paid_at=timezone.now(),
            stripe_checkout_session_id="cs_test_bank_duplicate_1",
        )
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.post(
            reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]),
            data={
                "manual_received_amount": str(self.invoice.total_amount),
                "manual_received_date": timezone.localdate().isoformat(),
                "manual_bank_reference": "CUSTOMER-DUP-REF",
                "manual_confirmation_notes": "Trying to confirm after Stripe.",
            },
        )

        self.assertEqual(response.status_code, 302)
        payment_record.refresh_from_db()
        stripe_payment.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)
        self.assertEqual(stripe_payment.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.manual_customer_bank_reference, "CUSTOMER-DUP-REF")
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.duplicate_rejected",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )

    def test_finance_can_confirm_customer_submitted_bank_transfer_notice(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-CUSTOMER",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            manual_customer_amount=self.invoice.total_amount,
            manual_customer_transfer_date=timezone.localdate(),
            manual_customer_bank_reference="CUSTOMER-VERIFY-REF",
            manual_customer_notes="Please verify.",
            manual_customer_submitted_by=self.customer_user,
            manual_customer_submitted_at=timezone.now(),
        )
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.post(
            reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]),
            data={
                "manual_received_amount": str(self.invoice.total_amount),
                "manual_received_date": timezone.localdate().isoformat(),
                "manual_bank_reference": "CUSTOMER-VERIFY-REF",
                "manual_confirmation_notes": "Matched customer notice to bank portal.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertEqual(payment_record.manual_bank_reference, "CUSTOMER-VERIFY-REF")
        audit_log = AuditLog.objects.filter(
            action="payment.bank_transfer.confirmed",
            target_type="invoice",
            target_id=str(self.invoice.id),
        ).latest("created_at")
        self.assertEqual(audit_log.metadata["manual_customer_bank_reference"], "CUSTOMER-VERIFY-REF")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_finance_confirmation_sends_bank_transfer_success_email_to_customer(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-EMAIL",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            manual_customer_amount=self.invoice.total_amount,
            manual_customer_transfer_date=timezone.localdate(),
            manual_customer_bank_reference="CUSTOMER-EMAIL-REF",
            manual_customer_submitted_by=self.customer_user,
            manual_customer_submitted_at=timezone.now(),
        )
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.post(
            reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]),
            data={
                "manual_received_amount": str(self.invoice.total_amount),
                "manual_received_date": timezone.localdate().isoformat(),
                "manual_bank_reference": "CUSTOMER-EMAIL-REF",
                "manual_confirmation_notes": "Matched in bank portal.",
            },
        )

        self.assertEqual(response.status_code, 302)
        payment_record.refresh_from_db()
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Bank transfer verified", mail.outbox[0].subject)
        self.assertIn("Payment Method: Bank Transfer", mail.outbox[0].body)
        self.assertIn("CUSTOMER-EMAIL-REF", mail.outbox[0].body)
        self.assertTrue(
            EmailDeliveryLog.objects.filter(
                template_key="bank_transfer_payment_success_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.email.sent",
                target_type="invoice",
                target_id=str(self.invoice.id),
                metadata__payment_outcome="bank_transfer_successful",
            ).exists()
        )

    def test_finance_can_confirm_bank_transfer_for_overdue_invoice_and_mark_it_paid(self):
        self.invoice.status = Invoice.STATUS_OVERDUE
        self.invoice.issue_date = timezone.localdate() - timedelta(days=10)
        self.invoice.due_date = timezone.localdate() - timedelta(days=2)
        self.invoice.save(update_fields=["status", "issue_date", "due_date", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-OVERDUE",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
        )
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.post(
            reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]),
            data={
                "manual_received_amount": str(self.invoice.total_amount),
                "manual_received_date": timezone.localdate().isoformat(),
                "manual_bank_reference": "BANKREF-OVERDUE",
                "manual_confirmation_notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("invoice-detail", args=[self.invoice.pk]))
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_SUCCEEDED)

    def test_customer_and_staff_cannot_confirm_bank_transfer(self):
        PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-DENIED",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
        )

        self.client.login(username="customer_stripe", password="TempPass123!")
        customer_response = self.client.post(reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]))
        self.client.logout()
        self.client.login(username="staff_stripe", password="TempPass123!")
        staff_response = self.client.post(reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]))

        self.assertEqual(customer_response.status_code, 403)
        self.assertEqual(staff_response.status_code, 403)
        self.invoice.refresh_from_db()
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)

    def test_confirm_already_paid_manual_payment_does_not_duplicate_records(self):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-PAID",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            paid_at=timezone.now(),
        )
        self.client.login(username="finance_stripe", password="TempPass123!")

        response = self.client.post(reverse("payment-bank-transfer-confirm", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            PaymentRecord.objects.filter(
                invoice=self.invoice,
                provider=PaymentRecord.PROVIDER_MANUAL,
            ).count(),
            1,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_webhook_completed_marks_invoice_paid(self, construct_event_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-WEBHOOK-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_webhook_1",
        )
        construct_event_mock.return_value = {
            "id": "evt_test_1001",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_webhook_1",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_1001",
                    "metadata": {
                        "payment_record_id": str(payment_record.id),
                        "invoice_id": str(self.invoice.id),
                    },
                }
            },
        }

        response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertEqual(payment_record.external_transaction_id, "pi_test_1001")
        self.assertEqual(StripeWebhookEvent.objects.filter(event_id="evt_test_1001").count(), 1)
        email_log = EmailDeliveryLog.objects.get(
            template_key="stripe_payment_success_invoice_email_v1",
            related_object_id=str(self.invoice.id),
        )
        self.assertEqual(email_log.status, EmailDeliveryLog.STATUS_SENT)
        self.assertEqual(email_log.metadata.get("payment_reference"), payment_record.payment_reference)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Payment Status: Successful", mail.outbox[0].body)
        self.assertIn(self.invoice.invoice_number, mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_second_stripe_attempt_cannot_finalize_paid_invoice(self, construct_event_mock):
        first_payment = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-WEBHOOK-DUP-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_duplicate_first",
        )
        second_payment = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-WEBHOOK-DUP-002",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_duplicate_second",
        )
        first_event = {
            "id": "evt_test_duplicate_first",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_duplicate_first",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_duplicate_first",
                    "metadata": {"payment_record_id": str(first_payment.id)},
                }
            },
        }
        second_event = {
            "id": "evt_test_duplicate_second",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_duplicate_second",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_duplicate_second",
                    "metadata": {"payment_record_id": str(second_payment.id)},
                }
            },
        }
        construct_event_mock.side_effect = [first_event, second_event]

        first_response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )
        second_response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.invoice.refresh_from_db()
        first_payment.refresh_from_db()
        second_payment.refresh_from_db()
        second_event_record = StripeWebhookEvent.objects.get(event_id="evt_test_duplicate_second")
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(first_payment.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertEqual(second_payment.status, PaymentRecord.STATUS_PENDING)
        self.assertEqual(second_payment.external_transaction_id, "pi_test_duplicate_second")
        self.assertEqual(second_event_record.status, StripeWebhookEvent.STATUS_IGNORED)
        self.assertEqual(
            PaymentRecord.objects.filter(
                invoice=self.invoice,
                status=PaymentRecord.STATUS_SUCCEEDED,
            ).count(),
            1,
        )
        self.assertEqual(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_payment_success_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).count(),
            1,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.duplicate_rejected",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_stripe_webhook_cannot_replace_confirmed_bank_transfer(self, construct_event_mock):
        manual_payment = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="BANK-INV-STRIPE-1001-SUCCEEDED",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            paid_at=timezone.now(),
            manual_received_amount=self.invoice.total_amount,
            manual_received_date=timezone.localdate(),
            manual_bank_reference="BANK-WON-FIRST",
        )
        stripe_payment = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-STRIPE-AFTER-BANK",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_after_bank_1",
        )
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        construct_event_mock.return_value = {
            "id": "evt_test_after_bank_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_after_bank_1",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_after_bank_1",
                    "metadata": {"payment_record_id": str(stripe_payment.id)},
                }
            },
        }

        response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(response.status_code, 200)
        manual_payment.refresh_from_db()
        stripe_payment.refresh_from_db()
        event = StripeWebhookEvent.objects.get(event_id="evt_test_after_bank_1")
        self.assertEqual(manual_payment.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertEqual(stripe_payment.status, PaymentRecord.STATUS_PENDING)
        self.assertEqual(stripe_payment.external_transaction_id, "pi_test_after_bank_1")
        self.assertEqual(event.status, StripeWebhookEvent.STATUS_IGNORED)
        self.assertEqual(
            PaymentRecord.objects.filter(
                invoice=self.invoice,
                status=PaymentRecord.STATUS_SUCCEEDED,
            ).count(),
            1,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_webhook_completed_rejects_amount_mismatch(self, construct_event_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-WEBHOOK-MISMATCH-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_webhook_mismatch_1",
        )
        construct_event_mock.return_value = {
            "id": "evt_test_mismatch_1001",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_webhook_mismatch_1",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_mismatch_1001",
                    "amount_total": 100,
                    "currency": "sgd",
                    "metadata": {
                        "payment_record_id": str(payment_record.id),
                        "invoice_id": str(self.invoice.id),
                    },
                }
            },
        }

        response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        event = StripeWebhookEvent.objects.get(event_id="evt_test_mismatch_1001")
        self.assertEqual(event.status, StripeWebhookEvent.STATUS_IGNORED)
        self.assertIn("Stripe amount mismatch", event.error_message)
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_payment_success_email_service_sends_invoice_pdf(self):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-SUCCESS-EMAIL-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            paid_at=timezone.now(),
            stripe_checkout_session_id="cs_test_success_email_1",
        )

        success, log = send_stripe_payment_success_email(
            invoice=self.invoice,
            payment_record=payment_record,
            public_invoice_url="http://testserver/invoices/view/test-token/",
        )

        self.assertTrue(success)
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_SENT)
        self.assertEqual(log.template_key, "stripe_payment_success_invoice_email_v1")
        self.assertEqual(log.related_object_type, "invoice")
        self.assertEqual(log.related_object_id, str(self.invoice.id))
        self.assertEqual(log.metadata.get("payment_reference"), payment_record.payment_reference)
        self.assertEqual(log.metadata.get("pdf_attachment_added"), True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Payment received", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [self.invoice.customer.email])
        self.assertIn(payment_record.payment_reference, mail.outbox[0].body)
        self.assertIn("Payment Status: Successful", mail.outbox[0].body)
        self.assertIn("Total Amount", mail.outbox[0].body)
        self.assertIn("http://testserver/invoices/view/test-token/", mail.outbox[0].body)
        self.assertTrue(mail.outbox[0].attachments)
        attachment_name, _attachment_content, attachment_type = mail.outbox[0].attachments[0]
        self.assertIn(self.invoice.invoice_number, attachment_name)
        self.assertEqual(attachment_type, "application/pdf")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("notifications.services.generate_invoice_pdf", side_effect=RuntimeError("PDF unavailable"))
    def test_payment_success_email_service_requires_pdf_attachment(self, _pdf_mock):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-SUCCESS-EMAIL-002",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            paid_at=timezone.now(),
            stripe_checkout_session_id="cs_test_success_email_2",
        )

        success, log = send_stripe_payment_success_email(
            invoice=self.invoice,
            payment_record=payment_record,
            public_invoice_url="http://testserver/invoices/view/test-token/",
        )

        self.assertFalse(success)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_FAILED)
        self.assertIn("Invoice PDF could not be generated", log.error_message)
        self.assertEqual(log.metadata.get("pdf_attachment_added"), False)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_payment_failed_email_service_sends_invoice_details(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-FAILED-EMAIL-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_FAILED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_failed_email_1",
        )

        success, log = send_stripe_payment_failed_email(
            invoice=self.invoice,
            payment_record=payment_record,
            public_invoice_url="http://testserver/invoices/view/test-token/",
            failure_reason="Stripe reported that the payment failed.",
        )

        self.assertTrue(success)
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_SENT)
        self.assertEqual(log.template_key, "stripe_payment_failed_invoice_email_v1")
        self.assertEqual(log.metadata.get("payment_reference"), payment_record.payment_reference)
        self.assertEqual(log.metadata.get("payment_outcome"), "failed")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Payment Status: Failed", mail.outbox[0].body)
        self.assertIn("Stripe reported that the payment failed.", mail.outbox[0].body)
        self.assertIn(self.invoice.invoice_number, mail.outbox[0].body)
        self.assertIn("Total Amount", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_refund_success_email_service_sends_invoice_details(self):
        self.invoice.status = Invoice.STATUS_SENT
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REFUND-EMAIL-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_REFUNDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            external_transaction_id="pi_refund_email_001",
            paid_at=timezone.now(),
        )

        success, log = send_stripe_refund_success_email(
            invoice=self.invoice,
            payment_record=payment_record,
            public_invoice_url="http://testserver/invoices/view/test-token/",
        )

        self.assertTrue(success)
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_SENT)
        self.assertEqual(log.template_key, "stripe_refund_success_invoice_email_v1")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Refund Status: Successful", mail.outbox[0].body)
        self.assertIn(self.invoice.invoice_number, mail.outbox[0].body)
        self.assertIn(payment_record.payment_reference, mail.outbox[0].body)
        self.assertIn("Total Amount", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_refund_failed_email_service_sends_failure_reason(self):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REFUND-EMAIL-002",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            external_transaction_id="pi_refund_email_002",
            paid_at=timezone.now(),
        )

        success, log = send_stripe_refund_failed_email(
            invoice=self.invoice,
            payment_record=payment_record,
            public_invoice_url="http://testserver/invoices/view/test-token/",
            failure_reason="The refund destination account was closed.",
        )

        self.assertTrue(success)
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_SENT)
        self.assertEqual(log.template_key, "stripe_refund_failed_invoice_email_v1")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Refund Status: Failed", mail.outbox[0].body)
        self.assertIn("The refund destination account was closed.", mail.outbox[0].body)
        self.assertIn(self.invoice.invoice_number, mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_webhook_duplicate_event_is_idempotent(self, construct_event_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-WEBHOOK-002",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_webhook_2",
        )
        event_payload = {
            "id": "evt_test_duplicate_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_webhook_2",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_2002",
                    "metadata": {
                        "payment_record_id": str(payment_record.id),
                    },
                }
            },
        }
        construct_event_mock.return_value = event_payload

        first = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )
        second = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(StripeWebhookEvent.objects.filter(event_id="evt_test_duplicate_1").count(), 1)
        self.assertEqual(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_payment_success_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).count(),
            1,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_webhook_async_failed_marks_payment_failed_and_invoice_not_paid(self, construct_event_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-WEBHOOK-FAILED-1",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_failed_1",
        )
        construct_event_mock.return_value = {
            "id": "evt_test_failed_1",
            "type": "checkout.session.async_payment_failed",
            "data": {
                "object": {
                    "id": "cs_test_failed_1",
                    "payment_status": "unpaid",
                    "metadata": {
                        "payment_record_id": str(payment_record.id),
                    },
                }
            },
        }

        response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_FAILED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.stripe.failed",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )
        self.assertTrue(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_payment_failed_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Payment Status: Failed", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_webhook_expired_marks_payment_cancelled_and_invoice_not_paid(self, construct_event_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-WEBHOOK-EXPIRED-1",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_expired_1",
        )
        construct_event_mock.return_value = {
            "id": "evt_test_expired_1",
            "type": "checkout.session.expired",
            "data": {
                "object": {
                    "id": "cs_test_expired_1",
                    "payment_status": "unpaid",
                    "metadata": {
                        "payment_record_id": str(payment_record.id),
                    },
                }
            },
        }

        response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_CANCELLED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.stripe.cancelled",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )
        self.assertTrue(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_payment_failed_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Stripe Checkout expired", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_webhook_retries_failed_event_and_allows_reprocessing(self, construct_event_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-WEBHOOK-RETRY-1",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_retry_1",
        )
        event_payload = {
            "id": "evt_test_retry_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_retry_1",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_retry_1",
                    "metadata": {
                        "payment_record_id": str(payment_record.id),
                    },
                }
            },
        }
        construct_event_mock.return_value = event_payload

        self.client.raise_request_exception = False
        with patch("payments.services._mark_success_from_session", side_effect=RuntimeError("temporary failure")):
            first = self.client.post(
                reverse("payment-stripe-webhook"),
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
            )

        failed_event = StripeWebhookEvent.objects.get(event_id="evt_test_retry_1")
        self.assertEqual(first.status_code, 500)
        self.assertEqual(failed_event.status, StripeWebhookEvent.STATUS_FAILED)

        second = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        failed_event.refresh_from_db()
        payment_record.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(failed_event.status, StripeWebhookEvent.STATUS_PROCESSED)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(StripeWebhookEvent.objects.filter(event_id="evt_test_retry_1").count(), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_webhook_refund_updated_marks_payment_refunded_and_invoice_not_paid(self, construct_event_mock):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REFUND-WEBHOOK-1",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_refund_1",
            external_transaction_id="pi_test_refund_1",
            paid_at=timezone.now(),
        )
        construct_event_mock.return_value = {
            "id": "evt_test_refund_1",
            "type": "refund.updated",
            "data": {
                "object": {
                    "id": "re_test_refund_1",
                    "status": "succeeded",
                    "payment_intent": "pi_test_refund_1",
                    "metadata": {
                        "payment_record_id": str(payment_record.id),
                    },
                }
            },
        }

        response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(response.status_code, 200)
        payment_record.refresh_from_db()
        self.invoice.refresh_from_db()
        event = StripeWebhookEvent.objects.get(event_id="evt_test_refund_1")
        self.assertEqual(event.status, StripeWebhookEvent.STATUS_PROCESSED)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_REFUNDED)
        self.assertEqual(self.invoice.status, Invoice.STATUS_REFUNDED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.refund.succeeded",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )
        self.assertTrue(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_refund_success_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Refund Status: Successful", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_webhook_refund_failed_keeps_payment_succeeded(self, construct_event_mock):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REFUND-WEBHOOK-2",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_refund_2",
            external_transaction_id="pi_test_refund_2",
            paid_at=timezone.now(),
        )
        construct_event_mock.return_value = {
            "id": "evt_test_refund_2",
            "type": "refund.failed",
            "data": {
                "object": {
                    "id": "re_test_refund_2",
                    "status": "failed",
                    "failure_reason": "lost_or_stolen_card",
                    "payment_intent": "pi_test_refund_2",
                    "metadata": {
                        "payment_record_id": str(payment_record.id),
                    },
                }
            },
        }

        response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(response.status_code, 200)
        payment_record.refresh_from_db()
        self.invoice.refresh_from_db()
        event = StripeWebhookEvent.objects.get(event_id="evt_test_refund_2")
        self.assertEqual(event.status, StripeWebhookEvent.STATUS_PROCESSED)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.refund.failed",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )
        self.assertTrue(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_refund_failed_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Refund Status: Failed", mail.outbox[0].body)
        self.assertIn("lost_or_stolen_card", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.construct_webhook_event")
    def test_webhook_refund_duplicate_event_is_idempotent(self, construct_event_mock):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REFUND-WEBHOOK-3",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_refund_3",
            external_transaction_id="pi_test_refund_3",
            paid_at=timezone.now(),
        )
        event_payload = {
            "id": "evt_test_refund_duplicate_1",
            "type": "refund.updated",
            "data": {
                "object": {
                    "id": "re_test_refund_3",
                    "status": "succeeded",
                    "payment_intent": "pi_test_refund_3",
                    "metadata": {
                        "payment_record_id": str(payment_record.id),
                    },
                }
            },
        }
        construct_event_mock.return_value = event_payload

        first = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )
        second = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=abc",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        payment_record.refresh_from_db()
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_REFUNDED)
        self.assertEqual(StripeWebhookEvent.objects.filter(event_id="evt_test_refund_duplicate_1").count(), 1)
        self.assertEqual(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_refund_success_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).count(),
            1,
        )

    @patch("payments.views.construct_webhook_event", side_effect=Exception("bad signature"))
    def test_webhook_invalid_signature_returns_400(self, _construct_event_mock):
        response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="invalid",
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.retrieve_checkout_session")
    def test_success_page_renders_session_data(self, retrieve_session_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-SUCCESS-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_success_1",
        )
        retrieve_session_mock.return_value = Mock(payment_status="paid")

        response = self.client.get(
            reverse("payment-checkout-success"),
            data={"session_id": payment_record.stripe_checkout_session_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment processing")
        self.assertContains(response, payment_record.payment_reference)
        self.assertTrue(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_payment_success_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.retrieve_checkout_session")
    def test_repeated_success_page_does_not_duplicate_payment_email(self, retrieve_session_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-SUCCESS-REPEAT-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_success_repeat_1",
        )
        retrieve_session_mock.return_value = Mock(
            payment_status="paid",
            payment_intent="pi_test_success_repeat_1",
            amount_total=10900,
            currency="sgd",
            metadata={},
        )

        first = self.client.get(
            reverse("payment-checkout-success"),
            data={"session_id": payment_record.stripe_checkout_session_id},
        )
        second = self.client.get(
            reverse("payment-checkout-success"),
            data={"session_id": payment_record.stripe_checkout_session_id},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_SUCCEEDED)
        self.assertEqual(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_payment_success_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    @patch("payments.views.retrieve_checkout_session")
    def test_success_page_rejects_amount_mismatch(self, retrieve_session_mock):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-SUCCESS-MISMATCH-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_success_mismatch_1",
        )
        retrieve_session_mock.return_value = Mock(
            payment_status="paid",
            payment_intent="pi_test_success_mismatch_1",
            amount_total=100,
            currency="sgd",
            metadata={},
        )

        response = self.client.get(
            reverse("payment-checkout-success"),
            data={"session_id": payment_record.stripe_checkout_session_id},
        )

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        payment_record.refresh_from_db()
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.stripe.redirect_rejected",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )

    def test_cancel_page_renders(self):
        response = self.client.get(reverse("payment-checkout-cancel"), data={"next": "/invoices/"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment cancelled")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_cancel_page_marks_payment_cancelled_when_reference_is_present(self):
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-CANCEL-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_cancel_1",
        )

        response = self.client.get(
            reverse("payment-checkout-cancel"),
            data={
                "next": "/invoices/",
                "payment_reference": payment_record.payment_reference,
            },
        )

        self.assertEqual(response.status_code, 200)
        payment_record.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_CANCELLED)
        self.assertNotEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.checkout.cancelled",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )
        self.assertTrue(
            EmailDeliveryLog.objects.filter(
                template_key="stripe_payment_failed_invoice_email_v1",
                related_object_id=str(self.invoice.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Payment Status: Failed", mail.outbox[0].body)

    @override_settings(STRIPE_SECRET_KEY="")
    def test_create_checkout_requires_stripe_secret_key(self):
        with self.assertRaises(ImproperlyConfigured):
            create_checkout_for_invoice(
                invoice=self.invoice,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                initiated_by=self.customer_user,
            )
        self.assertEqual(PaymentRecord.objects.count(), 0)

    @override_settings(STRIPE_SECRET_KEY="sk_test_guardrails")
    @patch("payments.services._import_stripe")
    def test_create_checkout_reuses_existing_pending_stripe_session(self, import_stripe_mock):
        existing_payment = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REUSE-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_reuse_1",
        )
        stripe_mock = Mock()
        import_stripe_mock.return_value = stripe_mock

        payment_record = create_checkout_for_invoice(
            invoice=self.invoice,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            initiated_by=self.customer_user,
        )

        self.assertEqual(payment_record.id, existing_payment.id)
        stripe_mock.checkout.Session.create.assert_not_called()
        self.assertEqual(
            PaymentRecord.objects.filter(
                invoice=self.invoice,
                provider=PaymentRecord.PROVIDER_STRIPE,
            ).count(),
            1,
        )

    @override_settings(STRIPE_SECRET_KEY="sk_test_guardrails")
    @patch("payments.services._import_stripe")
    def test_failed_and_cancelled_stripe_records_do_not_block_later_checkout(self, import_stripe_mock):
        PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-FAILED-OLD-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_FAILED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_failed_old_1",
        )
        PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-CANCELLED-OLD-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_CANCELLED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            stripe_checkout_session_id="cs_test_cancelled_old_1",
        )
        stripe_mock = Mock()
        stripe_mock.checkout.Session.create.return_value = Mock(
            id="cs_test_later_valid_1",
            payment_intent="pi_test_later_valid_1",
        )
        import_stripe_mock.return_value = stripe_mock

        payment_record = create_checkout_for_invoice(
            invoice=self.invoice,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            initiated_by=self.customer_user,
        )

        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)
        self.assertEqual(payment_record.stripe_checkout_session_id, "cs_test_later_valid_1")
        stripe_mock.checkout.Session.create.assert_called_once()

    def test_paid_invoice_cannot_start_another_checkout(self):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-PAID-BLOCK-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            paid_at=timezone.now(),
            stripe_checkout_session_id="cs_test_paid_block_1",
        )

        with self.assertRaises(ValueError):
            create_checkout_for_invoice(
                invoice=self.invoice,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                initiated_by=self.customer_user,
            )

    def test_refund_endpoint_denies_customer_role(self):
        self.client.login(username="customer_stripe", password="TempPass123!")
        response = self.client.post(reverse("payment-refund-invoice", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 403)

    def test_refund_endpoint_validation_requires_existing_stripe_payment(self):
        self.client.login(username="finance_stripe", password="TempPass123!")
        response = self.client.post(reverse("payment-refund-invoice", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("invoice-detail", args=[self.invoice.pk]))

    def test_refund_endpoint_validation_rejects_non_succeeded_payment(self):
        PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REFUND-VAL-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_PENDING,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            external_transaction_id="pi_pending_001",
        )
        self.client.login(username="finance_stripe", password="TempPass123!")
        response = self.client.post(reverse("payment-refund-invoice", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 302)
        payment_record = PaymentRecord.objects.get(payment_reference="PAY-REFUND-VAL-001")
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)

    @patch("payments.views.create_full_refund_for_payment")
    def test_refund_endpoint_success_writes_audit_logs(self, refund_mock):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REFUND-AUDIT-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            external_transaction_id="pi_refund_audit_001",
            paid_at=timezone.now(),
        )
        refund_mock.return_value = Mock(status="succeeded")

        self.client.login(username="finance_stripe", password="TempPass123!")
        response = self.client.post(reverse("payment-refund-invoice", args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("invoice-detail", args=[self.invoice.pk]))
        refund_mock.assert_called_once()
        self.assertEqual(refund_mock.call_args.kwargs["payment_record"].id, payment_record.id)
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.refund.requested",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="payment.refund.succeeded",
                target_type="invoice",
                target_id=str(self.invoice.id),
            ).exists()
        )

    @override_settings(STRIPE_SECRET_KEY="sk_test_guardrails")
    @patch("payments.services._import_stripe")
    def test_create_full_refund_marks_payment_refunded_and_reopens_invoice(self, import_stripe_mock):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REFUND-OK-001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            external_transaction_id="pi_refund_ok_001",
            paid_at=timezone.now(),
        )
        stripe_mock = Mock()
        stripe_mock.Refund.create.return_value = Mock(
            id="re_test_001",
            status="succeeded",
            payment_intent="pi_refund_ok_001",
        )
        import_stripe_mock.return_value = stripe_mock

        refund = create_full_refund_for_payment(
            payment_record=payment_record,
            initiated_by=self.finance_user,
        )

        self.assertEqual(getattr(refund, "status"), "succeeded")
        payment_record.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_REFUNDED)
        self.assertEqual(self.invoice.status, Invoice.STATUS_REFUNDED)
        stripe_mock.Refund.create.assert_called_once()

    @override_settings(STRIPE_SECRET_KEY="sk_test_guardrails")
    @patch("payments.services._import_stripe")
    def test_create_full_refund_syncs_local_state_when_stripe_already_refunded(self, import_stripe_mock):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save(update_fields=["status", "updated_at"])
        payment_record = PaymentRecord.objects.create(
            invoice=self.invoice,
            payment_reference="PAY-REFUND-OK-002",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=self.invoice.total_amount,
            currency=self.invoice.currency,
            external_transaction_id="pi_refund_ok_002",
            paid_at=timezone.now(),
        )
        stripe_mock = Mock()
        stripe_mock.Refund.create.side_effect = Exception("Charge ch_test has already been refunded.")
        import_stripe_mock.return_value = stripe_mock

        refund = create_full_refund_for_payment(
            payment_record=payment_record,
            initiated_by=self.finance_user,
        )

        self.assertEqual(getattr(refund, "status"), "succeeded")
        payment_record.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_REFUNDED)
        self.assertEqual(self.invoice.status, Invoice.STATUS_REFUNDED)

    @override_settings(STRIPE_SECRET_KEY="sk_test_guardrails")
    @patch("payments.services._import_stripe")
    def test_create_checkout_uses_card_only_for_non_sgd_currency(self, import_stripe_mock):
        usd_invoice = Invoice.objects.create(
            invoice_number="INV-STRIPE-USD-1001",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="USD",
            subtotal=Decimal("80.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("80.00"),
        )
        stripe_mock = Mock()
        stripe_mock.checkout.Session.create.return_value = Mock(
            id="cs_test_usd_1",
            payment_intent="pi_test_usd_1",
        )
        import_stripe_mock.return_value = stripe_mock

        payment_record = create_checkout_for_invoice(
            invoice=usd_invoice,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            initiated_by=self.customer_user,
        )

        self.assertEqual(payment_record.currency, "USD")
        self.assertEqual(payment_record.provider, PaymentRecord.PROVIDER_STRIPE)
        self.assertEqual(payment_record.status, PaymentRecord.STATUS_PENDING)
        stripe_mock.checkout.Session.create.assert_called_once()
        create_kwargs = stripe_mock.checkout.Session.create.call_args.kwargs
        self.assertEqual(create_kwargs["payment_method_types"], ["card"])
