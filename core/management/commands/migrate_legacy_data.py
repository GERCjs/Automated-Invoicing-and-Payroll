import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.core.validators import validate_email
from django.db import connection, transaction

from accounts.models import UserRole
from accounts.roles import ROLE_CHOICES, STAFF
from core.models import AuditLog
from imports.models import ImportJob
from invoicing.models import Customer, Invoice, InvoiceItem
from notifications.models import EmailDeliveryLog
from payments.models import PaymentRecord
from payroll.models import Employee, PayrollBatch, PayrollEntry, PayslipRecord


ROLE_SET = {role for role, _ in ROLE_CHOICES}

PAYMENT_STATUS_MAP = {
    "paid": PaymentRecord.STATUS_SUCCEEDED,
    "succeeded": PaymentRecord.STATUS_SUCCEEDED,
    "success": PaymentRecord.STATUS_SUCCEEDED,
    "pending": PaymentRecord.STATUS_PENDING,
    "failed": PaymentRecord.STATUS_FAILED,
    "refunded": PaymentRecord.STATUS_REFUNDED,
    "cancelled": PaymentRecord.STATUS_CANCELLED,
    "canceled": PaymentRecord.STATUS_CANCELLED,
}

INVOICE_STATUS_MAP = {
    "draft": Invoice.STATUS_DRAFT,
    "sent": Invoice.STATUS_SENT,
    "viewed": Invoice.STATUS_VIEWED,
    "paid": Invoice.STATUS_PAID,
    "overdue": Invoice.STATUS_OVERDUE,
}

PAYROLL_ENTRY_STATUS_MAP = {
    "generated": PayrollEntry.STATUS_APPROVED,
    "issued": PayrollEntry.STATUS_APPROVED,
    "sent": PayrollEntry.STATUS_APPROVED,
    "approved": PayrollEntry.STATUS_APPROVED,
    "rejected": PayrollEntry.STATUS_REJECTED,
    "failed": PayrollEntry.STATUS_REJECTED,
    "pending": PayrollEntry.STATUS_PENDING,
}

PAYSLIP_STATUS_MAP = {
    "generated": PayslipRecord.STATUS_ISSUED,
    "issued": PayslipRecord.STATUS_ISSUED,
    "sent": PayslipRecord.STATUS_SENT,
    "draft": PayslipRecord.STATUS_DRAFT,
}

NOTIFICATION_STATUS_MAP = {
    "sent": EmailDeliveryLog.STATUS_SENT,
    "failed": EmailDeliveryLog.STATUS_FAILED,
    "pending": EmailDeliveryLog.STATUS_PENDING,
}

IMPORT_STATUS_MAP = {
    "pending": ImportJob.STATUS_PENDING,
    "processing": ImportJob.STATUS_PROCESSING,
    "completed": ImportJob.STATUS_COMPLETED,
    "completed_with_errors": ImportJob.STATUS_COMPLETED_WITH_ERRORS,
    "failed": ImportJob.STATUS_FAILED,
}

BATCH_STATUS_TERMINAL = {
    ImportJob.STATUS_COMPLETED,
    ImportJob.STATUS_COMPLETED_WITH_ERRORS,
    ImportJob.STATUS_FAILED,
}


def normalize_text(value, default=""):
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def normalize_decimal(value, default=Decimal("0.00")):
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def normalize_status(value, mapping, fallback):
    key = normalize_text(value).lower()
    return mapping.get(key, fallback)


def safe_email(value, fallback):
    candidate = normalize_text(value)
    if candidate:
        try:
            validate_email(candidate)
            return candidate.lower()
        except ValidationError:
            pass
    return fallback


def split_name(full_name):
    parts = normalize_text(full_name).split()
    if not parts:
        return ("Legacy", "User")
    if len(parts) == 1:
        return (parts[0], "User")
    return (parts[0], " ".join(parts[1:]))


class Command(BaseCommand):
    help = "Migrate data from legacy tables (user/invoice/payment/payroll/etc.) into Django tables."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run migration in a transaction and roll it back at the end.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        self.summary = defaultdict(lambda: {"created": 0, "updated": 0, "skipped": 0, "errors": 0})
        self.error_samples = defaultdict(list)
        self.user_map = {}
        self.invoice_map = {}
        self.employee_map = {}
        self.existing_tables = set(connection.introspection.table_names())
        self.entities = [
            "users",
            "invoices",
            "payments",
            "payroll_entries",
            "payslips",
            "notifications",
            "imports",
            "audit_logs",
            "sessions",
        ]

        if dry_run:
            self.stdout.write(self.style.WARNING("Running in DRY RUN mode. No data will be committed."))

        try:
            with transaction.atomic():
                self.migrate_users()
                self.migrate_customers_and_invoices()
                self.migrate_payments()
                self.migrate_payroll()
                self.migrate_notifications()
                self.migrate_import_jobs()
                self.migrate_audit_logs()
                self.migrate_sessions()
                if dry_run:
                    raise RuntimeError("__dry_run_rollback__")
        except RuntimeError as exc:
            if str(exc) != "__dry_run_rollback__":
                raise

        self.print_summary()

    def fetch_rows(self, table_name):
        if table_name not in self.existing_tables:
            self.stdout.write(self.style.WARNING(f"Skipping `{table_name}`: table does not exist."))
            return []
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM `{table_name}`")
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def mark(self, entity, outcome):
        self.summary[entity][outcome] += 1

    def add_error_sample(self, entity, details):
        if len(self.error_samples[entity]) < 3:
            self.error_samples[entity].append(details)

    def parse_legacy_json_list(self, raw_value):
        if raw_value in (None, ""):
            return []
        if isinstance(raw_value, list):
            return raw_value
        if isinstance(raw_value, str):
            try:
                parsed = json.loads(raw_value)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return []

    def migrate_users(self):
        User = get_user_model()
        users = self.fetch_rows("user")
        if not users:
            return

        for row in users:
            try:
                legacy_id = row.get("userId")
                if legacy_id is None:
                    self.mark("users", "skipped")
                    continue

                username = normalize_text(row.get("userName"), f"legacy_user_{legacy_id}")
                email = safe_email(row.get("userEmail"), f"legacy_user_{legacy_id}@legacy.local")
                role = normalize_text(row.get("userRole"), STAFF).lower()
                if role not in ROLE_SET:
                    role = STAFF

                defaults = {
                    "username": username,
                    "email": email,
                    "is_active": bool(row.get("isActive", True)),
                    "is_staff": bool(row.get("isStaff", False)),
                    "is_superuser": bool(row.get("isSuperuser", False)),
                }

                user, created = User.objects.update_or_create(id=legacy_id, defaults=defaults)
                user.set_unusable_password()
                user.save(update_fields=["password"])

                UserRole.objects.update_or_create(user=user, defaults={"role": role})
                self.user_map[legacy_id] = user

                for group_name in self.parse_legacy_json_list(row.get("groups")):
                    if not isinstance(group_name, str):
                        continue
                    group = Group.objects.filter(name=group_name.strip()).first()
                    if group:
                        user.groups.add(group)

                for perm_code in self.parse_legacy_json_list(row.get("permissions")):
                    if not isinstance(perm_code, str):
                        continue
                    permission = Permission.objects.filter(codename=perm_code.strip()).first()
                    if permission:
                        user.user_permissions.add(permission)

                self.mark("users", "created" if created else "updated")
            except Exception as exc:
                self.mark("users", "errors")
                self.add_error_sample("users", f"userId={row.get('userId')}: {exc}")

    def get_or_create_customer(self, row):
        invoice_id = row.get("invoiceId", "x")
        fallback_email = f"legacy_invoice_{invoice_id}@legacy.local"
        email = safe_email(row.get("customerEmail"), fallback_email)
        name = normalize_text(row.get("customerName"), f"Legacy Customer {invoice_id}")
        user = self.user_map.get(row.get("customerId"))
        customer_defaults = {
            "name": name,
            "status": Customer.STATUS_ACTIVE,
            "created_by": user,
        }
        customer, _ = Customer.objects.update_or_create(email=email, defaults=customer_defaults)
        return customer

    def ensure_legacy_invoice_item(self, invoice):
        if invoice.items.exists():
            return
        subtotal = invoice.subtotal or Decimal("0.00")
        tax_amount = invoice.tax_amount or Decimal("0.00")
        total_amount = invoice.total_amount or Decimal("0.00")
        tax_rate = Decimal("0.00")
        if subtotal > 0 and tax_amount > 0:
            tax_rate = (tax_amount / subtotal) * Decimal("100")
        InvoiceItem.objects.create(
            invoice=invoice,
            description="Migrated legacy invoice total",
            quantity=Decimal("1.00"),
            unit_price=subtotal,
            tax_rate=tax_rate.quantize(Decimal("0.01")),
            line_total=total_amount,
        )

    def migrate_customers_and_invoices(self):
        invoices = self.fetch_rows("invoice")
        if not invoices:
            return

        for row in invoices:
            try:
                invoice_number = normalize_text(row.get("invoiceNumber"))
                if not invoice_number:
                    self.mark("invoices", "skipped")
                    continue
                customer = self.get_or_create_customer(row)
                status = normalize_status(row.get("invoiceStatus"), INVOICE_STATUS_MAP, Invoice.STATUS_DRAFT)
                user = self.user_map.get(row.get("customerId"))
                defaults = {
                    "customer": customer,
                    "status": status,
                    "issue_date": row.get("issueDate"),
                    "due_date": row.get("dueDate"),
                    "currency": "SGD",
                    "subtotal": normalize_decimal(row.get("subtotal")),
                    "tax_amount": normalize_decimal(row.get("taxAmount")),
                    "total_amount": normalize_decimal(row.get("totalAmount")),
                    "notes": "Migrated from legacy invoice table.",
                    "created_by": user,
                }
                invoice, created = Invoice.objects.update_or_create(
                    invoice_number=invoice_number,
                    defaults=defaults,
                )
                self.ensure_legacy_invoice_item(invoice)
                self.invoice_map[row.get("invoiceId")] = invoice
                self.mark("invoices", "created" if created else "updated")
            except Exception as exc:
                self.mark("invoices", "errors")
                self.add_error_sample("invoices", f"invoiceId={row.get('invoiceId')}: {exc}")

    def migrate_payments(self):
        payments = self.fetch_rows("payment")
        if not payments:
            return

        for row in payments:
            try:
                invoice = self.invoice_map.get(row.get("invoiceId"))
                if invoice is None:
                    invoice_number = normalize_text(row.get("invoiceNumber"))
                    invoice = Invoice.objects.filter(invoice_number=invoice_number).first()
                if invoice is None:
                    self.mark("payments", "skipped")
                    continue

                method = normalize_text(row.get("method")).lower()
                provider = (
                    PaymentRecord.PROVIDER_STRIPE
                    if "stripe" in method
                    else PaymentRecord.PROVIDER_MANUAL
                )
                status = normalize_status(row.get("status"), PAYMENT_STATUS_MAP, PaymentRecord.STATUS_PENDING)
                reference = normalize_text(row.get("reference"))
                if not reference:
                    reference = f"legacy-payment-{row.get('paymentId')}"

                defaults = {
                    "invoice": invoice,
                    "provider": provider,
                    "status": status,
                    "amount": normalize_decimal(row.get("amount")),
                    "currency": "SGD",
                    "paid_at": row.get("paidAt"),
                    "external_transaction_id": normalize_text(row.get("reference")),
                }
                _, created = PaymentRecord.objects.update_or_create(
                    payment_reference=reference,
                    defaults=defaults,
                )
                self.mark("payments", "created" if created else "updated")
            except Exception as exc:
                self.mark("payments", "errors")
                self.add_error_sample("payments", f"paymentId={row.get('paymentId')}: {exc}")

    def parse_or_default_payout_date(self, row):
        return row.get("payoutDate") or row.get("periodEnd") or row.get("periodStart") or date.today()

    def get_or_create_employee(self, row):
        legacy_emp_id = row.get("employeeId")
        code = normalize_text(row.get("employeeCode"), f"LEGACY-{row.get('payrollEntryId')}")
        name = normalize_text(row.get("employeeName"), "Legacy Employee")
        first_name, last_name = split_name(name)
        email = safe_email(row.get("employeeEmail"), f"legacy_employee_{code.lower()}@legacy.local")
        user = self.user_map.get(legacy_emp_id)

        defaults = {
            "first_name": first_name[:150],
            "last_name": last_name[:150],
            "email": email,
            "department": normalize_text(row.get("department")),
            "position": "",
            "hire_date": row.get("periodStart") or date.today(),
            "base_salary": normalize_decimal(row.get("grossPay")),
            "status": Employee.STATUS_ACTIVE,
            "user": user,
        }
        employee, _ = Employee.objects.update_or_create(employee_code=code, defaults=defaults)
        self.employee_map[(legacy_emp_id, code)] = employee
        return employee

    def migrate_payroll(self):
        payroll_rows = self.fetch_rows("payroll")
        if not payroll_rows:
            return

        for row in payroll_rows:
            try:
                batch_ref = normalize_text(row.get("batchReference"), f"LEGACY-BATCH-{row.get('payrollEntryId')}")
                period_start = row.get("periodStart")
                period_end = row.get("periodEnd") or period_start
                payout_date = self.parse_or_default_payout_date(row)
                batch_defaults = {
                    "period_start": period_start,
                    "period_end": period_end,
                    "payout_date": payout_date,
                    "status": PayrollBatch.STATUS_PROCESSED,
                    "notes": "Migrated from legacy payroll table.",
                }
                batch, _ = PayrollBatch.objects.update_or_create(batch_reference=batch_ref, defaults=batch_defaults)
                employee = self.get_or_create_employee(row)

                entry_status = normalize_status(
                    row.get("payslipStatus"),
                    PAYROLL_ENTRY_STATUS_MAP,
                    PayrollEntry.STATUS_PENDING,
                )
                entry_defaults = {
                    "gross_pay": normalize_decimal(row.get("grossPay")),
                    "allowances": normalize_decimal(row.get("allowances")),
                    "deductions": normalize_decimal(row.get("deductions")),
                    "tax_amount": normalize_decimal(row.get("taxAmount")),
                    "net_pay": normalize_decimal(row.get("netPay")),
                    "status": entry_status,
                }
                entry, created = PayrollEntry.objects.update_or_create(
                    batch=batch,
                    employee=employee,
                    defaults=entry_defaults,
                )
                self.mark("payroll_entries", "created" if created else "updated")

                payslip_number = normalize_text(row.get("payslipNumber"))
                if payslip_number:
                    payslip_status = normalize_status(
                        row.get("payslipStatus"),
                        PAYSLIP_STATUS_MAP,
                        PayslipRecord.STATUS_DRAFT,
                    )
                    payslip_defaults = {
                        "status": payslip_status,
                        "issued_at": row.get("paidAt") if "paidAt" in row else None,
                        "file_path": "",
                    }
                    payslip, payslip_created = PayslipRecord.objects.update_or_create(
                        payroll_entry=entry,
                        defaults={**payslip_defaults, "payslip_number": payslip_number},
                    )
                    if payslip.payslip_number != payslip_number:
                        payslip.payslip_number = payslip_number
                        payslip.save(update_fields=["payslip_number"])
                    self.mark("payslips", "created" if payslip_created else "updated")
            except Exception as exc:
                self.mark("payroll_entries", "errors")
                self.add_error_sample("payroll_entries", f"payrollEntryId={row.get('payrollEntryId')}: {exc}")

    def migrate_notifications(self):
        notifications = self.fetch_rows("notification")
        if not notifications:
            return

        for row in notifications:
            try:
                legacy_id = row.get("notificationId")
                recipient = safe_email(
                    row.get("recipientEmail"),
                    f"legacy_notification_{legacy_id}@legacy.local",
                )
                status = normalize_status(
                    row.get("status"),
                    NOTIFICATION_STATUS_MAP,
                    EmailDeliveryLog.STATUS_PENDING,
                )
                defaults = {
                    "recipient_email": recipient,
                    "subject": "[Migrated] Legacy notification",
                    "template_key": "legacy",
                    "status": status,
                    "related_object_type": normalize_text(row.get("relatedObjectType")),
                    "related_object_id": normalize_text(row.get("relatedObjectId")),
                    "error_message": normalize_text(row.get("errorMessage")),
                    "metadata": {"source": "legacy.notification", "legacy_id": legacy_id},
                    "attempted_at": row.get("attemptedAt"),
                    "sent_at": row.get("sentAt"),
                }
                _, created = EmailDeliveryLog.objects.update_or_create(
                    related_object_type=defaults["related_object_type"],
                    related_object_id=defaults["related_object_id"],
                    recipient_email=recipient,
                    attempted_at=defaults["attempted_at"],
                    defaults=defaults,
                )
                self.mark("notifications", "created" if created else "updated")
            except Exception as exc:
                self.mark("notifications", "errors")
                self.add_error_sample("notifications", f"notificationId={row.get('notificationId')}: {exc}")

    def infer_module(self, summary):
        text = normalize_text(summary).lower()
        if "payroll" in text:
            return ImportJob.MODULE_PAYROLL
        return ImportJob.MODULE_INVOICING

    def migrate_import_jobs(self):
        import_rows = self.fetch_rows("import")
        if not import_rows:
            return

        for row in import_rows:
            try:
                legacy_id = row.get("importJobId")
                status = normalize_status(row.get("status"), IMPORT_STATUS_MAP, ImportJob.STATUS_PENDING)
                invalid_rows = int(row.get("errorCount") or 0)
                defaults = {
                    "module": self.infer_module(row.get("summary")),
                    "source_file_name": f"legacy_import_{legacy_id}.csv",
                    "status": status,
                    "total_rows": invalid_rows,
                    "valid_rows": 0,
                    "invalid_rows": invalid_rows,
                    "saved_rows": 0,
                    "started_at": row.get("createdAt"),
                    "completed_at": row.get("createdAt") if status in BATCH_STATUS_TERMINAL else None,
                }
                _, created = ImportJob.objects.update_or_create(source_file_name=defaults["source_file_name"], defaults=defaults)
                self.mark("imports", "created" if created else "updated")
            except Exception as exc:
                self.mark("imports", "errors")
                self.add_error_sample("imports", f"importJobId={row.get('importJobId')}: {exc}")

    def migrate_audit_logs(self):
        audit_rows = self.fetch_rows("system_audit")
        if not audit_rows:
            return

        for row in audit_rows:
            try:
                legacy_id = row.get("auditId")
                actor = self.user_map.get(row.get("actorUserId"))
                metadata = row.get("metadata") or {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:
                        metadata = {"raw": metadata}
                if not isinstance(metadata, dict):
                    metadata = {"raw": str(metadata)}
                metadata["source"] = normalize_text(row.get("source"))
                metadata["legacy_id"] = legacy_id

                defaults = {
                    "user": actor,
                    "action": normalize_text(row.get("action"), "legacy_action"),
                    "target_type": normalize_text(row.get("targetType")),
                    "target_id": normalize_text(row.get("targetId")),
                    "metadata": metadata,
                    "created_at": row.get("timestamp"),
                }
                _, created = AuditLog.objects.update_or_create(
                    action=defaults["action"],
                    target_type=defaults["target_type"],
                    target_id=defaults["target_id"],
                    created_at=defaults["created_at"],
                    defaults=defaults,
                )
                self.mark("audit_logs", "created" if created else "updated")
            except Exception as exc:
                self.mark("audit_logs", "errors")
                self.add_error_sample("audit_logs", f"auditId={row.get('auditId')}: {exc}")

    def migrate_sessions(self):
        sessions = self.fetch_rows("usersession")
        if not sessions:
            return

        for row in sessions:
            try:
                key = normalize_text(row.get("sessionKey"))
                if not key:
                    self.mark("sessions", "skipped")
                    continue
                defaults = {
                    "session_data": normalize_text(row.get("sessionData")),
                    "expire_date": row.get("expireDate"),
                }
                _, created = Session.objects.update_or_create(session_key=key, defaults=defaults)
                self.mark("sessions", "created" if created else "updated")
            except Exception as exc:
                self.mark("sessions", "errors")
                self.add_error_sample("sessions", f"sessionKey={row.get('sessionKey')}: {exc}")

    def print_summary(self):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Legacy Migration Summary"))
        for entity in self.entities:
            _ = self.summary[entity]
        for entity in sorted(self.summary.keys()):
            counters = self.summary[entity]
            self.stdout.write(
                f"- {entity}: created={counters['created']}, updated={counters['updated']}, "
                f"skipped={counters['skipped']}, errors={counters['errors']}"
            )
            for sample in self.error_samples.get(entity, []):
                self.stdout.write(f"  error: {sample}")
