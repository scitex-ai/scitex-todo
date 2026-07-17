#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verb ``scitex-cards serve`` — run the hub's authenticated RPC surface.

Thin wrapper over :func:`scitex_cards._server.make_server` (remote-hub PR-2,
docs/design/remote-hub-backend.md §3/§4). Loopback-only by construction —
there is deliberately NO bind-address flag in v1; remote hosts reach the
API through hub-initiated ssh reverse tunnels.

``--rotate-token`` mints a fresh ``hub.token`` and exits (tokens are
re-read per request, so the running server picks the new value up without
a restart; the old value stops authenticating immediately).
"""

from __future__ import annotations

import click


def register(main: click.Group) -> None:
    """Attach the ``serve`` verb to the root group."""
    main.add_command(serve_cmd)


@click.command(
    "serve",
    help=(
        "Run the hub RPC service: POST /v1/rpc/<verb> over the backend "
        "verb surface, bearer-authenticated, loopback-only.\n\n"
        "The store is PINNED at boot (requests can never retarget it); "
        "X-Scitex-Agent is required on every request; one JSONL audit "
        "line per request lands in ~/.scitex/cards/logs/hub_access.jsonl. "
        "GET /v1/health is the one public route.\n\n"
        "Examples:\n"
        "  scitex-cards serve --port 8765\n"
        "  scitex-cards serve --rotate-token"
    ),
)
@click.option("--port", default=8765, show_default=True, type=int)
@click.option(
    "--store",
    default=None,
    help="Task store path (default: the standard resolution chain).",
)
@click.option(
    "--rotate-token",
    is_flag=True,
    help="Mint a fresh hub.token (revoking the old) and exit.",
)
def serve_cmd(port: int, store: str | None, rotate_token: bool) -> None:
    from scitex_cards import _server

    if rotate_token:
        path = _server.mint_token(_server.default_tokens_dir(), "hub")
        click.echo(f"rotated: {path} (0600; old value no longer authenticates)")
        return

    server = _server.make_server(store=store, port=port)
    click.echo(
        f"scitex-cards serve on http://127.0.0.1:{port} "
        f"(store={store or 'resolved-default'}, "
        f"tokens={server.tokens_dir}, audit={server.audit_path})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("serve: interrupted, shutting down")
    finally:
        server.server_close()


# EOF
