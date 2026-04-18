import { useEffect, useRef, useState } from "react";
import { getAgentRuntime, type AgentRuntimeInfo } from "../lib/api-client";

const CACHE_TTL_MS = 30_000;

type CacheEntry = { data: AgentRuntimeInfo; at: number };
const cache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<AgentRuntimeInfo>>();

export interface UseRuntimeStatusResult {
  data: AgentRuntimeInfo | null;
  error: Error | null;
  loading: boolean;
  refresh: () => void;
}

export function useRuntimeStatus(agentId: string | null): UseRuntimeStatusResult {
  const [data, setData] = useState<AgentRuntimeInfo | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [tick, setTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!agentId) return;
    const cached = cache.get(agentId);
    if (cached && Date.now() - cached.at < CACHE_TTL_MS) {
      setData(cached.data);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const promise =
      inflight.get(agentId) ??
      getAgentRuntime(agentId).then((res) => {
        cache.set(agentId, { data: res, at: Date.now() });
        inflight.delete(agentId);
        return res;
      });
    inflight.set(agentId, promise);
    promise
      .then((res) => { if (!cancelled && mountedRef.current) { setData(res); setError(null); } })
      .catch((err) => { if (!cancelled && mountedRef.current) setError(err as Error); })
      .finally(() => { if (!cancelled && mountedRef.current) setLoading(false); });
    return () => { cancelled = true; };
  }, [agentId, tick]);

  return {
    data,
    error,
    loading,
    refresh: () => {
      if (agentId) cache.delete(agentId);
      setTick((t) => t + 1);
    },
  };
}
