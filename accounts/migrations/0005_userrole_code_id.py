from django.db import migrations, models


ROLE_CODE_PREFIXES = {
    "superadmin": "SUP",
    "admin": "ADM",
    "finance": "FIN",
    "hr": "HR",
    "staff": "STF",
    "customer": "CUS",
}


def generate_code_id(role, used_codes, counters):
    prefix = ROLE_CODE_PREFIXES.get(role, "USR")
    sequence = counters.get(prefix, 0) + 1
    code_id = f"{prefix}-{sequence:06d}"
    while code_id in used_codes:
        sequence += 1
        code_id = f"{prefix}-{sequence:06d}"
    counters[prefix] = sequence
    used_codes.add(code_id)
    return code_id


def backfill_code_ids(apps, schema_editor):
    UserRole = apps.get_model("accounts", "UserRole")
    used_codes = {
        code_id.strip().upper()
        for code_id in UserRole.objects.exclude(code_id__isnull=True)
        .exclude(code_id="")
        .order_by()
        .values_list("code_id", flat=True)
    }
    counters = {}
    for code_id in used_codes:
        try:
            prefix, raw_sequence = code_id.rsplit("-", 1)
            counters[prefix] = max(counters.get(prefix, 0), int(raw_sequence))
        except ValueError:
            continue

    for role_profile in UserRole.objects.order_by("user_id", "id"):
        if role_profile.code_id:
            normalized_code = role_profile.code_id.strip().upper()
            if role_profile.code_id != normalized_code:
                role_profile.code_id = normalized_code
                role_profile.save(update_fields=["code_id"])
            continue
        role_profile.code_id = generate_code_id(role_profile.role, used_codes, counters)
        role_profile.save(update_fields=["code_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_alter_userrole_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="userrole",
            name="code_id",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.RunPython(backfill_code_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="userrole",
            name="code_id",
            field=models.CharField(blank=True, max_length=30, unique=True),
        ),
    ]
