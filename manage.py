#!/usr/bin/env python3
"""Django's command-line utility for Agora."""

import os
import sys


def main():
    """Run an Agora management command using the environment-driven settings."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mta2.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Install requirements_new.txt and activate "
            "the Agora virtual environment before running this command."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
