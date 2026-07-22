from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0013_payrollsetup"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="payrollsetup",
            new_name="payroll_set_payment_a11092_idx",
            old_name="payroll_set_payment_33a5a8_idx",
        ),
        migrations.RenameIndex(
            model_name="payrollsetup",
            new_name="payroll_set_is_acti_caefd1_idx",
            old_name="payroll_set_is_acti_87da2f_idx",
        ),
    ]
