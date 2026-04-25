import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Key, Save, Trash2, Check } from "lucide-react";
import {
  getKiroKey, putKiroKey, deleteKiroKey,
  type KiroKeyInfo, type KiroRegion,
} from "../../lib/api-client";
import { toast } from "../../lib/toast";
import { formatDateTime } from "../../lib/date-format";
import { invalidateKiroKeyStatus } from "../../hooks/useKiroKeyStatus";

const KIRO_REGIONS: { id: KiroRegion; label: string }[] = [
  { id: "us-east-1", label: "us-east-1 (N. Virginia)" },
  { id: "eu-central-1", label: "eu-central-1 (Frankfurt)" },
];

// Minimal card that lives inside WorkspaceSettingsTab. Admin-only edit;
// viewers see read-only "Configured / Not configured". The plaintext
// value never round-trips through the API, so the input is always empty
// on load — user types a new key to overwrite.
export default function KiroKeySection({ canEdit }: { canEdit: boolean }) {
  const { t } = useTranslation();
  const [info, setInfo] = useState<KiroKeyInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [region, setRegion] = useState<KiroRegion>("us-east-1");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await getKiroKey();
      setInfo(data);
      setRegion(data.region || "us-east-1");
    } catch {
      setInfo(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const onSave = async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    try {
      const updated = await putKiroKey(apiKey.trim(), region);
      setInfo(updated);
      invalidateKiroKeyStatus();
      setApiKey("");
      toast.success(t("kiroKey.saved", "API key saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    if (!confirm(t("kiroKey.confirmDelete", "Remove the Kiro API key for this workspace? Meta-Agent will stop working until a new key is set."))) return;
    setSaving(true);
    try {
      const updated = await deleteKiroKey();
      setInfo(updated);
      invalidateKiroKeyStatus();
      toast.success(t("kiroKey.removed", "API key removed"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "delete failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-md border border-gray-200 dark:border-gray-800 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Key className="w-4 h-4 text-gray-500 dark:text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          {t("kiroKey.title", "Kiro API Key")}
        </h3>
      </div>
      <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-3">
        {t(
          "kiroKey.description",
          "Per-workspace API key used to reach the Meta-Agent backend. Admins only. The key is stored encrypted and never returned to the browser after saving.",
        )}
      </p>

      {loading && (
        <div className="text-[11px] text-gray-400">{t("common.loading")}</div>
      )}

      {!loading && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            {info?.configured ? (
              <span className="inline-flex items-center gap-1 text-green-700 dark:text-green-400">
                <Check className="w-3 h-3" /> {t("kiroKey.configured", "Configured")}
              </span>
            ) : (
              <span className="text-amber-700 dark:text-amber-400">
                {t("kiroKey.notConfigured", "Not configured — Meta-Agent will not work until a key is set.")}
              </span>
            )}
            {info?.configured && (
              <span className="text-gray-400 dark:text-gray-500">
                · {t("kiroKey.region", "Region")} {info.region}
              </span>
            )}
            {info?.configured && info.lastUpdated && (
              <span className="text-gray-400 dark:text-gray-500">
                · {t("kiroKey.lastUpdated", "Updated")} {formatDateTime(info.lastUpdated)}
                {info.updatedBy ? ` ${t("kiroKey.by", "by")} ${info.updatedBy}` : ""}
              </span>
            )}
          </div>

          {canEdit && (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={info?.configured
                    ? t("kiroKey.inputReplace", "Enter a new key to replace")
                    : t("kiroKey.inputNew", "Paste Kiro API key")}
                  className="flex-1 px-2 py-1 text-xs border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
                <select
                  value={region}
                  onChange={(e) => setRegion(e.target.value as KiroRegion)}
                  className="px-2 py-1 text-xs border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200"
                  title={t("kiroKey.regionHint", "Kiro service region. Pick the region matching your Kiro subscription.")}
                >
                  {KIRO_REGIONS.map((r) => (
                    <option key={r.id} value={r.id}>{r.label}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={onSave}
                  disabled={saving || !apiKey.trim()}
                  className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  <Save className="w-3 h-3" /> {t("common.save")}
                </button>
                {info?.configured && (
                  <button
                    type="button"
                    onClick={onDelete}
                    disabled={saving}
                    className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded border border-red-200 text-red-700 hover:bg-red-50 dark:border-red-900/60 dark:text-red-400 dark:hover:bg-red-950/40 disabled:opacity-50"
                  >
                    <Trash2 className="w-3 h-3" /> {t("common.remove", "Remove")}
                  </button>
                )}
              </div>
            </div>
          )}
          {!canEdit && (
            <p className="text-[11px] text-gray-400 dark:text-gray-500">
              {t("kiroKey.adminOnly", "Workspace admins can configure the API key.")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
