import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { useSessionTrace } from "../../hooks/useTraces";
import SpanTree from "./SpanTree";

interface Props {
  agentId: string;
  sessionId: string | null;
}

export default function TracesTab({ agentId, sessionId }: Props) {
  const { t } = useTranslation();
  const trace = useSessionTrace(agentId, sessionId);

  if (!sessionId) {
    return (
      <div className="p-4 text-sm text-gray-500 dark:text-gray-400">
        {t("traces.selectSessionHint", "Select a session to view spans")}
      </div>
    );
  }

  if (trace.loading && !trace.pending && !trace.root) {
    return (
      <div className="p-4 text-sm text-gray-500 dark:text-gray-400">Loading…</div>
    );
  }

  if (trace.pending && !trace.root) {
    return (
      <div className="p-4 text-sm text-gray-500 dark:text-gray-400 flex items-center gap-2">
        <RefreshCw className="w-3 h-3 animate-spin" />
        {t("traces.pendingSpans", "Waiting for spans to land...")}
      </div>
    );
  }

  if (trace.error && !trace.root) {
    return (
      <div className="p-4 text-sm text-red-600 dark:text-red-400">
        {t("traces.loadError", "Failed to load trace")}: {trace.error.message}
      </div>
    );
  }

  if (!trace.root) {
    return (
      <div className="p-4 text-sm text-gray-500 dark:text-gray-400">
        {t("traces.noSpans", "No spans found")}
      </div>
    );
  }

  return (
    <div className="p-4">
      <SpanTree root={trace.root} />
    </div>
  );
}
