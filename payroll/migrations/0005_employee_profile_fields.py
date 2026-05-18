from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0004_swap_payslip_table_names"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    """
                    ALTER TABLE employee
                    ADD COLUMN date_of_birth date NULL,
                    ADD COLUMN date_of_appointment date NULL,
                    ADD COLUMN legal_status varchar(30) NOT NULL DEFAULT '',
                    ADD COLUMN gender varchar(20) NOT NULL DEFAULT '',
                    ADD COLUMN race varchar(60) NOT NULL DEFAULT '',
                    ADD COLUMN religion varchar(60) NOT NULL DEFAULT '',
                    ADD COLUMN sdl_exempt bool NOT NULL DEFAULT 0,
                    ADD COLUMN cpf_exempt bool NOT NULL DEFAULT 0,
                    ADD COLUMN job_title varchar(150) NOT NULL DEFAULT '',
                    ADD COLUMN payment_method varchar(20) NOT NULL DEFAULT '',
                    ADD COLUMN bank_name varchar(120) NOT NULL DEFAULT '',
                    ADD COLUMN bank_account_number varchar(50) NOT NULL DEFAULT '',
                    ADD COLUMN bank_branch_code varchar(30) NOT NULL DEFAULT '';
                    """,
                    reverse_sql="""
                    ALTER TABLE employee
                    DROP COLUMN bank_branch_code,
                    DROP COLUMN bank_account_number,
                    DROP COLUMN bank_name,
                    DROP COLUMN payment_method,
                    DROP COLUMN job_title,
                    DROP COLUMN cpf_exempt,
                    DROP COLUMN sdl_exempt,
                    DROP COLUMN religion,
                    DROP COLUMN race,
                    DROP COLUMN gender,
                    DROP COLUMN legal_status,
                    DROP COLUMN date_of_appointment,
                    DROP COLUMN date_of_birth;
                    """,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="employee",
                    name="bank_account_number",
                    field=models.CharField(blank=True, max_length=50),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="bank_branch_code",
                    field=models.CharField(blank=True, max_length=30),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="bank_name",
                    field=models.CharField(blank=True, max_length=120),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="cpf_exempt",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="date_of_appointment",
                    field=models.DateField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="date_of_birth",
                    field=models.DateField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="gender",
                    field=models.CharField(
                        blank=True,
                        choices=[("male", "Male"), ("female", "Female"), ("other", "Other")],
                        max_length=20,
                    ),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="job_title",
                    field=models.CharField(blank=True, max_length=150),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="legal_status",
                    field=models.CharField(
                        blank=True,
                        choices=[
                            ("citizen", "Singapore Citizen"),
                            ("pr", "Permanent Resident"),
                            ("work_permit", "Work Permit"),
                            ("employment_pass", "Employment Pass"),
                            ("s_pass", "S Pass"),
                        ],
                        max_length=30,
                    ),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="payment_method",
                    field=models.CharField(
                        blank=True,
                        choices=[("cash", "Cash"), ("cheque", "Cheque"), ("giro", "GIRO")],
                        max_length=20,
                    ),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="race",
                    field=models.CharField(blank=True, max_length=60),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="religion",
                    field=models.CharField(blank=True, max_length=60),
                ),
                migrations.AddField(
                    model_name="employee",
                    name="sdl_exempt",
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
