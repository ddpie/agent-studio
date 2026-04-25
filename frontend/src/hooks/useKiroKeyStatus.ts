import { useEffect, useState } from "react";
import { getKiroKey, type KiroKeyInfo } from "../lib/api-client";

// Shared cached status — the banner in ChatPanel and the Settings
// section both want this, so caching once keeps the API quiet.
let cached: KiroKeyInfo | null = null;
let inflight: Promise<KiroKeyInfo | null> | null = null;

async function fetchOnce(): Promise<KiroKeyInfo | null> {
  if (inflight) return inflight;
  inflight = getKiroKey()
    .then((s) => { cached = s; return s; })
    .catch(() => null)
    .finally(() => { inflight = null; });
  return inflight;
}

export function invalidateKiroKeyStatus() {
  cached = null;
}

export default function useKiroKeyStatus(): KiroKeyInfo | null {
  const [status, setStatus] = useState<KiroKeyInfo | null>(cached);

  useEffect(() => {
    let alive = true;
    if (cached) { setStatus(cached); return; }
    fetchOnce().then((s) => { if (alive) setStatus(s); });
    return () => { alive = false; };
  }, []);

  return status;
}
