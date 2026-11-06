from django.db import migrations
from django.db.models import Count


def remove_duplicate_totp_devices(apps, schema_editor):
    TOTPDevice = apps.get_model("otp_totp", "TOTPDevice")

    duplicate_confirmed = (
        TOTPDevice.objects.filter(confirmed=True)
        .values("user_id")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
    )
    for row in duplicate_confirmed.iterator():
        # The broken replacement flow created the replacement last. Keeping
        # the newest device both honors the user's choice and revokes the old
        # factor they intended to replace.
        ids = list(
            TOTPDevice.objects.filter(
                user_id=row["user_id"], confirmed=True
            ).order_by("-pk").values_list("pk", flat=True)
        )
        TOTPDevice.objects.filter(pk__in=ids[1:]).delete()

    duplicate_pending = (
        TOTPDevice.objects.filter(confirmed=False)
        .values("user_id")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
    )
    for row in duplicate_pending.iterator():
        # Existing setup pages selected the oldest pending device, so preserve
        # that secret if simultaneous GETs ever created more than one.
        ids = list(
            TOTPDevice.objects.filter(
                user_id=row["user_id"], confirmed=False
            ).order_by("pk").values_list("pk", flat=True)
        )
        TOTPDevice.objects.filter(pk__in=ids[1:]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("otp_totp", "0003_add_timestamps"),
        ("peer_account", "0004_profile_preferred_first_name_and_more"),
    ]

    operations = [
        migrations.RunPython(
            remove_duplicate_totp_devices,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunSQL(
            sql=(
                "CREATE UNIQUE INDEX otp_totp_one_state_per_user "
                "ON otp_totp_totpdevice (user_id, confirmed)"
            ),
            reverse_sql="DROP INDEX otp_totp_one_state_per_user",
        ),
    ]
