from io import BytesIO
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from accounts.models import UserRole
from accounts.roles import HR
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
