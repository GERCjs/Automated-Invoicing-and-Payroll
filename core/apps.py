from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from django.contrib.auth.models import Group, Permission, User
        from django.db import connection
        from django.db.backends.signals import connection_created

        from core.models import AuditLog
        from invoicing.models import Customer, Invoice, InvoiceItem, InvoiceSourceRow
        from payments.models import PaymentRecord
        from payroll.models import Employee, PayrollBatch, PayrollEntry, PayrollRecord, PayslipRecord

        def apply_table_mapping(use_renamed_tables):
            if use_renamed_tables:
                User._meta.db_table = "user"
                Group._meta.db_table = "user_group"
                Permission._meta.db_table = "user_permission"

                User.groups.through._meta.db_table = "user_account_groups"
                User.user_permissions.through._meta.db_table = "user_account_permissions"
                Group.permissions.through._meta.db_table = "user_group_permissions"

                AuditLog._meta.db_table = "audit_log"
                Customer._meta.db_table = "customer"
                Invoice._meta.db_table = "invoice"
                InvoiceItem._meta.db_table = "invoice_item"
                InvoiceSourceRow._meta.db_table = "invoice_source_row"
                PaymentRecord._meta.db_table = "payment"
                Employee._meta.db_table = "employee"
                PayrollBatch._meta.db_table = "payroll_details"
                PayrollEntry._meta.db_table = "payroll"
                PayslipRecord._meta.db_table = "legacy_payslip_record"
                PayrollRecord._meta.db_table = "payslip_record"
            else:
                User._meta.db_table = "auth_user"
                Group._meta.db_table = "auth_group"
                Permission._meta.db_table = "auth_permission"

                User.groups.through._meta.db_table = "auth_user_groups"
                User.user_permissions.through._meta.db_table = "auth_user_user_permissions"
                Group.permissions.through._meta.db_table = "auth_group_permissions"

                AuditLog._meta.db_table = "audit_log"
                Customer._meta.db_table = "customer"
                Invoice._meta.db_table = "invoice"
                InvoiceItem._meta.db_table = "invoice_item"
                InvoiceSourceRow._meta.db_table = "invoice_source_row"
                PaymentRecord._meta.db_table = "payment"
                Employee._meta.db_table = "employee"
                PayrollBatch._meta.db_table = "payroll_details"
                PayrollEntry._meta.db_table = "payroll"
                PayslipRecord._meta.db_table = "legacy_payslip_record"
                PayrollRecord._meta.db_table = "payslip_record"

        def should_use_renamed_tables(db_connection):
            import os

            db_name = str(db_connection.settings_dict.get("NAME") or "")
            engine = str(db_connection.settings_dict.get("ENGINE") or "")
            if engine.endswith("sqlite3"):
                return False
            if "test" in db_name.lower():
                # Django test databases keep auth_* tables but use the current payroll migration names.
                return False

            env_override = os.getenv("USE_RENAMED_TABLES", "").strip().lower()
            if env_override in {"1", "true", "yes", "on"}:
                return True
            if env_override in {"0", "false", "no", "off"}:
                return False

            return "test" not in db_name.lower()

        def configure_tables(sender, connection, **kwargs):
            apply_table_mapping(should_use_renamed_tables(connection))

        connection_created.connect(configure_tables, dispatch_uid="core_dynamic_table_mapping", weak=False)
        configure_tables(sender=None, connection=connection)
