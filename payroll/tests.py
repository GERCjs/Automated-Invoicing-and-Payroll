from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from accounts.models import UserRole
from accounts.roles import HR


class PayrollUploadPreviewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="hr1", password="pass12345")
        UserRole.objects.filter(user=self.user).update(role=HR)
        self.client.login(username="hr1", password="pass12345")

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

    def test_payroll_preview_displays_cpf(self):
        response = self.client.post(
            reverse("payroll-upload-preview"),
            {"payroll_file": self._build_upload_file()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payslip Preview")
        self.assertContains(response, "CPF employee share (20%)")
        self.assertContains(response, "808.18")

    def test_template_download(self):
        response = self.client.get(reverse("payroll-template-download"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment; filename=", response["Content-Disposition"])
