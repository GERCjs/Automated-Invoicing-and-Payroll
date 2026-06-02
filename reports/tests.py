from decimal import Decimal
from datetime import timedelta
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

    def test_hr_cannot_access_report(self):
        user = self._make_user("report_hr", HR)
        self.client.force_login(user)

        response = self.client.get(reverse("payment-stripe-report"))

        self.assertEqual(response.status_code, 403)


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
        self.assertNotContains(finance_response, reverse("payment-stripe-report"))

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
        AuditLog.objects.create(
            user=flagged_user,
            action="auth.login.failed",
            metadata={"username": flagged_user.username},
        )
        AuditLog.objects.create(
            user=flagged_user,
            action="auth.permission_denied",
            metadata={"path": "/admin-dashboard/"},
        )
        admin_actor = self._make_user("admin_actor_report", ADMIN)
        AuditLog.objects.create(
            user=admin_actor,
            action="admin.account.created",
            metadata={"username": "new_user"},
        )
        AuditLog.objects.create(
            user=admin_actor,
            action="admin.account.role_changed",
            metadata={"username": "new_user", "new_role": STAFF},
        )
        AuditLog.objects.create(
            user=admin_actor,
            action="admin.account.password_updated",
            metadata={"username": "new_user"},
        )
        EmailDeliveryLog.objects.create(
            recipient_email="security@example.com",
            subject="Reminder",
            template_key="payment_reminder_due_date",
            status=EmailDeliveryLog.STATUS_SENT,
            related_object_type="invoice",
            related_object_id=str(self.invoice.id),
        )

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
        self.assertContains(response, "User Summary")
        self.assertContains(response, "Security Summary")
        self.assertContains(response, "Admin Activity Summary")
        self.assertContains(response, "Reminder Summary")
        self.assertContains(response, "Recent Suspicious Activities")
        self.assertContains(response, "Recent Login-Related Audit Logs")
        self.assertContains(response, "Recent Account Creations")
        self.assertContains(response, "Recent Role Changes")
        self.assertContains(response, "Recent Password Changes")

    def test_admin_dashboard_has_admin_security_report_link(self):
        admin = self._make_user("security_link_admin", ADMIN)
        self.client.force_login(admin)

        response = self.client.get(reverse("admin-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin-security-report"))


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
        EmailDeliveryLog.objects.create(
            recipient_email="cust1@example.com",
            subject="Invoice Email",
            template_key="invoice_email_v1",
            status=EmailDeliveryLog.STATUS_SENT,
            related_object_type="invoice",
            related_object_id=str(self.invoice_paid.id),
        )

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
        self.assertContains(response, "Invoice Status Summary")
        self.assertContains(response, "Customer Summary")
        self.assertContains(response, "Recent Invoices Created")
        self.assertContains(response, "Recent Invoices Paid")
        self.assertContains(response, "Recent Invoice Emails Sent")
        self.assertContains(response, "Filter / Drill-down")
        self.assertContains(response, 'name="status"', html=False)
        self.assertContains(response, 'name="customer"', html=False)
        self.assertContains(response, 'name="month"', html=False)
        self.assertContains(response, "Detailed Invoice Drill-down")
        self.assertContains(response, "Follow-up Focus")

    def test_invoice_customer_report_filters_by_status_customer_and_month(self):
        user = self._make_user("invoice_report_filter_finance", FINANCE)
        self.client.force_login(user)
        selected_month = self.invoice_overdue.issue_date.strftime("%Y-%m")

        response = self.client.get(
            reverse("invoice-customer-report"),
            data={
                "status": Invoice.STATUS_OVERDUE,
                "customer": str(self.customer_1.id),
                "month": selected_month,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Showing: Status = Overdue")
        self.assertContains(response, f"Customer = {self.customer_1.name} ({self.customer_1.email})")
        self.assertContains(response, self.invoice_overdue.invoice_number)
        self.assertNotContains(response, self.invoice_pending.invoice_number)
        self.assertNotContains(response, self.invoice_paid.invoice_number)

    def test_invoice_customer_report_shows_empty_state_for_no_matches(self):
        user = self._make_user("invoice_report_empty_finance", FINANCE)
        self.client.force_login(user)

        response = self.client.get(
            reverse("invoice-customer-report"),
            data={"status": Invoice.STATUS_REFUNDED, "month": "2099-01"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No data found for the selected filters.")
        self.assertNotContains(response, self.invoice_paid.invoice_number)
        self.assertNotContains(response, self.invoice_overdue.invoice_number)
        self.assertNotContains(response, self.invoice_pending.invoice_number)

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
        self.assertNotContains(finance_response, reverse("invoice-customer-report"))
