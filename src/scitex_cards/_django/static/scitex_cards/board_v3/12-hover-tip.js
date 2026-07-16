/* 12-hover-tip.js — cursor-offset hover tooltip.
 *
 * Operator 2026-07-10 (msg 944): "scatter の上にカーソルを合わせて出てくる
 * ポップアップがカーソルにかぶって読みにくいの UX として最悪".
 *
 * The timeline dots used SVG <title> and the wall notes used title="". Both
 * render the browser's NATIVE tooltip, which appears UNDER THE CURSOR — it
 * covers the very dot you are pointing at, and its position cannot be
 * changed from CSS or JS. The only fix is to stop using native tooltips.
 *
 * This module renders one reusable <div> that trails the cursor with a fixed
 * offset and flips near the viewport edges so it never runs off-screen and
 * never sits under the pointer. Any element with `data-tip` gets it, in any
 * view — a single delegated listener, so new views inherit it for free.
 *
 * Pure DOM, no dependencies. Publishes window.STX.hoverTip mainly so tests
 * (and future views) can call `place()` directly.
 */
"use strict";

(function (global) {
  var OFFSET_X = 16; // right of the cursor — never beneath it
  var OFFSET_Y = 18; // below the cursor
  var EDGE_PAD = 8;

  var _el = null;

  function _ensure(doc) {
    if (_el && _el.isConnected) return _el;
    _el = doc.createElement("div");
    _el.className = "stx-tip";
    _el.setAttribute("role", "tooltip");
    _el.setAttribute("aria-hidden", "true");
    doc.body.appendChild(_el);
    return _el;
  }

  /* Choose a position that keeps the tip fully on-screen AND never under the
   * cursor. Returns {left, top, flippedX, flippedY} so it is unit-testable
   * without a DOM. */
  function place(cursorX, cursorY, tipW, tipH, viewW, viewH) {
    var left = cursorX + OFFSET_X;
    var top = cursorY + OFFSET_Y;
    var flippedX = false;
    var flippedY = false;
    if (left + tipW + EDGE_PAD > viewW) {
      left = cursorX - OFFSET_X - tipW; // flip to the cursor's left
      flippedX = true;
    }
    if (top + tipH + EDGE_PAD > viewH) {
      top = cursorY - OFFSET_Y - tipH; // flip above the cursor
      flippedY = true;
    }
    if (left < EDGE_PAD) left = EDGE_PAD;
    if (top < EDGE_PAD) top = EDGE_PAD;
    return { left: left, top: top, flippedX: flippedX, flippedY: flippedY };
  }

  function _show(doc, target, ev) {
    var text = target.getAttribute("data-tip");
    if (!text) return;
    var el = _ensure(doc);
    // Multi-line tips: the payload uses \n (dot titles carry started/ended).
    el.textContent = text;
    el.style.visibility = "hidden";
    el.classList.add("stx-tip--on");
    var r = el.getBoundingClientRect();
    var p = place(
      ev.clientX,
      ev.clientY,
      r.width,
      r.height,
      global.innerWidth,
      global.innerHeight,
    );
    el.style.left = p.left + "px";
    el.style.top = p.top + "px";
    el.style.visibility = "";
    el.setAttribute("aria-hidden", "false");
  }

  function _hide() {
    if (!_el) return;
    _el.classList.remove("stx-tip--on");
    _el.setAttribute("aria-hidden", "true");
  }

  function attach(doc) {
    doc = doc || global.document;
    if (!doc || !doc.addEventListener) return;
    doc.addEventListener("mouseover", function (ev) {
      var t = ev.target.closest && ev.target.closest("[data-tip]");
      if (t) _show(doc, t, ev);
    });
    doc.addEventListener("mousemove", function (ev) {
      if (!_el || !_el.classList.contains("stx-tip--on")) return;
      var t = ev.target.closest && ev.target.closest("[data-tip]");
      if (!t) return _hide();
      var r = _el.getBoundingClientRect();
      var p = place(
        ev.clientX,
        ev.clientY,
        r.width,
        r.height,
        global.innerWidth,
        global.innerHeight,
      );
      _el.style.left = p.left + "px";
      _el.style.top = p.top + "px";
    });
    doc.addEventListener("mouseout", function (ev) {
      var t = ev.target.closest && ev.target.closest("[data-tip]");
      if (t) _hide();
    });
    // A scroll or a click should never leave a stale tip floating.
    doc.addEventListener("scroll", _hide, true);
    doc.addEventListener("click", _hide, true);
  }

  global.STX = global.STX || {};
  global.STX.hoverTip = { place: place, attach: attach, OFFSET_X: OFFSET_X, OFFSET_Y: OFFSET_Y };

  if (global.document) {
    if (global.document.readyState === "loading") {
      global.document.addEventListener("DOMContentLoaded", function () {
        attach(global.document);
      });
    } else {
      attach(global.document);
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
