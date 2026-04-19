import { useCallback, useEffect, useRef, useState } from "react";
import {
  listA2aKeys,
  createA2aKey,
  revokeA2aKey,
  listMetaA2aKeys,
  createMetaA2aKey,
  revokeMetaA2aKey,
  type A2aKey,
  type A2aKeyCreated,
} from "../lib/api-client";

export type A2aKeyKind = "sub-agent" | "meta-agent";

export function useA2aKeys(agentId: string | null, kind: A2aKeyKind = "sub-agent") {
  const [keys, setKeys] = useState<A2aKey[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => {
    if (kind === "sub-agent" && !agentId) return;
    let cancelled = false;
    setLoading(true);
    const loader = kind === "meta-agent" ? listMetaA2aKeys() : listA2aKeys(agentId!);
    loader
      .then((k) => {
        if (!cancelled && mountedRef.current) {
          setKeys(k);
          setError(null);
        }
      })
      .catch((err) => { if (!cancelled && mountedRef.current) setError(err as Error); })
      .finally(() => { if (!cancelled && mountedRef.current) setLoading(false); });
    return () => { cancelled = true; };
  }, [agentId, kind, tick]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const generate = useCallback(async (): Promise<A2aKeyCreated> => {
    const created =
      kind === "meta-agent"
        ? await createMetaA2aKey()
        : await createA2aKey(agentId!);
    refresh();
    return created;
  }, [agentId, kind, refresh]);

  const revoke = useCallback(async (keyId: string) => {
    if (kind === "meta-agent") await revokeMetaA2aKey(keyId);
    else await revokeA2aKey(agentId!, keyId);
    refresh();
  }, [agentId, kind, refresh]);

  return { keys, error, loading, generate, revoke, refresh };
}
