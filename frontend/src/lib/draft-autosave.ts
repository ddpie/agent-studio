/**
 * Lightweight localStorage autosave for unsaved editor drafts.
 *
 * Scope: the three spots where a browser reload currently wipes user work —
 *   - Tool code editor            → key = "tool-draft:{toolId}"
 *   - Skill file editor (edits)   → key = "skill-draft:{skillId}"
 *   - Agent edit form metadata    → key = "agent-draft:{agentId}"
 *
 * Not a replacement for real server-side persistence; just a short-term
 * guardrail so a stray F5 doesn't lose fifteen minutes of typing.
 *
 * The store is best-effort: oversized payloads, quota errors, and stale
 * entries older than MAX_AGE_MS are dropped silently. The caller is
 * responsible for clearing the draft on successful save/publish.
 */

// Cap any single draft to keep one editor from hogging localStorage. Above
// this size the caller usually has file-level content that belongs in an S3
// draft, not localStorage. 500 KB fits a few dozen markdown files comfortably.
const MAX_DRAFT_BYTES = 500 * 1024;

// Drop drafts older than a week — avoids littering the browser with drafts
// for resources the user abandoned or that no longer exist.
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

interface Envelope<T> {
  v: 1;
  ts: number;
  data: T;
}

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): boolean {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    // QuotaExceededError, Safari private mode, etc. Give up silently.
    return false;
  }
}

function safeRemove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function saveDraft<T>(key: string, data: T): void {
  const envelope: Envelope<T> = { v: 1, ts: Date.now(), data };
  let serialised: string;
  try {
    serialised = JSON.stringify(envelope);
  } catch {
    return;
  }
  if (serialised.length > MAX_DRAFT_BYTES) return;
  safeSet(key, serialised);
}

export function loadDraft<T>(key: string): T | null {
  const raw = safeGet(key);
  if (!raw) return null;
  let parsed: Envelope<T>;
  try {
    parsed = JSON.parse(raw) as Envelope<T>;
  } catch {
    safeRemove(key);
    return null;
  }
  if (parsed?.v !== 1 || typeof parsed.ts !== "number") {
    safeRemove(key);
    return null;
  }
  if (Date.now() - parsed.ts > MAX_AGE_MS) {
    safeRemove(key);
    return null;
  }
  return parsed.data;
}

/**
 * Same as loadDraft but also returns the capture timestamp so callers can
 * surface "restored from N minutes ago" to the user. Returns null for
 * missing / corrupt / expired drafts, same as loadDraft.
 */
export function loadDraftWithMeta<T>(key: string): { data: T; ts: number } | null {
  const raw = safeGet(key);
  if (!raw) return null;
  let parsed: Envelope<T>;
  try {
    parsed = JSON.parse(raw) as Envelope<T>;
  } catch {
    safeRemove(key);
    return null;
  }
  if (parsed?.v !== 1 || typeof parsed.ts !== "number") {
    safeRemove(key);
    return null;
  }
  if (Date.now() - parsed.ts > MAX_AGE_MS) {
    safeRemove(key);
    return null;
  }
  return { data: parsed.data, ts: parsed.ts };
}

export function clearDraft(key: string): void {
  safeRemove(key);
}

/**
 * Human-readable relative time ("just now", "3m ago") for a draft timestamp.
 * Uses i18next if available, falls back to English. Deliberately coarse —
 * the draft toast just needs a rough "how stale is this".
 */
export function formatDraftAge(ts: number, t?: (key: string, opts?: { count?: number }) => string): string {
  const tr = t ?? ((k: string, o?: { count?: number }) => {
    const n = o?.count;
    if (k === "common.draftJustNow") return "just now";
    if (k === "common.draftMinutesAgo") return `${n}m ago`;
    if (k === "common.draftHoursAgo") return `${n}h ago`;
    if (k === "common.draftDaysAgo") return `${n}d ago`;
    return "";
  });
  const ageMs = Math.max(0, Date.now() - ts);
  const mins = Math.floor(ageMs / 60_000);
  if (mins < 1) return tr("common.draftJustNow");
  if (mins < 60) return tr("common.draftMinutesAgo", { count: mins });
  const hours = Math.floor(mins / 60);
  if (hours < 24) return tr("common.draftHoursAgo", { count: hours });
  const days = Math.floor(hours / 24);
  return tr("common.draftDaysAgo", { count: days });
}

/**
 * Drop every localStorage draft whose key starts with one of the given
 * prefixes. Called on workspace switch — the underlying resource ids
 * (agentId, skillId, toolId) are workspace-scoped, so drafts from the
 * old workspace make no sense in the new one.
 */
export function clearDraftsByPrefix(prefixes: readonly string[]): void {
  try {
    const toRemove: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k) continue;
      if (prefixes.some((p) => k.startsWith(p))) toRemove.push(k);
    }
    for (const k of toRemove) localStorage.removeItem(k);
  } catch {
    /* ignore */
  }
}

/**
 * Create a debounced saver bound to a single draft key. Returns a function
 * callers invoke with the current draft value on every change; the actual
 * localStorage write is trailing-debounced by `delay` ms.
 *
 * The returned object also exposes `flush()` to force an immediate write
 * (use before navigation) and `cancel()` to drop any pending write.
 */
export function createDebouncedSaver<T>(key: string, delay = 500) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending: T | null = null;
  let hasPending = false;

  const flush = () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (hasPending) {
      saveDraft(key, pending as T);
      hasPending = false;
      pending = null;
    }
  };

  const schedule = (data: T) => {
    pending = data;
    hasPending = true;
    if (timer) clearTimeout(timer);
    timer = setTimeout(flush, delay);
  };

  const cancel = () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    pending = null;
    hasPending = false;
  };

  return { schedule, flush, cancel };
}
