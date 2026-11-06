"""Link an existing local account to its CWL so the holder can sign in via SAML.

UBC releases ``ubcEduStudentNumber`` for students only, so staff assertions
carry no student number and cannot be resolved by username. This command
records the CWL an administrator has verified out of band and retires the
account's password, which is what authorises the SSO binding at sign-in.

Retiring the password is not incidental. It preserves the rule that a single
account is never reachable by both a password and an SSO assertion.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from peer_account.models import FederatedIdentity, Profile


class Command(BaseCommand):
    help = "Link a local account to a CWL for SAML sign-in."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument(
            "--cwl",
            help="The CWL an administrator has verified out of band.",
        )
        parser.add_argument(
            "--batch",
            metavar="FILE",
            help=(
                "A file of 'username cwl' lines, one per person, '#' for "
                "comments. Checked as a whole before anything is written, so "
                "one bad line stops the lot rather than leaving half the "
                "instructors linked."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Say what would happen and write nothing.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help=(
                "Remove a recorded CWL instead of setting one. Does not "
                "touch a FederatedIdentity -- that is a real binding made "
                "at sign-in, and removing it is a separate decision."
            ),
        )
        parser.add_argument(
            "--keep-password",
            action="store_true",
            help=(
                "Record the CWL but leave the password usable. The account "
                "will still be refused at SAML sign-in; use only to stage a "
                "link you intend to complete later."
            ),
        )

    def handle(self, *args, **options):
        if options["batch"]:
            return self._batch(options)
        username = (options["username"] or "").strip()
        if not username:
            raise CommandError("--username is required unless --batch is given.")
        clear = options["clear"]
        cwl = (options["cwl"] or "").strip()
        if clear and cwl:
            raise CommandError("Give either --cwl or --clear, not both.")
        if not clear and not cwl:
            raise CommandError("A CWL is required.")

        with transaction.atomic():
            try:
                user = User.objects.select_for_update().get(username=username)
            except User.DoesNotExist as exc:
                raise CommandError(f"No account named {username!r}.") from exc

            if not user.is_active:
                raise CommandError(
                    f"{username!r} is disabled; re-enable it before linking."
                )

            if clear:
                profile = Profile.objects.filter(user=user).first()
                previous = getattr(profile, "cwl", None)
                if not previous:
                    self.stdout.write(f"No CWL recorded on {username!r}; nothing to do.")
                    return
                Profile.objects.filter(user=user).update(cwl=None)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Cleared CWL {previous!r} from {username!r}. Its"
                        f" password is not restored by this; set one in the"
                        f" admin if the account still needs password access."
                    )
                )
                return

            clash = (
                Profile.objects.select_related("user")
                .filter(cwl=cwl)
                .exclude(user_id=user.id)
                .first()
            )
            if clash is not None:
                raise CommandError(
                    f"CWL {cwl!r} is already recorded on {clash.user.username!r}."
                )

            # And the binding that actually decides where a sign-in lands.
            # _resolve_saml_user checks FederatedIdentity first and returns
            # immediately, so recording a CWL that some other account is
            # already federated with achieves nothing at all -- the holder
            # keeps landing in that other account, while this one loses its
            # password and becomes unreachable. Checking only Profile.cwl let
            # exactly that happen.
            bound = (
                FederatedIdentity.objects.select_related("user")
                .filter(subject=cwl)
                .exclude(user_id=user.id)
                .first()
            )
            if bound is not None:
                raise CommandError(
                    f"CWL {cwl!r} is already federated to"
                    f" {bound.user.username!r}, which is where its holder"
                    f" signs in. Recording it here would change nothing and"
                    f" would retire this account's password for no reason."
                    f" If the two accounts are one person, move the course"
                    f" memberships instead."
                )

            if options["dry_run"]:
                self.stdout.write(
                    f"would link {username!r} to CWL {cwl!r}"
                    + ("" if user.has_usable_password()
                       else " (it has no password to retire)")
                )
                return

            profile, _ = Profile.objects.get_or_create(user=user)
            profile.cwl = cwl
            profile.save(update_fields=["cwl"])

            if options["keep_password"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"Recorded CWL {cwl!r} on {username!r}, password kept. "
                        "SAML sign-in stays refused until the password is retired."
                    )
                )
                return

            if user.has_usable_password():
                user.set_unusable_password()
                user.save(update_fields=["password"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Linked {username!r} to CWL {cwl!r}; password retired."
            )
        )

    def _batch(self, options):
        """Link a whole list, checking every line before writing any of it.

        Doing this by hand once per person is how a CWL ends up pasted
        against the wrong account. Every row is validated first -- account
        exists, is active, CWL not recorded elsewhere, CWL not already
        federated to somebody else -- and only then is anything written.
        """
        path = options["batch"]
        rows, seen_cwl = [], {}
        try:
            lines = open(path).read().splitlines()
        except OSError as exc:
            raise CommandError(f"cannot read {path}: {exc}") from exc

        for number, raw in enumerate(lines, start=1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 2:
                raise CommandError(
                    f"{path}:{number}: expected 'username cwl', got {raw!r}"
                )
            name, cwl = parts
            if cwl in seen_cwl:
                raise CommandError(
                    f"{path}:{number}: CWL {cwl!r} is given twice"
                    f" ({seen_cwl[cwl]} and {name})"
                )
            seen_cwl[cwl] = name
            rows.append((number, name, cwl))

        if not rows:
            raise CommandError(f"{path} has no rows.")

        problems = []
        for number, name, cwl in rows:
            user = User.objects.filter(username=name).first()
            if user is None:
                problems.append(f"{path}:{number}: no account named {name!r}")
                continue
            if not user.is_active:
                problems.append(f"{path}:{number}: {name!r} is archived")
            other = (
                Profile.objects.select_related("user")
                .filter(cwl=cwl).exclude(user_id=user.id).first()
            )
            if other is not None:
                problems.append(
                    f"{path}:{number}: CWL {cwl!r} already recorded on"
                    f" {other.user.username!r}"
                )
            bound = (
                FederatedIdentity.objects.select_related("user")
                .filter(subject=cwl).exclude(user_id=user.id).first()
            )
            if bound is not None:
                problems.append(
                    f"{path}:{number}: CWL {cwl!r} already federated to"
                    f" {bound.user.username!r} -- that is where its holder"
                    f" signs in; move the memberships instead"
                )

        for problem in problems:
            self.stderr.write(self.style.ERROR("  " + problem))
        if problems:
            raise CommandError(
                f"{len(problems)} problem(s); nothing was written."
            )

        for _, name, cwl in rows:
            self.stdout.write(f"  {name:<24} -> CWL {cwl}")

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"\n  {len(rows)} row(s) check out. Nothing written."
                    f" Drop --dry-run to apply."
                )
            )
            return

        with transaction.atomic():
            for _, name, cwl in rows:
                user = User.objects.select_for_update().get(username=name)
                Profile.objects.get_or_create(user=user)
                Profile.objects.filter(user=user).update(cwl=cwl)
                if user.has_usable_password():
                    user.set_unusable_password()
                    user.save(update_fields=["password"])

        self.stdout.write(
            self.style.SUCCESS(
                f"\n  linked {len(rows)} account(s); their passwords are"
                f" retired and their next CWL sign-in lands in the same"
                f" account, with its courses."
            )
        )
