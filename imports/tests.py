from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import UserRole
from accounts.roles import FINANCE

from .models import ImportJob, ImportRowError


class ImportTrackingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.finance_user = user_model.objects.create_user(
            username="import_fixture_finance",
            password="TestOnlyPass123!",
        )
        UserRole.objects.filter(user=self.finance_user).update(role=FINANCE)

    def test_import_job_tracks_row_errors_and_cascades(self):
        import_job = ImportJob.objects.create(
            module=ImportJob.MODULE_INVOICING,
            source_file_name="test-only-invoice-upload.csv",
            status=ImportJob.STATUS_COMPLETED_WITH_ERRORS,
            total_rows=2,
            valid_rows=1,
            invalid_rows=1,
            saved_rows=1,
            initiated_by=self.finance_user,
        )
        ImportRowError.objects.create(
            import_job=import_job,
            row_number=2,
            field_name="customer_email",
            error_message="Customer email is required.",
            raw_data={"invoice_number": "TEST-ONLY-001"},
        )

        self.assertEqual(import_job.row_errors.count(), 1)
        self.assertEqual(str(import_job), "invoicing - test-only-invoice-upload.csv")

        import_job.delete()
        self.assertEqual(ImportRowError.objects.count(), 0)

    def test_import_job_rejects_saved_rows_greater_than_valid_rows(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ImportJob.objects.create(
                    module=ImportJob.MODULE_PAYROLL,
                    source_file_name="test-only-payroll-upload.xlsx",
                    total_rows=1,
                    valid_rows=0,
                    invalid_rows=1,
                    saved_rows=1,
                    initiated_by=self.finance_user,
                )
