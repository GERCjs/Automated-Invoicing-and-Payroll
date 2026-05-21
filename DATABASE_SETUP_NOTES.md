# Database Setup and Integration Notes (Gerald Scope)

## 1) Naming mode and schema consistency

This project supports two table-mapping modes through `core/apps.py`.

- Shared MySQL mode (recommended for integration):
  - `USE_SQLITE=false`
  - `USE_RENAMED_TABLES=true`
  - Uses simplified business table names such as `customer`, `invoice`, `invoice_item`, `payment`, `employee`, `payroll`, `audit_log`.
- Local SQLite mode (legacy compatibility):
  - `USE_SQLITE=true`
  - `USE_RENAMED_TABLES=false` (default behavior for sqlite when no override is set)
  - Uses older payroll/auth table names such as `auth_user`, `payroll_payrollbatch`, `payroll_payrollentry`.

Do not rename Django system tables (`auth_*`, `django_*`, `sessions`) manually.

## 2) Remaining confusing table names (do not rename yet)

- `payroll_details`: payroll batch/header table (not detailed line rows).
- `payroll`: payroll entry lines per employee/batch.
- `legacy_payslip_record`: generated payslip documents (historical naming kept for compatibility).
- `payslip_record`: aggregated payroll import-style records (name overlaps with payslip concept).
- `payments_stripewebhookevent`: Stripe webhook event history table.
- `notifications_paymentremindersettings`: reminder configuration table (default Django naming).

These names are currently compatible with existing migrations and integration behavior.

## 3) Shared database setup (team)

### Environment

1. Copy `.env.example` to `.env`.
2. Set:
   - `USE_SQLITE=false`
   - `USE_RENAMED_TABLES=true`
   - `MYSQL_DB`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_HOST`, `MYSQL_PORT`
3. Configure Stripe keys if testing payment flow:
   - `STRIPE_SECRET_KEY`
   - `STRIPE_PUBLISHABLE_KEY`
   - `STRIPE_WEBHOOK_SECRET`

### Migrations

```powershell
python manage.py migrate
python manage.py showmigrations
python manage.py check
```

### Seed/test accounts

There is no dedicated fixture seed command in the repository.

- Create initial superuser manually:

```powershell
python manage.py createsuperuser
```

- Additional admin/finance/hr/staff/customer users can be created through app flows (`accounts` UI) or Django admin.

## 4) Database reset and import options

### A) Fresh schema from migrations (recommended)

```powershell
python manage.py migrate
```

For full reset, drop and recreate the target MySQL database, then run migrations again.

### B) Legacy/demo data migration path (if needed)

If legacy source tables are available, use:

```powershell
python manage.py migrate_legacy_data --dry-run
python manage.py migrate_legacy_data
```

### C) SQL dump import (with caution)

`LatestSql.sql` is the current shared dump.
Use it only in isolated/local environments, not production shared environments.

Important migration-note after importing `LatestSql.sql`:
- The dump includes a historical `django_migrations` row `payroll:0003_payrollbatch_import_job_paysliprecord_details`.
- The current codebase migration file is `payroll/0003_payrollrecord_and_more.py`.
- Do not manually delete Django system migration rows unless the team is doing a controlled migration-history reset.
- Safest approach: import the SQL dump to align data, then run `python manage.py showmigrations` and `python manage.py migrate` on the same environment to reconcile any pending states.

## 5) Final table list for ERD/report

Use these logical names in the ERD/report, with physical table names in parentheses.

- Users (`user` in renamed mode, `auth_user` in sqlite/legacy mode): authentication accounts.
- Customer (`customer`): billing customer master data.
- Invoice (`invoice`): invoice header and status lifecycle.
- Invoice Item (`invoice_item`): invoice line items.
- Payment (`payment`): payment transactions linked to invoices.
- Employee (`employee` in renamed mode, `payroll_employee` in sqlite/legacy mode): employee master data.
- Payroll Batch (`payroll_details` in renamed mode, `payroll_payrollbatch` in sqlite/legacy mode): payroll run headers.
- Payroll Entry (`payroll` in renamed mode, `payroll_payrollentry` in sqlite/legacy mode): payroll rows per employee.
- Payslip Document (`legacy_payslip_record` in renamed mode, `payroll_paysliprecord` in sqlite/legacy mode): issued payslip artifacts.
- Payroll Record (`payslip_record` in renamed mode, `payroll_payrollrecord` in sqlite/legacy mode): structured payroll record snapshots.
- Email Log (`email_log`): outbound email delivery tracking.
- Audit Log (`audit_log`): system/business action audit trail.
- Reminder Setting (`notifications_paymentremindersettings`): payment reminder and mass-email controls.
- Import Job (`import_job`): import batch metadata.
- Import Error (`import_error`): row-level validation/import errors.
- Stripe Webhook Event (`payments_stripewebhookevent`): Stripe event processing history.
