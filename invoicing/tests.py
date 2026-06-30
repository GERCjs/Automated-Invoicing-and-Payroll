from datetime import datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.templatetags.static import static
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF, SUPERADMIN
from core.models import AuditLog
from imports.models import ImportJob
from notifications.models import EmailDeliveryLog
from payments.models import PaymentRecord

from .models import Customer, Invoice, InvoiceItem, InvoiceSourceRow
from .services import (
    apply_overdue_status,
    parse_invoice_csv,
    parse_invoice_excel,
    refresh_overdue_invoices,
)

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

    def _create_invoice_with_item(
        self,
        *,
        invoice_number="INV-2099-2001",
        status=Invoice.STATUS_DRAFT,
        customer=None,
    ):
        customer = customer or self.customer
        invoice = Invoice.objects.create(
            invoice_number=invoice_number,
            customer=customer,
            status=status,
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

    def _create_basic_invoice(
        self,
        *,
        invoice_number,
        status,
        due_date,
        issue_date=None,
        customer=None,
    ):
        customer = customer or self.customer
        return Invoice.objects.create(
            invoice_number=invoice_number,
            customer=customer,
            status=status,
            issue_date=issue_date or timezone.localdate(),
            due_date=due_date,
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
            created_by=self.finance_user,
        )

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

    def test_invoice_create_rejects_zero_unit_price(self):
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
                "notes": "Invalid invoice",
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "1",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-description": "Service Fee",
                "items-0-quantity": "2",
                "items-0-unit_price": "0.00",
                "items-0-tax_rate": "9.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unit price must be greater than 0.")
        self.assertFalse(Invoice.objects.exists())

    def test_invoice_create_rejects_negative_unit_price(self):
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
                "notes": "Invalid invoice",
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "1",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-description": "Service Fee",
                "items-0-quantity": "2",
                "items-0-unit_price": "-1.00",
                "items-0-tax_rate": "9.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unit price must be greater than 0.")
        self.assertFalse(Invoice.objects.exists())

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

    def test_past_due_draft_invoice_remains_draft(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0003",
            status=Invoice.STATUS_DRAFT,
            due_date=timezone.localdate() - timedelta(days=1),
            issue_date=timezone.localdate() - timedelta(days=10),
        )

        changed = apply_overdue_status(invoice)

        self.assertFalse(changed)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_DRAFT)

    def test_draft_invoice_due_today_remains_draft(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0004",
            status=Invoice.STATUS_DRAFT,
            due_date=timezone.localdate(),
            issue_date=timezone.localdate() - timedelta(days=7),
        )

        changed = refresh_overdue_invoices()

        self.assertEqual(changed, 0)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_DRAFT)

    def test_future_due_draft_invoice_remains_draft(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0005",
            status=Invoice.STATUS_DRAFT,
            due_date=timezone.localdate() + timedelta(days=5),
        )

        changed = apply_overdue_status(invoice)

        self.assertFalse(changed)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_DRAFT)

    def test_past_due_viewed_invoice_becomes_overdue(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0006",
            status=Invoice.STATUS_VIEWED,
            due_date=timezone.localdate() - timedelta(days=2),
            issue_date=timezone.localdate() - timedelta(days=14),
        )

        changed = apply_overdue_status(invoice)

        self.assertTrue(changed)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_OVERDUE)

    def test_sent_invoice_due_today_does_not_become_overdue(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0007",
            status=Invoice.STATUS_SENT,
            due_date=timezone.localdate(),
            issue_date=timezone.localdate() - timedelta(days=7),
        )

        changed = refresh_overdue_invoices()

        self.assertEqual(changed, 0)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_SENT)

    def test_future_due_sent_invoice_remains_sent(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0008",
            status=Invoice.STATUS_SENT,
            due_date=timezone.localdate() + timedelta(days=4),
        )

        changed = apply_overdue_status(invoice)

        self.assertFalse(changed)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_SENT)

    def test_future_due_viewed_invoice_remains_viewed(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0009",
            status=Invoice.STATUS_VIEWED,
            due_date=timezone.localdate() + timedelta(days=4),
        )

        changed = apply_overdue_status(invoice)

        self.assertFalse(changed)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_VIEWED)

    def test_past_due_paid_invoice_remains_paid(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0010",
            status=Invoice.STATUS_PAID,
            due_date=timezone.localdate() - timedelta(days=3),
            issue_date=timezone.localdate() - timedelta(days=10),
        )

        changed = apply_overdue_status(invoice)

        self.assertFalse(changed)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_PAID)

    def test_past_due_refunded_invoice_remains_refunded(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0011",
            status=Invoice.STATUS_REFUNDED,
            due_date=timezone.localdate() - timedelta(days=3),
            issue_date=timezone.localdate() - timedelta(days=10),
        )

        changed = apply_overdue_status(invoice)

        self.assertFalse(changed)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_REFUNDED)

    def test_existing_overdue_invoice_remains_overdue(self):
        invoice = self._create_basic_invoice(
            invoice_number="INV-2099-0012",
            status=Invoice.STATUS_OVERDUE,
            due_date=timezone.localdate() - timedelta(days=5),
            issue_date=timezone.localdate() - timedelta(days=12),
        )

        changed = apply_overdue_status(invoice)

        self.assertFalse(changed)
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

    def test_invoice_list_filters_by_issue_date_range_when_from_is_earlier_than_to(self):
        earlier_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1001",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=10),
            due_date=timezone.localdate() + timedelta(days=5),
        )
        later_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1002",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=2),
            due_date=timezone.localdate() + timedelta(days=7),
        )
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(
            reverse("invoice-list"),
            data={
                "issue_date_from": (timezone.localdate() - timedelta(days=11)).strftime("%Y-%m-%d"),
                "issue_date_to": (timezone.localdate() - timedelta(days=8)).strftime("%Y-%m-%d"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, earlier_invoice.invoice_number)
        self.assertNotContains(response, later_invoice.invoice_number)

    def test_invoice_list_allows_same_day_issue_date_range(self):
        same_day = timezone.localdate() - timedelta(days=4)
        matching_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1003",
            status=Invoice.STATUS_SENT,
            issue_date=same_day,
            due_date=timezone.localdate() + timedelta(days=5),
        )
        other_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1004",
            status=Invoice.STATUS_SENT,
            issue_date=same_day - timedelta(days=1),
            due_date=timezone.localdate() + timedelta(days=6),
        )
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(
            reverse("invoice-list"),
            data={
                "issue_date_from": same_day.strftime("%Y-%m-%d"),
                "issue_date_to": same_day.strftime("%Y-%m-%d"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, matching_invoice.invoice_number)
        self.assertNotContains(response, other_invoice.invoice_number)

    def test_invoice_list_allows_issue_date_from_only(self):
        older_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1005",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=9),
            due_date=timezone.localdate() + timedelta(days=4),
        )
        newer_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1006",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=1),
            due_date=timezone.localdate() + timedelta(days=8),
        )
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(
            reverse("invoice-list"),
            data={"issue_date_from": (timezone.localdate() - timedelta(days=3)).strftime("%Y-%m-%d")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, older_invoice.invoice_number)
        self.assertContains(response, newer_invoice.invoice_number)

    def test_invoice_list_allows_issue_date_to_only(self):
        older_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1007",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=9),
            due_date=timezone.localdate() + timedelta(days=4),
        )
        newer_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1008",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=1),
            due_date=timezone.localdate() + timedelta(days=8),
        )
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(
            reverse("invoice-list"),
            data={"issue_date_to": (timezone.localdate() - timedelta(days=3)).strftime("%Y-%m-%d")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, older_invoice.invoice_number)
        self.assertNotContains(response, newer_invoice.invoice_number)

    def test_invoice_list_rejects_issue_date_from_later_than_to(self):
        first_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1009",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=8),
            due_date=timezone.localdate() + timedelta(days=4),
        )
        second_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1010",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=2),
            due_date=timezone.localdate() + timedelta(days=7),
        )
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(
            reverse("invoice-list"),
            data={
                "issue_date_from": "2026-06-18",
                "issue_date_to": "2026-06-14",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From date cannot be later than To date.")
        self.assertContains(response, first_invoice.invoice_number)
        self.assertContains(response, second_invoice.invoice_number)
        self.assertContains(response, 'name="issue_date_from"', html=False)
        self.assertContains(response, 'value="2026-06-18"', html=False)
        self.assertContains(response, 'value="2026-06-14"', html=False)

    def test_invoice_list_rejects_invalid_issue_date_from_without_crashing(self):
        matching_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1011",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=5),
            due_date=timezone.localdate() + timedelta(days=5),
        )
        later_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1012",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=1),
            due_date=timezone.localdate() + timedelta(days=7),
        )
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(
            reverse("invoice-list"),
            data={
                "issue_date_from": "2026-99-99",
                "issue_date_to": (timezone.localdate() - timedelta(days=3)).strftime("%Y-%m-%d"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'From date &quot;2026-99-99&quot; is invalid. Use YYYY-MM-DD.', html=False)
        self.assertContains(response, 'value="2026-99-99"', html=False)
        self.assertContains(response, matching_invoice.invoice_number)
        self.assertNotContains(response, later_invoice.invoice_number)

    def test_invoice_list_rejects_invalid_issue_date_to_without_crashing(self):
        older_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1013",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=6),
            due_date=timezone.localdate() + timedelta(days=4),
        )
        newer_invoice = self._create_basic_invoice(
            invoice_number="INV-RANGE-1014",
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=1),
            due_date=timezone.localdate() + timedelta(days=7),
        )
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(
            reverse("invoice-list"),
            data={
                "issue_date_from": (timezone.localdate() - timedelta(days=3)).strftime("%Y-%m-%d"),
                "issue_date_to": "2026-99-88",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'To date &quot;2026-99-88&quot; is invalid. Use YYYY-MM-DD.', html=False)
        self.assertContains(response, 'value="2026-99-88"', html=False)
        self.assertNotContains(response, older_invoice.invoice_number)
        self.assertContains(response, newer_invoice.invoice_number)

    def test_invoice_list_combines_search_status_and_valid_issue_date_range(self):
        target_customer = Customer.objects.create(
            name="Gamma Holdings",
            email="billing@gamma.com",
            created_by=self.finance_user,
        )
        matching_invoice = self._create_basic_invoice(
            invoice_number="INV-GAMMA-1015",
            status=Invoice.STATUS_SENT,
            customer=target_customer,
            issue_date=timezone.localdate() - timedelta(days=3),
            due_date=timezone.localdate() + timedelta(days=5),
        )
        other_status_invoice = self._create_basic_invoice(
            invoice_number="INV-GAMMA-1016",
            status=Invoice.STATUS_PAID,
            customer=target_customer,
            issue_date=timezone.localdate() - timedelta(days=3),
            due_date=timezone.localdate() + timedelta(days=5),
        )
        other_date_invoice = self._create_basic_invoice(
            invoice_number="INV-GAMMA-1017",
            status=Invoice.STATUS_SENT,
            customer=target_customer,
            issue_date=timezone.localdate() - timedelta(days=10),
            due_date=timezone.localdate() + timedelta(days=5),
        )
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(
            reverse("invoice-list"),
            data={
                "q": "Gamma",
                "status": "sent",
                "issue_date_from": (timezone.localdate() - timedelta(days=4)).strftime("%Y-%m-%d"),
                "issue_date_to": (timezone.localdate() - timedelta(days=2)).strftime("%Y-%m-%d"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, matching_invoice.invoice_number)
        self.assertNotContains(response, other_status_invoice.invoice_number)
        self.assertNotContains(response, other_date_invoice.invoice_number)

    def test_invoice_list_reset_link_returns_base_url_without_filters(self):
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(
            reverse("invoice-list"),
            data={
                "q": "Acme",
                "status": "draft",
                "issue_date_from": "2026-06-10",
                "issue_date_to": "2026-06-12",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a href="{reverse("invoice-list")}" class="btn btn-outline-secondary invoice-action-btn" id="invoiceListResetButton">Reset</a>',
            html=True,
        )

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

    def test_draft_invoice_detail_shows_delete_button(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-detail", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("invoice-delete-draft", args=[invoice.pk]))

    def test_non_draft_invoice_detail_hides_delete_button(self):
        invoice = self._create_invoice_with_item()
        invoice.status = Invoice.STATUS_SENT
        invoice.save(update_fields=["status", "updated_at"])
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-detail", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("invoice-delete-draft", args=[invoice.pk]))

    def test_finance_can_delete_draft_invoice_with_confirmation(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="finance_u", password="TempPass123!")

        confirm_response = self.client.get(reverse("invoice-delete-draft", args=[invoice.pk]))
        self.assertEqual(confirm_response.status_code, 200)
        self.assertContains(confirm_response, "Delete Draft Invoice")
        self.assertContains(confirm_response, invoice.invoice_number)

        delete_response = self.client.post(reverse("invoice-delete-draft", args=[invoice.pk]))

        self.assertRedirects(delete_response, reverse("invoice-list"))
        self.assertFalse(Invoice.objects.filter(pk=invoice.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="invoice.deleted",
                target_type="invoice",
                target_id=str(invoice.id),
            ).exists()
        )

    def test_non_draft_invoice_cannot_be_deleted(self):
        invoice = self._create_invoice_with_item()
        invoice.status = Invoice.STATUS_SENT
        invoice.save(update_fields=["status", "updated_at"])
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.post(reverse("invoice-delete-draft", args=[invoice.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only Draft invoices can be deleted.")
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())

    def test_staff_cannot_delete_draft_invoice(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="staff_u", password="TempPass123!")

        response = self.client.post(reverse("invoice-delete-draft", args=[invoice.pk]))

        self.assertEqual(response.status_code, 403)

    def test_invoice_detail_shows_resend_email_button_and_last_email_label(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-detail", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send / Resend Invoice Email")
        self.assertContains(response, "Last Invoice Email Sent:")
        self.assertContains(response, "GST %")
        self.assertContains(response, "GST</th>", html=False)

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
        self.assertContains(response, 'id="issueDateFrom"', html=False)
        self.assertContains(response, 'id="issueDateTo"', html=False)
        self.assertContains(response, 'id="invoiceDateRangeMessage"', html=False)
        self.assertContains(response, static("js/invoice_list.js"))
        self.assertContains(response, "Choose a date range to limit invoices by issue date.")

    def test_invoice_list_shows_batch_email_controls(self):
        eligible_invoice = self._create_invoice_with_item(invoice_number="INV-2099-2101", status=Invoice.STATUS_SENT)
        ineligible_invoice = self._create_invoice_with_item(invoice_number="INV-2099-2102", status=Invoice.STATUS_PAID)
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.get(reverse("invoice-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send Selected Invoice Emails")
        self.assertContains(response, 'name="selected_invoice_ids"', html=False)
        self.assertContains(response, f'aria-label="Select {eligible_invoice.invoice_number}"', html=False)
        self.assertContains(
            response,
            f'aria-label="{ineligible_invoice.invoice_number} cannot be batch emailed"',
            html=False,
        )

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
        self.assertEqual(ws["D11"].value, "GST %")
        self.assertAlmostEqual(float(ws["E14"].value), float(invoice.subtotal), places=2)
        self.assertEqual(ws["D15"].value, "GST")
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
    def test_finance_can_batch_send_selected_invoice_emails_and_skip_paid_invoice(self):
        draft_invoice = self._create_invoice_with_item(invoice_number="INV-2099-2201", status=Invoice.STATUS_DRAFT)
        sent_invoice = self._create_invoice_with_item(invoice_number="INV-2099-2202", status=Invoice.STATUS_SENT)
        paid_invoice = self._create_invoice_with_item(invoice_number="INV-2099-2203", status=Invoice.STATUS_PAID)
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.post(
            reverse("invoice-send-email-batch"),
            data={
                "selected_invoice_ids": [str(draft_invoice.id), str(sent_invoice.id), str(paid_invoice.id)],
                "next": reverse("invoice-list"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        draft_invoice.refresh_from_db()
        sent_invoice.refresh_from_db()
        paid_invoice.refresh_from_db()
        self.assertEqual(draft_invoice.status, Invoice.STATUS_SENT)
        self.assertEqual(sent_invoice.status, Invoice.STATUS_SENT)
        self.assertEqual(paid_invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(len(mail.outbox), 2)
        self.assertGreaterEqual(len(mail.outbox[0].attachments), 1)
        self.assertGreaterEqual(len(mail.outbox[1].attachments), 1)
        sent_logs = EmailDeliveryLog.objects.filter(
            related_object_type="invoice",
            template_key="invoice_email_v1",
            status=EmailDeliveryLog.STATUS_SENT,
        )
        self.assertEqual(sent_logs.count(), 2)
        self.assertTrue(
            AuditLog.objects.filter(action="invoice.email.sent", target_id=str(draft_invoice.id)).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action="invoice.email.sent", target_id=str(sent_invoice.id)).exists()
        )
        self.assertContains(response, "Invoice emails sent: 2.")
        self.assertContains(response, "Skipped 1 invoice(s):")
        self.assertContains(response, paid_invoice.invoice_number)
        self.assertContains(response, "Paid invoices are excluded from batch email sending.")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="billing@example.com",
    )
    def test_batch_send_reports_missing_customer_email_failure(self):
        ok_invoice = self._create_invoice_with_item(invoice_number="INV-2099-2301", status=Invoice.STATUS_SENT)
        missing_email_customer = Customer.objects.create(
            name="No Email Customer",
            email="temp-no-email@example.com",
            created_by=self.finance_user,
        )
        failing_invoice = self._create_invoice_with_item(
            invoice_number="INV-2099-2302",
            status=Invoice.STATUS_SENT,
            customer=missing_email_customer,
        )
        missing_email_customer.email = ""
        missing_email_customer.save(update_fields=["email", "updated_at"])
        self.client.login(username="finance_u", password="TempPass123!")

        response = self.client.post(
            reverse("invoice-send-email-batch"),
            data={
                "selected_invoice_ids": [str(ok_invoice.id), str(failing_invoice.id)],
                "next": reverse("invoice-list"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        failed_log = EmailDeliveryLog.objects.filter(
            related_object_type="invoice",
            related_object_id=str(failing_invoice.id),
        ).latest("attempted_at")
        self.assertEqual(failed_log.status, EmailDeliveryLog.STATUS_FAILED)
        self.assertIn("Customer email is missing", failed_log.error_message)
        self.assertTrue(
            AuditLog.objects.filter(action="invoice.email.failed", target_id=str(failing_invoice.id)).exists()
        )
        self.assertContains(response, "Invoice emails sent: 1.")
        self.assertContains(response, "Failed to send 1 invoice email(s):")
        self.assertContains(response, failing_invoice.invoice_number)

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

    def test_staff_cannot_batch_send_invoice_email(self):
        invoice = self._create_invoice_with_item()
        self.client.login(username="staff_u", password="TempPass123!")

        response = self.client.post(
            reverse("invoice-send-email-batch"),
            data={"selected_invoice_ids": [str(invoice.id)], "next": reverse("invoice-list")},
        )

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

    def test_customer_dashboard_excludes_past_due_draft_invoice_and_keeps_it_draft(self):
        hidden_draft = self._create_basic_invoice(
            invoice_number="INV-2099-9010",
            status=Invoice.STATUS_DRAFT,
            due_date=timezone.localdate() - timedelta(days=1),
            issue_date=timezone.localdate() - timedelta(days=8),
        )
        visible_sent = self._create_basic_invoice(
            invoice_number="INV-2099-9011",
            status=Invoice.STATUS_SENT,
            due_date=timezone.localdate() + timedelta(days=3),
        )

        self.client.login(username="customer_u", password="TempPass123!")
        response = self.client.get(reverse("customer-invoice-dashboard"))

        self.assertEqual(response.status_code, 200)
        hidden_draft.refresh_from_db()
        self.assertEqual(hidden_draft.status, Invoice.STATUS_DRAFT)
        self.assertNotContains(response, hidden_draft.invoice_number)
        self.assertContains(response, visible_sent.invoice_number)

    def test_customer_can_view_own_invoice_detail_and_download_pdf(self):
        own_invoice = self._create_invoice_with_item()
        own_invoice.status = Invoice.STATUS_SENT
        own_invoice.save(update_fields=["status", "updated_at"])
        self.client.login(username="customer_u", password="TempPass123!")

        detail_response = self.client.get(reverse("customer-invoice-detail", args=[own_invoice.pk]))
        pdf_response = self.client.get(reverse("customer-invoice-download-pdf", args=[own_invoice.pk]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, own_invoice.invoice_number)
        self.assertContains(detail_response, "GST %")
        self.assertContains(detail_response, "GST</th>", html=False)
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")

    def test_public_invoice_view_uses_gst_wording(self):
        invoice = self._create_invoice_with_item()

        response = self.client.get(reverse("invoice-public-view", args=[invoice.public_view_token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GST %")
        self.assertContains(response, "GST</th>", html=False)

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


class InvoiceCollectionReportingTests(TestCase):
    def setUp(self):
        self.finance_user = User.objects.create_user(username="finance_collect", password="TempPass123!")
        self.finance_user.role_profile.role = FINANCE
        self.finance_user.role_profile.save(update_fields=["role", "updated_at"])
        self.customer = Customer.objects.create(
            name="Collection Customer",
            email="collection@example.com",
            created_by=self.finance_user,
        )

    def _month_start(self, months_ago=0):
        month_start = timezone.localdate().replace(day=1)
        for _ in range(months_ago):
            month_start = (month_start - timedelta(days=1)).replace(day=1)
        return month_start

    def _aware_datetime(self, day_value, hour=10):
        return timezone.make_aware(datetime.combine(day_value, time(hour=hour)))

    def _create_invoice(self, *, invoice_number, status, issue_date, due_date, total_amount="109.00"):
        return Invoice.objects.create(
            invoice_number=invoice_number,
            customer=self.customer,
            status=status,
            issue_date=issue_date,
            due_date=due_date,
            currency="SGD",
            subtotal=Decimal(total_amount) - Decimal("9.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal(total_amount),
            created_by=self.finance_user,
        )

    def test_invoice_dashboard_counts_collection_by_payment_date(self):
        issue_month_start = self._month_start(2)
        payment_month_start = self._month_start(1)
        current_month_start = self._month_start(0)
        invoice = self._create_invoice(
            invoice_number="INV-COLL-2001",
            status=Invoice.STATUS_PAID,
            issue_date=issue_month_start,
            due_date=issue_month_start + timedelta(days=7),
        )
        PaymentRecord.objects.create(
            invoice=invoice,
            payment_reference="PAY-COLL-2001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("109.00"),
            currency="SGD",
            paid_at=self._aware_datetime(payment_month_start + timedelta(days=4)),
        )
        Invoice.objects.filter(pk=invoice.pk).update(
            updated_at=self._aware_datetime(current_month_start + timedelta(days=2), hour=15)
        )

        self.client.force_login(self.finance_user)
        response = self.client.get(reverse("invoice-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["collected_month"], Decimal("0.00"))
        self.assertEqual(response.context["collected_year"], Decimal("109.00"))

    def test_invoice_dashboard_excludes_failed_cancelled_and_unpaid_invoices_from_collection(self):
        month_start = self._month_start(0)
        unpaid_invoice = self._create_invoice(
            invoice_number="INV-COLL-2002",
            status=Invoice.STATUS_SENT,
            issue_date=month_start,
            due_date=month_start + timedelta(days=5),
            total_amount="150.00",
        )
        failed_invoice = self._create_invoice(
            invoice_number="INV-COLL-2003",
            status=Invoice.STATUS_SENT,
            issue_date=month_start,
            due_date=month_start + timedelta(days=6),
            total_amount="80.00",
        )
        cancelled_invoice = self._create_invoice(
            invoice_number="INV-COLL-2004",
            status=Invoice.STATUS_OVERDUE,
            issue_date=month_start,
            due_date=month_start + timedelta(days=3),
            total_amount="70.00",
        )
        PaymentRecord.objects.create(
            invoice=failed_invoice,
            payment_reference="PAY-COLL-2003",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_FAILED,
            amount=Decimal("80.00"),
            currency="SGD",
        )
        PaymentRecord.objects.create(
            invoice=cancelled_invoice,
            payment_reference="PAY-COLL-2004",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_CANCELLED,
            amount=Decimal("70.00"),
            currency="SGD",
        )

        self.client.force_login(self.finance_user)
        response = self.client.get(reverse("invoice-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["collected_month"], Decimal("0.00"))
        self.assertEqual(response.context["outstanding_amount"], Decimal("300.00"))
        self.assertContains(response, unpaid_invoice.invoice_number)

    def test_invoice_dashboard_counts_confirmed_bank_transfer_using_paid_at(self):
        today = timezone.localdate()
        invoice = self._create_invoice(
            invoice_number="INV-COLL-2005",
            status=Invoice.STATUS_PAID,
            issue_date=self._month_start(1),
            due_date=self._month_start(1) + timedelta(days=8),
            total_amount="54.50",
        )
        PaymentRecord.objects.create(
            invoice=invoice,
            payment_reference="PAY-COLL-2005",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("54.50"),
            currency="SGD",
            paid_at=self._aware_datetime(today),
        )

        self.client.force_login(self.finance_user)
        response = self.client.get(reverse("invoice-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["collected_month"], Decimal("54.50"))


class InvoiceFileUploadTests(TestCase):
    def setUp(self):
        self.finance_user = User.objects.create_user(username="finance_upload", password="TempPass123!")
        self.finance_user.role_profile.role = FINANCE
        self.finance_user.role_profile.save()

        self.admin_user = User.objects.create_user(username="admin_upload", password="TempPass123!")
        self.admin_user.role_profile.role = ADMIN
        self.admin_user.role_profile.save()

        self.superadmin_user = User.objects.create_user(username="super_upload", password="TempPass123!")
        self.superadmin_user.role_profile.role = SUPERADMIN
        self.superadmin_user.role_profile.save()

        self.hr_user = User.objects.create_user(username="hr_upload", password="TempPass123!")
        self.hr_user.role_profile.role = HR
        self.hr_user.role_profile.save()

        self.staff_user = User.objects.create_user(username="staff_upload", password="TempPass123!")
        self.staff_user.role_profile.role = STAFF
        self.staff_user.role_profile.save()

        self.customer_user = User.objects.create_user(username="customer_upload", password="TempPass123!")
        self.customer_user.role_profile.role = CUSTOMER
        self.customer_user.role_profile.save()

    def _sample_csv_rows(self):
        return [
            {
                "seller_id": "S1",
                "shop_title": "Acme Salon",
                "OrderID": "ORD-001",
                "paymentMethod": "Credit Card",
                "email": "billing@acme.com",
                "customerName": "Acme Customer",
                "qty": "2",
                "serviceName": "Hair Cut",
                "bookedDate": "2026-05-01 10:00:00",
                "vanidayShare": "120.00",
            },
            {
                "seller_id": "S1",
                "shop_title": "Acme Salon",
                "OrderID": "ORD-002",
                "paymentMethod": "Credit Card",
                "email": "billing@acme.com",
                "customerName": "Acme Customer",
                "qty": "1",
                "serviceName": "Nail Service",
                "bookedDate": "2026-05-02 11:00:00",
                "vanidayShare": "80.00",
            },
        ]

    def _build_csv_upload(self, rows=None, filename="vaniday_sample.csv"):
        rows = rows or self._sample_csv_rows()
        headers = [
            "seller_id",
            "shop_title",
            "OrderID",
            "paymentMethod",
            "email",
            "customerName",
            "qty",
            "serviceName",
            "bookedDate",
            "vanidayShare",
        ]
        lines = [",".join(headers)]
        for row in rows:
            lines.append(",".join(str(row.get(header, "")) for header in headers))
        content = ("\n".join(lines) + "\n").encode("utf-8")
        return SimpleUploadedFile(filename, content, content_type="text/csv")

    def _build_excel_upload(self, rows=None, filename="vaniday_sample.xlsx", headers=None):
        workbook = Workbook()
        sheet = workbook.active
        header_row = headers or [
            "seller_id",
            "shop_title",
            "OrderID",
            "paymentMethod",
            "email",
            "customerName",
            "qty",
            "serviceName",
            "bookedDate",
            "vanidayShare",
        ]
        sheet.append(header_row)
        for row in rows or self._sample_csv_rows():
            if isinstance(row, dict):
                sheet.append([row.get(header, "") for header in header_row])
            else:
                sheet.append(row)
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return SimpleUploadedFile(
            filename,
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_finance_can_upload_valid_csv_file(self):
        self.client.login(username="finance_upload", password="TempPass123!")

        response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": self._build_csv_upload()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview Summary")
        self.assertContains(response, "vaniday_sample.csv")

    def test_finance_can_upload_valid_xlsx_file(self):
        self.client.login(username="finance_upload", password="TempPass123!")
        rows = [
            [
                "S1",
                "Acme Salon",
                "ORD-001",
                "Credit Card",
                "billing@acme.com",
                "Acme Customer",
                2,
                "Hair Cut",
                timezone.datetime(2026, 5, 1, 10, 0),
                120,
            ],
            [
                "S1",
                "Acme Salon",
                "ORD-002",
                "Credit Card",
                "billing@acme.com",
                "Acme Customer",
                1,
                "Nail Service",
                timezone.datetime(2026, 5, 2, 11, 0),
                80.5,
            ],
            [None, None, None, None, None, None, None, None, None, None],
        ]

        response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": self._build_excel_upload(rows=rows)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview Summary")
        self.assertContains(response, "Invoice Upload Preview")
        preview = response.context["preview"]
        self.assertEqual(preview["total_rows"], 2)
        self.assertEqual(len(preview["valid_rows"]), 2)
        self.assertEqual(len(preview["invalid_rows"]), 0)
        self.assertEqual(preview["valid_rows"][0]["amount"], "120.00")
        self.assertEqual(preview["valid_rows"][1]["amount"], "80.50")

    def test_admin_and_superadmin_can_access_invoice_upload(self):
        for username in ["admin_upload", "super_upload"]:
            with self.subTest(username=username):
                self.client.login(username=username, password="TempPass123!")
                response = self.client.get(reverse("invoice-csv-upload"))
                self.assertEqual(response.status_code, 200)
                self.client.logout()

    def test_hr_customer_and_staff_cannot_access_invoice_upload(self):
        for username in ["hr_upload", "customer_upload", "staff_upload"]:
            with self.subTest(username=username):
                self.client.login(username=username, password="TempPass123!")
                response = self.client.get(reverse("invoice-csv-upload"))
                self.assertEqual(response.status_code, 403)
                self.client.logout()

    def test_unsupported_extension_is_rejected(self):
        self.client.login(username="finance_upload", password="TempPass123!")
        upload_file = SimpleUploadedFile("bad.pdf", b"%PDF-1.4", content_type="application/pdf")

        response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": upload_file},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a CSV or Excel (.xlsx) file.")

    def test_corrupted_xlsx_is_rejected_safely(self):
        self.client.login(username="finance_upload", password="TempPass123!")
        upload_file = SimpleUploadedFile(
            "broken.xlsx",
            b"this-is-not-a-real-workbook",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": upload_file},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Excel workbook is corrupted or unreadable.")

    def test_empty_excel_workbook_is_rejected(self):
        self.client.login(username="finance_upload", password="TempPass123!")
        workbook = Workbook()
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        upload_file = SimpleUploadedFile(
            "empty.xlsx",
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": upload_file},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Excel workbook is empty.")

    def test_missing_required_excel_headers_are_reported(self):
        self.client.login(username="finance_upload", password="TempPass123!")
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["invoice_number", "issue_date", "total"])
        sheet.append(["INV-001", "2026-05-01", 120])
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        upload_file = SimpleUploadedFile(
            "wrong_headers.xlsx",
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": upload_file},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Missing required columns for:")

    def test_excel_values_are_normalized_like_csv(self):
        csv_rows = [
            {
                "seller_id": "S1",
                "shop_title": "Acme Salon",
                "OrderID": "12345",
                "paymentMethod": "Credit Card",
                "email": "billing@acme.com",
                "customerName": "Acme Customer",
                "qty": "2",
                "serviceName": "Hair Cut",
                "bookedDate": "2026-05-01 10:00:00",
                "vanidayShare": "120.50",
            }
        ]
        excel_rows = [
            [
                "S1",
                "Acme Salon",
                12345,
                "Credit Card",
                "billing@acme.com",
                "Acme Customer",
                2,
                "Hair Cut",
                timezone.datetime(2026, 5, 1, 10, 0),
                120.5,
            ]
        ]

        parsed_csv = parse_invoice_csv(self._build_csv_upload(rows=csv_rows))
        parsed_excel = parse_invoice_excel(self._build_excel_upload(rows=excel_rows))

        csv_row = parsed_csv["valid_rows"][0]
        excel_row = parsed_excel["valid_rows"][0]
        self.assertEqual(excel_row["source"]["order_id"], "12345")
        self.assertEqual(csv_row["amount"], excel_row["amount"])
        self.assertEqual(csv_row["quantity"], excel_row["quantity"])
        self.assertEqual(csv_row["group_period"], excel_row["group_period"])
        self.assertEqual(csv_row["item_description"], excel_row["item_description"])

    def test_excel_preview_shows_valid_and_invalid_rows(self):
        self.client.login(username="finance_upload", password="TempPass123!")
        rows = [
            [
                "S1",
                "Acme Salon",
                "ORD-001",
                "Credit Card",
                "billing@acme.com",
                "Acme Customer",
                2,
                "Hair Cut",
                timezone.datetime(2026, 5, 1, 10, 0),
                120,
            ],
            [
                "S1",
                "",
                "ORD-002",
                "Credit Card",
                "",
                "",
                1,
                "Nail Service",
                timezone.datetime(2026, 5, 2, 11, 0),
                "",
            ],
        ]

        response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": self._build_excel_upload(rows=rows)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Validation Issues")
        preview = response.context["preview"]
        self.assertEqual(len(preview["valid_rows"]), 1)
        self.assertEqual(len(preview["invalid_rows"]), 1)

    def test_confirmation_saves_valid_excel_rows_only(self):
        self.client.login(username="finance_upload", password="TempPass123!")
        rows = [
            [
                "S1",
                "Acme Salon",
                "ORD-001",
                "Credit Card",
                "billing@acme.com",
                "Acme Customer",
                2,
                "Hair Cut",
                timezone.datetime(2026, 5, 1, 10, 0),
                120,
            ],
            [
                "S1",
                "Acme Salon",
                "ORD-002",
                "Credit Card",
                "billing@acme.com",
                "Acme Customer",
                1,
                "Nail Service",
                timezone.datetime(2026, 5, 2, 11, 0),
                80,
            ],
            [
                "S1",
                "",
                "ORD-003",
                "Credit Card",
                "",
                "",
                1,
                "Bad Row",
                timezone.datetime(2026, 5, 3, 12, 0),
                "",
            ],
        ]
        preview_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={
                "action": "preview",
                "csv_file": self._build_excel_upload(rows=rows, filename="vaniday_batch.xlsx"),
            },
        )
        self.assertEqual(preview_response.status_code, 200)
        import_token = preview_response.context["preview"]["import_token"]

        confirm_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "confirm", "import_token": import_token},
        )

        self.assertEqual(confirm_response.status_code, 302)
        imported_invoice = Invoice.objects.get(notes__contains="vaniday_batch.xlsx")
        self.assertEqual(imported_invoice.items.count(), 2)
        self.assertEqual(imported_invoice.total_amount, Decimal("200.00"))
        self.assertEqual(InvoiceSourceRow.objects.filter(source_file_name="vaniday_batch.xlsx").count(), 3)
        job = ImportJob.objects.latest("id")
        self.assertEqual(job.saved_rows, 2)
        self.assertEqual(job.invalid_rows, 1)

    def test_excel_duplicate_rows_keep_existing_grouping_behavior(self):
        self.client.login(username="finance_upload", password="TempPass123!")
        duplicate_row = [
            "S1",
            "Acme Salon",
            "ORD-001",
            "Credit Card",
            "billing@acme.com",
            "Acme Customer",
            1,
            "Hair Cut",
            timezone.datetime(2026, 5, 1, 10, 0),
            120,
        ]
        preview_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={
                "action": "preview",
                "csv_file": self._build_excel_upload(rows=[duplicate_row, duplicate_row], filename="duplicates.xlsx"),
            },
        )
        import_token = preview_response.context["preview"]["import_token"]

        self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "confirm", "import_token": import_token},
        )

        imported_invoice = Invoice.objects.get(notes__contains="duplicates.xlsx")
        self.assertEqual(imported_invoice.items.count(), 2)
        self.assertEqual(imported_invoice.total_amount, Decimal("240.00"))

    def test_new_upload_replaces_previous_preview_in_same_session(self):
        self.client.login(username="finance_upload", password="TempPass123!")
        first_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": self._build_csv_upload(filename="first.csv")},
        )
        first_token = first_response.context["preview"]["import_token"]

        second_rows = [
            {
                "seller_id": "S2",
                "shop_title": "Beta Salon",
                "OrderID": "ORD-101",
                "paymentMethod": "Credit Card",
                "email": "billing@beta.com",
                "customerName": "Beta Customer",
                "qty": "1",
                "serviceName": "Facial",
                "bookedDate": "2026-06-10 09:00:00",
                "vanidayShare": "50.00",
            }
        ]
        second_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "preview", "csv_file": self._build_csv_upload(rows=second_rows, filename="second.csv")},
        )
        second_token = second_response.context["preview"]["import_token"]

        expired_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "confirm", "import_token": first_token},
            follow=True,
        )
        valid_response = self.client.post(
            reverse("invoice-csv-upload"),
            data={"action": "confirm", "import_token": second_token},
        )

        self.assertEqual(expired_response.status_code, 200)
        self.assertContains(expired_response, "Import preview expired. Please upload the file again.")
        self.assertEqual(valid_response.status_code, 302)
        imported_invoice = Invoice.objects.get(notes__contains="second.csv")
        self.assertEqual(imported_invoice.total_amount, Decimal("50.00"))
