import { useState } from "react";
import { useTranslation } from "react-i18next";

export interface EndpointDialogProps {
  mode: "create" | "switch";
  fixedName?: string;
  versions: string[];
  onCancel: () => void;
  onSubmit: (payload: { name: string; version: string }) => Promise<void>;
}

export default function EndpointDialog({ mode, fixedName, versions, onCancel, onSubmit }: EndpointDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(fixedName ?? "");
  const [version, setVersion] = useState(versions[0] ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = !!name.trim() && !!version.trim() && !submitting;

  return (
    <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm bg-white dark:bg-gray-900 rounded-lg shadow-xl p-5">
        <h3 className="text-base font-semibold mb-4">
          {mode === "create" ? t("endpoints.create") : t("endpoints.switch")}
        </h3>
        <div className="space-y-3 text-sm">
          <label className="block">
            <span className="text-xs text-gray-500">Name</span>
            <input
              type="text"
              value={name}
              disabled={mode === "switch"}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("endpoints.namePlaceholder")}
              className="mt-1 w-full rounded border border-gray-300 dark:border-gray-700 bg-transparent px-2 py-1"
            />
          </label>
          <label className="block">
            <span className="text-xs text-gray-500">{t("endpoints.versionLabel")}</span>
            <select
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              className="mt-1 w-full rounded border border-gray-300 dark:border-gray-700 bg-transparent px-2 py-1"
            >
              {versions.map((v) => <option key={v} value={v}>v{v}</option>)}
            </select>
          </label>
          {error && <div className="text-red-600 dark:text-red-400 text-xs">{error}</div>}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded px-3 py-1.5 text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={async () => {
              setSubmitting(true);
              setError(null);
              try {
                await onSubmit({ name: name.trim(), version: version.trim() });
              } catch (err) {
                setError((err as Error).message);
                setSubmitting(false);
              }
            }}
            className="rounded bg-blue-600 text-white px-3 py-1.5 text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {mode === "create" ? t("endpoints.create") : t("endpoints.switch")}
          </button>
        </div>
      </div>
    </div>
  );
}
