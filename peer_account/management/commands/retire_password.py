"""Move a legacy password account onto CWL instead of archiving it.

A named account that predates single sign-on holds a password and nothing
else. While that password is there, ``_resolve_saml_user`` refuses to bind a
CWL to it -- "Username conflicts with a local password account" -- so the
person cannot sign in with CWL even though they have one. Archiving such an
account does not move them to CWL; it locks them out.

Clearing the password is what moves them. With no usable password the next
CWL sign-in adopts the very same account: same pk, same course memberships,
same history. And because the account then has no password, it needs no
second factor of its own -- UBC's sign-in already carries one.

Two things have to be true for that to work, and both are checked here:

* a CWL has to be on file (``Profile.cwl``), or the resolver has nothing to
  match on and would create a new account instead, orphaning this one;
* no federated identity may already exist for that CWL, or the person has
  *already* been given a second account and clearing this password does not
  merge them. Those are reported, not touched -- merging accounts is not
  something to do behind somebody's back.

Reports and changes nothing without --apply.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from peer_account import mfa
from peer_account.models import FederatedIdentity, Profile


class Command(BaseCommand):
    help = "Clear the password on legacy accounts so CWL can adopt them."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--include-superusers",
            action="store_true",
            help="Retire superuser passwords too. Off by default.",
        )
        parser.add_argument(
            "--keep", action="append", default=[], metavar="USERNAME",
            help="Leave this account's password alone.",
        )
        parser.add_argument(
            "--issuer",
            default="https://authentication.ubc.ca",
            help="Which identity provider to check for an existing binding.",
        )

    def handle(self, *args, **options):
        keep = set(options["keep"])
        issuer = options["issuer"]

        ready, no_cwl, already_bound, supers, kept = [], [], [], [], []
        for user in User.objects.filter(is_active=True).order_by("username"):
            if not user.has_usable_password():
                continue  # already on CWL, or a guest
            if mfa.confirmed_device(user) is not None:
                continue  # enrolled with a second factor of its own; leave it
            if user.username in keep:
                kept.append((user, None))
                continue
            cwl = (
                Profile.objects.filter(user=user)
                .values_list("cwl", flat=True)
                .first()
            )
            if not cwl:
                no_cwl.append((user, None))
                continue
            if user.is_superuser and not options["include_superusers"]:
                supers.append((user, cwl))
                continue
            clash = (
                FederatedIdentity.objects.filter(issuer=issuer, subject=cwl)
                .exclude(user=user)
                .select_related("user")
                .first()
            )
            if clash is not None:
                already_bound.append((user, f"{cwl} -> already {clash.user.username}"))
                continue
            ready.append((user, cwl))

        def show(title, rows):
            if not rows:
                return
            self.stdout.write(f"\n  {title} ({len(rows)}):")
            for user, note in rows:
                when = (
                    user.last_login.strftime("%Y-%m-%d")
                    if user.last_login else "never signed in"
                )
                # The email is the only honest hint at somebody's CWL --
                # its local part is usually it. A hint for a person to check
                # out of band, never something to act on automatically: a
                # wrong guess hands this account to whoever really holds
                # that CWL the next time they sign in.
                self.stdout.write(
                    f"    {user.username:<28} last login {when:<16}"
                    f" {(user.email or '-'):<32} {note or ''}"
                )

        show("ready to move to CWL", ready)
        show("no CWL on file -- record one first, or archive", no_cwl)
        show(
            "a second account already holds that CWL -- merge by hand",
            already_bound,
        )
        show("superusers, not touched", supers)
        show("kept by name", kept)

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "\n  Nothing changed. Re-run with --apply to retire"
                    " these passwords."
                )
            )
            return

        if not ready:
            self.stdout.write("\n  nothing to do")
            return

        with transaction.atomic():
            for user, _ in ready:
                user.set_unusable_password()
                user.save(update_fields=["password"])

        self.stdout.write(
            self.style.SUCCESS(
                f"\n  retired {len(ready)} password(s). Those people sign in"
                f" with CWL now, which carries its own second factor, and"
                f" their accounts are otherwise untouched."
            )
        )
