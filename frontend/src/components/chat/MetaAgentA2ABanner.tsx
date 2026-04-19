import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check, Code2, X } from "lucide-react";
import { getMetaAgentCard, type AgentCard } from "../../lib/api-client";
import { toast } from "../../lib/toast";

type CopyTarget = "endpoint" | "arn" | "card";

export default function MetaAgentA2ABanner() {
  const { t } = useTranslation();
  const [card, setCard] = useState<AgentCard | null>(null);
  const [copied, setCopied] = useState<CopyTarget | null>(null);
  const [showCardModal, setShowCardModal] = useState(false);

  useEffect(() => {
    getMetaAgentCard().then(setCard);
  }, []);

  if (!card || !card.url) return null;

  async function copy(target: CopyTarget) {
    let text = "";
    if (target === "endpoint") text = card!.url || "";
    else if (target === "arn") text = card!.runtimeArn || "";
    else text = JSON.stringify(card, null, 2);
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(target);
      toast.success(t("a2a.copied"));
      setTimeout(() => setCopied((c) => (c === target ? null : c)), 2000);
    } catch {
      // Clipboard API may be blocked; fall back silently — the JSON modal
      // still lets users select + copy manually.
    }
  }

  return (
    <>
      <div
        data-testid="a2a-banner"
        data-url={card.url}
        data-arn={card.runtimeArn || ""}
        className="mx-4 mt-2 rounded border border-blue-200 dark:border-blue-900 bg-blue-50 dark:bg-blue-950/30 px-3 py-2 text-xs flex items-center gap-3"
      >
        <Code2 className="w-4 h-4 text-blue-600 dark:text-blue-400 flex-shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="font-medium">{t("a2a.title")}</div>
          <div className="text-gray-600 dark:text-gray-400 truncate" title={t("a2a.subtitle")}>
            {t("a2a.subtitle")}
          </div>
        </div>
        <button
          type="button"
          onClick={() => copy("endpoint")}
          className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded bg-white dark:bg-gray-900 border dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800"
          data-testid="a2a-copy-endpoint"
        >
          {copied === "endpoint" ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
          {t("a2a.copyEndpoint")}
        </button>
        {card.runtimeArn && (
          <button
            type="button"
            onClick={() => copy("arn")}
            className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded bg-white dark:bg-gray-900 border dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800"
            data-testid="a2a-copy-arn"
          >
            {copied === "arn" ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
            {t("a2a.copyArn")}
          </button>
        )}
        <button
          type="button"
          onClick={() => setShowCardModal(true)}
          className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
          data-testid="a2a-view-card"
        >
          {t("a2a.viewCard")}
        </button>
      </div>
      {showCardModal && (
        <div
          className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
          onClick={() => setShowCardModal(false)}
          data-testid="a2a-card-modal"
        >
          <div
            className="bg-white dark:bg-gray-900 rounded-lg p-4 w-[640px] max-w-full max-h-[80vh] overflow-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold">{card.name}</h3>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => copy("card")}
                  className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700"
                >
                  {copied === "card" ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                  {t("a2a.copyCard")}
                </button>
                <button
                  type="button"
                  onClick={() => setShowCardModal(false)}
                  className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
                  aria-label="close"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
            <pre
              className="text-xs font-mono whitespace-pre-wrap break-words bg-gray-50 dark:bg-gray-950 p-3 rounded"
              data-testid="a2a-card-json"
            >
              {JSON.stringify(card, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </>
  );
}
