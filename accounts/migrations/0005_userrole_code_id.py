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
    # Generate the next available code ID for a role.
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
    # Add code IDs to existing UserRole rows.
    UserRole = apps.get_model("accounts", "UserRole")
    # Track existing codes so we do not create duplicates.
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
            # Remember the highest existing number for each prefix.
            prefix, raw_sequence = code_id.rsplit("-", 1)
            counters[prefix] = max(counters.get(prefix, 0), int(raw_sequence))
        except ValueError:
            continue

    for role_profile in UserRole.objects.order_by("user_id", "id"):
        if role_profile.code_id:
            # Normalize existing manual codes to uppercase.
            normalized_code = role_profile.code_id.strip().upper()
            if role_profile.code_id != normalized_code:
                role_profile.code_id = normalized_code
                role_profile.save(update_fields=["code_id"])
            continue
        # Fill missing code IDs.
        role_profile.code_id = generate_code_id(role_profile.role, used_codes, counters)
        role_profile.save(update_fields=["code_id"])


class Migration(migrations.Migration):
    # This migration runs after role labels are updated.
    dependencies = [
        ("accounts", "0004_alter_userrole_role"),
    ]

    # Add code_id, fill old rows, then make code_id unique.
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
