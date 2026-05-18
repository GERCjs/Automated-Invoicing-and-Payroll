from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0003_payrollrecord_and_more"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="paysliprecord",
            table="legacy_payslip_record",
        ),
        migrations.AlterModelTable(
            name="payrollrecord",
            table="payslip_record",
        ),
    ]
