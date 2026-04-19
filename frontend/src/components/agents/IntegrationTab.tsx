import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check, Plus, Trash2, AlertTriangle } from "lucide-react";
import { useA2aKeys, type A2aKeyKind } from "../../hooks/useA2aKeys";
import {
  getPublicAgentCardUrl,
  getA2aEndpointUrl,
  getMetaA2aCardUrl,
  getMetaA2aEndpointUrl,
  type A2aKeyCreated,
} from "../../lib/api-client";
import { toast } from "../../lib/toast";

interface Props {
  agentId: string;
  kind?: A2aKeyKind;
}

export default function IntegrationTab({ agentId, kind = "sub-agent" }: Props) {
  const { t } = useTranslation();
  const { keys, loading, error, generate, revoke } = useA2aKeys(agentId, kind);
  const [justCreated, setJustCreated] = useState<A2aKeyCreated | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const cardUrl = kind === "meta-agent" ? getMetaA2aCardUrl() : getPublicAgentCardUrl(agentId);
  const endpointUrl = kind === "meta-agent" ? getMetaA2aEndpointUrl() : getA2aEndpointUrl(agentId);

  async function copy(id: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(id);
      toast.success(t("integration.copied"));
      setTimeout(() => setCopied((c) => (c === id ? null : c)), 2000);
    } catch {
      /* ignore */
    }
  }

  async function onGenerate() {
    try {
      const created = await generate();
      setJustCreated(created);
    } catch (e) {
      toast.error((e as Error).message || "Failed to create key");
    }
  }

  async function onRevoke(keyId: string, prefix: string) {
    if (!confirm(t("integration.revokeConfirm", { prefix }))) return;
    try {
      await revoke(keyId);
      toast.success(t("integration.keyRevoked"));
    } catch (e) {
      toast.error((e as Error).message || "Failed to revoke key");
    }
  }

  const activeKeys = (keys ?? []).filter((k) => !k.revoked);

  return (
    <div className="p-4 space-y-4" data-testid="integration-tab">
      <div>
        <h3 className="text-sm font-semibold">{t("integration.title")}</h3>
        <p className="text-xs text-gray-500 dark:text-gray-400">{t("integration.description")}</p>
      </div>

      <UrlBox label={t("integration.cardUrl")} value={cardUrl} id="card-url" copied={copied} onCopy={copy} />
      <UrlBox label={t("integration.endpointUrl")} value={endpointUrl} id="endpoint-url" copied={copied} onCopy={copy} />

      <div className="border-t border-gray-200 dark:border-gray-800 pt-4">
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-sm font-medium">{t("integration.keysTitle")}</h4>
          <button
            type="button"
            onClick={onGenerate}
            data-testid="generate-key-btn"
            className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600"
          >
            <Plus className="w-3 h-3" />
            {t("integration.generateKey")}
          </button>
        </div>

        {error && <div className="text-sm text-red-600">{error.message}</div>}
        {activeKeys.length === 0 && !loading && (
          <div className="text-sm text-gray-500">{t("integration.noKeys")}</div>
        )}

        {activeKeys.length > 0 && (
          <table className="w-full text-sm" data-testid="a2a-keys-table">
            <thead className="text-xs text-gray-500 uppercase">
              <tr>
                <th className="text-left py-2">{t("integration.keysTitle")}</th>
                <th className="text-left py-2">{t("integration.createdAt")}</th>
                <th className="text-left py-2">{t("integration.lastUsedAt")}</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {activeKeys.map((k) => (
                <tr key={k.keyId} className="border-t border-gray-200 dark:border-gray-800" data-testid={`a2a-key-row-${k.keyId}`}>
                  <td className="py-2 font-mono text-xs">{k.keyPrefix}…</td>
                  <td className="py-2 text-xs">{k.createdAt}</td>
                  <td className="py-2 text-xs">{k.lastUsedAt || t("integration.never")}</td>
                  <td className="py-2 text-right">
                    <button
                      type="button"
                      onClick={() => onRevoke(k.keyId, k.keyPrefix)}
                      data-testid={`revoke-key-${k.keyId}`}
                      className="p-1 text-red-500 hover:text-red-700"
                      aria-label={t("integration.revoke")}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {justCreated && (
        <div
          className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
          onClick={() => setJustCreated(null)}
          data-testid="new-key-modal"
        >
          <div
            className="bg-white dark:bg-gray-900 rounded-lg p-5 w-[540px] max-w-full"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold mb-2">{t("integration.newKeyTitle")}</h3>
            <div className="flex items-start gap-2 p-3 mb-3 rounded bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900">
              <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
              <div className="text-xs text-amber-800 dark:text-amber-200">{t("integration.newKeyWarning")}</div>
            </div>
            <div className="flex items-center gap-2 p-2 rounded bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800">
              <code className="flex-1 text-xs font-mono break-all" data-testid="new-key-value">
                {justCreated.apiKey}
              </code>
              <button
                type="button"
                onClick={() => copy("new-key", justCreated.apiKey)}
                data-testid="copy-new-key"
                className="p-1 text-gray-500 hover:text-gray-700"
                aria-label={t("integration.copyKey")}
              >
                {copied === "new-key" ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
              </button>
            </div>
            <div className="flex justify-end mt-4">
              <button
                type="button"
                onClick={() => setJustCreated(null)}
                className="px-2.5 py-1.5 text-xs rounded bg-gray-200 dark:bg-gray-800 hover:bg-gray-300 dark:hover:bg-gray-700"
                data-testid="new-key-close"
              >
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function UrlBox({
  label, value, id, copied, onCopy,
}: {
  label: string; value: string; id: string; copied: string | null;
  onCopy: (id: string, value: string) => void;
}) {
  return (
    <div>
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className="flex items-center gap-2 p-2 rounded bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800">
        <code className="flex-1 text-xs font-mono break-all" data-testid={`${id}-value`}>
          {value}
        </code>
        <button
          type="button"
          onClick={() => onCopy(id, value)}
          data-testid={`${id}-copy`}
          className="p-1 text-gray-500 hover:text-gray-700"
        >
          {copied === id ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}
