import { useCallback, useEffect, useRef, useState } from "react";
import { listAgentEndpoints, type AgentEndpoint } from "../lib/api-client";

export function useRuntimeEndpoints(agentId: string | null) {
  const [data, setData] = useState<AgentEndpoint[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<number | null>(null);

  const fetchNow = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    try {
      const eps = await listAgentEndpoints(agentId);
      setData(eps);
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => { fetchNow(); }, [fetchNow]);

  useEffect(() => {
    if (!data) return;
    const needsPoll = data.some((e) => e.status === "UPDATING" || e.status === "CREATING");
    if (!needsPoll) {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
      return;
    }
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = window.setInterval(fetchNow, 2000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = null;
    };
  }, [data, fetchNow]);

  return { data, error, loading, refresh: fetchNow };
}
