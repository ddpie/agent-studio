import { useCallback, useEffect, useRef, useState } from "react";
import {
  listRuns,
  getRun,
  fetchRunOutput,
  type RunSummary,
  type RunDetail,
  type RunOutput,
} from "../lib/runs-client";

interface UseRunListOptions {
  scheduleId?: string;
  limit?: number;
}

/**
 * List runs for an agent, optionally filtered by scheduleId. Returns
 * the first page on mount/refresh; call `loadMore()` to append the next
 * page. `hasMore` reflects whether the backend returned a `nextToken`.
 */
export function useRunList(agentId: string | null, options: UseRunListOptions = {}) {
  const { scheduleId, limit = 20 } = options;
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    setLoading(true);
    listRuns(agentId, { limit, scheduleId })
      .then((r) => {
        if (!cancelled && mountedRef.current) {
          setRuns(r.runs);
          setCursor(r.nextToken ?? null);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled && mountedRef.current) setError(err as Error);
      })
      .finally(() => {
        if (!cancelled && mountedRef.current) setLoading(false);
      });
    return () => { cancelled = true; };
     
  }, [agentId, scheduleId, limit, tick]);

  const loadMore = useCallback(async () => {
    if (!agentId || !cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const r = await listRuns(agentId, { limit, scheduleId, cursor });
      if (!mountedRef.current) return;
      setRuns((prev) => [...(prev ?? []), ...r.runs]);
      setCursor(r.nextToken ?? null);
    } catch (err) {
      if (mountedRef.current) setError(err as Error);
    } finally {
      if (mountedRef.current) setLoadingMore(false);
    }
  }, [agentId, cursor, loadingMore, limit, scheduleId]);

  return {
    runs,
    error,
    loading,
    loadingMore,
    hasMore: cursor !== null,
    refresh: () => setTick((t) => t + 1),
    loadMore,
  };
}

export function useRunDetail(agentId: string | null, runId: string | null) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [output, setOutput] = useState<RunOutput | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!agentId || !runId) {
      setDetail(null);
      setOutput(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getRun(agentId, runId)
      .then(async (d) => {
        if (cancelled || !mountedRef.current) return;
        setDetail(d);
        setError(null);
        // Fetch output if available
        if (d.outputUrl) {
          try {
            const out = await fetchRunOutput(d.outputUrl);
            if (!cancelled && mountedRef.current) {
              setOutput(out);
            }
          } catch (err) {
            if (!cancelled && mountedRef.current) {
              console.error("Failed to fetch run output:", err);
              // Don't set error state — partial data (detail without output) is useful
            }
          }
        }
      })
      .catch((err) => {
        if (!cancelled && mountedRef.current) setError(err as Error);
      })
      .finally(() => {
        if (!cancelled && mountedRef.current) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [agentId, runId, tick]);

  return {
    detail,
    output,
    error,
    loading,
    refresh: () => setTick((t) => t + 1),
  };
}
