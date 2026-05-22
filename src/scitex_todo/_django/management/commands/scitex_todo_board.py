#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Management command to run the scitex-todo board standalone.

Usage:
    python -m django scitex_todo_board [--tasks PATH] [--port 8051]

Typically invoked via the ``scitex-todo board`` CLI verb.
"""

import os
import webbrowser

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the scitex-todo dependency-graph board as a standalone server"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tasks",
            dest="tasks",
            default="",
            help="Path to tasks.yaml (default: project -> user -> bundled example).",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8051,
            help="Server port (default: 8051).",
        )
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Don't open a browser automatically.",
        )

    def handle(self, *args, **options):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_todo._django.settings")

        tasks = options["tasks"]
        port = options["port"]
        url = f"http://127.0.0.1:{port}/"
        if tasks:
            url += f"?store={tasks}"

        if not options["no_browser"]:
            import threading

            threading.Timer(1.0, webbrowser.open, args=[url]).start()

        self.stdout.write(f"SciTeX Todo Board running at {url}")
        self.stdout.write("Press Ctrl+C to stop")

        from django.core.management import call_command

        call_command("runserver", f"127.0.0.1:{port}", "--noreload")


# EOF
