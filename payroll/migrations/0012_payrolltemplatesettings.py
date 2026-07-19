from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0011_payrollrecord_unique_payroll_employee_payment_date"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollTemplateSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company_display_name", models.CharField(blank=True, default="", max_length=255)),
                ("company_address", models.TextField(blank=True, default="")),
                ("company_email", models.EmailField(blank=True, default="", max_length=254)),
                ("company_phone", models.CharField(blank=True, default="", max_length=50)),
                ("company_registration_number", models.CharField(blank=True, default="", max_length=100)),
                ("header_text", models.TextField(blank=True, default="")),
                ("footer_text", models.TextField(blank=True, default="")),
                (
                    "logo",
                    models.ImageField(
                        blank=True,
                        default="",
                        upload_to="payroll_branding/logos/",
                        validators=[django.core.validators.FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg"])],
                    ),
                ),
                (
                    "logo_size",
                    models.CharField(
                        choices=[("small", "Small"), ("medium", "Medium"), ("large", "Large")],
                        default="medium",
                        max_length=10,
                    ),
                ),
                (
                    "logo_position",
                    models.CharField(
                        choices=[("left", "Left"), ("centre", "Centre"), ("right", "Right")],
                        default="left",
                        max_length=10,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payroll_template_settings_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Payroll template settings",
                "verbose_name_plural": "Payroll template settings",
                "db_table": "payroll_template_settings",
            },
        ),
    ]
