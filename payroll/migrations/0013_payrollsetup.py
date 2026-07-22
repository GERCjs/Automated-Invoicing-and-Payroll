from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0012_payrolltemplatesettings"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollSetup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("basic_salary", models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(0)])),
                ("physical_products_commission", models.DecimalField(decimal_places=2, default=0, max_digits=12, validators=[django.core.validators.MinValueValidator(0)])),
                ("credit_commission", models.DecimalField(decimal_places=2, default=0, max_digits=12, validators=[django.core.validators.MinValueValidator(0)])),
                ("services_commission", models.DecimalField(decimal_places=2, default=0, max_digits=12, validators=[django.core.validators.MinValueValidator(0)])),
                ("loan_deduction", models.DecimalField(decimal_places=2, default=0, max_digits=12, validators=[django.core.validators.MinValueValidator(0)])),
                ("other_deductions", models.DecimalField(decimal_places=2, default=0, max_digits=12, validators=[django.core.validators.MinValueValidator(0)])),
                (
                    "payment_date_type",
                    models.CharField(
                        choices=[("last_day", "Last day of month"), ("specific_day", "Specific day of month")],
                        default="last_day",
                        max_length=20,
                    ),
                ),
                ("payment_day_of_month", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payroll_setups_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "employee",
                    models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="payroll_setup", to="payroll.employee"),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payroll_setups_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "payroll_setup",
                "ordering": ["employee__employee_code"],
            },
        ),
        migrations.AddIndex(
            model_name="payrollsetup",
            index=models.Index(fields=["payment_date_type"], name="payroll_set_payment_33a5a8_idx"),
        ),
        migrations.AddIndex(
            model_name="payrollsetup",
            index=models.Index(fields=["is_active"], name="payroll_set_is_acti_87da2f_idx"),
        ),
    ]
