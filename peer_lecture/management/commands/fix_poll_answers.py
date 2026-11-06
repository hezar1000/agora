# In peer_lecture/management/commands/fix_poll_answers.py

from django.core.management.base import BaseCommand
from django.db import transaction
from peer_lecture.models import Poll
import json

class Command(BaseCommand):
    help = 'Converts all Poll.answer CharField values into a state ready for JSONField migration.'

    def handle(self, *args, **options):
        self.stdout.write("Starting the process to fix Poll answer data...")

        fixed_count = 0
        with transaction.atomic():
            # We iterate through every poll to check and fix its answer
            for poll in Poll.objects.all():
                original_answer = poll.answer
                needs_fixing = False

                # Determine if this row needs to be updated
                if original_answer is None:
                    # It's NULL, which is fine. No change needed.
                    pass
                elif isinstance(original_answer, str):
                    # It's a string, which is not valid JSON. It MUST be fixed.
                    needs_fixing = True

                if needs_fixing:
                    fixed_count += 1
                    self.stdout.write(f"  Fixing Poll ID: {poll.pk}, Original value: {repr(original_answer)}")

                    # Brute-force conversion to a valid JSON-serializable list
                    if original_answer == "":
                        poll.answer = json.dumps([]) # Store as the string '[]'
                    else:
                        poll.answer = json.dumps([original_answer]) # Store as '["value"]'

                    poll.save(update_fields=['answer'])

        self.stdout.write(self.style.SUCCESS(f"\nProcess complete. Fixed {fixed_count} poll records."))
        self.stdout.write("The database data is now ready for the migration.")