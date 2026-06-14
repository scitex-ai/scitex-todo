/** Fleet host-geometry panel — Phase 2 surface of the FLEET DASHBOARD.
 *
 * Renders the LOCAL host (name + interface count + peer count) read
 * live from ``sac host list --json``. The full interface list lives in
 * the tooltip so the panel itself stays compact next to the CI-status
 * pills strip.
 *
 * Polls ``/fleet/hosts`` every 30s (same cadence as ``FleetCiPills``).
 * On adapter error the panel renders a red ``!`` icon and surfaces the
 * back-end error message in ``title``.
 *
 * NO hardcoded proper nouns — the hostname / interface / peer values
 * all come from the back-end registry response.
 *
 * The component is intentionally self-contained — the back-end shape
 * (see ``handlers/fleet/sac_hosts.py``) is the only contract.
 */

import { useEffect, useState } from "react";

/** Shape of one interface row inside ``local.interfaces``. */
export interface HostInterface {
  iface: string;
  addr: string;
  family: string;
}

/** Shape of the ``local`` sub-document — what ``sac host list --json``
 * emits today. We deliberately don't tighten the type further — the
 * registry payload is the contract and the FE adapts to upstream
 * field additions without a TS rebuild. */
export interface HostLocal {
  name: string;
  scope?: string;
  aliases?: Record<string, string>;
  interfaces?: HostInterface[];
}

/** Successful payload shape. */
export interface HostsPayloadOk {
  config_path: string | null;
  local: HostLocal;
  peers: unknown[];
}

/** Adapter-failure payload shape (HTTP 500 body). */
export interface HostsPayloadErr {
  error: string;
}

export type HostsPayload = HostsPayloadOk | HostsPayloadErr;

/** Discriminator. Kept outside the component for trivial node testing. */
export function isHostsPayloadErr(p: HostsPayload): p is HostsPayloadErr {
  return Object.prototype.hasOwnProperty.call(p, "error");
}

/** Build the compact label rendered inside the panel pill. The pattern
 * mirrors the operator's spec verbatim:
 *
 *     🖥 <hostname> · <N> ifaces · <M> peers
 *
 * Empty hostname (defensive — the adapter raises before we get here in
 * the OK path) degrades to ``(unknown host)`` rather than emitting a
 * misleading blank.
 */
export function hostsPanelLabel(p: HostsPayloadOk): string {
  const name = p.local.name || "(unknown host)";
  const ifaceCount = Array.isArray(p.local.interfaces)
    ? p.local.interfaces.length
    : 0;
  const peerCount = Array.isArray(p.peers) ? p.peers.length : 0;
  return `🖥 ${name} · ${ifaceCount} ifaces · ${peerCount} peers`;
}

/** Build the full-interface tooltip text. Each interface is rendered
 * on its own line; the family is included so the operator can tell
 * IPv4 from IPv6 at a glance. Empty list yields ``(no interfaces)`` —
 * useful diagnostic when the host machine has no NICs visible to sac
 * (containerized environments, etc.). */
export function hostsPanelTooltip(p: HostsPayloadOk): string {
  const lines: string[] = [];
  lines.push(`host: ${p.local.name || "(unknown)"}`);
  if (p.local.scope) lines.push(`scope: ${p.local.scope}`);
  if (p.config_path) lines.push(`config: ${p.config_path}`);
  const ifaces = Array.isArray(p.local.interfaces) ? p.local.interfaces : [];
  if (ifaces.length === 0) {
    lines.push("interfaces: (none)");
  } else {
    lines.push("interfaces:");
    for (const i of ifaces) {
      lines.push(`  ${i.iface || "?"} ${i.addr} (${i.family})`);
    }
  }
  const peerCount = Array.isArray(p.peers) ? p.peers.length : 0;
  lines.push(`peers: ${peerCount}`);
  return lines.join("\n");
}

const POLL_MS = 30_000;
const ENDPOINT = "/fleet/hosts";

interface State {
  payload: HostsPayloadOk | null;
  /** Set when the endpoint itself fails (network down, 500 from a
   * missing-sac / malformed-registry condition). The panel renders a
   * red ``!`` icon with the message in the tooltip. */
  adapterError: string | null;
}

/** Top-level component — mounted by ``TodoBoard.tsx`` next to the
 * ``FleetCiPills`` strip in the board header. */
export function FleetHostsPanel() {
  const [state, setState] = useState<State>({
    payload: null,
    adapterError: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const res = await fetch(ENDPOINT, { credentials: "same-origin" });
        if (!res.ok) {
          // The view returns 500 when the adapter raises (sac missing,
          // malformed registry, …). Surface the body verbatim in the
          // tooltip so the operator can copy-paste and reproduce.
          let errBody = `HTTP ${res.status}`;
          try {
            const data = (await res.json()) as HostsPayloadErr;
            if (typeof data?.error === "string" && data.error.length > 0) {
              errBody = data.error;
            }
          } catch {
            /* fall through to the HTTP-status default */
          }
          if (cancelled) return;
          setState({ payload: null, adapterError: errBody });
          return;
        }
        const data: HostsPayloadOk = await res.json();
        if (cancelled) return;
        setState({ payload: data, adapterError: null });
      } catch (err) {
        if (cancelled) return;
        setState({
          payload: null,
          adapterError: err instanceof Error ? err.message : String(err),
        });
      }
    }

    void tick();
    const handle = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, []);

  if (state.adapterError) {
    // Fail-loud rendering: a single compact pill with the error in the
    // tooltip. The ``!`` marker mirrors the CI-pills per-repo error
    // affordance so the operator's eye recognizes the pattern.
    return (
      <span
        className="stx-todo-fleet-hosts stx-todo-fleet-hosts--error"
        title={state.adapterError}
        role="group"
        aria-label="Fleet host geometry — adapter error"
      >
        <span className="stx-todo-fleet-hosts__label">🖥 (no host data)</span>
        <span className="stx-todo-fleet-hosts__dot" aria-hidden="true">
          !
        </span>
      </span>
    );
  }

  if (!state.payload) {
    // Initial load — render an invisible placeholder so the toolbar
    // grid doesn't reflow on first paint.
    return (
      <span
        className="stx-todo-fleet-hosts stx-todo-fleet-hosts--loading"
        aria-hidden="true"
      />
    );
  }

  return (
    <span
      className="stx-todo-fleet-hosts stx-todo-fleet-hosts--ok"
      title={hostsPanelTooltip(state.payload)}
      role="group"
      aria-label="Fleet host geometry"
    >
      <span className="stx-todo-fleet-hosts__label">
        {hostsPanelLabel(state.payload)}
      </span>
    </span>
  );
}

export default FleetHostsPanel;
