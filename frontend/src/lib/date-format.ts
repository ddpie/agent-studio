/**
 * Parse a backend timestamp into a Date.
 *
 * Backend timestamps are always UTC. When the server omits the tz suffix
 * (e.g. CloudWatch Logs Insights returns "YYYY-MM-DD HH:MM:SS.fff"),
 * `new Date(...)` silently interprets it as *local* time and users in
 * non-UTC zones see the wrong hour. This helper treats naive strings as
 * UTC, matching the backend's contract.
 */
function parseBackendDate(value: string | number | Date | null | undefined): Date | null {
  if (value == null || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number") {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  let s = value.trim();
  if (!s) return null;
  const hasTz = /[zZ]$|[+\-]\d{2}:?\d{2}$/.test(s);
  if (!hasTz) {
    s = s.replace(" ", "T") + "Z";
  }
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Full local datetime, e.g. "2026/4/20 18:32:15". */
export function formatDateTime(value: string | number | Date | null | undefined, fallback = "—"): string {
  const d = parseBackendDate(value);
  return d ? d.toLocaleString() : fallback;
}

/** Local date only. */
export function formatDate(value: string | number | Date | null | undefined, fallback = "—"): string {
  const d = parseBackendDate(value);
  return d ? d.toLocaleDateString() : fallback;
}

/** Short local time, e.g. "18:32". */
export function formatTimeShort(value: string | number | Date | null | undefined, fallback = "—"): string {
  const d = parseBackendDate(value);
  return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : fallback;
}
