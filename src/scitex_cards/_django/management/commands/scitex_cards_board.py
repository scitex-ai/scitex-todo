#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Management command to run the scitex-cards board standalone.

Usage:
    python -m django scitex_cards_board [--tasks PATH] [--port 8051]

Typically invoked via the ``scitex-cards board`` CLI verb.
"""

import os
import webbrowser

from django.core.management.base import BaseCommand


def _apply_tasks_env(tasks: str) -> None:
    """Export ``SCITEX_TODO_TASKS_YAML_SHARED=tasks`` when the operator passed
    ``--tasks PATH``.

    Lifted out of ``Command.handle`` so the env-precedence behaviour can be
    unit-tested without starting a Django ``runserver``. Empty string (the
    argparse default for a missing ``--tasks``) is a no-op so an inherited
    ``SCITEX_TODO_TASKS_YAML_SHARED`` keeps winning. A non-empty value overrides any
    inherited env (``os.environ[...]`` instead of ``setdefault``) to match
    the resolver's documented precedence: an explicit ``--tasks`` wins
    over ``$SCITEX_TODO_TASKS_YAML_SHARED`` wins over the project store.
    """
    if tasks:
        os.environ["SCITEX_TODO_TASKS_YAML_SHARED"] = tasks


class Command(BaseCommand):
    help = "Run the scitex-cards dependency-graph board as a standalone server"

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
            "--host",
            default="127.0.0.1",
            help="Bind address (default: 127.0.0.1). The board is "
            "UNAUTHENTICATED and serves every agent's cards — binding it "
            "off loopback exposes the whole store, so a wider bind must be "
            "asked for explicitly.",
        )
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Don't open a browser automatically.",
        )

    def handle(self, *args, **options):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_cards._django.settings")

        tasks = options["tasks"]
        port = options["port"]
        # When the operator passes ``--tasks PATH``, export
        # ``SCITEX_TODO_TASKS_YAML_SHARED=PATH`` so the in-process Django views (and any
        # subprocess they fork) actually resolve to that store. Without this
        # the server fell through the project-store -> user-store -> bundled
        # fallback chain (``resolve_store_path``) and silently ignored
        # ``--tasks`` whenever a ``.scitex/todo/tasks.yaml`` existed at the
        # git-root -- the ``?store=`` query-string we add below only hints
        # the browser, it never reaches the resolver. The helper uses
        # ``os.environ[...]`` (NOT ``setdefault``) so an explicit CLI value
        # wins over any stale inherited env var, matching the resolver
        # precedence documented in ``scitex-cards --help`` ("an explicit
        # --tasks path, then $SCITEX_TODO_TASKS_YAML_SHARED, then the project store, ...").
        _apply_tasks_env(tasks)
        host = options.get("host") or "127.0.0.1"
        url = f"http://{host}:{port}/"
        if tasks:
            url += f"?store={tasks}"

        if not options["no_browser"]:
            import threading

            threading.Timer(1.0, webbrowser.open, args=[url]).start()

        self.stdout.write(f"SciTeX Todo Board running at {url}")
        self.stdout.write("Press Ctrl+C to stop")

        from django.core.management import call_command

        call_command("runserver", f"{host}:{port}", "--noreload")


# EOF
