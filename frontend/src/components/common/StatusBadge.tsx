import { useTranslation } from "react-i18next";
import type { AgentRuntimeInfo } from "../../lib/api-client";

// Agent runtime status (from bedrock-agentcore-control.GetAgentRuntime) plus a few
// related states that also flow through this component (endpoints' READY / UPDATING
// / CREATING).
type Status =
  | AgentRuntimeInfo["status"]
  | "UNKNOWN";

const STYLES: Record<Status, string> = {
  READY: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  CREATING: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300 animate-pulse",
  UPDATING: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300 animate-pulse",
  DELETING: "bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  CREATE_FAILED: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  UPDATE_FAILED: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  UNKNOWN: "bg-gray-100 text-gray-600 dark:bg-gray-900 dark:text-gray-400",
};

export interface StatusBadgeProps {
  status: Status | string | undefined | null;
  onClick?: () => void;
  "data-testid"?: string;
}

export default function StatusBadge({ status, onClick, ...rest }: StatusBadgeProps) {
  const { t } = useTranslation();
  const normalized = ((status as string) || "UNKNOWN").toUpperCase() as Status;
  const cls = STYLES[normalized] ?? STYLES.UNKNOWN;
  const labelKey = `runtime.status.${normalized.toLowerCase()}`;
  const label = t(labelKey, { defaultValue: normalized });
  const interactive = Boolean(onClick);
  const Tag = interactive ? "button" : "span";
  return (
    <Tag
      type={interactive ? "button" : undefined}
      onClick={onClick}
      data-testid={rest["data-testid"] ?? "status-badge"}
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${cls} ${interactive ? "cursor-pointer hover:opacity-80" : ""}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      {label}
    </Tag>
  );
}
