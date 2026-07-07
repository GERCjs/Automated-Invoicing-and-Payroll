from django.db import migrations, models


def convert_closed_to_resolved(apps, schema_editor):
    SupportTicket = apps.get_model("support", "SupportTicket")
    SupportTicket.objects.filter(status="closed").update(status="resolved")


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0002_supportticket_assigned_role"),
    ]

    operations = [
        migrations.RunPython(convert_closed_to_resolved, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="supportticket",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "Open"),
                    ("in_progress", "In Progress"),
                    ("resolved", "Resolved"),
                ],
                default="open",
                max_length=20,
            ),
        ),
    ]
