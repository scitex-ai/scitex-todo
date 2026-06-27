#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Concrete delivery channels shipped inside scitex-todo.

Slice 1 ships exactly one: :class:`scitex_todo._delivery._channels.log.\
LogChannel` — a creds-free stdlib-logging sink registered under the
``scitex_todo.delivery_channels`` entry-point group. Later slices add real
transports (telegram, email) as additional channels in this package.
"""

# EOF
