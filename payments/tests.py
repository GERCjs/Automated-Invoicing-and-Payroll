from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.roles import CUSTOMER
from invoicing.models import Customer, Invoice

from .models import PaymentRecord, StripeWebhookEvent
from .services import create_checkout_for_invoice

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

    @patch("payments.views.construct_webhook_event", side_effect=Exception("bad signature"))
    def test_webhook_invalid_signature_returns_400(self, _construct_event_mock):
        response = self.client.post(
            reverse("payment-stripe-webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="invalid",
        )
        self.assertEqual(response.status_code, 400)

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

    def test_cancel_page_renders(self):
        response = self.client.get(reverse("payment-checkout-cancel"), data={"next": "/invoices/"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment cancelled")

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
