import { useEffect, useState } from "react";
import { getMetaAgentStatus, type MetaAgentStatus } from "../lib/api-client";

const POLL_INTERVAL_MS = 60_000;

/**
 * Poll the Meta-Agent runtime status for the chat header dot. Shared
 * module-level state so multiple consumers don't each poll — the
 * ChatHeader is mounted once so this is precautionary but cheap.
 */
let cached: MetaAgentStatus | null = null;
let inflight: Promise<MetaAgentStatus | null> | null = null;
let lastFetch = 0;

async function refresh(): Promise<MetaAgentStatus | null> {
  if (inflight) return inflight;
  inflight = getMetaAgentStatus()
    .then((s) => { cached = s; lastFetch = Date.now(); return s; })
    .finally(() => { inflight = null; });
  return inflight;
}

export default function useMetaAgentStatus(): MetaAgentStatus | null {
  const [status, setStatus] = useState<MetaAgentStatus | null>(cached);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const s = await refresh();
      if (alive) setStatus(s);
    };
    if (!cached || Date.now() - lastFetch > POLL_INTERVAL_MS) tick();
    else setStatus(cached);
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  return status;
}
