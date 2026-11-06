# peer_account/models.py
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    photo = models.ImageField(upload_to='profile_photos/', null=True, blank=True)
    cwl = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="CWL identifier from SAML login",
    )

    # The name this person is called, as opposed to the name the registrar
    # has. Kept apart from User.first_name/last_name on purpose: the SAML
    # assertion consumer overwrites those from UBC on every single sign-in,
    # so a name edited there lasts until the next lecture and then reverts --
    # in front of the class, which is the worst possible moment for it.
    preferred_first_name = models.CharField(
        "Preferred first name", max_length=60, blank=True, default=""
    )
    preferred_last_name = models.CharField(
        "Preferred last name", max_length=60, blank=True, default=""
    )

    def display_name(self):
        """What to call this person, falling back to the registrar's name."""
        first = (self.preferred_first_name or self.user.first_name or "").strip()
        last = (self.preferred_last_name or self.user.last_name or "").strip()
        return " ".join(part for part in (first, last) if part)

    def __str__(self):
        return f"{self.user.username}'s profile"


class FederatedIdentity(models.Model):
    """Immutable SSO identity binding; usernames are display/login aliases only."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="federated_identity",
    )
    issuer = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("issuer", "subject"),
                name="unique_federated_issuer_subject",
            )
        ]

    def __str__(self):
        return f"{self.issuer}: {self.user_id}"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, raw=False, **kwargs):
    # `raw` marks a save that is replaying serialised data, where the fixture
    # supplies its own Profile rows. Creating one here collides with them on
    # the one-to-one constraint and aborts the whole load.
    if raw or not created:
        return
    Profile.objects.create(user=instance)


# There used to be a second post_save receiver here, save_user_profile, whose
# whole body was ``instance.profile.save()``.
#
# Accessing ``instance.profile`` caches it on the user instance, so any later
# ``user.save()`` wrote that cached copy back over the row -- silently
# dropping a ``cwl`` written in between by a queryset update, which is how
# link_saml_identity and the SAML resolver write it. A lost CWL binding turns
# the next CWL sign-in into a brand new account and orphans the old one along
# with its course memberships.
#
# Nothing depended on the write. A profile is created above when the user is;
# the edit page creates a missing one itself and saves the photo through its
# own form; and replacing the body with get_or_create only traded the bug for
# a UNIQUE violation racing that same page. So it is gone.
