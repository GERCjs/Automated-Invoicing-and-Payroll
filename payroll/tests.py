from io import BytesIO
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.db import connection
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from openpyxl import Workbook

from accounts.models import UserRole
from accounts.roles import ADMIN, CUSTOMER, HR, STAFF, SUPERADMIN
from core.models import AuditLog
from notifications.models import EmailDeliveryLog
from payroll.models import Employee, PayrollRecord


class PayrollTestEnvironmentTests(TestCase):
    def test_test_database_uses_migrated_payroll_tables(self):
        user_model = get_user_model()
        hr_user = user_model.objects.create_user(username="payroll_fixture_hr", password="TestOnlyPass123!")
        UserRole.objects.filter(user=hr_user).update(role=HR)

        table_names = set(connection.introspection.table_names())
        self.assertIn("employee", table_names)
        self.assertIn("payslip_record", table_names)

        employee = Employee.objects.create(
            employee_code="STF-999999",
            first_name="Fixture",
            last_name="Tester",
            email="fixture.payroll@example.com",
            hire_date=date(2026, 1, 1),
            base_salary=1000,
            created_by=hr_user,
        )
        payroll_record = PayrollRecord.objects.create(
            employee_name="Fixture Tester",
            employee_id=employee.employee_code,
            basic_salary=1000,
            allowances=100,
            deductions=50,
            cpf_contribution=200,
            net_salary=850,
            payment_date=date(2026, 6, 1),
            created_by=hr_user,
        )

        self.assertEqual(Employee.objects.get(pk=employee.pk).employee_code, "STF-999999")
        self.assertEqual(PayrollRecord.objects.get(pk=payroll_record.pk).employee_id, "STF-999999")


class PayrollUploadPreviewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="hr1", password="pass12345")
        UserRole.objects.filter(user=self.user).update(role=HR)
        self.client.login(username="hr1", password="pass12345")
        Employee.objects.create(
            employee_code="STF-000001",
            first_name="Alex",
            last_name="Tan",
            email="alex@example.com",
            hire_date=date(2025, 1, 1),
            base_salary=3000,
        )
        Employee.objects.create(
            employee_code="STF-000002",
            first_name="Jamie",
            last_name="Lim",
            email="jamie@example.com",
            hire_date=date(2025, 1, 1),
            base_salary=3200,
        )

    def _build_upload_file(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(
            [
                "employee_code",
                "employee_name",
                "employee_birthofdate",
                "working_days",
                "no_pay_leave_days",
                "basic_salary",
                "physical_products_commission",
                "credit_commission",
                "services_commission",
                "loan_deduction",
                "other_deductions",
                "notes",
            ]
        )
        sheet.append(["STF-000001", "Alex Tan", "01-01-1990", 27, 0, 3000, 15.9, 325, 700, 139.45, 0, "Test"])
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return SimpleUploadedFile(
            "payroll.xlsx",
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _build_upload_file_multiple(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(
            [
                "employee_code",
                "employee_name",
                "employee_birthofdate",
                "working_days",
                "no_pay_leave_days",
                "basic_salary",
                "physical_products_commission",
                "credit_commission",
                "services_commission",
                "loan_deduction",
                "other_deductions",
                "notes",
            ]
        )
        sheet.append(["STF-000001", "Alex Tan", "01-01-1990", 27, 0, 3000, 15.9, 325, 700, 139.45, 0, "Row 1"])
        sheet.append(["STF-000002", "Jamie Lim", "01-01-1995", 26, 0, 3200, 50, 120, 450, 100, 20, "Row 2"])
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return SimpleUploadedFile(
            "payroll_multi.xlsx",
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _build_upload_file_with_invalid_numeric(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(
            [
                "employee_code",
                "employee_name",
                "employee_birthofdate",
                "working_days",
                "no_pay_leave_days",
                "basic_salary",
                "physical_products_commission",
                "credit_commission",
                "services_commission",
                "loan_deduction",
                "other_deductions",
                "notes",
            ]
        )
        sheet.append(["STF-000001", "Alex Tan", "01-01-1990", 27, 0, "abc", 15.9, 325, 700, 139.45, 0, "Bad salary"])
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return SimpleUploadedFile(
            "payroll_invalid_numeric.xlsx",
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_payroll_preview_displays_cpf(self):
        response = self.client.post(
            reverse("payroll-upload-preview"),
            {"payroll_file": self._build_upload_file(), "payment_date": "2026-05-01"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload Preview")
        self.assertContains(response, "CPF employee share (20%)")
        self.assertContains(response, "808.18")
        self.assertContains(response, "Total rows:")
        self.assertContains(response, "Valid rows:")
        self.assertContains(response, "Invalid rows:")

    def test_confirm_save_creates_payroll_record(self):
        preview_response = self.client.post(
            reverse("payroll-upload-preview"),
            {"payroll_file": self._build_upload_file(), "payment_date": "2026-05-24"},
        )
        self.assertEqual(preview_response.status_code, 200)

        save_response = self.client.post(reverse("payroll-upload-confirm-save"))
        self.assertEqual(save_response.status_code, 302)
        self.assertEqual(PayrollRecord.objects.count(), 1)
        record = PayrollRecord.objects.first()
        self.assertEqual(record.employee_id, "STF-000001")
        self.assertEqual(record.payment_date, date(2026, 5, 24))

    def test_confirm_save_creates_multiple_payroll_records(self):
        preview_response = self.client.post(
            reverse("payroll-upload-preview"),
            {"payroll_file": self._build_upload_file_multiple(), "payment_date": "2026-05-24"},
        )
        self.assertEqual(preview_response.status_code, 200)

        save_response = self.client.post(reverse("payroll-upload-confirm-save"))
        self.assertEqual(save_response.status_code, 302)
        self.assertEqual(PayrollRecord.objects.count(), 2)

    def test_confirm_save_skips_duplicate_employee_and_payment_date(self):
        PayrollRecord.objects.create(
            employee_name="Alex Tan",
            employee_id="STF-000001",
            basic_salary=3000,
            allowances=1040.90,
            deductions=139.45,
            cpf_contribution=808.18,
            net_salary=3093.27,
            payment_date=date(2026, 5, 24),
        )
        preview_response = self.client.post(
            reverse("payroll-upload-preview"),
            {"payroll_file": self._build_upload_file(), "payment_date": "2026-05-24"},
        )
        self.assertEqual(preview_response.status_code, 200)

        save_response = self.client.post(reverse("payroll-upload-confirm-save"), follow=True)
        self.assertEqual(save_response.status_code, 200)
        self.assertEqual(PayrollRecord.objects.count(), 1)
        self.assertContains(save_response, "duplicate record(s) were skipped")

    def test_upload_preview_invalid_numeric_cell_is_row_level_error(self):
        response = self.client.post(
            reverse("payroll-upload-preview"),
            {"payroll_file": self._build_upload_file_with_invalid_numeric(), "payment_date": "2026-05-24"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid Rows")
        self.assertContains(response, "Basic salary must be a valid number.")
        self.assertContains(response, "2")

    def test_template_download(self):
        response = self.client.get(reverse("payroll-template-download"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment; filename=", response["Content-Disposition"])

    def test_payroll_list_supports_month_filter(self):
        PayrollRecord.objects.create(
            employee_name="Alex Tan",
            employee_id="EMP001",
            basic_salary=3000,
            allowances=100,
            deductions=50,
            cpf_contribution=600,
            net_salary=2450,
            payment_date=date(2026, 5, 24),
        )
        PayrollRecord.objects.create(
            employee_name="Jamie Lim",
            employee_id="EMP002",
            basic_salary=3200,
            allowances=100,
            deductions=50,
            cpf_contribution=640,
            net_salary=2610,
            payment_date=date(2026, 4, 24),
        )
        response = self.client.get(reverse("payroll-list"), {"month": "2026-05"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EMP001")
        self.assertNotContains(response, "EMP002")


class PayslipPdfAccessTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="staff1", password="pass12345", email="staff1@example.com"
        )
        UserRole.objects.filter(user=self.staff_user).update(role=STAFF)
        self.other_staff_user = user_model.objects.create_user(
            username="staff2", password="pass12345", email="staff2@example.com"
        )
        UserRole.objects.filter(user=self.other_staff_user).update(role=STAFF)
        self.hr_user = user_model.objects.create_user(username="hr2", password="pass12345")
        UserRole.objects.filter(user=self.hr_user).update(role=HR)

        self.staff_employee = Employee.objects.create(
            user=self.staff_user,
            employee_code="EMP100",
            first_name="Staff",
            last_name="One",
            email="staff1@example.com",
            hire_date=date(2024, 1, 1),
            base_salary=2000,
        )
        self.other_employee = Employee.objects.create(
            user=self.other_staff_user,
            employee_code="EMP200",
            first_name="Staff",
            last_name="Two",
            email="staff2@example.com",
            hire_date=date(2024, 1, 1),
            base_salary=2200,
        )

        self.staff_record = PayrollRecord.objects.create(
            employee_name="Staff One",
            employee_id=self.staff_employee.employee_code,
            basic_salary=2000,
            allowances=100,
            deductions=50,
            cpf_contribution=400,
            net_salary=1650,
            payment_date=date(2026, 5, 24),
        )
        self.other_record = PayrollRecord.objects.create(
            employee_name="Staff Two",
            employee_id=self.other_employee.employee_code,
            basic_salary=2200,
            allowances=100,
            deductions=50,
            cpf_contribution=440,
            net_salary=1810,
            payment_date=date(2026, 5, 24),
        )

    def test_hr_can_download_any_payslip_pdf(self):
        self.client.login(username="hr2", password="pass12345")
        response = self.client.get(reverse("payslip-pdf-download", args=[self.other_record.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_staff_can_download_own_payslip_pdf_only(self):
        self.client.login(username="staff1", password="pass12345")
        own_response = self.client.get(reverse("payslip-pdf-download", args=[self.staff_record.pk]))
        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(own_response["Content-Type"], "application/pdf")

        denied_response = self.client.get(reverse("payslip-pdf-download", args=[self.other_record.pk]))
        self.assertEqual(denied_response.status_code, 403)


class PayrollReportPlacementAndAccessTests(TestCase):
    def _make_user(self, username, role):
        user = get_user_model().objects.create_user(username=username, password="pass12345")
        UserRole.objects.filter(user=user).update(role=role)
        return user

    def test_main_navbar_does_not_show_payroll_report_link(self):
        user = self._make_user("hr_nav_user", HR)
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            f'<a class="nav-link" href="{reverse("payroll-report")}">Payroll Report</a>',
            html=True,
        )

    def test_payroll_dashboard_shows_payroll_report_link_for_hr(self):
        user = self._make_user("hr_dashboard_user", HR)
        self.client.force_login(user)
        response = self.client.get(reverse("payroll-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("payroll-report"))

    def test_staff_and_customer_cannot_access_overall_payroll_report(self):
        staff_user = self._make_user("staff_no_payroll_report", STAFF)
        self.client.force_login(staff_user)
        staff_response = self.client.get(reverse("payroll-report"))
        self.assertEqual(staff_response.status_code, 403)
        self.client.logout()

        customer_user = self._make_user("customer_no_payroll_report", CUSTOMER)
        self.client.force_login(customer_user)
        customer_response = self.client.get(reverse("payroll-report"))
        self.assertEqual(customer_response.status_code, 403)

    def test_admin_and_superadmin_can_access_payroll_report(self):
        admin_user = self._make_user("admin_payroll_report", ADMIN)
        self.client.force_login(admin_user)
        admin_response = self.client.get(reverse("payroll-report"))
        self.assertEqual(admin_response.status_code, 200)
        self.client.logout()

        superadmin_user = self._make_user("super_payroll_report", SUPERADMIN)
        self.client.force_login(superadmin_user)
        super_response = self.client.get(reverse("payroll-report"))
        self.assertEqual(super_response.status_code, 200)


class MyPayslipsViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="staff_list_1", password="pass12345", email="stafflist1@example.com"
        )
        UserRole.objects.filter(user=self.staff_user).update(role=STAFF)
        self.other_staff_user = user_model.objects.create_user(
            username="staff_list_2", password="pass12345", email="stafflist2@example.com"
        )
        UserRole.objects.filter(user=self.other_staff_user).update(role=STAFF)

        self.staff_employee = Employee.objects.create(
            user=self.staff_user,
            employee_code="EMP300",
            first_name="List",
            last_name="One",
            email="stafflist1@example.com",
            hire_date=date(2024, 1, 1),
            base_salary=2100,
        )
        self.other_employee = Employee.objects.create(
            user=self.other_staff_user,
            employee_code="EMP400",
            first_name="List",
            last_name="Two",
            email="stafflist2@example.com",
            hire_date=date(2024, 1, 1),
            base_salary=2300,
        )

        self.own_record = PayrollRecord.objects.create(
            employee_name="List One",
            employee_id=self.staff_employee.employee_code,
            basic_salary=2100,
            allowances=100,
            deductions=50,
            cpf_contribution=420,
            net_salary=1730,
            payment_date=date(2026, 5, 24),
        )
        self.other_record = PayrollRecord.objects.create(
            employee_name="List Two",
            employee_id=self.other_employee.employee_code,
            basic_salary=2300,
            allowances=100,
            deductions=50,
            cpf_contribution=460,
            net_salary=1890,
            payment_date=date(2026, 5, 24),
        )

    def test_staff_my_payslips_shows_only_own_records_without_page_view_log(self):
        self.client.login(username="staff_list_1", password="pass12345")
        response = self.client.get(reverse("my-payslips"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.own_record.employee_id)
        self.assertNotContains(response, self.other_record.employee_id)
        self.assertFalse(
            AuditLog.objects.filter(
                user=self.staff_user,
                action="payroll.my_payslips.viewed",
            ).exists()
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
)
class PayslipEmailSendTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.hr_user = user_model.objects.create_user(username="hr_email_test", password="pass12345")
        UserRole.objects.filter(user=self.hr_user).update(role=HR)

        self.employee = Employee.objects.create(
            employee_code="EMP500",
            first_name="Mail",
            last_name="Tester",
            email="mail.tester@example.com",
            hire_date=date(2024, 1, 1),
            base_salary=2400,
        )
        self.record = PayrollRecord.objects.create(
            employee_name="Mail Tester",
            employee_id=self.employee.employee_code,
            basic_salary=2400,
            allowances=100,
            deductions=50,
            cpf_contribution=480,
            net_salary=1970,
            payment_date=date(2026, 5, 24),
        )

    def test_payslip_email_includes_pdf_attachment(self):
        self.client.login(username="hr_email_test", password="pass12345")
        response = self.client.post(reverse("payslip-email-send", args=[self.record.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        sent_message = mail.outbox[0]
        self.assertTrue(sent_message.attachments)
        self.assertTrue(any(attachment[2] == "application/pdf" for attachment in sent_message.attachments))
        self.assertTrue(
            EmailDeliveryLog.objects.filter(
                related_object_type="payroll_record",
                related_object_id=str(self.record.id),
                status=EmailDeliveryLog.STATUS_SENT,
            ).exists()
        )
