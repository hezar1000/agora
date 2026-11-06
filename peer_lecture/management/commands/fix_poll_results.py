# In peer_lecture/management/commands/fix_poll_results.py

from django.core.management.base import BaseCommand
from django.db import transaction
from peer_lecture.models import PollResult
import json

class Command(BaseCommand):
    help = 'Converts PollResult.answer CharField values into a state ready for JSONField migration.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting the process to fix PollResult answer data..."))

        fixed_count = 0
        with transaction.atomic():
            # We iterate through every PollResult to check and fix its answer
            for result in PollResult.objects.all():
                original_answer = result.answer

                # We only need to fix values that are strings. NULL is fine.
                if isinstance(original_answer, str):
                    fixed_count += 1
                    self.stdout.write(f"  Fixing PollResult ID: {result.pk}, Original value: {repr(original_answer)}")

                    # Convert empty strings to '[]' and other strings to '["value"]'
                    if original_answer == "":
                        result.answer = json.dumps([])
                    else:
                        result.answer = json.dumps([original_answer])

                    result.save(update_fields=['answer'])

        self.stdout.write(self.style.SUCCESS(f"\nProcess complete. Fixed {fixed_count} poll result records."))
        self.stdout.write("The database data is now ready for the migration.")