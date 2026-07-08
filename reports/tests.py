from decimal import Decimal
from datetime import date, datetime, time, timedelta
import warnings

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF, SUPERADMIN
from core.models import AuditLog
from invoicing.models import Customer, Invoice
from notifications.models import EmailDeliveryLog, PaymentReminderSettings
from payments.models import PaymentRecord


User = get_user_model()


warnings.filterwarnings(
    "ignore",
    message=r"DateTimeField AuditLog\.created_at received a naive datetime .* while time zone support is active\.",
    category=RuntimeWarning,
)


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

    def _aware_datetime(self, day_value, hour=10):
        return timezone.make_aware(datetime.combine(day_value, time(hour=hour)))

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
        self.assertContains(response, "Payment Report")

    def test_admin_can_access_report(self):
        user = self._make_user("report_admin", ADMIN)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detailed Payment Records")

    def test_finance_can_access_report(self):
        user = self._make_user("report_finance", FINANCE)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment Status Breakdown")

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

    def test_hr_cannot_access_report(self):
        user = self._make_user("report_hr", HR)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 403)

    def test_payment_report_shows_purpose_kpis_and_no_urgent_message(self):
        user = self._make_user("report_payment_ui", FINANCE)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Use this report to review successful payments, failed attempts, bank transfers and refunds requiring attention.",
        )
        self.assertContains(response, "Collected This Month")
        self.assertContains(response, "Successful Payments")
        self.assertContains(response, "Pending Bank Transfers")
        self.assertContains(response, "Refunded Amount")
        self.assertContains(response, "Collected This Year")
        self.assertContains(response, "Payment Attention")
        self.assertContains(response, "No urgent payment issues were found for the current report data.")
        self.assertContains(response, "S$109.00")
        self.assertContains(response, "Detailed Payment Records")
        self.assertContains(response, "Open invoice")
        self.assertNotContains(response, "View Related Invoice")
        self.assertNotContains(response, ">Open<")

    def test_payment_report_shows_pending_manual_bank_transfer_confirmation_action(self):
        user = self._make_user("report_manual_attention", FINANCE)
        pending_invoice = Invoice.objects.create(
            invoice_number="INV-REPORT-1003",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=10),
            currency="SGD",
            subtotal=Decimal("80.00"),
            tax_amount=Decimal("7.20"),
            total_amount=Decimal("87.20"),
        )
        PaymentRecord.objects.create(
            invoice=pending_invoice,
            payment_reference="PAY-REPORT-003",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=Decimal("87.20"),
            currency="SGD",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm bank transfer")
        self.assertContains(response, reverse("payment-bank-transfer-confirm", args=[pending_invoice.pk]))
        self.assertContains(response, pending_invoice.invoice_number)

    def test_payment_report_includes_date_range_hooks_and_shared_script(self):
        user = self._make_user("report_payment_dates_ui", FINANCE)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment Date From")
        self.assertContains(response, "Payment Date To")
        self.assertContains(response, "data-date-range-form")
        self.assertContains(response, "data-date-from")
        self.assertContains(response, "data-date-to")
        self.assertContains(response, "data-date-error")
        self.assertContains(response, "js/date-range-filters.js")
        self.assertContains(response, f'href="{reverse("payment-stripe-report")}"', html=False)

    def test_payment_report_filters_successful_payments_by_paid_at_range(self):
        user = self._make_user("report_payment_dates_filter", FINANCE)
        self.client.force_login(user)
        included_day = date(2026, 6, 14)
        excluded_day = date(2026, 5, 20)

        PaymentRecord.objects.filter(payment_reference="PAY-REPORT-001").update(
            paid_at=self._aware_datetime(included_day, hour=11)
        )

        invoice_outside = Invoice.objects.create(
            invoice_number="INV-REPORT-1002",
            customer=self.customer,
            status=Invoice.STATUS_PAID,
            issue_date=excluded_day,
            due_date=excluded_day + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("60.00"),
            tax_amount=Decimal("5.40"),
            total_amount=Decimal("65.40"),
        )
        PaymentRecord.objects.create(
            invoice=invoice_outside,
            payment_reference="PAY-REPORT-002",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("65.40"),
            currency="SGD",
            paid_at=self._aware_datetime(excluded_day, hour=9),
            stripe_checkout_session_id="cs_test_report_002",
        )

        response = self.client.get(
            reverse("payment-stripe-report"),
            data={"date_from": "2026-06-14", "date_to": "2026-06-14"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment Date: 14 Jun 2026 to 14 Jun 2026")
        self.assertContains(response, "PAY-REPORT-001")
        self.assertNotContains(response, "PAY-REPORT-002")
        self.assertContains(response, 'name="date_from"', html=False)
        self.assertContains(response, 'value="2026-06-14"', html=False)
        self.assertContains(response, 'name="date_to"', html=False)
        self.assertContains(response, 'value="2026-06-14"', html=False)

    def test_payment_report_rejects_invalid_date_range_and_preserves_values(self):
        user = self._make_user("report_payment_dates_invalid", FINANCE)
        self.client.force_login(user)

        response = self.client.get(
            reverse("payment-stripe-report"),
            data={"date_from": "2026-06-18", "date_to": "2026-06-14"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From date cannot be later than To date.")
        self.assertContains(response, 'name="date_from"', html=False)
        self.assertContains(response, 'value="2026-06-18"', html=False)
        self.assertContains(response, 'name="date_to"', html=False)
        self.assertContains(response, 'value="2026-06-14"', html=False)
        self.assertContains(response, "PAY-REPORT-001")

    def test_payment_report_allows_from_only_and_to_only_date_filters(self):
        user = self._make_user("report_payment_dates_partial", FINANCE)
        self.client.force_login(user)

        from_only_response = self.client.get(
            reverse("payment-stripe-report"),
            data={"date_from": "2026-06-14"},
        )
        to_only_response = self.client.get(
            reverse("payment-stripe-report"),
            data={"date_to": "2026-06-14"},
        )

        self.assertEqual(from_only_response.status_code, 200)
        self.assertEqual(to_only_response.status_code, 200)
        self.assertContains(from_only_response, "Payment Date: from 14 Jun 2026")
        self.assertContains(to_only_response, "Payment Date: up to 14 Jun 2026")
        self.assertContains(from_only_response, 'value="2026-06-14"', html=False)
        self.assertContains(to_only_response, 'value="2026-06-14"', html=False)


class PaymentStripeReportNavigationPlacementTests(TestCase):
    def _make_user(self, username, role):
        user = User.objects.create_user(username=username, password="TempPass123!")
        user.role_profile.role = role
        user.role_profile.save(update_fields=["role", "updated_at"])
        return user

    def test_main_navbar_does_not_show_payment_report_item(self):
        user = self._make_user("nav_super", SUPERADMIN)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            f'<a class="nav-link" href="{reverse("payment-stripe-report")}">Payment Report</a>',
            html=True,
        )

    def test_management_dashboard_shows_report_link_for_superadmin_and_admin_only(self):
        superadmin = self._make_user("dash_super", SUPERADMIN)
        self.client.force_login(superadmin)
        super_response = self.client.get(reverse("dashboard"))
        self.assertContains(super_response, reverse("payment-stripe-report"))
        self.client.logout()

        admin = self._make_user("dash_admin", ADMIN)
        self.client.force_login(admin)
        admin_response = self.client.get(reverse("dashboard"))
        self.assertContains(admin_response, reverse("payment-stripe-report"))
        self.client.logout()

        finance = self._make_user("dash_finance", FINANCE)
        self.client.force_login(finance)
        finance_response = self.client.get(reverse("dashboard"))
        self.assertEqual(finance_response.status_code, 302)
        self.assertEqual(finance_response.url, reverse("invoice-dashboard"))

    def test_invoice_dashboard_shows_report_link_for_finance_roles(self):
        superadmin = self._make_user("inv_super", SUPERADMIN)
        self.client.force_login(superadmin)
        super_response = self.client.get(reverse("invoice-dashboard"))
        self.assertEqual(super_response.status_code, 200)
        self.assertContains(super_response, reverse("payment-stripe-report"))
        self.client.logout()

        admin = self._make_user("inv_admin", ADMIN)
        self.client.force_login(admin)
        admin_response = self.client.get(reverse("invoice-dashboard"))
        self.assertEqual(admin_response.status_code, 200)
        self.assertContains(admin_response, reverse("payment-stripe-report"))
        self.client.logout()

        finance = self._make_user("inv_finance", FINANCE)
        self.client.force_login(finance)
        finance_response = self.client.get(reverse("invoice-dashboard"))
        self.assertEqual(finance_response.status_code, 200)
        self.assertContains(finance_response, reverse("payment-stripe-report"))


class AdminSecurityReportTests(TestCase):
    def _make_user(self, username, role):
        user = User.objects.create_user(username=username, password="TempPass123!")
        user.role_profile.role = role
        user.role_profile.save(update_fields=["role", "updated_at"])
        return user

    def setUp(self):
        self.customer = Customer.objects.create(name="Security Customer", email="security@example.com")
        self.invoice = Invoice.objects.create(
            invoice_number="INV-SEC-1001",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("50.00"),
            tax_amount=Decimal("4.50"),
            total_amount=Decimal("54.50"),
        )
        PaymentReminderSettings.load()

        flagged_user = self._make_user("flagged_for_report", STAFF)
        self.failed_login_log = AuditLog.objects.create(
            user=flagged_user,
            action="auth.login.failed",
            metadata={"username": flagged_user.username},
        )
        self.permission_denied_log = AuditLog.objects.create(
            user=flagged_user,
            action="auth.permission_denied",
            metadata={"path": "/admin-dashboard/"},
        )
        admin_actor = self._make_user("admin_actor_report", ADMIN)
        self.account_created_log = AuditLog.objects.create(
            user=admin_actor,
            action="admin.account.created",
            metadata={"username": "new_user"},
        )
        self.role_changed_log = AuditLog.objects.create(
            user=admin_actor,
            action="admin.account.role_changed",
            metadata={"username": "new_user", "new_role": STAFF},
        )
        self.password_updated_log = AuditLog.objects.create(
            user=admin_actor,
            action="admin.account.password_updated",
            metadata={"username": "new_user"},
        )
        self.reminder_email_log = EmailDeliveryLog.objects.create(
            recipient_email="security@example.com",
            subject="Reminder",
            template_key="payment_reminder_due_date",
            status=EmailDeliveryLog.STATUS_SENT,
            related_object_type="invoice",
            related_object_id=str(self.invoice.id),
        )

    def _aware_datetime(self, day_value, hour=10):
        return timezone.make_aware(datetime.combine(day_value, time(hour=hour)))

    def test_superadmin_and_admin_can_access_admin_security_report(self):
        superadmin = self._make_user("security_super", SUPERADMIN)
        self.client.force_login(superadmin)
        super_response = self.client.get(reverse("admin-security-report"))
        self.assertEqual(super_response.status_code, 200)
        self.assertContains(super_response, "Admin & Security Report")
        self.client.logout()

        admin = self._make_user("security_admin", ADMIN)
        self.client.force_login(admin)
        admin_response = self.client.get(reverse("admin-security-report"))
        self.assertEqual(admin_response.status_code, 200)
        self.assertContains(admin_response, "User Summary")

    def test_non_admin_roles_cannot_access_admin_security_report(self):
        for role, username in [
            (FINANCE, "security_finance"),
            (HR, "security_hr"),
            (STAFF, "security_staff"),
            (CUSTOMER, "security_customer"),
        ]:
            user = self._make_user(username, role)
            self.client.force_login(user)
            response = self.client.get(reverse("admin-security-report"))
            self.assertEqual(response.status_code, 403)
            self.client.logout()

    def test_admin_security_report_contains_expected_sections(self):
        admin = self._make_user("security_content_admin", ADMIN)
        self.client.force_login(admin)

        response = self.client.get(reverse("admin-security-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Use this report to investigate failed logins, suspended accounts, permission problems and email delivery failures.",
        )
        self.assertContains(response, "Requires Investigation")
        self.assertContains(response, "User Summary")
        self.assertContains(response, "Admin Activity Summary")
        self.assertContains(response, "Reminder and Announcement Tools")
        self.assertContains(response, "Recent Suspicious Activities")
        self.assertContains(response, "Recent Failed Email Deliveries")
        self.assertContains(response, "Recent Account Creations")
        self.assertContains(response, "Recent Role Changes")
        self.assertContains(response, "Recent Password Changes")
        self.assertContains(response, "Review Suspicious Activity")
        self.assertContains(response, "Open Reminder Settings")
        self.assertContains(response, "Send Announcement")
        self.assertNotContains(response, ">Open<")

    def test_admin_security_report_shows_failed_email_section_when_failure_exists(self):
        EmailDeliveryLog.objects.create(
            recipient_email="alert@example.com",
            subject="Invoice Email",
            template_key="invoice_email_v1",
            status=EmailDeliveryLog.STATUS_FAILED,
            error_message="SMTP timeout",
            related_object_type="invoice",
            related_object_id=str(self.invoice.id),
        )
        admin = self._make_user("security_failed_email_admin", ADMIN)
        self.client.force_login(admin)

        response = self.client.get(reverse("admin-security-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Failed Email Deliveries")
        self.assertContains(response, "Review Failed Emails")
        self.assertContains(response, "alert@example.com")

    def test_admin_dashboard_has_admin_security_report_link(self):
        admin = self._make_user("security_link_admin", ADMIN)
        self.client.force_login(admin)

        response = self.client.get(reverse("admin-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin-security-report"))

    def test_admin_security_report_includes_date_range_hooks_and_shared_script(self):
        admin = self._make_user("security_dates_admin", ADMIN)
        self.client.force_login(admin)

        response = self.client.get(reverse("admin-security-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Event Date From")
        self.assertContains(response, "Event Date To")
        self.assertContains(response, "data-date-range-form")
        self.assertContains(response, "data-date-from")
        self.assertContains(response, "data-date-to")
        self.assertContains(response, "data-date-error")
        self.assertContains(response, "js/date-range-filters.js")
        self.assertContains(response, f'href="{reverse("admin-security-report")}"', html=False)

    def test_admin_security_report_filters_audit_and_email_events_by_date_range(self):
        admin = self._make_user("security_filter_admin", ADMIN)
        self.client.force_login(admin)
        inside_day = date(2026, 6, 14)
        outside_day = date(2026, 5, 10)

        AuditLog.objects.filter(pk=self.failed_login_log.pk).update(created_at=self._aware_datetime(inside_day, 9))
        AuditLog.objects.filter(pk=self.permission_denied_log.pk).update(created_at=self._aware_datetime(inside_day, 12))
        AuditLog.objects.filter(pk=self.account_created_log.pk).update(created_at=self._aware_datetime(outside_day, 9))
        AuditLog.objects.filter(pk=self.role_changed_log.pk).update(created_at=self._aware_datetime(outside_day, 10))
        AuditLog.objects.filter(pk=self.password_updated_log.pk).update(created_at=self._aware_datetime(outside_day, 11))
        EmailDeliveryLog.objects.filter(pk=self.reminder_email_log.pk).update(
            attempted_at=self._aware_datetime(inside_day, 14)
        )
        failed_email_log = EmailDeliveryLog.objects.create(
            recipient_email="late@example.com",
            subject="Late Alert",
            template_key="invoice_email_v1",
            status=EmailDeliveryLog.STATUS_FAILED,
            error_message="SMTP timeout",
            related_object_type="invoice",
            related_object_id=str(self.invoice.id),
        )
        EmailDeliveryLog.objects.filter(pk=failed_email_log.pk).update(
            attempted_at=self._aware_datetime(inside_day, 16)
        )

        response = self.client.get(
            reverse("admin-security-report"),
            data={"date_from": "2026-06-14", "date_to": "2026-06-14"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Event Date: 14 Jun 2026 to 14 Jun 2026")
        self.assertContains(response, "auth.login.failed")
        self.assertContains(response, "auth.permission_denied")
        self.assertContains(response, "late@example.com")
        self.assertContains(response, "security@example.com")
        self.assertNotContains(response, "admin.account.created")
        self.assertNotContains(response, "admin.account.role_changed")

    def test_admin_security_report_rejects_invalid_date_range_and_preserves_values(self):
        admin = self._make_user("security_invalid_admin", ADMIN)
        self.client.force_login(admin)

        response = self.client.get(
            reverse("admin-security-report"),
            data={"date_from": "2026-06-18", "date_to": "2026-06-14"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From date cannot be later than To date.")
        self.assertContains(response, 'name="date_from"', html=False)
        self.assertContains(response, 'value="2026-06-18"', html=False)
        self.assertContains(response, 'name="date_to"', html=False)
        self.assertContains(response, 'value="2026-06-14"', html=False)
        self.assertContains(response, "User Summary")


class InvoiceCustomerReportTests(TestCase):
    def _make_user(self, username, role):
        user = User.objects.create_user(username=username, password="TempPass123!")
        user.role_profile.role = role
        user.role_profile.save(update_fields=["role", "updated_at"])
        return user

    def setUp(self):
        self.customer_1 = Customer.objects.create(name="Customer One", email="cust1@example.com")
        self.customer_2 = Customer.objects.create(name="Customer Two", email="cust2@example.com")
        self.invoice_paid = Invoice.objects.create(
            invoice_number="INV-CUS-1001",
            customer=self.customer_1,
            status=Invoice.STATUS_PAID,
            issue_date=timezone.localdate() - timedelta(days=3),
            due_date=timezone.localdate() + timedelta(days=4),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
        )
        self.invoice_overdue = Invoice.objects.create(
            invoice_number="INV-CUS-1002",
            customer=self.customer_1,
            status=Invoice.STATUS_OVERDUE,
            issue_date=timezone.localdate() - timedelta(days=20),
            due_date=timezone.localdate() - timedelta(days=2),
            currency="SGD",
            subtotal=Decimal("150.00"),
            tax_amount=Decimal("13.50"),
            total_amount=Decimal("163.50"),
        )
        self.invoice_pending = Invoice.objects.create(
            invoice_number="INV-CUS-1003",
            customer=self.customer_2,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate() - timedelta(days=1),
            due_date=timezone.localdate() + timedelta(days=9),
            currency="SGD",
            subtotal=Decimal("80.00"),
            tax_amount=Decimal("7.20"),
            total_amount=Decimal("87.20"),
        )
        PaymentRecord.objects.create(
            invoice=self.invoice_paid,
            payment_reference="PAY-CUS-1001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("109.00"),
            currency="SGD",
            paid_at=timezone.now() - timedelta(days=2),
        )
        EmailDeliveryLog.objects.create(
            recipient_email="cust1@example.com",
            subject="Invoice Email",
            template_key="invoice_email_v1",
            status=EmailDeliveryLog.STATUS_SENT,
            related_object_type="invoice",
            related_object_id=str(self.invoice_paid.id),
        )

    def _report_response(self, user, **params):
        self.client.force_login(user)
        response = self.client.get(reverse("invoice-customer-report"), data=params)
        return response

    def _aware_datetime(self, day_value, hour=10):
        return timezone.make_aware(datetime.combine(day_value, time(hour=hour)))

    def test_finance_admin_superadmin_can_access_invoice_customer_report(self):
        for role, username in [
            (FINANCE, "invoice_report_finance"),
            (ADMIN, "invoice_report_admin"),
            (SUPERADMIN, "invoice_report_super"),
        ]:
            user = self._make_user(username, role)
            self.client.force_login(user)
            response = self.client.get(reverse("invoice-customer-report"))
            self.assertEqual(response.status_code, 200)
            self.client.logout()

    def test_customer_staff_and_hr_cannot_access_invoice_customer_report(self):
        for role, username in [
            (CUSTOMER, "invoice_report_customer"),
            (STAFF, "invoice_report_staff"),
            (HR, "invoice_report_hr"),
        ]:
            user = self._make_user(username, role)
            self.client.force_login(user)
            response = self.client.get(reverse("invoice-customer-report"))
            self.assertEqual(response.status_code, 403)
            self.client.logout()

    def test_invoice_customer_report_renders_sections(self):
        user = self._make_user("invoice_report_render_finance", FINANCE)
        self.client.force_login(user)

        response = self.client.get(reverse("invoice-customer-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invoice / Customer Report")
        self.assertContains(
            response,
            "Use this report to analyse collection trends, overdue exposure, customer balances, and the invoice records returned by your filters.",
        )
        self.assertContains(response, "Analytical Charts")
        self.assertContains(response, "Customer Summary")
        self.assertContains(response, "Status Summary")
        self.assertContains(response, "Filters")
        self.assertContains(response, 'name="q"', html=False)
        self.assertContains(response, 'name="status"', html=False)
        self.assertContains(response, 'name="customer"', html=False)
        self.assertContains(response, 'name="date_type"', html=False)
        self.assertContains(response, 'name="date_from"', html=False)
        self.assertContains(response, 'name="date_to"', html=False)
        self.assertContains(response, 'name="quick_range"', html=False)
        self.assertContains(response, 'name="ageing"', html=False)
        self.assertContains(response, "Apply Filters")
        self.assertContains(response, "Clear Filters")
        self.assertContains(response, "Monthly Collection Trend")
        self.assertContains(response, "Overdue Ageing")
        self.assertContains(response, "Top Customers by Outstanding Amount")
        self.assertContains(response, "Customer-Level Analysis")
        self.assertContains(response, "Filtered Invoice Records")
        self.assertContains(response, "View Invoice Details")
        self.assertContains(response, "S$163.50")
        self.assertContains(response, "table-responsive")
        self.assertContains(response, "Showing ")
        self.assertNotContains(response, ">Open<")

    def test_invoice_customer_report_omits_dashboard_only_sections(self):
        user = self._make_user("invoice_report_analysis_finance", FINANCE)
        self.client.force_login(user)

        response = self.client.get(reverse("invoice-customer-report"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Finance Follow-up Required")
        self.assertNotContains(response, "Invoice Attention")
        self.assertNotContains(response, "Recent Invoices Requiring Action")
        self.assertNotContains(response, "Failed Invoice Email Deliveries")
        self.assertNotContains(response, "Invoice Status Distribution")

    def test_invoice_customer_report_filters_by_status_customer_and_month(self):
        user = self._make_user("invoice_report_filter_finance", FINANCE)
        self.client.force_login(user)
        date_from = (self.invoice_overdue.issue_date - timedelta(days=1)).strftime("%Y-%m-%d")
        date_to = (self.invoice_overdue.issue_date + timedelta(days=1)).strftime("%Y-%m-%d")

        response = self.client.get(
            reverse("invoice-customer-report"),
            data={
                "q": "Customer One",
                "status": Invoice.STATUS_OVERDUE,
                "customer": str(self.customer_1.id),
                "date_type": "issue_date",
                "date_from": date_from,
                "date_to": date_to,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search: Customer One")
        self.assertContains(response, "Status: Overdue")
        self.assertContains(response, f"Customer: {self.customer_1.name} ({self.customer_1.email})")
        self.assertContains(response, "Issue Date: ")
        self.assertContains(response, self.invoice_overdue.invoice_number)
        self.assertNotContains(response, self.invoice_pending.invoice_number)
        self.assertNotContains(response, self.invoice_paid.invoice_number)
        self.assertContains(response, 'name="q"', html=False)
        self.assertContains(response, 'value="Customer One"', html=False)
        self.assertContains(response, 'option value="overdue" selected')
        self.assertContains(response, f'option value="{self.customer_1.id}" selected')
        self.assertContains(response, 'option value="issue_date" selected')
        self.assertContains(response, 'name="date_from"', html=False)
        self.assertContains(response, f'value="{date_from}"', html=False)
        self.assertContains(response, 'name="date_to"', html=False)
        self.assertContains(response, f'value="{date_to}"', html=False)

    def test_invoice_customer_report_shows_empty_state_for_no_matches(self):
        user = self._make_user("invoice_report_empty_finance", FINANCE)
        self.client.force_login(user)

        response = self.client.get(
            reverse("invoice-customer-report"),
            data={"status": Invoice.STATUS_REFUNDED, "month": "2099-01"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No invoices match the selected filters.")
        self.assertContains(response, "Clear Filters")
        self.assertContains(response, "View All Invoices")
        self.assertNotContains(response, self.invoice_paid.invoice_number)
        self.assertNotContains(response, self.invoice_overdue.invoice_number)
        self.assertNotContains(response, self.invoice_pending.invoice_number)

    def test_invoice_customer_report_has_drill_down_links(self):
        user = self._make_user("invoice_report_links_finance", FINANCE)
        self.client.force_login(user)

        response = self.client.get(
            reverse("invoice-customer-report"),
            data={"customer": str(self.customer_1.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("invoice-list"))
        self.assertContains(response, reverse("invoice-detail", args=[self.invoice_overdue.pk]))
        self.assertContains(response, "View outstanding invoices ->")

    def test_invoice_customer_report_includes_date_range_hooks_and_shared_script(self):
        user = self._make_user("invoice_report_dates_ui", FINANCE)
        self.client.force_login(user)

        response = self.client.get(reverse("invoice-customer-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-date-range-form")
        self.assertContains(response, "data-date-from")
        self.assertContains(response, "data-date-to")
        self.assertContains(response, "data-date-error")
        self.assertContains(response, "js/date-range-filters.js")
        self.assertContains(response, f'href="{reverse("invoice-customer-report")}"', html=False)

    def test_invoice_customer_report_rejects_invalid_date_range_and_preserves_values(self):
        user = self._make_user("invoice_report_dates_invalid", FINANCE)
        self.client.force_login(user)

        response = self.client.get(
            reverse("invoice-customer-report"),
            data={
                "date_type": "issue_date",
                "date_from": "2026-06-18",
                "date_to": "2026-06-14",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From date cannot be later than To date.")
        self.assertContains(response, 'name="date_from"', html=False)
        self.assertContains(response, 'value="2026-06-18"', html=False)
        self.assertContains(response, 'name="date_to"', html=False)
        self.assertContains(response, 'value="2026-06-14"', html=False)
        self.assertContains(response, self.invoice_paid.invoice_number)
        self.assertContains(response, self.invoice_overdue.invoice_number)

    def test_invoice_customer_report_filters_payment_date_using_paid_at(self):
        user = self._make_user("invoice_report_payment_date_filter", FINANCE)
        self.client.force_login(user)
        included_day = date(2026, 6, 15)
        excluded_day = date(2026, 5, 12)

        PaymentRecord.objects.filter(invoice=self.invoice_paid).update(
            paid_at=self._aware_datetime(included_day, 11)
        )
        Invoice.objects.filter(pk=self.invoice_paid.pk).update(
            updated_at=self._aware_datetime(excluded_day, 15)
        )

        extra_invoice = Invoice.objects.create(
            invoice_number="INV-CUS-1004",
            customer=self.customer_2,
            status=Invoice.STATUS_PAID,
            issue_date=timezone.localdate() - timedelta(days=15),
            due_date=timezone.localdate() - timedelta(days=8),
            currency="SGD",
            subtotal=Decimal("40.00"),
            tax_amount=Decimal("3.60"),
            total_amount=Decimal("43.60"),
        )
        PaymentRecord.objects.create(
            invoice=extra_invoice,
            payment_reference="PAY-CUS-1004",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("43.60"),
            currency="SGD",
            paid_at=self._aware_datetime(excluded_day, 10),
        )
        Invoice.objects.filter(pk=extra_invoice.pk).update(
            updated_at=self._aware_datetime(included_day, 16)
        )

        response = self.client.get(
            reverse("invoice-customer-report"),
            data={
                "date_type": "payment_date",
                "date_from": "2026-06-15",
                "date_to": "2026-06-15",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.invoice_paid.invoice_number)
        self.assertNotContains(response, extra_invoice.invoice_number)
        self.assertContains(response, "View outstanding invoices ->")
        self.assertContains(response, "Payment Date: 15 Jun 2026 to 15 Jun 2026")

    def test_invoice_customer_report_limits_filtered_invoice_records_table(self):
        user = self._make_user("invoice_report_limit_finance", FINANCE)
        for index in range(30):
            Invoice.objects.create(
                invoice_number=f"INV-CUS-LIMIT-{index:02d}",
                customer=self.customer_1,
                status=Invoice.STATUS_SENT,
                issue_date=timezone.localdate() - timedelta(days=index),
                due_date=timezone.localdate() + timedelta(days=10),
                currency="SGD",
                subtotal=Decimal("50.00"),
                tax_amount=Decimal("4.50"),
                total_amount=Decimal("54.50"),
            )

        response = self._report_response(user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["detailed_invoices"]), 25)
        self.assertTrue(response.context["has_more_detailed_invoices"])
        self.assertContains(response, "Showing 25 of ")
        self.assertContains(response, "Refine the filters to narrow the result set further.")

    def test_invoice_customer_report_hides_active_filter_badges_when_no_filters(self):
        user = self._make_user("invoice_report_no_badges_finance", FINANCE)
        self.client.force_login(user)

        response = self.client.get(reverse("invoice-customer-report"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Search:")
        self.assertNotContains(response, "Date type:")

    def test_invoice_customer_report_does_not_count_past_due_draft_as_overdue(self):
        draft_invoice = Invoice.objects.create(
            invoice_number="INV-CUS-1004",
            customer=self.customer_2,
            status=Invoice.STATUS_DRAFT,
            issue_date=timezone.localdate() - timedelta(days=12),
            due_date=timezone.localdate() - timedelta(days=1),
            currency="SGD",
            subtotal=Decimal("40.00"),
            tax_amount=Decimal("3.60"),
            total_amount=Decimal("43.60"),
        )
        user = self._make_user("invoice_report_draft_finance", FINANCE)
        self.client.force_login(user)

        list_response = self.client.get(reverse("invoice-list"))
        report_response = self.client.get(reverse("invoice-customer-report"))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(report_response.status_code, 200)
        draft_invoice.refresh_from_db()
        self.assertEqual(draft_invoice.status, Invoice.STATUS_DRAFT)
        status_counts = {
            row["label"]: row["count"] for row in report_response.context["status_summary"]
        }
        self.assertEqual(status_counts["Draft"], 1)
        self.assertEqual(status_counts["Overdue"], 1)

    def test_invoice_customer_report_outstanding_amount_uses_issued_unpaid_statuses_only(self):
        user = self._make_user("invoice_report_outstanding_finance", FINANCE)
        Invoice.objects.create(
            invoice_number="INV-CUS-DRAFT-OUT",
            customer=self.customer_2,
            status=Invoice.STATUS_DRAFT,
            issue_date=timezone.localdate() - timedelta(days=4),
            due_date=timezone.localdate() + timedelta(days=4),
            currency="SGD",
            subtotal=Decimal("60.00"),
            tax_amount=Decimal("5.40"),
            total_amount=Decimal("65.40"),
        )
        Invoice.objects.create(
            invoice_number="INV-CUS-VIEWED-OUT",
            customer=self.customer_2,
            status=Invoice.STATUS_VIEWED,
            issue_date=timezone.localdate() - timedelta(days=3),
            due_date=timezone.localdate() + timedelta(days=5),
            currency="SGD",
            subtotal=Decimal("40.00"),
            tax_amount=Decimal("3.60"),
            total_amount=Decimal("43.60"),
        )
        Invoice.objects.create(
            invoice_number="INV-CUS-REFUNDED-OUT",
            customer=self.customer_2,
            status=Invoice.STATUS_REFUNDED,
            issue_date=timezone.localdate() - timedelta(days=6),
            due_date=timezone.localdate() - timedelta(days=1),
            currency="SGD",
            subtotal=Decimal("50.00"),
            tax_amount=Decimal("4.50"),
            total_amount=Decimal("54.50"),
        )

        response = self._report_response(user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["outstanding_amount"], Decimal("294.30"))
        self.assertContains(
            response,
            "Issued unpaid invoice balance across sent, viewed, and overdue records in the filtered set.",
        )

    def test_invoice_customer_report_searches_by_invoice_number_customer_name_and_email(self):
        user = self._make_user("invoice_report_search_finance", FINANCE)

        invoice_response = self._report_response(user, q=self.invoice_overdue.invoice_number)
        self.assertContains(invoice_response, self.invoice_overdue.invoice_number)
        self.assertNotContains(invoice_response, self.invoice_pending.invoice_number)

        name_response = self._report_response(user, q=self.customer_2.name)
        self.assertContains(name_response, self.invoice_pending.invoice_number)
        self.assertNotContains(name_response, self.invoice_overdue.invoice_number)

        email_response = self._report_response(user, q=self.customer_1.email)
        self.assertContains(email_response, self.invoice_paid.invoice_number)
        self.assertContains(email_response, self.invoice_overdue.invoice_number)
        self.assertNotContains(email_response, self.invoice_pending.invoice_number)

    def test_invoice_customer_report_trims_search_query(self):
        user = self._make_user("invoice_report_trim_finance", FINANCE)

        response = self._report_response(user, q=f"  {self.customer_1.name}  ")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search: Customer One")
        self.assertContains(response, 'value="Customer One"', html=False)
        self.assertContains(response, self.invoice_overdue.invoice_number)

    def test_invoice_customer_report_filters_by_due_date_range(self):
        user = self._make_user("invoice_report_due_date_finance", FINANCE)
        date_from = (self.invoice_overdue.due_date - timedelta(days=1)).strftime("%Y-%m-%d")
        date_to = (self.invoice_overdue.due_date + timedelta(days=1)).strftime("%Y-%m-%d")

        response = self._report_response(
            user,
            date_type="due_date",
            date_from=date_from,
            date_to=date_to,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.invoice_overdue.invoice_number)
        self.assertNotContains(response, self.invoice_pending.invoice_number)
        self.assertContains(response, "Due Date: ")

    def test_invoice_customer_report_filters_by_payment_date_using_paid_at(self):
        user = self._make_user("invoice_report_payment_date_finance", FINANCE)
        payment_day = timezone.localdate() - timedelta(days=2)

        response = self._report_response(
            user,
            date_type="payment_date",
            date_from=payment_day.strftime("%Y-%m-%d"),
            date_to=payment_day.strftime("%Y-%m-%d"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.invoice_paid.invoice_number)
        self.assertNotContains(response, self.invoice_pending.invoice_number)
        self.assertEqual(response.context["filtered_invoice_count"], 1)
        self.assertEqual(response.context["total_amount_collected_month"], Decimal("109.00"))

    def test_invoice_customer_report_payment_date_filter_does_not_use_invoice_updated_at(self):
        user = self._make_user("invoice_report_payment_date_updated_finance", FINANCE)
        payment_day = timezone.localdate() - timedelta(days=2)
        later_day = timezone.localdate()
        Invoice.objects.filter(pk=self.invoice_paid.pk).update(
            updated_at=timezone.make_aware(datetime.combine(later_day, time(15, 0)))
        )

        response = self._report_response(
            user,
            date_type="payment_date",
            date_from=later_day.strftime("%Y-%m-%d"),
            date_to=later_day.strftime("%Y-%m-%d"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.invoice_paid.invoice_number)
        self.assertEqual(response.context["total_amount_collected_month"], Decimal("0.00"))

        paid_response = self._report_response(
            user,
            date_type="payment_date",
            date_from=payment_day.strftime("%Y-%m-%d"),
            date_to=payment_day.strftime("%Y-%m-%d"),
        )
        self.assertContains(paid_response, self.invoice_paid.invoice_number)

    def test_invoice_customer_report_payment_date_collection_excludes_failed_cancelled_and_refunded(self):
        user = self._make_user("invoice_report_payment_status_finance", FINANCE)
        filter_day = timezone.localdate() - timedelta(days=1)
        invoice_refunded = Invoice.objects.create(
            invoice_number="INV-CUS-1006",
            customer=self.customer_2,
            status=Invoice.STATUS_REFUNDED,
            issue_date=filter_day - timedelta(days=3),
            due_date=filter_day + timedelta(days=3),
            currency="SGD",
            subtotal=Decimal("50.00"),
            tax_amount=Decimal("4.50"),
            total_amount=Decimal("54.50"),
        )
        PaymentRecord.objects.create(
            invoice=self.invoice_pending,
            payment_reference="PAY-CUS-FAILED",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_FAILED,
            amount=Decimal("87.20"),
            currency="SGD",
            paid_at=timezone.now() - timedelta(days=1),
        )
        PaymentRecord.objects.create(
            invoice=self.invoice_overdue,
            payment_reference="PAY-CUS-CANCELLED",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_CANCELLED,
            amount=Decimal("163.50"),
            currency="SGD",
            paid_at=timezone.now() - timedelta(days=1),
        )
        PaymentRecord.objects.create(
            invoice=invoice_refunded,
            payment_reference="PAY-CUS-REFUNDED",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_REFUNDED,
            amount=Decimal("54.50"),
            currency="SGD",
            paid_at=timezone.now() - timedelta(days=1),
        )

        response = self._report_response(
            user,
            date_type="payment_date",
            date_from=filter_day.strftime("%Y-%m-%d"),
            date_to=filter_day.strftime("%Y-%m-%d"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_amount_collected_month"], Decimal("0.00"))
        self.assertContains(response, "No invoices match the selected filters.")

    def test_invoice_customer_report_today_quick_range(self):
        user = self._make_user("invoice_report_today_quick_finance", FINANCE)
        today_invoice = Invoice.objects.create(
            invoice_number="INV-CUS-TODAY",
            customer=self.customer_1,
            status=Invoice.STATUS_PAID,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("30.00"),
            tax_amount=Decimal("2.70"),
            total_amount=Decimal("32.70"),
        )
        PaymentRecord.objects.create(
            invoice=today_invoice,
            payment_reference="PAY-CUS-TODAY",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("32.70"),
            currency="SGD",
            paid_at=timezone.now(),
        )

        response = self._report_response(user, date_type="payment_date", quick_range="today")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Range: Today")
        self.assertContains(response, today_invoice.invoice_number)
        self.assertNotContains(response, self.invoice_paid.invoice_number)

    def test_invoice_customer_report_last_7_days_quick_range(self):
        user = self._make_user("invoice_report_last7_finance", FINANCE)

        response = self._report_response(user, date_type="payment_date", quick_range="last_7_days")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Range: Last 7 Days")
        self.assertContains(response, self.invoice_paid.invoice_number)

    def test_invoice_customer_report_last_30_days_quick_range(self):
        user = self._make_user("invoice_report_last30_finance", FINANCE)

        response = self._report_response(user, date_type="payment_date", quick_range="last_30_days")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Range: Last 30 Days")
        self.assertContains(response, self.invoice_paid.invoice_number)

    def test_invoice_customer_report_this_month_quick_range(self):
        user = self._make_user("invoice_report_this_month_finance", FINANCE)
        current_month_invoice = Invoice.objects.create(
            invoice_number="INV-CUS-THIS-MONTH",
            customer=self.customer_1,
            status=Invoice.STATUS_PAID,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("30.00"),
            tax_amount=Decimal("2.70"),
            total_amount=Decimal("32.70"),
        )
        PaymentRecord.objects.create(
            invoice=current_month_invoice,
            payment_reference="PAY-CUS-THIS-MONTH",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("32.70"),
            currency="SGD",
            paid_at=timezone.now(),
        )

        response = self._report_response(user, date_type="payment_date", quick_range="this_month")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Range: This Month")
        self.assertContains(response, current_month_invoice.invoice_number)

    def test_invoice_customer_report_previous_month_quick_range(self):
        user = self._make_user("invoice_report_previous_month_finance", FINANCE)
        previous_month_day = timezone.localdate().replace(day=1) - timedelta(days=3)
        previous_invoice = Invoice.objects.create(
            invoice_number="INV-CUS-PREVIOUS",
            customer=self.customer_2,
            status=Invoice.STATUS_PAID,
            issue_date=previous_month_day,
            due_date=previous_month_day + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("60.00"),
            tax_amount=Decimal("5.40"),
            total_amount=Decimal("65.40"),
        )
        PaymentRecord.objects.create(
            invoice=previous_invoice,
            payment_reference="PAY-CUS-PREVIOUS",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("65.40"),
            currency="SGD",
            paid_at=timezone.make_aware(datetime.combine(previous_month_day, time(11, 0))),
        )
        current_month_invoice = Invoice.objects.create(
            invoice_number="INV-CUS-CURRENT",
            customer=self.customer_1,
            status=Invoice.STATUS_PAID,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("30.00"),
            tax_amount=Decimal("2.70"),
            total_amount=Decimal("32.70"),
        )
        PaymentRecord.objects.create(
            invoice=current_month_invoice,
            payment_reference="PAY-CUS-CURRENT",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("32.70"),
            currency="SGD",
            paid_at=timezone.now(),
        )

        response = self._report_response(user, date_type="payment_date", quick_range="previous_month")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Range: Previous Month")
        self.assertContains(response, previous_invoice.invoice_number)
        self.assertNotContains(response, current_month_invoice.invoice_number)

    def test_invoice_customer_report_invalid_date_shows_error_without_server_error(self):
        user = self._make_user("invoice_report_invalid_date_finance", FINANCE)

        response = self._report_response(
            user,
            date_type="issue_date",
            date_from="2026-99-99",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From date is invalid. Use YYYY-MM-DD.")

    def test_invoice_customer_report_from_date_after_to_date_shows_error(self):
        user = self._make_user("invoice_report_date_order_finance", FINANCE)

        response = self._report_response(
            user,
            date_type="due_date",
            date_from="2026-06-20",
            date_to="2026-06-10",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From date cannot be later than To date.")

    def test_invoice_customer_report_filters_overdue_ageing_bands(self):
        user = self._make_user("invoice_report_ageing_finance", FINANCE)
        ageing_customer = Customer.objects.create(name="Ageing Customer", email="ageing@example.com")
        invoice_1_7 = Invoice.objects.create(
            invoice_number="INV-AGE-1",
            customer=ageing_customer,
            status=Invoice.STATUS_OVERDUE,
            issue_date=timezone.localdate() - timedelta(days=10),
            due_date=timezone.localdate() - timedelta(days=4),
            currency="SGD",
            subtotal=Decimal("10.00"),
            tax_amount=Decimal("0.90"),
            total_amount=Decimal("10.90"),
        )
        invoice_8_30 = Invoice.objects.create(
            invoice_number="INV-AGE-2",
            customer=ageing_customer,
            status=Invoice.STATUS_OVERDUE,
            issue_date=timezone.localdate() - timedelta(days=25),
            due_date=timezone.localdate() - timedelta(days=14),
            currency="SGD",
            subtotal=Decimal("20.00"),
            tax_amount=Decimal("1.80"),
            total_amount=Decimal("21.80"),
        )
        invoice_31_60 = Invoice.objects.create(
            invoice_number="INV-AGE-3",
            customer=ageing_customer,
            status=Invoice.STATUS_OVERDUE,
            issue_date=timezone.localdate() - timedelta(days=50),
            due_date=timezone.localdate() - timedelta(days=45),
            currency="SGD",
            subtotal=Decimal("30.00"),
            tax_amount=Decimal("2.70"),
            total_amount=Decimal("32.70"),
        )
        invoice_over_60 = Invoice.objects.create(
            invoice_number="INV-AGE-4",
            customer=ageing_customer,
            status=Invoice.STATUS_OVERDUE,
            issue_date=timezone.localdate() - timedelta(days=90),
            due_date=timezone.localdate() - timedelta(days=75),
            currency="SGD",
            subtotal=Decimal("40.00"),
            tax_amount=Decimal("3.60"),
            total_amount=Decimal("43.60"),
        )

        response_1_7 = self._report_response(user, ageing="days_1_7")
        self.assertContains(response_1_7, invoice_1_7.invoice_number)
        self.assertNotContains(response_1_7, invoice_8_30.invoice_number)

        response_8_30 = self._report_response(user, ageing="days_8_30")
        self.assertContains(response_8_30, invoice_8_30.invoice_number)
        self.assertNotContains(response_8_30, invoice_31_60.invoice_number)

        response_31_60 = self._report_response(user, ageing="days_31_60")
        self.assertContains(response_31_60, invoice_31_60.invoice_number)
        self.assertNotContains(response_31_60, invoice_over_60.invoice_number)

        response_over_60 = self._report_response(user, ageing="days_over_60")
        self.assertContains(response_over_60, invoice_over_60.invoice_number)
        self.assertNotContains(response_over_60, invoice_1_7.invoice_number)

    def test_invoice_customer_report_ageing_excludes_draft_invoice(self):
        user = self._make_user("invoice_report_ageing_draft_finance", FINANCE)
        draft_invoice = Invoice.objects.create(
            invoice_number="INV-AGE-DRAFT",
            customer=self.customer_2,
            status=Invoice.STATUS_DRAFT,
            issue_date=timezone.localdate() - timedelta(days=20),
            due_date=timezone.localdate() - timedelta(days=10),
            currency="SGD",
            subtotal=Decimal("25.00"),
            tax_amount=Decimal("2.25"),
            total_amount=Decimal("27.25"),
        )

        response = self._report_response(user, ageing="all_overdue")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, draft_invoice.invoice_number)

    def test_invoice_customer_report_combines_customer_status_and_due_date_filters(self):
        user = self._make_user("invoice_report_combined_finance", FINANCE)
        date_from = (self.invoice_overdue.due_date - timedelta(days=1)).strftime("%Y-%m-%d")
        date_to = (self.invoice_overdue.due_date + timedelta(days=1)).strftime("%Y-%m-%d")

        response = self._report_response(
            user,
            customer=str(self.customer_1.id),
            status=Invoice.STATUS_OVERDUE,
            date_type="due_date",
            date_from=date_from,
            date_to=date_to,
            ageing="days_1_7",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.invoice_overdue.invoice_number)
        self.assertNotContains(response, self.invoice_paid.invoice_number)
        self.assertContains(response, "Ageing: 1-7 days overdue")
        self.assertContains(response, f'option value="{self.customer_1.id}" selected')
        self.assertContains(response, 'option value="days_1_7" selected')

    def test_invoice_customer_report_does_not_duplicate_invoice_rows_for_multiple_payments(self):
        user = self._make_user("invoice_report_duplicate_rows_finance", FINANCE)
        payment_day = timezone.localdate()
        multi_payment_invoice = Invoice.objects.create(
            invoice_number="INV-CUS-MULTI",
            customer=self.customer_1,
            status=Invoice.STATUS_PAID,
            issue_date=timezone.localdate() - timedelta(days=5),
            due_date=timezone.localdate() + timedelta(days=5),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
        )
        PaymentRecord.objects.create(
            invoice=multi_payment_invoice,
            payment_reference="PAY-CUS-MULTI-1",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("50.00"),
            currency="SGD",
            paid_at=timezone.make_aware(datetime.combine(payment_day, time(9, 0))),
        )
        PaymentRecord.objects.create(
            invoice=multi_payment_invoice,
            payment_reference="PAY-CUS-MULTI-2",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("59.00"),
            currency="SGD",
            paid_at=timezone.make_aware(datetime.combine(payment_day, time(16, 0))),
        )

        response = self._report_response(
            user,
            date_type="payment_date",
            date_from=payment_day.strftime("%Y-%m-%d"),
            date_to=payment_day.strftime("%Y-%m-%d"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filtered_invoice_count"], 1)
        self.assertEqual(len(response.context["recent_payments_received"]), 2)

    def test_invoice_customer_report_shows_clear_filters_link_without_query_parameters(self):
        user = self._make_user("invoice_report_clear_filters_finance", FINANCE)

        response = self._report_response(
            user,
            status=Invoice.STATUS_OVERDUE,
            quick_range="last_7_days",
            date_type="issue_date",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a href="{reverse("invoice-customer-report")}" class="btn btn-outline-secondary">Clear Filters</a>',
            html=True,
        )

    def test_invoice_dashboard_has_invoice_customer_report_link_for_finance(self):
        user = self._make_user("invoice_report_link_finance", FINANCE)
        self.client.force_login(user)

        response = self.client.get(reverse("invoice-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("invoice-customer-report"))

    def test_management_dashboard_has_invoice_customer_report_link_for_admin_superadmin_only(self):
        superadmin = self._make_user("invoice_report_link_super", SUPERADMIN)
        self.client.force_login(superadmin)
        super_response = self.client.get(reverse("dashboard"))
        self.assertContains(super_response, reverse("invoice-customer-report"))
        self.client.logout()

        admin = self._make_user("invoice_report_link_admin", ADMIN)
        self.client.force_login(admin)
        admin_response = self.client.get(reverse("dashboard"))
        self.assertContains(admin_response, reverse("invoice-customer-report"))
        self.client.logout()

        finance = self._make_user("invoice_report_link_finance_dashboard", FINANCE)
        self.client.force_login(finance)
        finance_response = self.client.get(reverse("dashboard"))
        self.assertEqual(finance_response.status_code, 302)
        self.assertEqual(finance_response.url, reverse("invoice-dashboard"))

    def test_management_dashboard_does_not_count_past_due_draft_as_overdue(self):
        draft_invoice = Invoice.objects.create(
            invoice_number="INV-CUS-1005",
            customer=self.customer_2,
            status=Invoice.STATUS_DRAFT,
            issue_date=timezone.localdate() - timedelta(days=15),
            due_date=timezone.localdate() - timedelta(days=2),
            currency="SGD",
            subtotal=Decimal("60.00"),
            tax_amount=Decimal("5.40"),
            total_amount=Decimal("65.40"),
        )
        admin = self._make_user("invoice_report_dashboard_admin", ADMIN)
        self.client.force_login(admin)

        list_response = self.client.get(reverse("invoice-list"))
        dashboard_response = self.client.get(reverse("dashboard"))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(dashboard_response.status_code, 200)
        draft_invoice.refresh_from_db()
        self.assertEqual(draft_invoice.status, Invoice.STATUS_DRAFT)
        self.assertEqual(dashboard_response.context["invoice_draft_count"], 1)
        self.assertEqual(dashboard_response.context["invoice_overdue_count"], 1)


class CollectionDateAccuracyReportTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Collections Customer", email="collections@example.com")
        self.finance_user = self._make_user("collections_finance", FINANCE)
        self.admin_user = self._make_user("collections_admin", ADMIN)

    def _make_user(self, username, role):
        user = User.objects.create_user(username=username, password="TempPass123!")
        user.role_profile.role = role
        user.role_profile.save(update_fields=["role", "updated_at"])
        return user

    def _month_start(self, months_ago=0):
        month_start = timezone.localdate().replace(day=1)
        for _ in range(months_ago):
            month_start = (month_start - timedelta(days=1)).replace(day=1)
        return month_start

    def _aware_datetime(self, day_value, hour=10):
        return timezone.make_aware(datetime.combine(day_value, time(hour=hour)))

    def test_invoice_customer_report_counts_payment_in_payment_month_not_issue_month(self):
        issue_month_start = self._month_start(2)
        payment_month_start = self._month_start(1)
        invoice = Invoice.objects.create(
            invoice_number="INV-REPORT-COLLECT-1",
            customer=self.customer,
            status=Invoice.STATUS_PAID,
            issue_date=issue_month_start,
            due_date=issue_month_start + timedelta(days=10),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
        )
        PaymentRecord.objects.create(
            invoice=invoice,
            payment_reference="PAY-REPORT-COLLECT-1",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("109.00"),
            currency="SGD",
            paid_at=self._aware_datetime(payment_month_start + timedelta(days=5)),
        )

        self.client.force_login(self.finance_user)
        payment_month_response = self.client.get(
            reverse("invoice-customer-report"),
            data={"month": payment_month_start.strftime("%Y-%m")},
        )
        issue_month_response = self.client.get(
            reverse("invoice-customer-report"),
            data={"month": issue_month_start.strftime("%Y-%m")},
        )

        self.assertEqual(payment_month_response.status_code, 200)
        self.assertEqual(issue_month_response.status_code, 200)
        self.assertEqual(payment_month_response.context["total_amount_collected_month"], Decimal("109.00"))
        self.assertEqual(issue_month_response.context["total_amount_collected_month"], Decimal("0.00"))

    def test_invoice_customer_report_uses_paid_at_even_after_later_invoice_edit(self):
        issue_month_start = self._month_start(2)
        payment_month_start = self._month_start(1)
        current_month_start = self._month_start(0)
        invoice = Invoice.objects.create(
            invoice_number="INV-REPORT-COLLECT-2",
            customer=self.customer,
            status=Invoice.STATUS_PAID,
            issue_date=issue_month_start,
            due_date=issue_month_start + timedelta(days=9),
            currency="SGD",
            subtotal=Decimal("120.00"),
            tax_amount=Decimal("10.80"),
            total_amount=Decimal("130.80"),
        )
        payment = PaymentRecord.objects.create(
            invoice=invoice,
            payment_reference="PAY-REPORT-COLLECT-2",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("130.80"),
            currency="SGD",
            paid_at=self._aware_datetime(payment_month_start + timedelta(days=8)),
        )
        Invoice.objects.filter(pk=invoice.pk).update(
            updated_at=self._aware_datetime(current_month_start + timedelta(days=3), hour=14)
        )

        self.client.force_login(self.finance_user)
        response = self.client.get(
            reverse("invoice-customer-report"),
            data={"month": payment_month_start.strftime("%Y-%m")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_amount_collected_month"], Decimal("130.80"))
        recent_payment = response.context["recent_payments_received"][0]
        self.assertEqual(recent_payment.id, payment.id)
        self.assertEqual(recent_payment.paid_at.date(), payment.paid_at.date())

    def test_invoice_customer_report_excludes_refunded_payment_from_collected_total(self):
        month_start = self._month_start(0)
        invoice = Invoice.objects.create(
            invoice_number="INV-REPORT-COLLECT-3",
            customer=self.customer,
            status=Invoice.STATUS_REFUNDED,
            issue_date=month_start,
            due_date=month_start + timedelta(days=10),
            currency="SGD",
            subtotal=Decimal("90.00"),
            tax_amount=Decimal("8.10"),
            total_amount=Decimal("98.10"),
        )
        PaymentRecord.objects.create(
            invoice=invoice,
            payment_reference="PAY-REPORT-COLLECT-3",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_REFUNDED,
            amount=Decimal("98.10"),
            currency="SGD",
            paid_at=self._aware_datetime(month_start + timedelta(days=4)),
        )

        self.client.force_login(self.finance_user)
        response = self.client.get(reverse("invoice-customer-report"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_amount_collected_month"], Decimal("0.00"))
        self.assertEqual(response.context["refunded_count"], 1)

    def test_payment_report_excludes_failed_and_cancelled_payments_from_collected_total(self):
        month_start = self._month_start(0)
        today = timezone.localdate()
        paid_invoice = Invoice.objects.create(
            invoice_number="INV-REPORT-COLLECT-4",
            customer=self.customer,
            status=Invoice.STATUS_PAID,
            issue_date=month_start,
            due_date=month_start + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
        )
        failed_invoice = Invoice.objects.create(
            invoice_number="INV-REPORT-COLLECT-5",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=month_start,
            due_date=month_start + timedelta(days=8),
            currency="SGD",
            subtotal=Decimal("50.00"),
            tax_amount=Decimal("4.50"),
            total_amount=Decimal("54.50"),
        )
        cancelled_invoice = Invoice.objects.create(
            invoice_number="INV-REPORT-COLLECT-6",
            customer=self.customer,
            status=Invoice.STATUS_OVERDUE,
            issue_date=month_start,
            due_date=month_start + timedelta(days=5),
            currency="SGD",
            subtotal=Decimal("60.00"),
            tax_amount=Decimal("5.40"),
            total_amount=Decimal("65.40"),
        )
        PaymentRecord.objects.create(
            invoice=paid_invoice,
            payment_reference="PAY-REPORT-COLLECT-4",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("109.00"),
            currency="SGD",
            paid_at=self._aware_datetime(today),
        )
        PaymentRecord.objects.create(
            invoice=failed_invoice,
            payment_reference="PAY-REPORT-COLLECT-5",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_FAILED,
            amount=Decimal("54.50"),
            currency="SGD",
        )
        PaymentRecord.objects.create(
            invoice=cancelled_invoice,
            payment_reference="PAY-REPORT-COLLECT-6",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_CANCELLED,
            amount=Decimal("65.40"),
            currency="SGD",
        )

        self.client.force_login(self.finance_user)
        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["successful_month_amount"], Decimal("109.00"))
        self.assertEqual(response.context["failed_cancelled_count"], 2)

    def test_management_dashboard_and_invoice_dashboard_match_current_month_collection(self):
        month_start = self._month_start(0)
        today = timezone.localdate()
        invoice = Invoice.objects.create(
            invoice_number="INV-REPORT-COLLECT-7",
            customer=self.customer,
            status=Invoice.STATUS_PAID,
            issue_date=self._month_start(1),
            due_date=self._month_start(1) + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("70.00"),
            tax_amount=Decimal("6.30"),
            total_amount=Decimal("76.30"),
        )
        PaymentRecord.objects.create(
            invoice=invoice,
            payment_reference="PAY-REPORT-COLLECT-7",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("76.30"),
            currency="SGD",
            paid_at=self._aware_datetime(today),
        )

        self.client.force_login(self.admin_user)
        dashboard_response = self.client.get(reverse("dashboard"))
        self.client.logout()
        self.client.force_login(self.finance_user)
        invoice_dashboard_response = self.client.get(reverse("invoice-dashboard"))

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(invoice_dashboard_response.status_code, 200)
        self.assertEqual(dashboard_response.context["collected_this_month"], Decimal("76.30"))
        self.assertEqual(invoice_dashboard_response.context["collected_month"], Decimal("76.30"))
