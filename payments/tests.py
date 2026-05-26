from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.roles import CUSTOMER, FINANCE
from core.models import AuditLog
from invoicing.models import Customer, Invoice
from notifications.models import EmailDeliveryLog
from notifications.services import (
    send_stripe_payment_failed_email,
    send_stripe_payment_success_email,
    send_stripe_refund_failed_email,
    send_stripe_refund_success_email,
)

from .models import PaymentRecord, StripeWebhookEvent
from .services import create_checkout_for_invoice, create_full_refund_for_payment

User = get_user_model()


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
