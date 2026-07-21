from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.roles import ADMIN, CUSTOMER, FINANCE, HR, STAFF, SUPERADMIN
from imports.models import ImportJob
from invoicing.models import Customer, Invoice, InvoiceItem
from notifications.models import EmailDeliveryLog
from payments.models import PaymentRecord
from payroll.models import Employee, PayrollRecord
from .models import AuditLog

User = get_user_model()


class CorePhaseOneTests(TestCase):
    def test_customer_entry_page_is_public(self):
        response = self.client.get(reverse("customer-entry"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome to Vaniday")
        self.assertContains(response, "Access Your Invoice Portal")
        self.assertContains(response, reverse("login"))

    def test_login_page_has_customer_invoice_access_link(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customer Invoice Access")
        self.assertContains(response, reverse("customer-entry"))

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_login_logout_and_dashboard_flow(self):
        user = User.objects.create_user(username="coreuser", password="TempPass123!")
        user.role_profile.role = ADMIN
        user.role_profile.save(update_fields=["role", "updated_at"])

        login_response = self.client.post(
            reverse("login"),
            data={"username": "coreuser", "password": "TempPass123!"},
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.headers["Location"], reverse("dashboard"))

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertFalse(AuditLog.objects.filter(action="core.dashboard.viewed", user=user).exists())

        logout_response = self.client.post(reverse("logout"))
        self.assertEqual(logout_response.status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="auth.login", user=user).exists())
        self.assertTrue(AuditLog.objects.filter(action="auth.logout", user=user).exists())

    def test_audit_log_page_hides_noisy_actions_without_deleting_rows(self):
        admin = User.objects.create_superuser(
            username="auditadmin",
            email="auditadmin@example.com",
            password="TempPass123!",
        )
        noisy_log = AuditLog.objects.create(action="invoice.list.viewed", user=admin)
        important_log = AuditLog.objects.create(action="payment.invoice.marked_paid", user=admin)

        self.client.login(username="auditadmin", password="TempPass123!")

        response = self.client.get(reverse("dashboard-audit-logs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invoice marked as paid from payment")
        self.assertNotContains(response, "payment.invoice.marked_paid")
        self.assertNotContains(response, "invoice.list.viewed")

        filtered_response = self.client.get(
            reverse("dashboard-audit-logs"),
            data={"action": "invoice.list.viewed"},
        )
        self.assertEqual(filtered_response.status_code, 200)
        self.assertNotContains(filtered_response, "invoice.list.viewed")

        self.assertTrue(AuditLog.objects.filter(pk=noisy_log.pk).exists())
        self.assertTrue(AuditLog.objects.filter(pk=important_log.pk).exists())

    def test_audit_log_page_uses_scrollable_list_without_pagination(self):
        admin = User.objects.create_superuser(
            username="auditpager",
            email="auditpager@example.com",
            password="TempPass123!",
        )
        self.client.login(username="auditpager", password="TempPass123!")

        for index in range(15):
            AuditLog.objects.create(
                action="invoice.created",
                user=admin,
                target_type="invoice",
                target_id=str(index + 1),
                metadata={"invoice_number": f"INV-PAGE-{index + 1:04d}"},
            )

        response = self.client.get(
            reverse("dashboard-audit-logs"),
            data={"action": "invoice.created"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["logs"]), 15)
        self.assertContains(response, "Showing up to 500 matching audit logs")
        self.assertNotContains(response, "Previous")
        self.assertNotContains(response, "Next")

    def test_audit_log_page_shows_friendly_label_for_refund_actions(self):
        admin = User.objects.create_superuser(
            username="auditrefund",
            email="auditrefund@example.com",
            password="TempPass123!",
        )
        AuditLog.objects.create(
            action="payment.refund.succeeded",
            user=admin,
            target_type="invoice",
            target_id="1",
        )

        self.client.login(username="auditrefund", password="TempPass123!")
        response = self.client.get(reverse("dashboard-audit-logs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stripe refund succeeded")
        self.assertNotContains(response, "<td>payment.refund.succeeded</td>", html=True)


class CoreCollectionReportingTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username="core_admin", password="TempPass123!")
        self.admin_user.role_profile.role = ADMIN
        self.admin_user.role_profile.save(update_fields=["role", "updated_at"])
        self.customer = Customer.objects.create(name="Core Report Customer", email="core-report@example.com")

    def _month_start(self, months_ago=0):
        month_start = timezone.localdate().replace(day=1)
        for _ in range(months_ago):
            month_start = (month_start - timedelta(days=1)).replace(day=1)
        return month_start

    def _aware_datetime(self, day_value, hour=10):
        return timezone.make_aware(datetime.combine(day_value, time(hour=hour)))

    def test_dashboard_uses_payment_month_not_invoice_update_month_for_collection(self):
        issue_month_start = self._month_start(2)
        payment_month_start = self._month_start(1)
        current_month_start = self._month_start(0)
        invoice = Invoice.objects.create(
            invoice_number="INV-CORE-2001",
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
            payment_reference="PAY-CORE-2001",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("109.00"),
            currency="SGD",
            paid_at=self._aware_datetime(payment_month_start + timedelta(days=5)),
        )
        Invoice.objects.filter(pk=invoice.pk).update(
            updated_at=self._aware_datetime(current_month_start + timedelta(days=5), hour=12)
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["collected_this_month"], Decimal("0.00"))
        self.assertIn("Collected", response.context["outstanding_vs_collected_labels"])
        self.assertIn(109.0, response.context["collection_trend_values"])

    def test_dashboard_shows_refunded_amount_separately_and_not_in_collected_total(self):
        month_start = self._month_start(0)
        invoice = Invoice.objects.create(
            invoice_number="INV-CORE-2002",
            customer=self.customer,
            status=Invoice.STATUS_REFUNDED,
            issue_date=month_start,
            due_date=month_start + timedelta(days=10),
            currency="SGD",
            subtotal=Decimal("80.00"),
            tax_amount=Decimal("7.20"),
            total_amount=Decimal("87.20"),
        )
        PaymentRecord.objects.create(
            invoice=invoice,
            payment_reference="PAY-CORE-2002",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_REFUNDED,
            amount=Decimal("87.20"),
            currency="SGD",
            paid_at=self._aware_datetime(month_start + timedelta(days=3)),
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["collected_this_month"], Decimal("0.00"))
        self.assertEqual(response.context["outstanding_vs_collected_values"][2], 87.2)

    def test_management_dashboard_excludes_drafts_from_ceo_summary(self):
        draft_invoice = Invoice.objects.create(
            invoice_number="INV-CORE-DRAFT",
            customer=self.customer,
            status=Invoice.STATUS_DRAFT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("50.00"),
            tax_amount=Decimal("4.50"),
            total_amount=Decimal("54.50"),
        )
        issued_invoice = Invoice.objects.create(
            invoice_number="INV-CORE-ISSUED",
            customer=self.customer,
            status=Invoice.STATUS_SENT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["invoice_outstanding"], issued_invoice.total_amount)
        self.assertContains(response, "Issued Outstanding")
        self.assertContains(response, "Sent, viewed, and overdue invoices waiting for customer payment.")
        self.assertContains(response, "S$109.00")
        self.assertNotContains(response, "Drafts are tracked separately.")
        self.assertNotContains(response, "Draft Invoice Value")
        self.assertNotContains(response, "Review draft invoices")
        self.assertNotContains(response, "S$54.50")

    def test_management_dashboard_keeps_payroll_burden_off_ceo_snapshot(self):
        today = timezone.localdate()
        invoice = Invoice.objects.create(
            invoice_number="INV-CORE-NO-PAYROLL-KPI",
            customer=self.customer,
            status=Invoice.STATUS_PAID,
            issue_date=today,
            due_date=today + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("200.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
        )
        PaymentRecord.objects.create(
            invoice=invoice,
            payment_reference="PAY-CORE-NO-PAYROLL-KPI",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("200.00"),
            currency="SGD",
            paid_at=self._aware_datetime(today),
        )
        PayrollRecord.objects.create(
            employee_name="Cash Proxy Employee",
            employee_id="CORE-CASH-EMP",
            basic_salary=Decimal("120.00"),
            allowances=Decimal("30.00"),
            deductions=Decimal("10.00"),
            cpf_contribution=Decimal("20.00"),
            net_salary=Decimal("100.00"),
            payment_date=today,
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["collected_this_month"], Decimal("200.00"))
        self.assertContains(response, "CEO Health Snapshot")
        self.assertContains(response, "Payroll details stay in the Payroll Report.")
        self.assertNotContains(response, "Payroll Burden This Month")
        self.assertNotContains(response, "Cash After Payroll")
        self.assertNotContains(response, "Collections minus payroll burden")

    def test_management_dashboard_shows_previous_month_collection_comparison(self):
        today = timezone.localdate()
        previous_month_start = self._month_start(1)
        previous_payment_day = previous_month_start + timedelta(days=5)
        current_invoice = Invoice.objects.create(
            invoice_number="INV-CORE-CURRENT-MONTH",
            customer=self.customer,
            status=Invoice.STATUS_PAID,
            issue_date=today,
            due_date=today + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
        )
        previous_invoice = Invoice.objects.create(
            invoice_number="INV-CORE-PREV-MONTH",
            customer=self.customer,
            status=Invoice.STATUS_PAID,
            issue_date=previous_payment_day,
            due_date=previous_payment_day + timedelta(days=7),
            currency="SGD",
            subtotal=Decimal("70.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("70.00"),
        )
        PaymentRecord.objects.create(
            invoice=current_invoice,
            payment_reference="PAY-CORE-CURRENT-MONTH",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("100.00"),
            currency="SGD",
            paid_at=self._aware_datetime(today),
        )
        PaymentRecord.objects.create(
            invoice=previous_invoice,
            payment_reference="PAY-CORE-PREV-MONTH",
            provider=PaymentRecord.PROVIDER_STRIPE,
            status=PaymentRecord.STATUS_SUCCEEDED,
            amount=Decimal("70.00"),
            currency="SGD",
            paid_at=self._aware_datetime(previous_payment_day),
        )
        PayrollRecord.objects.create(
            employee_name="Current Payroll Employee",
            employee_id="CORE-CUR-PAY",
            basic_salary=Decimal("20.00"),
            allowances=Decimal("10.00"),
            deductions=Decimal("0.00"),
            cpf_contribution=Decimal("0.00"),
            net_salary=Decimal("30.00"),
            payment_date=today,
        )
        PayrollRecord.objects.create(
            employee_name="Previous Payroll Employee",
            employee_id="CORE-PREV-PAY",
            basic_salary=Decimal("40.00"),
            allowances=Decimal("10.00"),
            deductions=Decimal("0.00"),
            cpf_contribution=Decimal("0.00"),
            net_salary=Decimal("50.00"),
            payment_date=previous_payment_day,
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["collected_previous_month"], Decimal("70.00"))
        self.assertContains(response, response.context["previous_month_label"])
        self.assertContains(response, "S$30.00 higher than")

    def test_management_dashboard_shows_purpose_text_clear_links_and_empty_attention_state(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "CEO view of cash collection, issued receivables, operational risk, and drill-down reports.",
        )
        self.assertContains(response, "Reporting period:")
        self.assertContains(response, "Last updated:")
        self.assertContains(response, "CEO Health Snapshot")
        self.assertContains(response, "Collected This Month")
        self.assertContains(response, "Issued Outstanding")
        self.assertContains(response, "Operational Risk Items")
        self.assertContains(response, "Overdue Amount")
        self.assertContains(response, "Payment Issues")
        self.assertContains(response, "Import Issues")
        self.assertContains(response, "Top Collection Risks")
        self.assertContains(response, "No issued unpaid customer balances were found.")
        self.assertContains(response, "Management Attention")
        self.assertContains(response, "View Finance Report")
        self.assertContains(response, "View Payment Report")
        self.assertContains(response, "Payroll Report")
        self.assertContains(response, "Security Report")
        self.assertContains(response, "No urgent management issues were found for the current reporting period.")
        self.assertContains(response, "Collection Trend")
        self.assertContains(response, "Payroll details stay in the Payroll Report.")
        self.assertContains(response, "management-dashboard-container")
        self.assertContains(response, "report-kpi-grid")
        self.assertContains(response, "report-chart-summary")
        self.assertContains(response, "No collection trend data is available yet.")
        self.assertNotContains(response, "Draft Invoice Value")
        self.assertNotContains(response, "Review draft invoices")
        self.assertNotContains(response, "Failed Email Deliveries")
        self.assertNotContains(response, "Review failed emails")
        self.assertNotContains(response, "Payroll Burden This Month")
        self.assertNotContains(response, "Cash After Payroll")
        self.assertNotContains(response, "Monthly Collections vs Payroll")
        self.assertNotContains(response, "Report Centre")
        self.assertNotContains(response, "Detailed Reports")
        self.assertContains(response, "S$0.00")
        self.assertNotContains(response, "->")
        self.assertNotContains(response, ">Open<")

    def test_management_dashboard_shows_ranked_attention_rows_with_correct_links(self):
        overdue_invoice = Invoice.objects.create(
            invoice_number="INV-CORE-2003",
            customer=self.customer,
            status=Invoice.STATUS_OVERDUE,
            issue_date=timezone.localdate() - timedelta(days=20),
            due_date=timezone.localdate() - timedelta(days=5),
            currency="SGD",
            subtotal=Decimal("120.00"),
            tax_amount=Decimal("10.80"),
            total_amount=Decimal("130.80"),
        )
        AuditLog.objects.create(
            action="auth.login.failed",
            user=self.admin_user,
        )
        EmailDeliveryLog.objects.create(
            recipient_email="ops@example.com",
            subject="Invoice Email",
            template_key="invoice_email_v1",
            status=EmailDeliveryLog.STATUS_FAILED,
        )
        ImportJob.objects.create(
            module=ImportJob.MODULE_INVOICING,
            source_file_name="broken.csv",
            status=ImportJob.STATUS_FAILED,
            total_rows=10,
            valid_rows=0,
            invalid_rows=10,
            saved_rows=0,
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Priority")
        self.assertContains(response, "Area")
        self.assertContains(response, "Finding")
        self.assertContains(response, "Business Impact")
        self.assertContains(response, "Recommended Action")
        self.assertContains(response, "High")
        self.assertContains(response, "Low")
        self.assertContains(response, "Finance")
        self.assertContains(response, "Security")
        self.assertContains(response, "Imports")
        self.assertContains(response, "Top Collection Risks")
        self.assertContains(response, self.customer.name)
        self.assertContains(response, "Review customer")
        self.assertContains(response, f'{reverse("invoice-list")}?status=overdue')
        self.assertNotContains(response, f'{reverse("email-delivery-log-list")}?status=failed')
        self.assertNotContains(response, "failed delivery")
        self.assertContains(response, reverse("admin-security-report"))
        self.assertContains(response, reverse("dashboard-validation-errors"))
        self.assertNotContains(response, overdue_invoice.invoice_number)
        self.assertContains(response, "report-attention-table")

    def test_management_dashboard_surfaces_submitted_bank_transfers_for_verification(self):
        invoice = Invoice.objects.create(
            invoice_number="INV-CORE-BANK-VERIFY",
            customer=self.customer,
            status=Invoice.STATUS_OVERDUE,
            issue_date=timezone.localdate() - timedelta(days=20),
            due_date=timezone.localdate() - timedelta(days=2),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
        )
        PaymentRecord.objects.create(
            invoice=invoice,
            payment_reference="BANK-CORE-VERIFY-001",
            provider=PaymentRecord.PROVIDER_MANUAL,
            status=PaymentRecord.STATUS_PENDING,
            amount=Decimal("109.00"),
            currency="SGD",
            manual_customer_amount=Decimal("109.00"),
            manual_customer_transfer_date=timezone.localdate(),
            manual_customer_bank_reference="CORE-CUSTOMER-REF",
            manual_customer_submitted_at=timezone.now(),
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["submitted_bank_transfer_count"], 1)
        self.assertContains(response, "1 customer-submitted bank transfer(s) awaiting verification.")
        self.assertContains(response, "1 submitted bank transfer(s) need bank-account verification.")
        self.assertContains(response, reverse("payment-stripe-report"))

    def test_management_dashboard_is_reserved_for_management_roles(self):
        superadmin = User.objects.create_superuser(
            username="core_super",
            email="core_super@example.com",
            password="TempPass123!",
        )
        self.client.force_login(superadmin)
        super_response = self.client.get(reverse("dashboard"))
        self.assertEqual(super_response.status_code, 200)
        self.assertContains(super_response, "Collected This Month")
        self.client.logout()

        staff_user = User.objects.create_user(username="core_staff", password="TempPass123!")
        staff_user.role_profile.role = STAFF
        staff_user.role_profile.save(update_fields=["role", "updated_at"])
        Employee.objects.create(
            user=staff_user,
            employee_code="STF-900001",
            first_name="Core",
            last_name="Staff",
            email="core_staff@example.com",
            hire_date=timezone.localdate(),
            base_salary=Decimal("2500.00"),
        )
        self.client.force_login(staff_user)
        staff_response = self.client.get(reverse("dashboard"))
        self.assertEqual(staff_response.status_code, 302)
        self.assertEqual(staff_response.url, reverse("my-payslips"))

    def test_customer_is_redirected_to_customer_dashboard_from_management_dashboard(self):
        customer_user = User.objects.create_user(username="core_customer", password="TempPass123!")
        customer_user.role_profile.role = CUSTOMER
        customer_user.role_profile.save(update_fields=["role", "updated_at"])

        self.client.force_login(customer_user)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("customer-invoice-dashboard"))

    def test_dashboard_redirects_finance_hr_and_staff_to_role_landing_pages(self):
        finance_user = User.objects.create_user(username="core_finance", password="TempPass123!")
        finance_user.role_profile.role = FINANCE
        finance_user.role_profile.save(update_fields=["role", "updated_at"])

        hr_user = User.objects.create_user(username="core_hr", password="TempPass123!")
        hr_user.role_profile.role = HR
        hr_user.role_profile.save(update_fields=["role", "updated_at"])

        staff_user = User.objects.create_user(username="core_staff_portal", password="TempPass123!")
        staff_user.role_profile.role = STAFF
        staff_user.role_profile.save(update_fields=["role", "updated_at"])
        Employee.objects.create(
            user=staff_user,
            employee_code="STF-900002",
            first_name="Portal",
            last_name="Staff",
            email="core_staff_portal@example.com",
            hire_date=timezone.localdate(),
            base_salary=Decimal("2300.00"),
        )

        cases = (
            (finance_user, reverse("invoice-dashboard")),
            (hr_user, reverse("payroll-dashboard")),
            (staff_user, reverse("my-payslips")),
        )

        for user, expected_location in cases:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("dashboard"))

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, expected_location)
                self.client.logout()

    def test_direct_unauthorised_dashboard_access_is_blocked(self):
        finance_user = User.objects.create_user(username="direct_finance", password="TempPass123!")
        finance_user.role_profile.role = FINANCE
        finance_user.role_profile.save(update_fields=["role", "updated_at"])

        hr_user = User.objects.create_user(username="direct_hr", password="TempPass123!")
        hr_user.role_profile.role = HR
        hr_user.role_profile.save(update_fields=["role", "updated_at"])

        staff_user = User.objects.create_user(username="direct_staff", password="TempPass123!")
        staff_user.role_profile.role = STAFF
        staff_user.role_profile.save(update_fields=["role", "updated_at"])
        Employee.objects.create(
            user=staff_user,
            employee_code="STF-900003",
            first_name="Direct",
            last_name="Staff",
            email="direct_staff@example.com",
            hire_date=timezone.localdate(),
            base_salary=Decimal("2200.00"),
        )

        cases = (
            (finance_user, "payroll-dashboard"),
            (hr_user, "invoice-dashboard"),
            (staff_user, "invoice-dashboard"),
        )

        for user, blocked_route_name in cases:
            with self.subTest(user=user.username, route=blocked_route_name):
                self.client.force_login(user)
                response = self.client.get(reverse(blocked_route_name))

                self.assertEqual(response.status_code, 403)
                self.client.logout()

    def test_navigation_links_match_each_role_landing_page(self):
        superadmin_user = User.objects.create_superuser(
            username="nav_superadmin",
            email="nav_superadmin@example.com",
            password="TempPass123!",
        )
        admin_user = User.objects.create_user(username="nav_admin", password="TempPass123!")
        admin_user.role_profile.role = ADMIN
        admin_user.role_profile.save(update_fields=["role", "updated_at"])

        finance_user = User.objects.create_user(username="nav_finance", password="TempPass123!")
        finance_user.role_profile.role = FINANCE
        finance_user.role_profile.save(update_fields=["role", "updated_at"])

        hr_user = User.objects.create_user(username="nav_hr", password="TempPass123!")
        hr_user.role_profile.role = HR
        hr_user.role_profile.save(update_fields=["role", "updated_at"])

        staff_user = User.objects.create_user(
            username="nav_staff",
            email="nav_staff@example.com",
            password="TempPass123!",
        )
        staff_user.role_profile.role = STAFF
        staff_user.role_profile.save(update_fields=["role", "updated_at"])
        Employee.objects.create(
            user=staff_user,
            employee_code="STF-900004",
            first_name="Nav",
            last_name="Staff",
            email="nav_staff@example.com",
            hire_date=timezone.localdate(),
            base_salary=Decimal("2100.00"),
        )

        customer_user = User.objects.create_user(
            username="nav_customer",
            email="nav_customer@example.com",
            password="TempPass123!",
        )
        customer_user.role_profile.role = CUSTOMER
        customer_user.role_profile.save(update_fields=["role", "updated_at"])
        Customer.objects.create(name="Nav Customer", email="nav_customer@example.com")

        cases = (
            (
                superadmin_user,
                reverse("dashboard"),
                ["Management Dashboard", "Invoicing", "Reports", "Admin Console"],
                ["My Payslips", "My Invoices", "Finance Dashboard", "CEO Dashboard"],
                [],
            ),
            (
                admin_user,
                reverse("dashboard"),
                ["Management Dashboard", "Invoicing", "Reports", "Admin Console"],
                ["My Payslips", "My Invoices", "Finance Dashboard", "CEO Dashboard"],
                [],
            ),
            (
                finance_user,
                reverse("invoice-dashboard"),
                ["Invoicing", "Reports", "Support Tickets"],
                ["Management Dashboard", "CEO Dashboard", "Admin Console", "My Payslips", "Finance Dashboard"],
                [],
            ),
            (
                hr_user,
                reverse("payroll-dashboard"),
                ["Payroll Officer Dashboard", "Support Tickets"],
                ["Management Dashboard", "CEO Dashboard", "Admin Console"],
                [
                    f'href="{reverse("invoice-dashboard")}"',
                    f'href="{reverse("invoice-list")}"',
                    f'href="{reverse("invoice-create")}"',
                    f'href="{reverse("invoice-csv-upload")}"',
                    f'href="{reverse("invoice-customer-create")}"',
                    f'href="{reverse("payment-stripe-report")}"',
                ],
            ),
            (
                staff_user,
                reverse("my-payslips"),
                ["My Payslips"],
                ["Management Dashboard", "CEO Dashboard", "Admin Console", "Support Tickets"],
                [
                    f'href="{reverse("invoice-dashboard")}"',
                    f'href="{reverse("invoice-list")}"',
                    f'href="{reverse("invoice-create")}"',
                    f'href="{reverse("invoice-csv-upload")}"',
                    f'href="{reverse("invoice-customer-create")}"',
                    f'href="{reverse("payment-stripe-report")}"',
                ],
            ),
            (
                customer_user,
                reverse("customer-invoice-dashboard"),
                ["My Invoices"],
                ["Management Dashboard", "CEO Dashboard", "Admin Console", "Support Tickets"],
                [
                    f'href="{reverse("invoice-dashboard")}"',
                    f'href="{reverse("invoice-list")}"',
                    f'href="{reverse("invoice-create")}"',
                    f'href="{reverse("invoice-csv-upload")}"',
                    f'href="{reverse("invoice-customer-create")}"',
                    f'href="{reverse("payment-stripe-report")}"',
                ],
            ),
        )

        for user, route, expected_texts, excluded_texts, excluded_html_fragments in cases:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(route)

                self.assertEqual(response.status_code, 200)
                for text in expected_texts:
                    self.assertContains(response, text)
                for text in excluded_texts:
                    self.assertNotContains(response, text)
                for fragment in excluded_html_fragments:
                    self.assertNotContains(response, fragment, html=False)
                self.client.logout()

    def test_invoicing_dropdown_links_use_existing_routes_and_old_top_level_invoice_link_is_removed(self):
        finance_user = User.objects.create_user(username="nav_invoice_finance", password="TempPass123!")
        finance_user.role_profile.role = FINANCE
        finance_user.role_profile.save(update_fields=["role", "updated_at"])

        self.client.force_login(finance_user)
        response = self.client.get(reverse("invoice-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invoicing")
        self.assertContains(response, f'href="{reverse("invoice-dashboard")}"', html=False)
        self.assertContains(response, f'href="{reverse("invoice-list")}"', html=False)
        self.assertContains(response, f'href="{reverse("invoice-create")}"', html=False)
        self.assertContains(response, f'href="{reverse("invoice-csv-upload")}"', html=False)
        self.assertContains(response, f'href="{reverse("invoice-customer-create")}"', html=False)
        self.assertContains(response, f'href="{reverse("payment-stripe-report")}"', html=False)
        self.assertNotContains(
            response,
            f'<a class="portal-nav-link" href="{reverse("invoice-dashboard")}">',
            html=False,
        )
        self.assertNotContains(response, "Finance Dashboard")

    def test_invoicing_dropdown_is_active_on_invoice_and_customer_management_pages(self):
        finance_user = User.objects.create_user(username="nav_invoice_active", password="TempPass123!")
        finance_user.role_profile.role = FINANCE
        finance_user.role_profile.save(update_fields=["role", "updated_at"])
        customer = Customer.objects.create(name="Active Nav Customer", email="active-nav@example.com")
        invoice = Invoice.objects.create(
            invoice_number="INV-NAV-1001",
            customer=customer,
            status=Invoice.STATUS_DRAFT,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate(),
            currency="SGD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("9.00"),
            total_amount=Decimal("109.00"),
        )

        self.client.force_login(finance_user)
        invoice_response = self.client.get(reverse("invoice-detail", args=[invoice.pk]))
        customer_response = self.client.get(reverse("invoice-customer-create"))

        self.assertEqual(invoice_response.status_code, 200)
        self.assertContains(invoice_response, 'portal-nav-link dropdown-toggle is-active', html=False)
        self.assertEqual(customer_response.status_code, 200)
        self.assertContains(customer_response, 'portal-nav-link dropdown-toggle is-active', html=False)

    def test_test_settings_keep_email_backend_in_memory(self):
        self.assertEqual(settings.EMAIL_BACKEND, "django.core.mail.backends.locmem.EmailBackend")
        mail.send_mail(
            subject="Dashboard Test",
            message="This should stay in memory.",
            from_email="from@example.com",
            recipient_list=["to@example.com"],
        )
        self.assertEqual(len(mail.outbox), 1)
