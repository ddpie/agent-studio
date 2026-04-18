import { useCallback, useEffect, useState } from "react";
import { listAgentVersions, type AgentRuntimeVersion } from "../lib/api-client";

export function useRuntimeVersions(agentId: string | null) {
  const [data, setData] = useState<AgentRuntimeVersion[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchNow = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    try {
      const v = await listAgentVersions(agentId);
      setData(v);
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    fetchNow();
  }, [fetchNow]);

  return { data, error, loading, refresh: fetchNow };
}
