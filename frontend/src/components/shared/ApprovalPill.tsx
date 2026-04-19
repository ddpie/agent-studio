import { useTranslation } from "react-i18next";
import { ShieldCheck, ShieldAlert } from "lucide-react";

interface ApprovalPillProps {
  /** Skill type — approval only applies to "script" skills. */
  type?: string | null;
  /** Whether the skill has been approved. */
  approved?: boolean;
  /** Size variant. */
  size?: "sm" | "md";
}

/**
 * Renders an approval status pill for script-type skills.
 * - Approved → green "Approved" pill
 * - Script + not approved → yellow "Pending approval" pill
 * - Anything else → null (prompt-type skills don't require approval)
 */
export default function ApprovalPill({ type, approved, size = "sm" }: ApprovalPillProps) {
  const { t } = useTranslation();
  if (type !== "script") return null;

  const text = size === "md" ? "text-xs" : "text-[11px]";
  const px = size === "md" ? "px-2 py-0.5" : "px-1.5 py-0.5";
  const iconSize = size === "md" ? "w-3 h-3" : "w-2.5 h-2.5";

  if (approved) {
    return (
      <span
        data-testid="skill-approval-pill-approved"
        className={`inline-flex items-center gap-1 ${px} ${text} rounded-full bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400`}
      >
        <ShieldCheck className={iconSize} />
        {t("skills.approve.approved")}
      </span>
    );
  }

  return (
    <span
      data-testid="skill-approval-pill-pending"
      className={`inline-flex items-center gap-1 ${px} ${text} rounded-full bg-yellow-50 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400`}
    >
      <ShieldAlert className={iconSize} />
      {t("skills.approve.pending")}
    </span>
  );
}
