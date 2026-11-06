"""Archive password accounts that never set up a second factor.

Every sign-in now carries a second factor: CWL accounts through UBC, and
password accounts through their authenticator. That leaves the accounts that
existed before, still holding a password and nothing else. Being *offered*
enrolment is not good enough for those -- a stolen old password would still
be a way in, because whoever holds it could enrol an authenticator of their
own and be the account.

So they are archived: ``is_active = False``. Both sign-in routes already
refuse an inactive account -- ModelBackend rejects it, and
``_resolve_saml_user`` raises PermissionError -- and nothing is deleted.
Every course membership, hand raise and grade stays exactly where it is,
which is the reason this is not a ``delete``: CourseMember.user cascades.

Reports and changes nothing unless given --apply.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from peer_account import mfa


class Command(BaseCommand):
    help = "Deactivate password accounts that have no second factor."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually archive. Without this, only report.",
        )
        parser.add_argument(
            "--include-superusers",
            action="store_true",
            help=(
                "Archive superusers too. Off by default: locking every "
                "administrator out of a running site is not a thing to do "
                "as a side effect."
            ),
        )
        parser.add_argument(
            "--only",
            action="append",
            default=[],
            metavar="USERNAME",
            help=(
                "Archive exactly these accounts and nothing else. May be "
                "given more than once. Exact names, not prefixes: archiving "
                "the wrong account because a name happened to be a prefix "
                "of another is not a mistake worth leaving available."
            ),
        )
        parser.add_argument(
            "--only-prefix",
            action="append",
            default=[],
            metavar="PREFIX",
            help=(
                "Only archive accounts whose username starts with this. May "
                "be given more than once. The retired one-time-session "
                "accounts are temp_instructor_ and temp_student_, and they "
                "are safe to sweep in a way that a named human account is "
                "not."
            ),
        )
        parser.add_argument(
            "--idle-days",
            type=int,
            default=0,
            metavar="DAYS",
            help=(
                "Only archive accounts that have not signed in for this "
                "long. An account nobody has used for a year can go without "
                "anybody being inconvenienced; one somebody used last month "
                "deserves a conversation first. Accounts that have never "
                "signed in always qualify."
            ),
        )
        parser.add_argument(
            "--min-age-days",
            type=int,
            default=0,
            metavar="DAYS",
            help=(
                "Only archive accounts older than this. Somebody who signed "
                "up ten minutes ago and is still scanning the QR has no "
                "device yet either; a grace period is what makes this safe "
                "to run on a schedule rather than once by hand."
            ),
        )
        parser.add_argument(
            "--keep",
            action="append",
            default=[],
            metavar="USERNAME",
            help="Never archive this account. May be given more than once.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        keep = set(options["keep"])
        min_age = options["min_age_days"]
        only_prefix = tuple(options["only_prefix"])
        only_names = set(options["only"])
        if only_names and only_prefix:
            raise CommandError("Give either --only or --only-prefix, not both.")
        idle_days = options["idle_days"]
        idle_cutoff = (
            timezone.now() - timedelta(days=idle_days) if idle_days else None
        )
        cutoff = timezone.now() - timedelta(days=min_age) if min_age else None

        candidates, exempt_supers, kept, too_new, still_used = [], [], [], [], []
        for user in User.objects.filter(is_active=True).order_by("username"):
            if not user.has_usable_password():
                continue  # CWL, or a demonstration guest: no password to steal
            if mfa.confirmed_device(user) is not None:
                continue  # enrolled
            if only_names and user.username not in only_names:
                continue
            if only_prefix and not user.username.startswith(only_prefix):
                continue
            if user.username in keep:
                kept.append(user)
                continue
            if cutoff is not None and user.date_joined > cutoff:
                too_new.append(user)
                continue
            if (
                idle_cutoff is not None
                and user.last_login is not None
                and user.last_login > idle_cutoff
            ):
                still_used.append(user)
                continue
            if (
                user.is_superuser
                and not options["include_superusers"]
                and user.username not in only_names
            ):
                exempt_supers.append(user)
                continue
            candidates.append(user)

        def describe(user):
            when = (
                user.last_login.strftime("%Y-%m-%d")
                if user.last_login
                else "never signed in"
            )
            flags = "".join(
                c for c, on in (("S", user.is_superuser), ("s", user.is_staff)) if on
            )
            # A CWL on file means this account has somewhere to go: clear the
            # password and the next CWL sign-in adopts it, memberships and
            # all. Without one, CWL would create a new account instead and
            # orphan this one.
            cwl = getattr(getattr(user, "profile", None), "cwl", None)
            route = f"CWL:{cwl}" if cwl else "no CWL on file"
            return (
                f"    {user.username:<28} last login {when:<16}"
                f" {(user.email or '-'):<30} {route:<22} {flags}"
            )

        self.stdout.write(
            f"  password accounts with no second factor: {len(candidates)}"
        )
        for user in candidates:
            self.stdout.write(describe(user))

        if still_used:
            self.stdout.write(
                f"\n  used within {idle_days} day(s), NOT archived"
                f" ({len(still_used)}) -- somebody may still need these:"
            )
            for user in still_used:
                self.stdout.write(describe(user))

        if too_new:
            self.stdout.write(
                f"\n  too new to archive, under {min_age} day(s)"
                f" ({len(too_new)}) -- still enrolling:"
            )
            for user in too_new:
                self.stdout.write(describe(user))

        if kept:
            self.stdout.write(f"\n  kept by name ({len(kept)}):")
            for user in kept:
                self.stdout.write(describe(user))

        if exempt_supers:
            self.stdout.write(
                f"\n  superusers, NOT archived ({len(exempt_supers)}) --"
                f" enrol these rather than locking them out:"
            )
            for user in exempt_supers:
                self.stdout.write(describe(user))

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    "\n  Nothing changed. Re-run with --apply to archive."
                )
            )
            return

        if not candidates:
            self.stdout.write("\n  nothing to archive")
            return

        with transaction.atomic():
            count = User.objects.filter(
                pk__in=[u.pk for u in candidates]
            ).update(is_active=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"\n  archived {count} account(s). Nothing was deleted; to let"
                f" one back in, set is_active=True and it will be asked to"
                f" enrol on its next sign-in."
            )
        )
