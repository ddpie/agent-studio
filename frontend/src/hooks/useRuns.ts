import { useEffect, useRef, useState } from "react";
import {
  listRuns,
  getRun,
  fetchRunOutput,
  type RunSummary,
  type RunDetail,
  type RunOutput,
} from "../lib/runs-client";

export function useRunList(agentId: string | null) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    setLoading(true);
    listRuns(agentId)
      .then((r) => {
        if (!cancelled && mountedRef.current) {
          setRuns(r.runs);
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
    runs,
    error,
    loading,
    refresh: () => setTick((t) => t + 1),
  };
}

export function useRunDetail(agentId: string | null, runId: string | null) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [output, setOutput] = useState<RunOutput | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
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
  }, [agentId, runId]);

  return { detail, output, error, loading };
}
