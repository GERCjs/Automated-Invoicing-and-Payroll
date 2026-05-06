from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts.roles import CUSTOMER, FINANCE, STAFF
from core.models import AuditLog
from notifications.models import EmailDeliveryLog

from .models import Customer, Invoice, InvoiceItem

User = get_user_model()


class InvoicingMvpTests(TestCase):
    def setUp(self):
        self.finance_user = User.objects.create_user(username="finance_u", password="TempPass123!")
        self.finance_user.role_profile.role = FINANCE
        self.finance_user.role_profile.save()

        self.staff_user = User.objects.create_user(username="staff_u", password="TempPass123!")
        self.staff_user.role_profile.role = STAFF
        self.staff_user.role_profile.save()

        self.customer_user = User.objects.create_user(username="customer_u", password="TempPass123!")
        self.customer_user.role_profile.role = CUSTOMER
        self.customer_user.role_profile.save()

        self.customer = Customer.objects.create(
            name="Acme Pte Ltd",
            email="billing@acme.com",
            created_by=self.finance_user,
        )

    def _create_invoice_with_item(self):
        invoice = Invoice.objects.create(
            invoice_number="INV-2099-2001",
            customer=self.customer,
            status=Invoice.STATUS_DRAFT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=10),
            currency="SGD",
            subtotal=Decimal("200.00"),
            tax_amount=Decimal("18.00"),
            total_amount=Decimal("218.00"),
            created_by=self.finance_user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            description="Service Fee",
            quantity=Decimal("2.00"),
            unit_price=Decimal("100.00"),
            tax_rate=Decimal("9.00"),
            line_total=Decimal("218.00"),
        )
        return invoice

    def test_finance_can_create_invoice_and_calculates_totals(self):
        self.client.login(username="finance_u", password="TempPass123!")
        issue_date = timezone.localdate()
        due_date = issue_date + timedelta(days=15)

        response = self.client.post(
            reverse("invoice-create"),
            data={
                "customer": self.customer.pk,
                "issue_date": issue_date,
                "due_date": due_date,
                "currency": "SGD",
                "notes": "Test invoice",
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "1",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-description": "Service Fee",
                "items-0-quantity": "2",
                "items-0-unit_price": "100.00",
                "items-0-tax_rate": "9.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.first()
        self.assertIsNotNone(invoice)
        self.assertTrue(invoice.invoice_number.startswith(f"INV-{issue_date.year}-"))
        self.assertEqual(invoice.subtotal, Decimal("200.00"))
        self.assertEqual(invoice.tax_amount, Decimal("18.00"))
        self.assertEqual(invoice.total_amount, Decimal("218.00"))
        self.assertTrue(
            AuditLog.objects.filter(
                action="invoice.created",
                target_type="invoice",
                target_id=str(invoice.id),
            ).exists()
        )

    def test_staff_cannot_access_invoice_pages(self):
        self.client.login(username="staff_u", password="TempPass123!")
        response = self.client.get(reverse("invoice-list"))
        self.assertEqual(response.status_code, 403)

    def test_customer_cannot_access_invoice_pages(self):
        self.client.login(username="customer_u", password="TempPass123!")
        response = self.client.get(reverse("invoice-list"))
        self.assertEqual(response.status_code, 403)

    def test_public_view_marks_invoice_as_viewed_and_tracks_count(self):
        invoice = Invoice.objects.create(
            invoice_number="INV-2099-0001",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=10),
            currency="SGD",
            created_by=self.finance_user,
        )
        public_response = self.client.get(reverse("invoice-public-view", args=[invoice.public_view_token]))
        self.assertEqual(public_response.status_code, 200)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_VIEWED)
        self.assertEqual(invoice.view_count, 1)
        self.assertIsNotNone(invoice.viewed_at)

    def test_overdue_logic_applies_when_listing(self):
        invoice = Invoice.objects.create(
            invoice_number="INV-2099-0002",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=20),
            due_date=timezone.localdate() - timedelta(days=1),
            currency="SGD",
            created_by=self.finance_user,
        )
        self.client.login(username="finance_u", password="TempPass123!")
        list_response = self.client.get(reverse("invoice-list"))
        self.assertEqual(list_response.status_code, 200)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_OVERDUE)

    def test_invoice_create_rejects_when_all_items_are_deleted(self):
        self.client.login(username="finance_u", password="TempPass123!")
        issue_date = timezone.localdate()
        due_date = issue_date + timedelta(days=15)

        response = self.client.post(
            reverse("invoice-create"),
            data={
                "customer": self.customer.pk,
                "issue_date": issue_date,
                "due_date": due_date,
                "currency": "SGD",
                "notes": "",
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "1",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-description": "",
                "items-0-quantity": "",
                "items-0-unit_price": "",
                "items-0-tax_rate": "",
                "items-0-DELETE": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please submit at least 1 form.")
        self.assertEqual(Invoice.objects.count(), 0)

    def test_finance_can_edit_draft_invoice_and_recalculate_totals(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.post(
            reverse("invoice-edit", args=[invoice.pk]),
            data={
                "customer": self.customer.pk,
                "issue_date": invoice.issue_date,
                "due_date": invoice.due_date,
                "currency": "SGD",
                "notes": "Updated note",
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "1",
                "items-MIN_NUM_FORMS": "1",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-id": invoice.items.first().pk,
                "items-0-description": "Updated Service Fee",
                "items-0-quantity": "3",
                "items-0-unit_price": "100.00",
                "items-0-tax_rate": "9.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.notes, "Updated note")
        self.assertEqual(invoice.subtotal, Decimal("300.00"))
        self.assertEqual(invoice.tax_amount, Decimal("27.00"))
        self.assertEqual(invoice.total_amount, Decimal("327.00"))
        self.assertTrue(
            AuditLog.objects.filter(
                action="invoice.edited",
                target_type="invoice",
                target_id=str(invoice.id),
            ).exists()
        )

    def test_staff_cannot_edit_invoice(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="staff_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-edit", args=[invoice.pk]))

        self.assertEqual(response.status_code, 403)

    def test_non_draft_invoice_cannot_be_edited(self):
        invoice = self._create_invoice_with_item()
        invoice.status = Invoice.STATUS_SENT
        invoice.save(update_fields=["status", "updated_at"])
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-edit", args=[invoice.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only Draft invoices can be edited.")

    def test_finance_can_download_invoice_pdf(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-download-pdf", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(f'{invoice.invoice_number}.pdf', response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_finance_can_download_invoice_excel_and_values_match(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-download-excel", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn(f'{invoice.invoice_number}.xlsx', response["Content-Disposition"])

        wb = load_workbook(BytesIO(response.content))
        ws = wb["Invoice"]
        self.assertEqual(ws["F1"].value, invoice.invoice_number)
        self.assertAlmostEqual(float(ws["E14"].value), float(invoice.subtotal), places=2)
        self.assertAlmostEqual(float(ws["E15"].value), float(invoice.tax_amount), places=2)
        self.assertAlmostEqual(float(ws["E16"].value), float(invoice.total_amount), places=2)

    def test_staff_cannot_download_invoice_documents(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="staff_u", password="TempPass123!")

        pdf_response = self.client.get(reverse("invoice-download-pdf", args=[invoice.pk]))
        excel_response = self.client.get(reverse("invoice-download-excel", args=[invoice.pk]))

        self.assertEqual(pdf_response.status_code, 403)
        self.assertEqual(excel_response.status_code, 403)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_finance_can_send_invoice_email_and_log_delivery(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.post(reverse("invoice-send-email", args=[invoice.pk]))

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(invoice.invoice_number, mail.outbox[0].subject)
        self.assertIn("http://testserver/invoices/view/", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, [invoice.customer.email])
        log = EmailDeliveryLog.objects.latest("attempted_at")
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_SENT)
        self.assertEqual(log.related_object_type, "invoice")
        self.assertEqual(log.related_object_id, str(invoice.id))
        self.assertIsNotNone(log.sent_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="invoice.email.sent",
                target_type="invoice",
                target_id=str(invoice.id),
            ).exists()
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_send_invoice_email_failure_does_not_change_invoice_status(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="finance_u", password="TempPass123!")

        with patch("notifications.services.EmailMultiAlternatives.send", side_effect=Exception("SMTP unavailable")):
            response = self.client.post(reverse("invoice-send-email", args=[invoice.pk]))

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_DRAFT)
        log = EmailDeliveryLog.objects.latest("attempted_at")
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_FAILED)
        self.assertIn("SMTP unavailable", log.error_message)
        self.assertFalse(
            AuditLog.objects.filter(
                action="invoice.email.sent",
                target_type="invoice",
                target_id=str(invoice.id),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="invoice.email.failed",
                target_type="invoice",
                target_id=str(invoice.id),
            ).exists()
        )

    def test_staff_cannot_send_invoice_email(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="staff_u", password="TempPass123!")

        response = self.client.post(reverse("invoice-send-email", args=[invoice.pk]))

        self.assertEqual(response.status_code, 403)
