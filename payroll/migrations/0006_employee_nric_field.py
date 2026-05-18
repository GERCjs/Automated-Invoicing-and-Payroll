from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0005_employee_profile_fields"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    "ALTER TABLE employee ADD COLUMN nric varchar(20) NOT NULL DEFAULT '';",
                    reverse_sql="ALTER TABLE employee DROP COLUMN nric;",
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="employee",
                    name="nric",
                    field=models.CharField(blank=True, max_length=20),
                ),
            ],
        ),
    ]
