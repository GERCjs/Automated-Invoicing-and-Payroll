# Database Setup and Integration Notes

## 1) What changed

The project is currently configured to use a shared MySQL database instead of the local SQLite database.

  - Uses simplified business table names such as `customer`, `invoice`, `invoice_item`, `payment`, `employee`, `payroll`, `audit_log`.
Current database switch:

```env
USE_SQLITE=false
```

When `USE_SQLITE=false`, `config/settings.py` uses the MySQL configuration from `.env`:

```env
MYSQL_DB=<database-name>
MYSQL_USER=<database-user>
MYSQL_PASSWORD=<database-password>
MYSQL_HOST=<database-host>
MYSQL_PORT=3306
```

The current `.env` points to an Azure MySQL server. Do not commit real database passwords or secrets to source control.

## 2) Database mode behavior

This project supports two database modes.

### Shared MySQL mode

Recommended for team integration and deployment.

```env
USE_SQLITE=false
```

In MySQL mode, the app uses the MySQL connection details from `.env`.

By default, non-test MySQL connections use the renamed/shared business table mapping from `core/apps.py`, such as:

- `user`
- `user_group`
- `user_permission`
- `customer`
- `invoice`
- `invoice_item`
- `invoice_source_row`
- `payment`
- `employee`
- `payroll_details`
- `payroll`
- `legacy_payslip_record`
- `payslip_record`
- `audit_log`

You may explicitly force this mapping with:

```env
USE_RENAMED_TABLES=true
```

### Local SQLite mode

Useful for isolated local development only.

```env
USE_SQLITE=true
```

SQLite mode uses:

```text
db.sqlite3
```

SQLite/test-style mappings keep Django's default auth tables and older payroll table names, such as:

- `auth_user`
- `auth_group`
- `auth_permission`
- `payroll_employee`
- `payroll_payrollbatch`
- `payroll_payrollentry`
- `payroll_paysliprecord`
- `payroll_payrollrecord`

Do not manually rename Django system tables such as `auth_*`, `django_*`, or session tables unless the whole team is doing a controlled migration reset.

## 3) How to integrate a new database

Use this process when connecting the project to a new MySQL database.

### Step 1: Create the database

Create a blank MySQL database on the target server.

Recommended character set and collation:

```sql
CREATE DATABASE your_database_name
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;
```

Create or assign a database user with permission to create tables, alter tables, insert, update, delete, and select.

### Step 2: Update `.env`

Set the project to MySQL mode:

```env
USE_SQLITE=false
USE_RENAMED_TABLES=true
```

Then point the project to the new database:

```env
MYSQL_DB=your_database_name
MYSQL_USER=your_database_user
MYSQL_PASSWORD=your_database_password
MYSQL_HOST=your_database_host
MYSQL_PORT=3306
```

If the new database will test Stripe or emails, also configure:

```env
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=
EMAIL_BACKEND=
DEFAULT_FROM_EMAIL=
EMAIL_HOST=
EMAIL_PORT=
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=
```

### Step 3: Install dependencies

From the project folder:

```powershell
pip install -r requirements.txt
```

Use the existing virtual environment if one is already configured.

### Step 4: Run Django checks

```powershell
python manage.py check
```

This confirms that Django can load settings and app configuration.

### Step 5: Apply migrations

Run migrations against the new database:

```powershell
python manage.py migrate
```

Then confirm migration state:

```powershell
python manage.py showmigrations
```

### Step 6: Create the first admin account

There is no dedicated database seed command in this repository.

Create the first superuser manually:

```powershell
python manage.py createsuperuser
```

Additional SuperAdmin, Admin, Finance, HR, Staff, and Customer users can be created through the app UI or Django admin.

### Step 7: Smoke test the integration

Run the development server:

```powershell
python manage.py runserver
```

Then verify:

- Login works.
- Dashboard loads.
- Customer creation works.
- Invoice creation works.
- Payroll employee or payroll upload pages load.
- Reports pages load for the correct roles.
- Audit logs are created for protected actions.

## 4) Fresh database versus existing database

### Fresh blank database

Use this path for a clean integration:

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py check
```

This is the recommended approach for a new database.

### Existing database with project tables

Before connecting:

- Confirm whether the database uses renamed MySQL table names or older Django table names.
- Confirm the `django_migrations` table exists and matches the current codebase.
- Back up the database before running migrations.
- Run `python manage.py showmigrations` before applying new migrations.

If the existing database is shared with other teammates, do not drop tables or delete rows from `django_migrations` without team approval.

### Existing legacy/demo data

If legacy source tables are available and need to be migrated into the current structure, use:

```powershell
python manage.py migrate_legacy_data --dry-run
python manage.py migrate_legacy_data
```

Run the dry run first and review the output before importing data.

## 5) SQL dump import warning

`LatestSql.sql` is a shared dump and should be used carefully.

Use it only for isolated local testing or controlled database restoration. Do not import it into a production or shared database unless the team agrees.

Important migration note:

- The dump may contain historical migration rows in `django_migrations`.
- The current codebase migration files may not exactly match older dump history.
- After importing a dump, run:

```powershell
python manage.py showmigrations
python manage.py migrate
python manage.py check
```

Do not manually delete Django migration rows unless performing a controlled migration-history reset.

## 6) Logical table list

Use these names when explaining the ERD or system architecture.

- Users (`user` in renamed MySQL mode, `auth_user` in SQLite/test mode): authentication accounts.
- User Role (`accounts_userrole`): application role profile linked to a user.
- Login Security Policy (`accounts_loginsecuritypolicy`): failed-login policy by role.
- Email Verification Token (`accounts_emailverificationtoken`): account verification links.
- Customer (`customer`): billing customer master data.
- Invoice (`invoice`): invoice header and status lifecycle.
- Invoice Item (`invoice_item`): invoice line items.
- Invoice Source Row (`invoice_source_row`): stored raw invoice import data.
- Payment (`payment`): manual or Stripe payment records linked to invoices.
- Stripe Webhook Event (`payments_stripewebhookevent`): Stripe event processing history.
- Employee (`employee` in renamed MySQL mode, `payroll_employee` in SQLite/test mode): employee master data.
- Payroll Batch (`payroll_details` in renamed MySQL mode, `payroll_payrollbatch` in SQLite/test mode): payroll run headers.
- Payroll Entry (`payroll` in renamed MySQL mode, `payroll_payrollentry` in SQLite/test mode): payroll rows per employee and batch.
- Payslip Document (`legacy_payslip_record` in renamed MySQL mode, `payroll_paysliprecord` in SQLite/test mode): issued payslip artifact metadata.
- Payroll Record (`payslip_record` in renamed MySQL mode, `payroll_payrollrecord` in SQLite/test mode): structured payroll record snapshots.
- Email Log (`email_log`): outbound email delivery tracking.
- Payment Reminder Settings (`notifications_paymentremindersettings`): reminder and mass-email controls.
- Import Job (`import_job`): import batch metadata.
- Import Error (`import_error`): row-level validation and import errors.
- Audit Log (`audit_log`): security and business action audit trail.

## 7) Integration checklist for a new database owner

Before handing a new database to the project team, confirm:

- The database is MySQL-compatible.
- The database uses `utf8mb4`.
- The database user can create and alter tables.
- Network access from the application machine to the database host is allowed.
- Port `3306` is open or the correct MySQL port is provided.
- SSL requirements are known if the provider requires SSL.
- `.env` has `USE_SQLITE=false`.
- `.env` has the correct MySQL credentials.
- `python manage.py check` passes.
- `python manage.py migrate` completes.
- `python manage.py createsuperuser` works.
- Login and basic create/read workflows work in the browser.
