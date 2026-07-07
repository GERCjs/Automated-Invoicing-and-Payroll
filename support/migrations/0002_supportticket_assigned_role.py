from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportticket",
            name="assigned_role",
            field=models.CharField(
                blank=True,
                choices=[
                    ("finance", "Finance"),
                    ("hr", "Payroll"),
                    ("admin", "Admin"),
                ],
                max_length=20,
            ),
        ),
    ]
