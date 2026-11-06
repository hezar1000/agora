from django.db import migrations
from django.db.models import Count


RECOVERY_DEVICE_NAME = "recovery codes"


def consolidate_recovery_devices(apps, schema_editor):
    """Keep every issued code while reducing each account to one container."""
    StaticDevice = apps.get_model("otp_static", "StaticDevice")
    StaticToken = apps.get_model("otp_static", "StaticToken")

    duplicate_users = (
        StaticDevice.objects.filter(name=RECOVERY_DEVICE_NAME)
        .values("user_id")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
    )
    for row in duplicate_users.iterator():
        device_ids = list(
            StaticDevice.objects.filter(
                user_id=row["user_id"],
                name=RECOVERY_DEVICE_NAME,
            )
            # Prefer a confirmed container if historic/admin-created data
            # mixed states under the managed name.
            .order_by("-confirmed", "pk")
            .values_list("pk", flat=True)
        )
        keeper_id = device_ids[0]
        StaticDevice.objects.filter(pk=keeper_id).update(confirmed=True)
        StaticToken.objects.filter(device_id__in=device_ids[1:]).update(
            device_id=keeper_id
        )
        StaticDevice.objects.filter(pk__in=device_ids[1:]).delete()

        # A recovery string is single-use even if historic data happens to
        # contain the same value more than once.
        duplicate_tokens = (
            StaticToken.objects.filter(device_id=keeper_id)
            .values("token")
            .annotate(total=Count("pk"))
            .filter(total__gt=1)
        )
        for token_row in duplicate_tokens.iterator():
            token_ids = list(
                StaticToken.objects.filter(
                    device_id=keeper_id,
                    token=token_row["token"],
                )
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            StaticToken.objects.filter(pk__in=token_ids[1:]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("otp_static", "0003_add_timestamps"),
        ("peer_account", "0005_one_totp_device_per_state"),
    ]

    operations = [
        migrations.RunPython(
            consolidate_recovery_devices,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunSQL(
            sql=(
                "CREATE UNIQUE INDEX otp_static_one_agora_recovery_per_user "
                "ON otp_static_staticdevice (user_id) "
                "WHERE name = 'recovery codes'"
            ),
            reverse_sql=(
                "DROP INDEX otp_static_one_agora_recovery_per_user"
            ),
        ),
    ]
