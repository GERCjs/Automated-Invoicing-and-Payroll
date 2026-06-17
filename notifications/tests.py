from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone

from accounts.models import UserRole
from accounts.roles import ADMIN
from invoicing.models import Customer, Invoice

from .models import EmailDeliveryLog, PaymentReminderSettings
from .services import REMINDER_TEMPLATE_KEYS, run_payment_reminder_check


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class PaymentReminderServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_user(
            username="notification_fixture_admin",
            password="TestOnlyPass123!",
        )
        UserRole.objects.filter(user=self.admin_user).update(role=ADMIN)
        self.customer = Customer.objects.create(
            name="Reminder Fixture Customer",
            email="reminder.fixture@example.com",
        )

    def _create_sent_invoice_due_today(self):
        today = timezone.localdate()
        return Invoice.objects.create(
            invoice_number="REM-TEST-001",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=today - timedelta(days=7),
            due_date=today,
            subtotal=100,
            tax_amount=0,
            total_amount=100,
            created_by=self.admin_user,
        )

    def test_payment_reminder_settings_load_returns_singleton(self):
        first = PaymentReminderSettings.load()
        second = PaymentReminderSettings.load()

        self.assertEqual(first.pk, 1)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(PaymentReminderSettings.objects.count(), 1)

    def test_dry_run_creates_pending_reminder_log_without_sending_email(self):
        invoice = self._create_sent_invoice_due_today()

        summary = run_payment_reminder_check(
            triggered_by=self.admin_user,
            base_url="https://example.test",
            simulate=True,
        )

        self.assertEqual(summary["checked_invoices"], 1)
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["simulated"], 1)
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(len(mail.outbox), 0)

        log = EmailDeliveryLog.objects.get(related_object_id=str(invoice.id))
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_PENDING)
        self.assertEqual(log.template_key, REMINDER_TEMPLATE_KEYS["due_date"])
        self.assertTrue(log.metadata["simulate"])

    def test_real_send_skips_duplicate_reminder_for_same_day(self):
        invoice = self._create_sent_invoice_due_today()

        first_summary = run_payment_reminder_check(
            triggered_by=self.admin_user,
            base_url="https://example.test",
            simulate=False,
        )
        second_summary = run_payment_reminder_check(
            triggered_by=self.admin_user,
            base_url="https://example.test",
            simulate=False,
        )

        self.assertEqual(first_summary["sent"], 1)
        self.assertEqual(second_summary["processed"], 0)
        self.assertEqual(second_summary["skipped_already_logged_today"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            EmailDeliveryLog.objects.filter(
                related_object_id=str(invoice.id),
                template_key=REMINDER_TEMPLATE_KEYS["due_date"],
                status=EmailDeliveryLog.STATUS_SENT,
            ).count(),
            1,
        )

    def test_past_due_draft_invoice_does_not_receive_overdue_reminder(self):
        past_due_draft = Invoice.objects.create(
            invoice_number="REM-DRAFT-001",
            customer=self.customer,
            status=Invoice.STATUS_DRAFT,
            issue_date=timezone.localdate() - timedelta(days=10),
            due_date=timezone.localdate() - timedelta(days=1),
            subtotal=100,
            tax_amount=0,
            total_amount=100,
            created_by=self.admin_user,
        )

        summary = run_payment_reminder_check(
            triggered_by=self.admin_user,
            base_url="https://example.test",
            simulate=False,
        )

        self.assertEqual(summary["checked_invoices"], 0)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(len(mail.outbox), 0)
        past_due_draft.refresh_from_db()
        self.assertEqual(past_due_draft.status, Invoice.STATUS_DRAFT)
        self.assertFalse(
            EmailDeliveryLog.objects.filter(
                related_object_type="invoice",
                related_object_id=str(past_due_draft.id),
                template_key__startswith="payment_reminder_",
            ).exists()
        )
