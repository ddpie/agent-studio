import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, Trash2, Eye, EyeOff, Shield, KeyRound, X, Check } from "lucide-react";
import {
  listAgentSecrets,
  putAgentSecret,
  deleteAgentSecret,
  type AgentSecretItem,
} from "../../lib/api-client";
import { useWorkspaceStore } from "../../stores/workspace-store";
import { toast } from "../../lib/toast";
import Section from "./shared/Section";

// Mirrors lambda/shared/validators.py SECRET_KEY_PATTERN and the SecretsTab
// component — uppercase alnum + underscore, 1–64 chars.
const KEY_RE = /^[A-Z0-9_]{1,64}$/;
const MAX_VALUE_LEN = 4096;
const inputClass =
  "w-full px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded-lg text-[13px] focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all dark:bg-gray-800 dark:text-gray-100";

function SecretsSection({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspaceStore();
  // Backend gate is min_role="admin"; viewers/editors still get a list
  // (role check happens on the server side), but the UI suppresses
  // affordances they can't use, consistent with SecretsTab.
  const role = currentWorkspace?.role || "viewer";
  const canManage = role === "admin" || role === "owner";

  const [existing, setExisting] = useState<AgentSecretItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [drafts, setDrafts] = useState<{ key: string; value: string }[]>([]);
  const [showValues, setShowValues] = useState(false);
  const [saving, setSaving] = useState(false);
  // Per-row save flag so concurrent adds render their own spinner; batch
  // save used to set a single "saving" flag, which made the UI feel frozen
  // when one slow key blocked the others.
  const [savingKey, setSavingKey] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const items = await listAgentSecrets(agentId);
      setExisting(items);
    } catch (err) {
      setLoadError((err as Error).message || "Failed to load");
      setExisting([]);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const addDraftRow = () => setDrafts((rows) => [...rows, { key: "", value: "" }]);
  const removeDraftRow = (idx: number) =>
    setDrafts((rows) => rows.filter((_, i) => i !== idx));
  const updateDraft = (idx: number, field: "key" | "value", val: string) => {
    setDrafts((rows) =>
      rows.map((r, i) => (i === idx ? { ...r, [field]: val } : r)),
    );
  };

  async function saveDraft(idx: number) {
    const row = drafts[idx];
    if (!row) return;
    const key = row.key.trim().toUpperCase();
    const value = row.value;
    if (!KEY_RE.test(key)) {
      toast.error(t("secrets.errors.keyFormat"));
      return;
    }
    if (!value) {
      toast.error(t("secrets.errors.valueRequired"));
      return;
    }
    if (value.length > MAX_VALUE_LEN) {
      toast.error(t("secrets.errors.valueLength"));
      return;
    }
    // Capture before the write so the success toast can tell the user
    // whether a redeploy is needed. The ARN list passed to the agent
    // runtime is computed at deploy time — adding a NEW key doesn't take
    // effect until the agent is redeployed (rebuilding AGENT_STUDIO_
    // SECRET_ARNS). Overwriting an EXISTING key's value doesn't need
    // a redeploy: the ARN is unchanged, the agent's next cold start
    // fetches the new value from Secrets Manager automatically.
    const isNewKey = !existingKeys.has(key);
    setSavingKey(key);
    try {
      await putAgentSecret(agentId, key, value);
      if (isNewKey) {
        toast.success(t("secrets.createdRedeployHint", { name: key }));
      } else {
        toast.success(t("secrets.updated", { name: key }));
      }
      removeDraftRow(idx);
      refresh();
    } catch (err) {
      toast.error((err as Error).message || t("secrets.saveFailed"));
    } finally {
      setSavingKey(null);
    }
  }

  async function saveAllDrafts() {
    // Sequential save — keeps per-row toast UX and preserves ordering of
    // createdAt timestamps. N is small (< 10 typical).
    const valid = drafts.filter(
      (r) => KEY_RE.test(r.key.trim().toUpperCase()) && r.value,
    );
    if (valid.length === 0) return;
    setSaving(true);
    // Count how many of the pending writes are NEW keys vs overwrites so
    // the summary toast can tell the user whether a redeploy is needed.
    let newCount = 0;
    try {
      for (let i = drafts.length - 1; i >= 0; i--) {
        const r = drafts[i];
        const key = r.key.trim().toUpperCase();
        if (!KEY_RE.test(key) || !r.value) continue;
        if (!existingKeys.has(key)) newCount += 1;
        try {
          await putAgentSecret(agentId, key, r.value);
        } catch (err) {
          toast.error(`${key}: ${(err as Error).message || "failed"}`);
          continue;
        }
      }
      if (newCount > 0) {
        toast.success(t("secrets.savedNRedeployHint", {
          count: valid.length,
          newCount,
        }));
      } else {
        toast.success(t("secrets.savedN", { count: valid.length }));
      }
      setDrafts([]);
      refresh();
    } finally {
      setSaving(false);
    }
  }

  async function onDeleteExisting(key: string) {
    try {
      await deleteAgentSecret(agentId, key);
      toast.success(t("secrets.deleted", { name: key }));
      refresh();
    } catch (err) {
      toast.error((err as Error).message || "failed");
    }
  }

  const existingKeys = new Set((existing ?? []).map((s) => s.key));
  const hasDraftOverwrite = drafts.some((r) => {
    const k = r.key.trim().toUpperCase();
    return k && existingKeys.has(k);
  });

  return (
    <Section title={t("secrets.title")} icon={<Shield className="w-3.5 h-3.5" />}>
      <p className="text-[11px] text-gray-400 dark:text-gray-500">
        {t("secrets.description")}
      </p>

      {loadError && (
        <div className="text-xs text-red-600 dark:text-red-400">
          {loadError}
        </div>
      )}

      {loading && !existing && (
        <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          {t("common.loading")}
        </div>
      )}

      {/* Existing secrets — read-only list with delete + overwrite */}
      {existing && existing.length > 0 && (
        <div className="space-y-1">
          {existing.map((s) => (
            <div
              key={s.key}
              className="flex items-center gap-2 text-xs px-2 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-800"
              data-testid={`existing-secret-${s.key}`}
            >
              <KeyRound className="w-3 h-3 text-gray-400 dark:text-gray-500 flex-shrink-0" />
              <span className="font-mono flex-1 truncate">{s.key}</span>
              <span className="font-mono text-gray-400 dark:text-gray-600 tracking-wider select-none">
                ••••••••
              </span>
              {canManage && (
                <button
                  type="button"
                  onClick={() => onDeleteExisting(s.key)}
                  aria-label={t("secrets.delete")}
                  className="p-1 text-gray-300 dark:text-gray-600 hover:text-red-500 dark:hover:text-red-400 transition-colors"
                  data-testid={`delete-secret-${s.key}`}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {existing?.length === 0 && !loading && drafts.length === 0 && (
        <div className="text-xs text-gray-400 dark:text-gray-500">
          {t("secrets.empty")}
        </div>
      )}

      {/* Draft rows — user-editable */}
      {drafts.map((r, idx) => {
        const key = r.key.trim().toUpperCase();
        const keyValid = !key || KEY_RE.test(key);
        const isOverwrite = key && existingKeys.has(key);
        const isSaving = savingKey === key;
        const confirmable =
          key && KEY_RE.test(key) && !!r.value && r.value.length <= MAX_VALUE_LEN;
        return (
          <div
            key={idx}
            className="flex gap-2 items-start"
            data-testid={`draft-secret-${idx}`}
          >
            <div className="flex-1 min-w-0">
              <input
                type="text"
                value={r.key}
                onChange={(e) => updateDraft(idx, "key", e.target.value.toUpperCase())}
                placeholder={t("secrets.keyPlaceholder")}
                className={`${inputClass} font-mono ${!keyValid ? "border-red-400 dark:border-red-600" : ""}`}
                maxLength={64}
                autoComplete="off"
                spellCheck={false}
                data-testid={`draft-key-input-${idx}`}
              />
              {!keyValid && (
                <div className="text-[11px] text-red-600 dark:text-red-400 mt-0.5">
                  {t("secrets.errors.keyFormat")}
                </div>
              )}
              {isOverwrite && keyValid && (
                <div className="text-[11px] text-amber-600 dark:text-amber-400 mt-0.5">
                  {t("secrets.overwriteWarning", { name: key })}
                </div>
              )}
            </div>
            <input
              type={showValues ? "text" : "password"}
              value={r.value}
              onChange={(e) => updateDraft(idx, "value", e.target.value)}
              placeholder={t("secrets.valuePlaceholder")}
              className={`${inputClass} flex-1 min-w-0`}
              maxLength={MAX_VALUE_LEN}
              autoComplete="off"
              spellCheck={false}
              data-testid={`draft-value-input-${idx}`}
            />
            <button
              type="button"
              onClick={() => saveDraft(idx)}
              disabled={!confirmable || isSaving || !canManage}
              aria-label={t("common.save")}
              className="p-1.5 text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              data-testid={`save-draft-${idx}`}
            >
              {isSaving ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Check className="w-3.5 h-3.5" />
              )}
            </button>
            <button
              type="button"
              onClick={() => removeDraftRow(idx)}
              aria-label={t("common.cancel")}
              className="p-1.5 text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 transition-colors"
              data-testid={`remove-draft-${idx}`}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}

      {/* Toolbar */}
      {canManage && (
        <div className="flex items-center gap-3 mt-1">
          <button
            type="button"
            onClick={addDraftRow}
            className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 font-medium"
            data-testid="add-secret-btn"
          >
            <Plus className="w-3.5 h-3.5" /> {t("secrets.addSecret")}
          </button>
          {drafts.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => setShowValues((v) => !v)}
                className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
              >
                {showValues ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                {showValues ? t("secrets.hide") : t("secrets.show")}
              </button>
              <button
                type="button"
                onClick={saveAllDrafts}
                disabled={saving || !drafts.some((r) => r.key && r.value)}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 ml-auto font-medium shadow-sm"
                data-testid="save-all-drafts-btn"
              >
                {saving ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Check className="w-3.5 h-3.5" />
                )}
                {hasDraftOverwrite ? t("secrets.overwriteAll") : t("secrets.saveSecrets")}
              </button>
            </>
          )}
        </div>
      )}

      {!canManage && existing?.length === 0 && (
        <div className="text-[11px] text-gray-400 dark:text-gray-500">
          {t("secrets.readOnlyHint")}
        </div>
      )}
    </Section>
  );
}

export default SecretsSection;
