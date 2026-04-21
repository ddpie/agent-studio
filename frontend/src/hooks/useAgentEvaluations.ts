import { useEffect, useMemo, useRef, useState } from "react";
import { listAgentEvaluations, type AgentEvaluation, type EvaluationDiagnostics } from "../lib/api-client";

export interface EvaluatorStats {
  latest: number;
  mean: number;
  count: number;
  sessions: Set<string>;
  trend: AgentEvaluation[];
}

export function useAgentEvaluations(agentId: string | null) {
  const [rows, setRows] = useState<AgentEvaluation[] | null>(null);
  const [diagnostics, setDiagnostics] = useState<EvaluationDiagnostics | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    setLoading(true);
    listAgentEvaluations(agentId)
      .then((r) => {
        if (!cancelled && mountedRef.current) {
          setRows(r.evaluations);
          setDiagnostics(r.diagnostics ?? null);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled && mountedRef.current) {
          setError(err as Error);
          setRows(null);
        }
      })
      .finally(() => {
        if (!cancelled && mountedRef.current) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [agentId, tick]);

  const grouped = useMemo<Record<string, EvaluatorStats> | null>(() => {
    if (!rows) return null;
    const out: Record<string, EvaluatorStats> = {};
    for (const r of rows) {
      const existing = out[r.evaluator];
      if (!existing) {
        out[r.evaluator] = {
          latest: r.score,
          mean: r.score,
          count: 1,
          sessions: new Set<string>(r.sessionId ? [r.sessionId] : []),
          trend: [r],
        };
      } else {
        existing.count += 1;
        existing.mean = (existing.mean * (existing.count - 1) + r.score) / existing.count;
        if (r.sessionId) existing.sessions.add(r.sessionId);
        existing.trend.push(r);
      }
    }
    return out;
  }, [rows]);

  return {
    rows,
    grouped,
    diagnostics,
    error,
    loading,
    refresh: () => setTick((t) => t + 1),
  };
}
