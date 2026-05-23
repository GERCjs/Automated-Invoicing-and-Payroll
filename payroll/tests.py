from io import BytesIO
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from accounts.models import UserRole
from accounts.roles import HR, STAFF
from payroll.models import Employee, PayrollRecord


class PayrollUploadPreviewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="hr1", password="pass12345")
        UserRole.objects.filter(user=self.user).update(role=HR)
        self.client.login(username="hr1", password="pass12345")
        Employee.objects.create(
            employee_code="EMP001",
            first_name="Alex",
            last_name="Tan",
            email="alex@example.com",
            hire_date=date(2025, 1, 1),
            base_salary=3000,
        )
        Employee.objects.create(
            employee_code="EMP002",
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
                "employee_age",
                "primary_work_location",
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
        sheet.append(["EMP001", "Alex Tan", 34, "Raffles", 27, 0, 3000, 15.9, 325, 700, 139.45, 0, "Test"])
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
                "employee_age",
                "primary_work_location",
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
        sheet.append(["EMP001", "Alex Tan", 34, "Raffles", 27, 0, 3000, 15.9, 325, 700, 139.45, 0, "Row 1"])
        sheet.append(["EMP002", "Jamie Lim", 29, "Somerset", 26, 0, 3200, 50, 120, 450, 100, 20, "Row 2"])
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return SimpleUploadedFile(
            "payroll_multi.xlsx",
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
        self.assertEqual(record.employee_id, "EMP001")
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
