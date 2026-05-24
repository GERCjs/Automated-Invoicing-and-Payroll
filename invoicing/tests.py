from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts.roles import CUSTOMER, FINANCE, STAFF
from core.models import AuditLog
from imports.models import ImportJob
from notifications.models import EmailDeliveryLog

from .models import Customer, Invoice, InvoiceItem, InvoiceSourceRow

User = get_user_model()


class InvoicingMvpTests(TestCase):
    def setUp(self):
        self.finance_user = User.objects.create_user(username="finance_u", password="TempPass123!")
        self.finance_user.role_profile.role = FINANCE
        self.finance_user.role_profile.save()

        self.staff_user = User.objects.create_user(username="staff_u", password="TempPass123!")
        self.staff_user.role_profile.role = STAFF
        self.staff_user.role_profile.save()

        self.customer_user = User.objects.create_user(
            username="customer_u",
            password="TempPass123!",
            email="billing@acme.com",
        )
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

    def test_finance_can_open_customer_create_page_from_invoice_flow(self):
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-customer-create"), data={"next": reverse("invoice-create")})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add New Customer")

    def test_finance_can_create_customer_and_return_to_invoice_create_with_selection(self):
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.post(
            reverse("invoice-customer-create"),
            data={
                "name": "New Merchant Partner",
                "email": "new-merchant@example.com",
                "phone": "",
                "billing_address": "",
                "tax_number": "",
                "status": "active",
                "next": reverse("invoice-create"),
            },
        )

        customer = Customer.objects.get(email="new-merchant@example.com")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('invoice-create')}?customer={customer.id}")
        self.assertTrue(
            AuditLog.objects.filter(
                action="invoice.customer.created",
                target_type="customer",
                target_id=str(customer.id),
            ).exists()
        )

    def test_staff_cannot_access_customer_create_page(self):
        self.client.login(username="staff_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-customer-create"))

        self.assertEqual(response.status_code, 403)

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

    def test_finance_can_filter_invoice_list_by_status(self):
        draft_invoice = Invoice.objects.create(
            invoice_number="INV-2099-3101",
            customer=self.customer,
            status=Invoice.STATUS_DRAFT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=5),
            currency="SGD",
            created_by=self.finance_user,
        )
        paid_invoice = Invoice.objects.create(
            invoice_number="INV-2099-3102",
            customer=self.customer,
            status=Invoice.STATUS_PAID,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=5),
            currency="SGD",
            created_by=self.finance_user,
        )
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-list"), data={"status": "draft"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, draft_invoice.invoice_number)
        self.assertNotContains(response, paid_invoice.invoice_number)

    def test_finance_can_search_invoice_list_by_number_name_and_email(self):
        acme_invoice = Invoice.objects.create(
            invoice_number="INV-ACME-5001",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=5),
            currency="SGD",
            created_by=self.finance_user,
        )
        other_customer = Customer.objects.create(
            name="Beta Industries",
            email="billing@beta.com",
            created_by=self.finance_user,
        )
        beta_invoice = Invoice.objects.create(
            invoice_number="INV-BETA-5002",
            customer=other_customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=5),
            currency="SGD",
            created_by=self.finance_user,
        )
        self.client.login(username="finance_u", password="TempPass123!")

        by_number = self.client.get(reverse("invoice-list"), data={"q": "ACME-5001"})
        by_name = self.client.get(reverse("invoice-list"), data={"q": "Beta Industries"})
        by_email = self.client.get(reverse("invoice-list"), data={"q": "billing@beta.com"})

        self.assertContains(by_number, acme_invoice.invoice_number)
        self.assertNotContains(by_number, beta_invoice.invoice_number)
        self.assertContains(by_name, beta_invoice.invoice_number)
        self.assertNotContains(by_name, acme_invoice.invoice_number)
        self.assertContains(by_email, beta_invoice.invoice_number)
        self.assertNotContains(by_email, acme_invoice.invoice_number)

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

    def test_invoice_detail_shows_resend_email_button_and_last_email_label(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-detail", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send / Resend Invoice Email")
        self.assertContains(response, "Last Invoice Email Sent:")

    def test_invoice_list_shows_visible_issue_date_labels_and_pending_payment_status(self):
        invoice = self._create_invoice_with_item()
        invoice.status = Invoice.STATUS_SENT
        invoice.save(update_fields=["status", "updated_at"])
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Issue Date From")
        self.assertContains(response, "Issue Date To")
        self.assertContains(response, "Pending Payment")

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
        self.assertGreaterEqual(len(mail.outbox[0].attachments), 1)
        attachment_name = mail.outbox[0].attachments[0][0]
        self.assertIn(invoice.invoice_number, attachment_name)
        log = EmailDeliveryLog.objects.latest("attempted_at")
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_SENT)
        self.assertEqual(log.related_object_type, "invoice")
        self.assertEqual(log.related_object_id, str(invoice.id))
        self.assertIsNotNone(log.sent_at)
        self.assertEqual(log.metadata.get("invoice_status_after_send"), Invoice.STATUS_SENT)
        self.assertEqual(log.metadata.get("pdf_attachment_added"), True)
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

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_send_invoice_email_fails_when_customer_email_missing(self):
        invoice = self._create_invoice_with_item()
        invoice.customer.email = ""
        invoice.customer.save(update_fields=["email", "updated_at"])
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.post(reverse("invoice-send-email", args=[invoice.pk]))

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_DRAFT)
        log = EmailDeliveryLog.objects.latest("attempted_at")
        self.assertEqual(log.status, EmailDeliveryLog.STATUS_FAILED)
        self.assertIn("Customer email is missing", log.error_message)
        self.assertEqual(len(mail.outbox), 0)

    def test_customer_dashboard_shows_only_own_invoices(self):
        own_invoice = self._create_invoice_with_item()
        own_invoice.status = Invoice.STATUS_SENT
        own_invoice.save(update_fields=["status", "updated_at"])
        other_customer = Customer.objects.create(
            name="Other Corp",
            email="othercorp@example.com",
            created_by=self.finance_user,
        )
        Invoice.objects.create(
            invoice_number="INV-2099-9001",
            customer=other_customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=5),
            currency="SGD",
            subtotal=Decimal("50.00"),
            tax_amount=Decimal("4.50"),
            total_amount=Decimal("54.50"),
            created_by=self.finance_user,
        )

        self.client.login(username="customer_u", password="TempPass123!")
        response = self.client.get(reverse("customer-invoice-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own_invoice.invoice_number)
        self.assertNotContains(response, "INV-2099-9001")

    def test_customer_can_view_own_invoice_detail_and_download_pdf(self):
        own_invoice = self._create_invoice_with_item()
        own_invoice.status = Invoice.STATUS_SENT
        own_invoice.save(update_fields=["status", "updated_at"])
        self.client.login(username="customer_u", password="TempPass123!")

        detail_response = self.client.get(reverse("customer-invoice-detail", args=[own_invoice.pk]))
        pdf_response = self.client.get(reverse("customer-invoice-download-pdf", args=[own_invoice.pk]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, own_invoice.invoice_number)
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")

    def test_customer_invoice_detail_shows_reminder_history_empty_state(self):
        own_invoice = self._create_invoice_with_item()
        own_invoice.status = Invoice.STATUS_SENT
        own_invoice.save(update_fields=["status", "updated_at"])
        self.client.login(username="customer_u", password="TempPass123!")

        response = self.client.get(reverse("customer-invoice-detail", args=[own_invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reminder History")
        self.assertContains(response, "No reminders sent yet.")

    def test_customer_cannot_view_other_customer_invoice(self):
        other_customer = Customer.objects.create(
            name="Other Corp",
            email="othercorp@example.com",
            created_by=self.finance_user,
        )
        other_invoice = Invoice.objects.create(
            invoice_number="INV-2099-9002",
            customer=other_customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=5),
            currency="SGD",
            subtotal=Decimal("50.00"),
            tax_amount=Decimal("4.50"),
            total_amount=Decimal("54.50"),
            created_by=self.finance_user,
        )

        self.client.login(username="customer_u", password="TempPass123!")
        detail_response = self.client.get(reverse("customer-invoice-detail", args=[other_invoice.pk]))
        pdf_response = self.client.get(reverse("customer-invoice-download-pdf", args=[other_invoice.pk]))

        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(pdf_response.status_code, 404)

    def test_finance_can_preview_and_confirm_invoice_csv_import(self):
        csv_content = (
            "seller_id,shop_title,OrderID,paymentMethod,email,customerName,qty,serviceName,bookedDate,vanidayShare\n"
            "S1,Acme Salon,ORD-001,Credit Card,billing@acme.com,Acme Customer,2,Hair Cut,2026-05-01 10:00,120.00\n"
            "S1,Acme Salon,ORD-002,Credit Card,billing@acme.com,Acme Customer,1,Nail Service,2026-05-02 11:00,80.00\n"
        ).encode("utf-8")
        upload_file = SimpleUploadedFile("vaniday_sample.csv", csv_content, content_type="text/csv")

        self.client.login(username="finance_u", password="TempPass123!")
        preview_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": upload_file},
        )

        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "Preview Summary")
        self.assertContains(preview_response, "Confirm Import")
        preview = preview_response.context["preview"]
        import_token = preview["import_token"]

        confirm_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "confirm", "import_token": import_token},
        )
        self.assertEqual(confirm_response.status_code, 302)

        imported_invoice = Invoice.objects.filter(notes__contains="vaniday_sample.csv").first()
        self.assertIsNotNone(imported_invoice)
        self.assertEqual(imported_invoice.items.count(), 2)
        self.assertEqual(InvoiceSourceRow.objects.filter(source_file_name="vaniday_sample.csv").count(), 2)
        self.assertEqual(imported_invoice.total_amount, Decimal("200.00"))

        job = ImportJob.objects.latest("id")
        self.assertEqual(job.module, ImportJob.MODULE_INVOICING)
        self.assertEqual(job.status, ImportJob.STATUS_COMPLETED)
        self.assertEqual(job.saved_rows, 2)

    def test_invoice_csv_preview_shows_validation_errors_for_missing_fields(self):
        csv_content = (
            "seller_id,shop_title,OrderID,email,qty,serviceName,bookedDate,vanidayShare\n"
            "S1,,ORD-001,,1,Hair Cut,2026-05-01 10:00,\n"
        ).encode("utf-8")
        upload_file = SimpleUploadedFile("invalid_vaniday.csv", csv_content, content_type="text/csv")

        self.client.login(username="finance_u", password="TempPass123!")
        preview_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": upload_file},
        )

        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "Validation Issues")
        self.assertContains(preview_response, "Missing merchant/customer identity")
        self.assertContains(preview_response, "Missing numeric amount")

    def test_staff_cannot_access_invoice_csv_upload(self):
        self.client.login(username="staff_u", password="TempPass123!")
        response = self.client.get(reverse("invoice-csv-upload"))
        self.assertEqual(response.status_code, 403)
