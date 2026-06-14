/** Fleet agent-mesh + ACL graph panel — Phase 3 surface of the FLEET
 * DASHBOARD.
 *
 * Renders the directed agent mesh:
 *   - nodes  = agents (local Registry + peer comms-nodes) read live
 *              from ``sac a2a list --json``
 *   - edges  = ``comms_grants`` ACL rows read live from
 *              ``sac a2a grants --json``; ALLOW edges are green, DENY
 *              edges (phase-3.b) muted red.
 *
 * The graph is laid out in a SIMPLE RADIAL pattern — agents placed
 * evenly around a circle — so we ship without a force-directed layout
 * dep. Click an edge to surface ``source → target (allow|deny)`` in a
 * compact tooltip; the on-hover ``title`` carries the audit ``note``.
 *
 * Polls ``/fleet/mesh`` every 30s (same cadence as ``FleetHostsPanel``).
 * On adapter error the panel renders ``🕸 (no mesh data)`` + a red
 * ``!`` and surfaces the back-end error in ``title``.
 *
 * NO hardcoded proper nouns — every agent label comes from the
 * back-end registry response. NO hardcoded colors — ``allow`` and
 * ``deny`` map to design-token CSS classes (status-success /
 * status-error) via :func:`edgeColorToken`.
 */

import { useEffect, useMemo, useState } from "react";

/** One agent node, mirrors the back-end shape. */
export interface MeshAgent {
  name: string;
  scope: "local" | "peer";
  status?: "online" | "offline" | "unknown";
}

/** One directed edge, mirrors the back-end shape. */
export interface MeshEdge {
  source: string;
  target: string;
  allow: boolean;
  note?: string;
}

/** Successful mesh payload shape. */
export interface MeshPayloadOk {
  agents: MeshAgent[];
  edges: MeshEdge[];
  config_path: string | null;
  source_versions: { peers: string; grants: string };
}

/** Adapter-failure payload shape (HTTP 500 body). */
export interface MeshPayloadErr {
  error: string;
}

export type MeshPayload = MeshPayloadOk | MeshPayloadErr;

/** Discriminator — kept outside the component for trivial node-side
 * testing in lock-step with the FleetHostsPanel pattern. */
export function isMeshPayloadErr(p: MeshPayload): p is MeshPayloadErr {
  return Object.prototype.hasOwnProperty.call(p, "error");
}

/** Map an edge's ``allow`` flag to the CSS-token class the FE applies.
 *
 * This is the SINGLE point where allow/deny becomes a visual token —
 * the helper is exported so the contract test can lock the mapping
 * without a TS rebuild.
 *
 * - ``allow: true``  → ``stx-todo-fleet-mesh__edge--allow`` (green
 *   ``--status-success`` border / stroke).
 * - ``allow: false`` → ``stx-todo-fleet-mesh__edge--deny`` (muted red
 *   ``--status-error`` border / stroke).
 *
 * NO hex literals here — the actual color value lives in
 * ``fleet-mesh.css`` and resolves against board.css design tokens.
 */
export function edgeColorToken(allow: boolean): string {
  return allow
    ? "stx-todo-fleet-mesh__edge--allow"
    : "stx-todo-fleet-mesh__edge--deny";
}

/** Compact panel label: ``🕸 <N> agents · <M> grants``. The number of
 * grants is the raw edge count (not deduped) so the operator can see
 * the ACL row total at a glance. */
export function meshPanelLabel(p: MeshPayloadOk): string {
  const a = Array.isArray(p.agents) ? p.agents.length : 0;
  const e = Array.isArray(p.edges) ? p.edges.length : 0;
  return `🕸 ${a} agents · ${e} grants`;
}

/** Tooltip body — full mesh summary, one line per agent + one line
 * per edge. The audit ``note`` surfaces verbatim so the operator
 * can match a grant to the ticket it was created under. */
export function meshPanelTooltip(p: MeshPayloadOk): string {
  const lines: string[] = [];
  const agents = Array.isArray(p.agents) ? p.agents : [];
  const edges = Array.isArray(p.edges) ? p.edges : [];
  lines.push(`agents: ${agents.length}`);
  for (const a of agents) {
    const status = a.status ? ` [${a.status}]` : "";
    lines.push(`  ${a.name} (${a.scope})${status}`);
  }
  lines.push(`grants: ${edges.length}`);
  for (const e of edges) {
    const tag = e.allow ? "allow" : "deny";
    const note = e.note ? ` — ${e.note}` : "";
    lines.push(`  ${e.source} → ${e.target} [${tag}]${note}`);
  }
  if (p.config_path) lines.push(`config: ${p.config_path}`);
  return lines.join("\n");
}

/** Place ``names.length`` points evenly on a circle of radius ``r``
 * centered at ``(cx, cy)``. Pure function; exported so the contract
 * test pins the layout deterministically.
 *
 * The first node lands at the TOP of the circle (12-o'clock) so the
 * operator's eye anchors on a consistent reference frame across
 * polls. Single-node case lands at the centre; two-node case spans
 * a horizontal diameter.
 */
export function radialLayout(
  names: string[],
  opts: { cx: number; cy: number; r: number },
): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>();
  const n = names.length;
  if (n === 0) return out;
  if (n === 1) {
    out.set(names[0], { x: opts.cx, y: opts.cy });
    return out;
  }
  for (let i = 0; i < n; i++) {
    // -π/2 starts at 12-o'clock; sweep clockwise.
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
    const x = opts.cx + opts.r * Math.cos(angle);
    const y = opts.cy + opts.r * Math.sin(angle);
    out.set(names[i], { x, y });
  }
  return out;
}

const POLL_MS = 30_000;
const ENDPOINT = "/fleet/mesh";

interface State {
  payload: MeshPayloadOk | null;
  /** Set when the endpoint itself fails. The panel renders a red
   * ``!`` icon with the message in the tooltip. */
  adapterError: string | null;
  /** Currently-selected edge index for the click-to-inspect tooltip;
   * ``null`` means no edge is selected. */
  selectedEdge: number | null;
}

/** SVG viewport geometry — small (fits in the toolbar) but readable.
 * The layout helper centres on (cx, cy) with radius r. */
const SVG_SIZE = 140;
const SVG_CENTER = SVG_SIZE / 2;
const SVG_RADIUS = SVG_SIZE / 2 - 18;

/** Top-level component — mounted by ``TodoBoard.tsx`` next to the
 * ``FleetHostsPanel`` in the board header's STATUS group. */
export function FleetMeshPanel() {
  const [state, setState] = useState<State>({
    payload: null,
    adapterError: null,
    selectedEdge: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const res = await fetch(ENDPOINT, { credentials: "same-origin" });
        if (!res.ok) {
          let errBody = `HTTP ${res.status}`;
          try {
            const data = (await res.json()) as MeshPayloadErr;
            if (typeof data?.error === "string" && data.error.length > 0) {
              errBody = data.error;
            }
          } catch {
            /* fall through to the HTTP-status default */
          }
          if (cancelled) return;
          setState({
            payload: null,
            adapterError: errBody,
            selectedEdge: null,
          });
          return;
        }
        const data: MeshPayloadOk = await res.json();
        if (cancelled) return;
        setState((prev) => ({
          payload: data,
          adapterError: null,
          // Preserve the operator's edge selection across polls — but
          // bound it to the new edge count so an out-of-range index
          // doesn't render stale tooltip data.
          selectedEdge:
            prev.selectedEdge !== null &&
            data.edges &&
            prev.selectedEdge < data.edges.length
              ? prev.selectedEdge
              : null,
        }));
      } catch (err) {
        if (cancelled) return;
        setState({
          payload: null,
          adapterError: err instanceof Error ? err.message : String(err),
          selectedEdge: null,
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

  // Compute the node positions once per payload — pure function of the
  // agent name list, so useMemo is the right tool.
  const positions = useMemo(() => {
    if (!state.payload) return new Map<string, { x: number; y: number }>();
    return radialLayout(
      state.payload.agents.map((a) => a.name),
      { cx: SVG_CENTER, cy: SVG_CENTER, r: SVG_RADIUS },
    );
  }, [state.payload]);

  if (state.adapterError) {
    return (
      <span
        className="stx-todo-fleet-mesh stx-todo-fleet-mesh--error"
        title={state.adapterError}
        role="group"
        aria-label="Fleet agent mesh — adapter error"
      >
        <span className="stx-todo-fleet-mesh__label">🕸 (no mesh data)</span>
        <span className="stx-todo-fleet-mesh__dot" aria-hidden="true">
          !
        </span>
      </span>
    );
  }

  if (!state.payload) {
    return (
      <span
        className="stx-todo-fleet-mesh stx-todo-fleet-mesh--loading"
        aria-hidden="true"
      />
    );
  }

  const payload = state.payload;
  const selectedEdge =
    state.selectedEdge !== null && payload.edges[state.selectedEdge]
      ? payload.edges[state.selectedEdge]
      : null;

  return (
    <span
      className="stx-todo-fleet-mesh stx-todo-fleet-mesh--ok"
      title={meshPanelTooltip(payload)}
      role="group"
      aria-label="Fleet agent mesh"
    >
      <span className="stx-todo-fleet-mesh__label">
        {meshPanelLabel(payload)}
      </span>
      <svg
        className="stx-todo-fleet-mesh__svg"
        width={SVG_SIZE}
        height={SVG_SIZE}
        viewBox={`0 0 ${SVG_SIZE} ${SVG_SIZE}`}
        role="img"
        aria-label="Agent mesh graph"
      >
        {/* Edges first so node circles paint over edge endpoints. */}
        {payload.edges.map((edge, i) => {
          const a = positions.get(edge.source);
          const b = positions.get(edge.target);
          if (!a || !b) return null;
          return (
            <line
              key={`e${i}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              className={`stx-todo-fleet-mesh__edge ${edgeColorToken(edge.allow)}`}
              onClick={() =>
                setState((prev) => ({
                  ...prev,
                  selectedEdge: prev.selectedEdge === i ? null : i,
                }))
              }
              aria-label={`${edge.source} to ${edge.target} (${edge.allow ? "allow" : "deny"})`}
            >
              <title>{`${edge.source} → ${edge.target} (${edge.allow ? "allow" : "deny"})${edge.note ? ` — ${edge.note}` : ""}`}</title>
            </line>
          );
        })}
        {payload.agents.map((agent) => {
          const p = positions.get(agent.name);
          if (!p) return null;
          return (
            <g key={`n-${agent.name}`} transform={`translate(${p.x},${p.y})`}>
              <circle
                r={5}
                className={`stx-todo-fleet-mesh__node stx-todo-fleet-mesh__node--${agent.scope}`}
              >
                <title>{`${agent.name} (${agent.scope}${agent.status ? `, ${agent.status}` : ""})`}</title>
              </circle>
            </g>
          );
        })}
      </svg>
      {/* Legend — minimal, two swatches: allow / deny. */}
      <span className="stx-todo-fleet-mesh__legend" aria-label="Edge legend">
        <span className="stx-todo-fleet-mesh__legend-swatch stx-todo-fleet-mesh__edge--allow" />
        <span className="stx-todo-fleet-mesh__legend-text">allow</span>
        <span className="stx-todo-fleet-mesh__legend-swatch stx-todo-fleet-mesh__edge--deny" />
        <span className="stx-todo-fleet-mesh__legend-text">deny</span>
      </span>
      {selectedEdge ? (
        <span
          className="stx-todo-fleet-mesh__edge-tooltip"
          role="status"
          aria-live="polite"
        >
          {`${selectedEdge.source} → ${selectedEdge.target} (${selectedEdge.allow ? "allow" : "deny"})`}
        </span>
      ) : null}
    </span>
  );
}

export default FleetMeshPanel;
