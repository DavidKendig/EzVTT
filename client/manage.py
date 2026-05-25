#!/usr/bin/env python
"""Django management entry point for the EzVTT UI app."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ezvtt.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django isn't installed. Run the project via run.ps1 / run.sh, "
            "which provisions the virtual environment."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
