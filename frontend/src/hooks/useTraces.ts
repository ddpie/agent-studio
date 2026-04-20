import { useEffect, useRef, useState } from "react";
import {
  listTraces,
  getSessionTrace,
  fetchTraceStats,
  type TraceSummary,
  type TraceSpan,
  type TraceStats,
  type TraceStatsRange,
} from "../lib/api-client";

export function useTraceSessions(agentId: string | null) {
  const [sessions, setSessions] = useState<TraceSummary[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    setLoading(true);
    listTraces(agentId)
      .then((r) => {
        if (!cancelled && mountedRef.current) {
          setSessions(r);
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
  }, [agentId, tick]);

  return {
    sessions,
    error,
    loading,
    refresh: () => setTick((t) => t + 1),
  };
}

export function useSessionTrace(agentId: string | null, sessionId: string | null) {
  const [root, setRoot] = useState<TraceSpan | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!agentId || !sessionId) return;
    let cancelled = false;
    let retry = 0;
    // A schedule-triggered run + OTEL export + CloudWatch ingestion +
    // Logs Insights indexing can easily take 40-60s before spans are
    // queryable. 36 × 2.5s = 90s covers the common case.
    const MAX_RETRIES = 36;
    const attempt = (): void => {
      setLoading(true);
      getSessionTrace(agentId, sessionId)
        .then((r) => {
          if (cancelled || !mountedRef.current) return;
          if (r) {
            setRoot(r);
            setError(null);
            setPending(false);
            setLoading(false);
          } else if (retry < MAX_RETRIES) {
            retry += 1;
            setPending(true);
            setTimeout(attempt, 2500);
          } else {
            setRoot(null);
            setPending(true);
            setLoading(false);
          }
        })
        .catch((err: unknown) => {
          if (cancelled || !mountedRef.current) return;
          // Spans haven't landed in CloudWatch yet — the backend returns
          // 404 until the first span for this session exists. Retry
          // silently instead of surfacing a scary error. Duck-type on
          // `status` so we don't depend on ApiError class identity (which
          // can fragment across bundled chunks).
          const status = (err as { status?: number })?.status;
          if (status === 404 && retry < MAX_RETRIES) {
            retry += 1;
            setPending(true);
            setTimeout(attempt, 2500);
            return;
          }
          setError(err as Error);
          setPending(false);
          setLoading(false);
        });
    };
    attempt();
    return () => { cancelled = true; };
  }, [agentId, sessionId]);

  return { root, error, loading, pending };
}


export function useTraceStats(agentId: string | null, range: TraceStatsRange) {
  const [stats, setStats] = useState<TraceStats | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    setLoading(true);
    fetchTraceStats(agentId, range)
      .then((r) => {
        if (!cancelled && mountedRef.current) {
          setStats(r);
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
  }, [agentId, range]);

  return { stats, error, loading };
}
