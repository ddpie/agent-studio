import { useEffect, useState } from "react";
import { listKiroModels, type KiroModelInfo } from "../lib/agentcore-client";
import { DEFAULT_KIRO_MODEL_ID } from "../lib/models";

// Bumped when fetch/parse logic changes — older cached lists get
// re-fetched instead of sticking on stale data. No TTL: every mount
// kicks off a background revalidation, so staleness is bounded by the
// time it takes the Meta-Agent to answer list_models (~1s warm).
const LS_KEY = "agent-studio:kiro-models:v2";

interface Cached {
  models: KiroModelInfo[];
  fetchedAt: number;
}

function readCache(): Cached | null {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Cached;
    if (!Array.isArray(parsed.models)) return null;
    // Length-≤-1 cached entries are almost always the fallback
    // persisted from an earlier failed fetch — ignore so we re-fetch
    // and also don't show a misleading one-item picker.
    if (parsed.models.length <= 1) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeCache(models: KiroModelInfo[]): void {
  if (models.length <= 1) return;
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ models, fetchedAt: Date.now() }));
  } catch {
    /* quota / SSR — non-fatal */
  }
}

// Kiro's --list-models emits a `name` like "2.20x credits      The
// Claude Opus 4.6 model" — a credits prefix plus vendor marketing
// copy. Neither belongs in the picker. Rather than fighting the
// format, derive a clean label from the id itself: lower-kebab ids
// become Title Case with hyphens replaced by spaces.
//
// Examples:
//   claude-opus-4.6      -> "Claude Opus 4.6"
//   claude-sonnet-4      -> "Claude Sonnet 4"
//   deepseek-3.2         -> "Deepseek 3.2"
//   qwen3-coder-next     -> "Qwen3 Coder Next"
const KNOWN_VENDORS = new Set(["claude", "deepseek", "minimax", "glm", "qwen", "qwen3"]);
function deriveLabel(id: string): string {
  return id
    .split("-")
    .map((part, i) => {
      // First segment: if it's a known vendor, capitalize it. Otherwise
      // leave lowercase digits/version strings alone (e.g. "4.6").
      if (i === 0 && KNOWN_VENDORS.has(part.toLowerCase())) {
        return part.charAt(0).toUpperCase() + part.slice(1);
      }
      // Pure number / version token: keep as-is.
      if (/^[0-9.]+$/.test(part)) return part;
      // Alphanumeric tokens starting with letter → Title Case.
      if (/^[a-z]/.test(part)) {
        return part.charAt(0).toUpperCase() + part.slice(1);
      }
      return part;
    })
    .join(" ");
}

function normalizeModels(models: KiroModelInfo[]): KiroModelInfo[] {
  return models.map((m) => ({ id: m.id, name: deriveLabel(m.id) }));
}

const FALLBACK: KiroModelInfo[] = [
  { id: DEFAULT_KIRO_MODEL_ID, name: deriveLabel(DEFAULT_KIRO_MODEL_ID) },
];

// Module-level state. Multiple components share one fetch-in-flight
// promise so StrictMode double-mount + concurrent consumers don't
// multiply the Secrets Manager hit.
let cached: KiroModelInfo[] | null = null;
let inflight: Promise<KiroModelInfo[]> | null = null;
// Module-level subscriber set so a background revalidation in one
// component rerenders every other component that showed the stale
// list — React state updates are scoped per-instance otherwise.
const subscribers = new Set<(m: KiroModelInfo[]) => void>();

function notify(models: KiroModelInfo[]): void {
  cached = models;
  for (const cb of subscribers) cb(models);
}

async function fetchFromRuntime(): Promise<KiroModelInfo[]> {
  if (inflight) return inflight;
  inflight = (async () => {
    const raw = await listKiroModels();
    const cleaned = normalizeModels(raw);
    const resolved = cleaned.length > 0 ? cleaned : FALLBACK;
    if (resolved.length > 1) writeCache(resolved);
    return resolved;
  })();
  try { return await inflight; }
  finally { inflight = null; }
}

/**
 * Stale-while-revalidate model list.
 *
 * On mount:
 *   1. Return whatever is in memory / localStorage immediately (so the
 *      picker never flashes empty), using the fallback if truly nothing
 *      is known yet.
 *   2. Kick off a background fetch regardless of cache freshness. When
 *      it lands, compare to what we showed — if it changed, notify all
 *      mounted consumers so they rerender with the new list.
 *
 * Callers thus always see the freshest data the runtime knows about,
 * without ever blocking on the network.
 */
export default function useKiroModels(): KiroModelInfo[] {
  const [models, setModels] = useState<KiroModelInfo[]>(() => {
    if (cached) return cached;
    const fromLs = readCache();
    if (fromLs) { cached = normalizeModels(fromLs.models); return cached; }
    return FALLBACK;
  });

  useEffect(() => {
    let alive = true;
    const onUpdate = (next: KiroModelInfo[]) => { if (alive) setModels(next); };
    subscribers.add(onUpdate);

    // Always revalidate in the background. We already showed whatever
    // we had — if this returns something different we'll rerender.
    fetchFromRuntime()
      .then((fresh) => {
        if (!alive) return;
        // Only notify if the fresh list actually differs. JSON-stringify
        // is cheap for lists of ~10 entries and sidesteps reference
        // inequality from re-parsing the cache.
        const prev = cached;
        if (!prev || JSON.stringify(prev) !== JSON.stringify(fresh)) {
          notify(fresh);
        } else if (!cached) {
          cached = fresh;
        }
      })
      .catch(() => {});

    return () => { alive = false; subscribers.delete(onUpdate); };
  }, []);

  return models;
}
