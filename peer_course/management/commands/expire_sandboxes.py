"""Delete demonstration classes that have run out of time.

The pages that make and show a sandbox already sweep on their way past, so a
person clicking around clears the backlog. This is for the case nobody visits:
a demonstration given on Friday should not still be in the database on Monday.
Run it from cron or a timer alongside `manage_live_sessions`.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from peer_course import sandbox
from peer_course.models import Course


class Command(BaseCommand):
    help = "Delete expired demonstration classes and everything in them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Say what would be deleted, delete nothing.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        due = list(
            Course.objects.filter(is_sandbox=True, sandbox_expires_at__lte=now)
        )
        live = Course.objects.filter(
            is_sandbox=True, sandbox_expires_at__gt=now
        ).count()

        if options["dry_run"]:
            for course in due:
                students = course.members.filter(role="student").count()
                self.stdout.write(
                    f"  would delete {course.displayname!r} "
                    f"({students} students, expired "
                    f"{course.sandbox_expires_at:%Y-%m-%d %H:%M})"
                )
            self.stdout.write(f"{len(due)} expired, {live} still running")
            return

        removed = sandbox.expire_due(now)
        for name in removed:
            self.stdout.write(f"  deleted {name!r}")
        self.stdout.write(
            self.style.SUCCESS(f"{len(removed)} deleted, {live} still running")
        )
