/* Operator↔agent DM chat view — behavior for templates/scitex_todo/chat.html.
 *
 * Minimal slice (card fleet-agent-direct-message-board-pane-20260707):
 *   - GET  /dm/threads              -> agent list + unread badges (poll ~10s)
 *   - GET  /dm/thread/<peer>?mark_read=1 -> open thread (poll ~5s)
 *   - POST /dm/thread/<peer>        -> compose (from=operator)
 *
 * Kept in a separate static file per the board's line-limit discipline
 * (js <512 lines). Plain browser JS, no build step, no dependencies.
 */
(function () {
  "use strict";

  var THREAD_POLL_MS = 5000;
  var LIST_POLL_MS = 10000;

  var state = {
    peer: null, // currently open peer name, or null
    lastCount: -1, // message count last rendered (skip redundant paints)
    timerThread: null,
    timerList: null,
  };

  var $agents = document.getElementById("agents");
  var $scrim = document.getElementById("scrim");
  var $menuBtn = document.getElementById("menu-btn");
  var $title = document.getElementById("thread-title");
  var $messages = document.getElementById("messages");
  var $form = document.getElementById("compose");
  var $body = document.getElementById("compose-body");
  var $send = document.getElementById("compose-send");
  var $errorBar = document.getElementById("error-bar");

  // ---- helpers -----------------------------------------------------------

  function showError(text) {
    $errorBar.textContent = text;
    $errorBar.style.display = "block";
  }

  function clearError() {
    $errorBar.style.display = "none";
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function shortTs(ts) {
    // "2026-07-07T09:15:02Z" -> "07-07 09:15" (local-enough for the floor).
    if (!ts) return "";
    var m = String(ts).match(/^\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2})/);
    return m ? m[1] + " " + m[2] : String(ts);
  }

  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(
      function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status + " on " + url);
        return resp.json();
      },
    );
  }

  // ---- agent list --------------------------------------------------------

  function renderAgents(agents) {
    $agents.textContent = "";
    if (!agents.length) {
      $agents.appendChild(
        el("div", "empty", "No agents registered and no threads yet."),
      );
      return;
    }
    agents.forEach(function (a) {
      var item = el("div", "agent" + (a.name === state.peer ? " active" : ""));
      var row1 = el("div", "row1");
      row1.appendChild(el("span", "name", a.name));
      if (a.unread > 0) row1.appendChild(el("span", "badge", String(a.unread)));
      item.appendChild(row1);
      var preview = a.last_body
        ? shortTs(a.last_ts) + "  " + a.last_body
        : a.kind
          ? a.kind
          : "no messages yet";
      item.appendChild(el("div", "preview", preview));
      item.addEventListener("click", function () {
        openThread(a.name);
        closeDrawer();
      });
      $agents.appendChild(item);
    });
  }

  function refreshAgents() {
    getJSON("/dm/threads")
      .then(function (data) {
        clearError();
        renderAgents(data.agents || []);
      })
      .catch(function (err) {
        showError("Agent list failed: " + err.message);
      });
  }

  // ---- thread pane -------------------------------------------------------

  function renderMessages(messages) {
    $messages.textContent = "";
    if (!messages.length) {
      $messages.appendChild(
        el("div", "hint", "No messages yet — say hello below."),
      );
      return;
    }
    messages.forEach(function (m) {
      var mine = m.from === "operator";
      var wrap = el("div", "msg " + (mine ? "from-operator" : "from-agent"));
      wrap.appendChild(el("div", "bubble", m.body || ""));
      wrap.appendChild(el("div", "meta", m.from + " · " + shortTs(m.ts)));
      $messages.appendChild(wrap);
    });
    $messages.scrollTop = $messages.scrollHeight;
  }

  function refreshThread(force) {
    if (!state.peer) return;
    var peer = state.peer;
    getJSON("/dm/thread/" + encodeURIComponent(peer) + "?mark_read=1")
      .then(function (data) {
        if (state.peer !== peer) return; // switched away mid-flight
        clearError();
        var msgs = data.messages || [];
        if (force || msgs.length !== state.lastCount) {
          state.lastCount = msgs.length;
          renderMessages(msgs);
        }
      })
      .catch(function (err) {
        showError("Thread failed: " + err.message);
      });
  }

  function openThread(peer) {
    state.peer = peer;
    state.lastCount = -1;
    $title.innerHTML = "";
    $title.appendChild(document.createTextNode("Thread with "));
    $title.appendChild(el("b", null, peer));
    $body.disabled = false;
    $send.disabled = false;
    $messages.textContent = "";
    $messages.appendChild(el("div", "hint", "Loading…"));
    refreshThread(true);
    refreshAgents(); // repaint the active highlight + clear the badge
    if (state.timerThread) clearInterval(state.timerThread);
    state.timerThread = setInterval(function () {
      refreshThread(false);
    }, THREAD_POLL_MS);
  }

  // ---- compose -----------------------------------------------------------

  function sendMessage(event) {
    event.preventDefault();
    if (!state.peer) return;
    var text = $body.value.trim();
    if (!text) return;
    $send.disabled = true;
    fetch("/dm/thread/" + encodeURIComponent(state.peer), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: text }),
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp
            .json()
            .catch(function () {
              return {};
            })
            .then(function (data) {
              throw new Error(data.error || "HTTP " + resp.status);
            });
        }
        $body.value = "";
        clearError();
        refreshThread(true);
        refreshAgents();
      })
      .catch(function (err) {
        showError("Send failed: " + err.message);
      })
      .then(function () {
        $send.disabled = false;
        $body.focus();
      });
  }

  // ---- mobile drawer -----------------------------------------------------

  function closeDrawer() {
    $agents.classList.remove("open");
    $scrim.classList.remove("open");
  }

  $menuBtn.addEventListener("click", function () {
    $agents.classList.toggle("open");
    $scrim.classList.toggle("open");
  });
  $scrim.addEventListener("click", closeDrawer);

  // Enter sends; Shift+Enter inserts a newline (phone keyboards send via
  // the button anyway — this is for desktop convenience).
  $body.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $form.requestSubmit();
    }
  });
  $form.addEventListener("submit", sendMessage);

  // ---- boot --------------------------------------------------------------

  refreshAgents();
  state.timerList = setInterval(refreshAgents, LIST_POLL_MS);
})();

/* EOF */
