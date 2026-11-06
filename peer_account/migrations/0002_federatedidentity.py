from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("peer_account", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FederatedIdentity",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("issuer", models.CharField(max_length=255)),
                ("subject", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="federated_identity",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="federatedidentity",
            constraint=models.UniqueConstraint(
                fields=("issuer", "subject"),
                name="unique_federated_issuer_subject",
            ),
        ),
    ]
