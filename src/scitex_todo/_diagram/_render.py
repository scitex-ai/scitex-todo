#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mermaid -> PNG renderer.

Primary path: ``mmdc`` (mermaid-cli) with a puppeteer- or playwright-cached
chromium (auto-discovered; launched with ``--no-sandbox``). Snap chromium is
intentionally excluded — snap confinement breaks puppeteer's launch protocol.

Fallback path: ``kroki.io`` POST when ``mmdc`` is unavailable or fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class RenderError(RuntimeError):
    """Raised when every render path (mmdc, kroki) fails."""


def find_chromium() -> str | None:
    """Locate a puppeteer- or playwright-cached chromium for headless render.

    Resolution order: ``$PUPPETEER_EXECUTABLE_PATH``, then the puppeteer
    cache, then the playwright cache. Snap chromium is excluded.

    Returns
    -------
    str or None
        Path to a chromium executable, or ``None`` if none is found.
    """
    env_path = os.environ.get("PUPPETEER_EXECUTABLE_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    # puppeteer cache: ~/.cache/puppeteer/chrome/<ver>/chrome-linux64/chrome
    pup_root = Path.home() / ".cache" / "puppeteer" / "chrome"
    if pup_root.is_dir():
        for cand in sorted(pup_root.glob("*/chrome-linux64/chrome"), reverse=True):
            if cand.exists():
                return str(cand)

    # playwright cache: ~/.cache/ms-playwright/chromium-*/chrome-linux/chrome
    pw_root = Path.home() / ".cache" / "ms-playwright"
    if pw_root.is_dir():
        for cand in sorted(
            pw_root.glob("chromium-*/chrome-linux/chrome"), reverse=True
        ):
            if cand.exists():
                return str(cand)

    return None


def render_with_mmdc(mermaid_src: str, out_png: str | Path) -> bool:
    """Render via mermaid-cli.

    Returns
    -------
    bool
        ``True`` on success, ``False`` to allow the caller to fall back.
    """
    out_png = Path(out_png)
    if shutil.which("mmdc") is None:
        sys.stderr.write("mmdc not found on PATH; skipping mmdc render\n")
        return False

    chromium = find_chromium()
    if chromium is None:
        sys.stderr.write(
            "No puppeteer/playwright chromium found; skipping mmdc render\n"
        )
        return False

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        mmd_path = tmp_dir / "graph.mmd"
        mmd_path.write_text(mermaid_src, encoding="utf-8")
        pptr_path = tmp_dir / "pptr.json"
        pptr_path.write_text(
            json.dumps({"args": ["--no-sandbox", "--disable-setuid-sandbox"]}),
            encoding="utf-8",
        )

        env = dict(os.environ)
        env["PUPPETEER_EXECUTABLE_PATH"] = chromium

        cmd = [
            "mmdc",
            "-i",
            str(mmd_path),
            "-o",
            str(out_png),
            "-b",
            "white",
            "-p",
            str(pptr_path),
            "-s",
            "2",
        ]
        try:
            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, timeout=120
            )
        except subprocess.TimeoutExpired:
            sys.stderr.write("mmdc render timed out\n")
            return False

        if result.returncode != 0:
            sys.stderr.write(
                f"mmdc render failed (rc={result.returncode}):\n{result.stderr}\n"
            )
            return False

    return out_png.exists() and out_png.stat().st_size > 0


def render_with_kroki(mermaid_src: str, out_png: str | Path) -> bool:
    """Fallback: render via kroki.io.

    Returns
    -------
    bool
        ``True`` on success, ``False`` on any network/HTTP failure.
    """
    out_png = Path(out_png)
    try:
        import urllib.request

        req = urllib.request.Request(
            "https://kroki.io/mermaid/png",
            data=mermaid_src.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status != 200:
                sys.stderr.write(f"kroki.io returned status {resp.status}\n")
                return False
            payload = resp.read()
    except Exception as exc:  # noqa: BLE001 — fallback must report and bail
        sys.stderr.write(f"kroki.io render failed: {exc}\n")
        return False

    out_png.write_bytes(payload)
    return out_png.exists() and out_png.stat().st_size > 0


def render(mermaid_src: str, out_png: str | Path) -> str:
    """Render mermaid source to PNG, mmdc-first with kroki fallback.

    Parameters
    ----------
    mermaid_src : str
        Mermaid source, typically from :func:`scitex_todo.build_mermaid`.
    out_png : str or pathlib.Path
        Destination PNG path. Parent directories are created if missing.

    Returns
    -------
    str
        The engine that produced the output: ``"mmdc"`` or ``"kroki"``.

    Raises
    ------
    RenderError
        If both the mmdc and kroki render paths fail.
    """
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if render_with_mmdc(mermaid_src, out_png):
        return "mmdc"
    sys.stderr.write("Falling back to kroki.io\n")
    if render_with_kroki(mermaid_src, out_png):
        return "kroki"
    raise RenderError("Both mmdc and kroki.io render paths failed")


# EOF
