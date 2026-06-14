/* searchSuggest.ts — TypeScript mirror of the vanilla
 * `static/scitex_todo/board_v3/searchSuggest.js` autocomplete engine,
 * consumed by the React `SearchAutocomplete.tsx` component.
 *
 * Operator pain (TG 12318, lead a2a `e09e0c886eb94e509f8daa87c23dca2a`,
 * 2026-06-12): wants GitHub-style autocomplete on the qualifier search.
 * Extends PR #102 (searchQuery.{ts,js}) — same vocabulary + closed enums,
 * imported here rather than redefined.
 *
 * Pure module — no React / DOM / fetch. The implementation tracks the
 * `.js` sibling line-for-line; the JS file is the canonical source the
 * board_v3 vanilla template loads as a <script>.
 */

import { KNOWN_QUALIFIER_NAMES, QUALIFIERS } from "./searchQuery";

export type SuggestKind = "key" | "value";

export interface Suggestion {
  kind: SuggestKind;
  label: string;
  value: string;
  count?: number;
  hint?: string;
}

export interface TokenAtCursor {
  prefix: string;
  token: string;
  tokenAfter: string;
  suffix: string;
  kind: SuggestKind;
  qualifierKey?: string;
}

export interface SuggestNode {
  id?: string | null;
  project?: string | null;
  repo?: string | null;
  agent?: string | null;
  assignee?: string | null;
  scope?: string | null;
  parent?: string | null;
  host?: string | null;
  status?: string | null;
  kind?: string | null;
  priority?: number | null;
}

export interface SuggestDataSource {
  nodes: SuggestNode[];
}

export const KEY_HINTS: Record<string, string> = {
  project: "qualify by project",
  repo: "alias of project:",
  agent: "qualify by agent",
  assignee: "alias of agent:",
  status: "qualify by status (enum)",
  kind: "qualify by kind (enum)",
  parent: "qualify by parent task id",
  scope: "qualify by scope",
  id: "qualify by task id (substring)",
  host: "qualify by compute host",
  priority: "qualify by priority (#, <N, >=N, …)",
};

export function tokenAtCursor(
  query: string | null | undefined,
  cursorPos: number,
): TokenAtCursor {
  const q = String(query == null ? "" : query);
  const len = q.length;
  const pos = Math.max(
    0,
    Math.min(len, Number.isFinite(cursorPos) ? cursorPos : len),
  );
  let start = pos;
  while (start > 0 && !/\s/.test(q[start - 1])) start--;
  let end = pos;
  while (end < q.length && !/\s/.test(q[end])) end++;
  const tokRun = q.slice(start, end);
  const prefix = q.slice(0, start);
  const suffix = q.slice(end);
  const relCursor = pos - start;
  const beforeCursor = tokRun.slice(0, relCursor);
  const afterCursor = tokRun.slice(relCursor);
  const colonIdx = beforeCursor.indexOf(":");
  if (colonIdx >= 0) {
    const key = beforeCursor.slice(0, colonIdx).toLowerCase();
    let valBefore = beforeCursor.slice(colonIdx + 1);
    let valAfter = afterCursor;
    if (valBefore.startsWith('"')) valBefore = valBefore.slice(1);
    if (valAfter.endsWith('"')) valAfter = valAfter.slice(0, -1);
    return {
      prefix,
      token: valBefore,
      tokenAfter: valAfter,
      suffix,
      kind: "value",
      qualifierKey: key,
    };
  }
  return {
    prefix,
    token: beforeCursor,
    tokenAfter: afterCursor,
    suffix,
    kind: "key",
  };
}

export function keySuggestions(
  prefix: string | null | undefined,
): Suggestion[] {
  const p = String(prefix == null ? "" : prefix).toLowerCase();
  const out: Suggestion[] = [];
  for (const key of KNOWN_QUALIFIER_NAMES) {
    if (p === "" || key.startsWith(p)) {
      out.push({
        kind: "key",
        label: key + ":",
        value: key + ":",
        hint: KEY_HINTS[key] || "",
      });
    }
  }
  out.sort((a, b) => a.label.localeCompare(b.label));
  return out;
}

function _harvestField(
  dataSource: SuggestDataSource | null | undefined,
  fields: string[],
): { value: string; count: number }[] {
  const nodes = (dataSource && dataSource.nodes) || [];
  const counts = new Map<string, number>();
  for (const n of nodes) {
    for (const f of fields) {
      const v = (n as Record<string, unknown>)[f];
      if (v == null || v === "") continue;
      const s = String(v);
      counts.set(s, (counts.get(s) || 0) + 1);
    }
  }
  const out: { value: string; count: number }[] = [];
  for (const [value, count] of counts) out.push({ value, count });
  out.sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
  return out;
}

function _matchesPrefix(
  value: string,
  prefix: string | null | undefined,
): boolean {
  if (!prefix) return true;
  return String(value).toLowerCase().includes(String(prefix).toLowerCase());
}

export function valueSuggestions(
  qualifierKey: string | null | undefined,
  prefix: string | null | undefined,
  dataSource: SuggestDataSource | null | undefined,
): Suggestion[] {
  const key = String(qualifierKey || "").toLowerCase();
  const spec = QUALIFIERS[key];
  if (!spec) return [];
  const p = String(prefix == null ? "" : prefix);

  let raw: { value: string; count?: number; hint?: string }[] = [];
  if (spec.strategy === "enum") {
    for (const v of spec.enum || []) raw.push({ value: v });
    raw.sort((a, b) => a.value.localeCompare(b.value));
  } else if (key === "priority") {
    const seen = _harvestField(dataSource, ["priority"]);
    const seenSet = new Set(seen.map((e) => String(e.value)));
    const out: { value: string; count?: number; hint?: string }[] = [];
    for (const e of seen) out.push({ value: String(e.value), count: e.count });
    for (const n of ["1", "2", "3", "4", "5"]) {
      if (!seenSet.has(n)) out.push({ value: n });
    }
    for (const op of ["<2", "<=2", ">2", ">=2"]) {
      out.push({ value: op, hint: "range" });
    }
    raw = out;
  } else {
    raw = _harvestField(dataSource, spec.fields as string[]);
  }
  const filtered = raw.filter((e) => _matchesPrefix(e.value, p));
  const out: Suggestion[] = filtered.slice(0, 8).map((e) => {
    const needsQuote = /\s/.test(e.value);
    const commitValue = needsQuote ? `"${e.value}"` : e.value;
    const hint =
      e.hint != null
        ? e.hint
        : e.count != null
          ? `${e.count} task${e.count === 1 ? "" : "s"}`
          : "";
    return {
      kind: "value",
      label: e.value,
      value: commitValue,
      count: e.count,
      hint,
    };
  });
  return out;
}

export interface ApplyResult {
  newQuery: string;
  newCursorPos: number;
}

export function applySuggestion(
  query: string | null | undefined,
  cursorPos: number,
  suggestion: Suggestion | null | undefined,
): ApplyResult {
  const tok = tokenAtCursor(query, cursorPos);
  const sug = suggestion;
  if (!sug) {
    const fallback = String(query == null ? "" : query);
    return {
      newQuery: fallback,
      newCursorPos: Number.isFinite(cursorPos) ? cursorPos : fallback.length,
    };
  }
  if (sug.kind === "key") {
    let tail = tok.tokenAfter || "";
    if (tail.startsWith(":")) tail = tail.slice(1);
    const newQuery = tok.prefix + sug.value + tail + tok.suffix;
    const newCursorPos = (tok.prefix + sug.value).length;
    return { newQuery, newCursorPos };
  }
  const keyPrefix = tok.qualifierKey ? tok.qualifierKey + ":" : "";
  let tail = tok.tokenAfter || "";
  if (tail.startsWith('"')) tail = tail.slice(1);
  const newQuery = tok.prefix + keyPrefix + sug.value + tail + tok.suffix;
  const newCursorPos = (tok.prefix + keyPrefix + sug.value).length;
  return { newQuery, newCursorPos };
}

export function formatSuggestion(suggestion: Suggestion | null | undefined): {
  label: string;
  hint: string;
} {
  if (!suggestion) return { label: "", hint: "" };
  return { label: suggestion.label, hint: suggestion.hint || "" };
}

export function computeSuggestions(
  query: string | null | undefined,
  cursorPos: number,
  dataSource: SuggestDataSource | null | undefined,
): Suggestion[] {
  const tok = tokenAtCursor(query, cursorPos);
  if (tok.kind === "key") return keySuggestions(tok.token);
  return valueSuggestions(tok.qualifierKey, tok.token, dataSource);
}
