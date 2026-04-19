import { useEffect, useRef, useState } from "react";
import {
  listTraces,
  getSessionTrace,
  type TraceSummary,
  type TraceSpan,
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
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!agentId || !sessionId) return;
    let cancelled = false;
    setLoading(true);
    getSessionTrace(agentId, sessionId)
      .then((r) => {
        if (!cancelled && mountedRef.current) {
          setRoot(r);
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
  }, [agentId, sessionId]);

  return { root, error, loading };
}
