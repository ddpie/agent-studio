import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Globe, Lock, Loader2 } from "lucide-react";
import ConfirmDialog from "../ui/ConfirmDialog";

interface PublishToggleProps {
  /** Current visibility value from the resource. */
  visibility: string | undefined | null;
  /** Whether the current user can publish (admin+ gated). */
  canPublish: boolean;
  /** Called on confirm to publish. Must return new visibility. */
  onPublish: () => Promise<void>;
  onUnpublish: () => Promise<void>;
  /** Called when visibility changes — lets parent update local state. */
  onChange?: (visibility: "public" | "private") => void;
  /** Size variant. */
  size?: "sm" | "md";
  /** data-testid prefix. */
  testId?: string;
}

/**
 * Publish/Unpublish toggle with confirmation.
 *
 * Publishing a resource makes it visible in the Marketplace to every logged-in
 * user of this deployment. The confirmation modal emphasises that.
 */
export default function PublishToggle({
  visibility,
  canPublish,
  onPublish,
  onUnpublish,
  onChange,
  size = "sm",
  testId = "publish-toggle",
}: PublishToggleProps) {
  const { t } = useTranslation();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const isPublic = visibility === "public";

  if (!canPublish) {
    // Still show the status badge so everyone sees visibility.
    return (
      <span
        data-testid={`${testId}-status`}
        className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full ${
          isPublic
            ? "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400"
            : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
        }`}
      >
        {isPublic ? <Globe className="w-3 h-3" /> : <Lock className="w-3 h-3" />}
        {isPublic ? t("publish.statusPublic") : t("publish.statusPrivate")}
      </span>
    );
  }

  const handleConfirm = async () => {
    setBusy(true);
    try {
      if (isPublic) {
        await onUnpublish();
        onChange?.("private");
      } else {
        await onPublish();
        onChange?.("public");
      }
      setConfirmOpen(false);
    } finally {
      setBusy(false);
    }
  };

  const px = size === "md" ? "px-3 py-1.5" : "px-2.5 py-1";
  const text = size === "md" ? "text-xs" : "text-[11px]";

  return (
    <>
      <button
        type="button"
        data-testid={testId}
        onClick={() => setConfirmOpen(true)}
        disabled={busy}
        className={`inline-flex items-center gap-1 ${px} ${text} rounded-lg transition-colors border ${
          isPublic
            ? "border-green-200 bg-green-50 text-green-700 hover:bg-green-100 dark:border-green-900 dark:bg-green-900/30 dark:text-green-400 dark:hover:bg-green-900/50"
            : "border-gray-200 bg-white text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
        } disabled:opacity-50`}
      >
        {busy ? (
          <Loader2 className="w-3 h-3 animate-spin" />
        ) : isPublic ? (
          <Globe className="w-3 h-3" />
        ) : (
          <Lock className="w-3 h-3" />
        )}
        {isPublic ? t("publish.unpublish") : t("publish.publish")}
      </button>

      <ConfirmDialog
        open={confirmOpen}
        title={isPublic ? t("publish.confirmUnpublishTitle") : t("publish.confirmPublishTitle")}
        message={isPublic ? t("publish.confirmUnpublishMessage") : t("publish.confirmPublishMessage")}
        confirmLabel={isPublic ? t("publish.unpublish") : t("publish.publish")}
        cancelLabel={t("common.cancel")}
        danger={!isPublic}
        onConfirm={handleConfirm}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  );
}
