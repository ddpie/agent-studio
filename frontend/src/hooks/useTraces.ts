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
    // Schedule-triggered runs need ~40-60s before spans are queryable
    // (runtime cold-start + OTEL flush + CloudWatch ingestion + Logs
    // Insights indexing). Poll every 5s for up to ~90s. A 10s pre-delay
    // avoids spamming the first ~9 requests that are guaranteed to 404.
    const MAX_RETRIES = 18;
    const POLL_INTERVAL_MS = 5000;
    const FIRST_POLL_DELAY_MS = 10_000;

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
            setTimeout(attempt, POLL_INTERVAL_MS);
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
            setTimeout(attempt, POLL_INTERVAL_MS);
            return;
          }
          setError(err as Error);
          setPending(false);
          setLoading(false);
        });
    };

    // Show "pending" state immediately so the UI doesn't look frozen
    // during the pre-delay, then fire the first request.
    setPending(true);
    setLoading(true);
    const firstTimer = setTimeout(attempt, FIRST_POLL_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(firstTimer);
    };
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
