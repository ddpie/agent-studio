import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Trash2, Loader2, AlertTriangle, Eye, EyeOff, KeyRound } from "lucide-react";
import {
  listAgentSecrets,
  putAgentSecret,
  deleteAgentSecret,
  type AgentSecretItem,
} from "../../lib/api-client";
import { useWorkspaceStore } from "../../stores/workspace-store";
import { toast } from "../../lib/toast";
import ConfirmDialog from "../ui/ConfirmDialog";

interface Props {
  agentId: string;
}

// Secrets Manager convention — uppercase alphanumerics + underscore, 1..64 chars.
// Mirrors the backend validator in shared/validators.py so users see a friendly
// message before hitting the server.
const KEY_RE = /^[A-Z0-9_]{1,64}$/;
const MAX_VALUE_LEN = 4096;

function isValidKey(key: string): boolean {
  return KEY_RE.test(key);
}

export default function SecretsTab({ agentId }: Props) {
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspaceStore();
  // Backend gate is `min_role="admin"` for all secret ops. Viewers/editors can
  // still try to list (and will get a 403), so we hide mutation affordances
  // unless the caller is admin/owner.
  const role = currentWorkspace?.role || "viewer";
  const canManage = role === "admin" || role === "owner";

  const [secrets, setSecrets] = useState<AgentSecretItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const items = await listAgentSecrets(agentId);
      setSecrets(items);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function onDelete(key: string) {
    setPendingDelete(key);
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    const key = pendingDelete;
    setPendingDelete(null);
    try {
      await deleteAgentSecret(agentId, key);
      toast.success(t("secrets.deleted", { name: key }));
      refresh();
    } catch (err) {
      toast.error(err as Error);
    }
  }

  return (
    <div className="p-4" data-testid="secrets-tab">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold">{t("secrets.title")}</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {t("secrets.description")}
          </p>
        </div>
        {canManage && (
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            data-testid="create-secret-btn"
            className="inline-flex items-center gap-1 rounded bg-blue-600 text-white px-3 py-1 text-xs font-medium hover:bg-blue-700"
          >
            <Plus className="w-3 h-3" />
            {t("secrets.create")}
          </button>
        )}
      </div>

      {error && (
        <div className="text-sm text-red-600 dark:text-red-400" data-testid="secrets-error">
          {error.message}
        </div>
      )}

      {loading && !secrets && (
        <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t("common.loading")}
        </div>
      )}

      {secrets?.length === 0 && !loading && (
        <div className="text-sm text-gray-500 dark:text-gray-400" data-testid="secrets-empty">
          {t("secrets.empty")}
        </div>
      )}

      {secrets && secrets.length > 0 && (
        <table className="w-full text-sm" data-testid="secrets-table">
          <thead className="text-xs text-gray-500 dark:text-gray-400 uppercase">
            <tr>
              <th className="text-left py-2">{t("secrets.key")}</th>
              <th className="text-left py-2">{t("secrets.createdAt")}</th>
              <th className="text-left py-2">{t("secrets.updatedAt")}</th>
              <th className="py-2"></th>
            </tr>
          </thead>
          <tbody>
            {secrets.map((s) => (
              <tr
                key={s.key}
                className="border-t border-gray-200 dark:border-gray-800"
                data-testid={`secret-row-${s.key}`}
              >
                <td className="py-2 font-mono text-xs flex items-center gap-1.5">
                  <KeyRound className="w-3 h-3 text-gray-400 dark:text-gray-500" />
                  {s.key}
                </td>
                <td className="py-2 text-xs">{s.created_at || "-"}</td>
                <td className="py-2 text-xs">{s.updated_at || "-"}</td>
                <td className="py-2 text-right">
                  {canManage && (
                    <button
                      type="button"
                      onClick={() => onDelete(s.key)}
                      data-testid={`delete-secret-${s.key}`}
                      className="p-1 text-red-500 hover:text-red-700 dark:hover:text-red-400"
                      aria-label={t("secrets.delete")}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {showCreate && (
        <CreateSecretModal
          agentId={agentId}
          existingKeys={secrets?.map((s) => s.key) ?? []}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            refresh();
          }}
        />
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        title={t("secrets.delete")}
        message={t("secrets.confirmDelete", { name: pendingDelete ?? "" })}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        danger
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

function CreateSecretModal({
  agentId,
  existingKeys,
  onClose,
  onCreated,
}: {
  agentId: string;
  existingKeys: string[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [showValue, setShowValue] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const keyErr = key && !isValidKey(key) ? t("secrets.errors.keyFormat") : null;
  const valueErr = value.length > MAX_VALUE_LEN ? t("secrets.errors.valueLength") : null;
  const isOverwrite = !!key && existingKeys.includes(key);

  const canSubmit =
    !submitting && !!key && !keyErr && !!value && !valueErr;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setErr(null);
    try {
      await putAgentSecret(agentId, key, value);
      // Never reference `value` in any log / toast — keep secrets out of
      // client-side observability surfaces entirely.
      toast.success(t("secrets.created", { name: key }));
      onCreated();
    } catch (e2) {
      const msg = (e2 as Error).message || "Failed to save secret";
      setErr(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="create-secret-modal"
    >
      <div
        className="bg-white dark:bg-gray-900 rounded-lg p-5 w-[520px] max-w-full"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold mb-3">{t("secrets.modalTitle")}</h3>

        <div className="mb-3 flex items-start gap-2 p-2 rounded bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900">
          <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
          <div className="text-xs text-amber-800 dark:text-amber-200">
            {t("secrets.warning")}
          </div>
        </div>

        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1" htmlFor="secret-key">
              {t("secrets.keyLabel")}
            </label>
            <input
              id="secret-key"
              data-testid="secret-key-input"
              type="text"
              value={key}
              onChange={(e) => setKey(e.target.value.toUpperCase())}
              placeholder="MY_API_KEY"
              className="w-full text-sm font-mono px-2 py-1.5 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100"
              maxLength={64}
              autoComplete="off"
              spellCheck={false}
            />
            <div className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">{t("secrets.keyHint")}</div>
            {keyErr && (
              <div className="text-xs text-red-600 dark:text-red-400 mt-1" data-testid="secret-key-error">
                {keyErr}
              </div>
            )}
            {isOverwrite && !keyErr && (
              <div className="text-xs text-amber-600 dark:text-amber-400 mt-1" data-testid="secret-overwrite-warning">
                {t("secrets.overwriteWarning", { name: key })}
              </div>
            )}
          </div>

          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1" htmlFor="secret-value">
              {t("secrets.valueLabel")}
            </label>
            <div className="relative">
              <input
                id="secret-value"
                data-testid="secret-value-input"
                type={showValue ? "text" : "password"}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="w-full text-sm px-2 py-1.5 pr-8 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100"
                maxLength={MAX_VALUE_LEN}
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setShowValue((v) => !v)}
                data-testid="toggle-secret-visibility"
                aria-label={showValue ? t("secrets.hideValue") : t("secrets.showValue")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-200"
              >
                {showValue ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
            <div className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
              {t("secrets.valueHint", { count: value.length, max: MAX_VALUE_LEN })}
            </div>
            {valueErr && (
              <div className="text-xs text-red-600 dark:text-red-400 mt-1" data-testid="secret-value-error">
                {valueErr}
              </div>
            )}
          </div>

          {err && (
            <div className="flex items-start gap-2 p-2 rounded bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900">
              <AlertTriangle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
              <div className="text-xs text-red-800 dark:text-red-200 break-all">{err}</div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-xs rounded bg-gray-200 dark:bg-gray-800 hover:bg-gray-300 dark:hover:bg-gray-700"
              data-testid="secret-cancel"
            >
              {t("common.cancel")}
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              data-testid="secret-submit"
              className="px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-1">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  {t("common.loading")}
                </span>
              ) : isOverwrite ? (
                t("secrets.overwrite")
              ) : (
                t("common.save")
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
