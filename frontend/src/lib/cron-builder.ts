/**
 * Small cron/rate expression helper for the Schedule modal.
 *
 * The backend (lambda/crud/schedules.py) accepts two shapes:
 *   cron(m h dom mon dow year)   — AWS EventBridge 6-field flavour
 *   rate(N minute|hour|day)      — rate-based
 *
 * We build + parse the subset that covers all UI preset modes:
 *   - minutes(N)    → rate(N minutes)
 *   - hourly(M)     → cron(M * * * ? *)
 *   - daily(M,H)    → cron(M H * * ? *)
 *   - weekly(M,H,[d]) → cron(M H ? * d1,d2,... *)
 *   - monthly(M,H,D)  → cron(M H D * ? *)
 *   - advanced    → freeform string, no builder involvement
 *
 * We also compute the next N occurrences in UTC so users can sanity
 * check their schedule before saving. Parsing is best-effort: if the
 * expression uses features outside our preset vocabulary (ranges,
 * steps like "slash-5", named months, year constraints) we return
 * mode=advanced and the UI falls back to raw editing.
 */

export type ScheduleMode = "minutes" | "hourly" | "daily" | "weekly" | "monthly" | "advanced";

export interface ScheduleSpec {
  mode: ScheduleMode;
  // minutes(N) / rate
  rateValue?: number;
  rateUnit?: "minutes" | "hours" | "days";
  // common: 0-59, 0-23
  minute?: number;
  hour?: number;
  // weekly: [0..6], 0=Sun
  weekdays?: number[];
  // monthly: 1-31
  dayOfMonth?: number;
  // advanced only
  raw?: string;
}

// ─── Build ────────────────────────────────────────────────────────────

export function buildExpression(spec: ScheduleSpec): string {
  switch (spec.mode) {
    case "minutes": {
      const n = Math.max(1, Math.min(60 * 24 * 30, spec.rateValue ?? 5));
      const unit = spec.rateUnit ?? "minutes";
      // AWS rate() singular vs plural is fine either way; prefer plural.
      return `rate(${n} ${unit})`;
    }
    case "hourly": {
      const m = clamp(spec.minute ?? 0, 0, 59);
      return `cron(${m} * * * ? *)`;
    }
    case "daily": {
      const m = clamp(spec.minute ?? 0, 0, 59);
      const h = clamp(spec.hour ?? 9, 0, 23);
      return `cron(${m} ${h} * * ? *)`;
    }
    case "weekly": {
      const m = clamp(spec.minute ?? 0, 0, 59);
      const h = clamp(spec.hour ?? 9, 0, 23);
      const raw = spec.weekdays && spec.weekdays.length > 0 ? spec.weekdays : [1];
      // AWS day-of-week is 1-7 = Sun-Sat; our UI is 0-6 = Sun-Sat.
      const dow = [...new Set(raw)].sort((a, b) => a - b).map((d) => String(d + 1)).join(",");
      return `cron(${m} ${h} ? * ${dow} *)`;
    }
    case "monthly": {
      const m = clamp(spec.minute ?? 0, 0, 59);
      const h = clamp(spec.hour ?? 9, 0, 23);
      const d = clamp(spec.dayOfMonth ?? 1, 1, 31);
      return `cron(${m} ${h} ${d} * ? *)`;
    }
    case "advanced":
    default:
      return (spec.raw || "").trim();
  }
}

// ─── Parse (best-effort, only patterns we produce) ────────────────────

const CRON_SIX = /^cron\(\s*([^)]+?)\s*\)$/i;
const RATE_RE = /^rate\(\s*(\d+)\s+(minute|minutes|hour|hours|day|days)\s*\)$/i;

export function parseExpression(expr: string): ScheduleSpec {
  const trimmed = (expr || "").trim();

  const rate = trimmed.match(RATE_RE);
  if (rate) {
    const n = parseInt(rate[1], 10);
    const u = rate[2].toLowerCase();
    const unit: ScheduleSpec["rateUnit"] = u.startsWith("hour")
      ? "hours"
      : u.startsWith("day")
        ? "days"
        : "minutes";
    return { mode: "minutes", rateValue: n, rateUnit: unit };
  }

  const cron = trimmed.match(CRON_SIX);
  if (!cron) return { mode: "advanced", raw: trimmed };
  const parts = cron[1].split(/\s+/);
  if (parts.length !== 6) return { mode: "advanced", raw: trimmed };
  const [minS, hourS, domS, monS, dowS] = parts;
  // We only recognise simple numeric fields to avoid silently mis-mapping
  // complex expressions. Month must be '*' for all presets.
  if (monS !== "*") return { mode: "advanced", raw: trimmed };

  const minute = parseIntStrict(minS);
  const hour = parseIntStrict(hourS);

  // hourly:  M * * * ? *
  if (minute !== null && hourS === "*" && domS === "*" && dowS === "?") {
    return { mode: "hourly", minute };
  }
  // daily:   M H * * ? *
  if (minute !== null && hour !== null && domS === "*" && dowS === "?") {
    return { mode: "daily", minute, hour };
  }
  // weekly:  M H ? * 1,2,3 *
  if (minute !== null && hour !== null && domS === "?") {
    const days = parseDowList(dowS);
    if (days !== null) return { mode: "weekly", minute, hour, weekdays: days };
  }
  // monthly: M H D * ? *
  if (minute !== null && hour !== null && dowS === "?") {
    const dom = parseIntStrict(domS);
    if (dom !== null) return { mode: "monthly", minute, hour, dayOfMonth: dom };
  }
  return { mode: "advanced", raw: trimmed };
}

function parseIntStrict(s: string): number | null {
  if (!/^\d+$/.test(s)) return null;
  return parseInt(s, 10);
}

function parseDowList(s: string): number[] | null {
  // Accept "1,2,3" of 1-7 (Sun=1). Convert back to 0-6 (Sun=0).
  const parts = s.split(",").map((p) => p.trim());
  const out: number[] = [];
  for (const p of parts) {
    if (!/^[1-7]$/.test(p)) return null;
    out.push(parseInt(p, 10) - 1);
  }
  return out.sort((a, b) => a - b);
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

// ─── Next-run preview (UTC) ───────────────────────────────────────────

/**
 * Return the next `count` trigger times after `from`, in UTC.
 * Returns [] if the expression uses features we don't simulate.
 */
export function nextOccurrences(expr: string, count = 5, from: Date = new Date()): Date[] {
  const spec = parseExpression(expr);
  const out: Date[] = [];
  const start = new Date(from.getTime() + 1_000); // one second in the future

  if (spec.mode === "minutes") {
    const n = spec.rateValue ?? 5;
    const unit = spec.rateUnit ?? "minutes";
    const stepMs =
      unit === "hours" ? n * 3600_000 : unit === "days" ? n * 86400_000 : n * 60_000;
    let t = start.getTime();
    for (let i = 0; i < count; i++) {
      t += stepMs;
      out.push(new Date(t));
    }
    return out;
  }

  if (spec.mode === "advanced") return [];

  // For cron presets we walk forwards minute-by-minute-ish using coarse
  // steps keyed by the preset shape. Accurate enough for "show me the
  // next 5 fires" without a full cron parser.
  const maxIter = 366 * 24 * 60; // one year cap
  let cur = new Date(Date.UTC(
    start.getUTCFullYear(),
    start.getUTCMonth(),
    start.getUTCDate(),
    start.getUTCHours(),
    start.getUTCMinutes(),
    0, 0,
  ));
  // If `from` is at exactly MM:SS != 0, next candidate minute is the one after.
  if (cur.getTime() <= from.getTime()) cur = new Date(cur.getTime() + 60_000);

  for (let iter = 0; iter < maxIter && out.length < count; iter++) {
    if (matches(spec, cur)) {
      out.push(new Date(cur));
      // Skip ahead past this match to avoid duplicate same-minute fires.
      cur = new Date(cur.getTime() + 60_000);
    } else {
      cur = advance(spec, cur);
    }
  }
  return out;
}

function matches(spec: ScheduleSpec, d: Date): boolean {
  const m = d.getUTCMinutes();
  const h = d.getUTCHours();
  const dom = d.getUTCDate();
  const dow = d.getUTCDay(); // 0..6, Sun=0
  switch (spec.mode) {
    case "hourly":
      return m === (spec.minute ?? 0);
    case "daily":
      return m === (spec.minute ?? 0) && h === (spec.hour ?? 0);
    case "weekly": {
      if (m !== (spec.minute ?? 0) || h !== (spec.hour ?? 0)) return false;
      const days = spec.weekdays && spec.weekdays.length ? spec.weekdays : [1];
      return days.includes(dow);
    }
    case "monthly":
      return (
        m === (spec.minute ?? 0) &&
        h === (spec.hour ?? 0) &&
        dom === (spec.dayOfMonth ?? 1)
      );
    default:
      return false;
  }
}

function advance(spec: ScheduleSpec, d: Date): Date {
  // Coarser steps when the minute/hour clearly can't match, to stay fast.
  const m = d.getUTCMinutes();
  const h = d.getUTCHours();
  if (spec.mode !== "hourly") {
    if (m !== (spec.minute ?? 0)) {
      const add = ((spec.minute ?? 0) - m + 60) % 60 || 60;
      return new Date(d.getTime() + add * 60_000);
    }
    if (h !== (spec.hour ?? 0)) {
      const add = ((spec.hour ?? 0) - h + 24) % 24 || 24;
      return new Date(d.getTime() + add * 3600_000);
    }
  } else if (m !== (spec.minute ?? 0)) {
    const add = ((spec.minute ?? 0) - m + 60) % 60 || 60;
    return new Date(d.getTime() + add * 60_000);
  }
  return new Date(d.getTime() + 60_000);
}

// ─── Formatters ───────────────────────────────────────────────────────

export function formatUtcShort(d: Date, locale = "en"): string {
  // 2026-04-21 14:05 UTC
  const pad = (n: number) => String(n).padStart(2, "0");
  const y = d.getUTCFullYear();
  const mo = pad(d.getUTCMonth() + 1);
  const da = pad(d.getUTCDate());
  const hh = pad(d.getUTCHours());
  const mm = pad(d.getUTCMinutes());
  const suffix = locale === "zh" ? "UTC" : "UTC";
  return `${y}-${mo}-${da} ${hh}:${mm} ${suffix}`;
}

export function formatLocalShort(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  const y = d.getFullYear();
  const mo = pad(d.getMonth() + 1);
  const da = pad(d.getDate());
  const hh = pad(d.getHours());
  const mm = pad(d.getMinutes());
  return `${y}-${mo}-${da} ${hh}:${mm}`;
}
