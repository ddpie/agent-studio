import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchUserAttributes } from "aws-amplify/auth";
import { Save, Settings2, Crown, AlertTriangle, Trash2 } from "lucide-react";
import {
  ApiError,
  deleteWorkspace,
  fetchWorkspaceDetail,
  transferWorkspaceOwnership,
  updateWorkspace,
  type WorkspaceDetail,
  type WorkspaceMember,
} from "../../lib/api-client";
import { toast } from "../../lib/toast";
import { useWorkspaceStore, type WorkspaceRole } from "../../stores/workspace-store";
import ConfirmDialog from "../ui/ConfirmDialog";

const ROLE_LEVEL: Record<WorkspaceRole, number> = {
  viewer: 1,
  editor: 2,
  admin: 3,
  owner: 4,
};

function formatDate(iso: string | undefined | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function WorkspaceSettingsTab() {
  const { t } = useTranslation();
  const { currentWorkspace, refreshWorkspaces } = useWorkspaceStore();
  const wsId = currentWorkspace?.workspaceId;
  const role = currentWorkspace?.role;
  // Backend allows admin+ for update; owner-only for transfer / delete.
  const canEdit = !!role && ROLE_LEVEL[role] >= ROLE_LEVEL.admin;
  const isOwner = role === "owner";

  const [detail, setDetail] = useState<WorkspaceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);

  // Editable fields
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  // Transfer ownership
  const [transferTargetId, setTransferTargetId] = useState<string>("");
  const [transferBusy, setTransferBusy] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);

  // Delete
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteInput, setDeleteInput] = useState("");
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    fetchUserAttributes()
      .then((attrs) => setCurrentUserId(attrs.sub || null))
      .catch(() => setCurrentUserId(null));
  }, []);

  const load = useCallback(async () => {
    if (!wsId) return;
    setLoading(true);
    setLoadErr(null);
    try {
      const d = await fetchWorkspaceDetail(wsId);
      setDetail(d);
      setName(d.name || "");
      setDescription(d.description || "");
    } catch (err) {
      setLoadErr(err instanceof Error ? err.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, [wsId]);

  useEffect(() => {
    load();
  }, [load]);

  const ownerMember = useMemo<WorkspaceMember | undefined>(
    () => detail?.members.find((m) => m.role === "owner"),
    [detail]
  );

  const transferCandidates = useMemo<WorkspaceMember[]>(
    () => (detail?.members ?? []).filter((m) => m.userId !== currentUserId && m.role !== "owner"),
    [detail, currentUserId]
  );

  const dirty =
    !!detail && (name.trim() !== (detail.name || "") || description !== (detail.description || ""));

  const nameError = useMemo(() => {
    const trimmed = name.trim();
    if (!trimmed) return t("workspace.settings.nameRequired");
    if (trimmed.length > 100) return t("workspace.settings.nameTooLong");
    return null;
  }, [name, t]);

  const handleSave = async () => {
    if (!wsId || !detail || !canEdit) return;
    if (nameError) {
      toast.error(nameError);
      return;
    }
    setSaving(true);
    try {
      const resp = await updateWorkspace(wsId, {
        name: name.trim(),
        description: description || "",
        expected_updated_at: detail.updated_at || "",
      });
      toast.success(t("workspace.settings.saved"));
      setDetail({
        ...detail,
        name: resp.name,
        description: resp.description || "",
        updated_at: resp.updated_at,
      });
      // Refresh the sidebar switcher so the new name propagates.
      await refreshWorkspaces();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error(t("workspace.settings.conflict"));
        await load();
      } else {
        const msg =
          err instanceof ApiError && err.body?.error
            ? err.body.error
            : t("workspace.settings.saveFailed");
        toast.error(msg);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleTransfer = async () => {
    if (!wsId || !transferTargetId) return;
    setTransferBusy(true);
    try {
      await transferWorkspaceOwnership(wsId, transferTargetId);
      toast.success(t("workspace.settings.transferSuccess"));
      setTransferOpen(false);
      setTransferTargetId("");
      await refreshWorkspaces();
      await load();
    } catch (err) {
      const msg =
        err instanceof ApiError && err.body?.error
          ? err.body.error
          : t("workspace.settings.transferFailed");
      toast.error(msg);
      setTransferOpen(false);
    } finally {
      setTransferBusy(false);
    }
  };

  const deleteBlocked = (detail?.members.length ?? 0) > 1;
  const deleteConfirmText = (detail?.name || "").trim();
  const deleteMatches = deleteInput.trim() === deleteConfirmText && !!deleteConfirmText;

  const handleDelete = async () => {
    if (!wsId || !isOwner || deleteBlocked || !deleteMatches) return;
    setDeleting(true);
    try {
      await deleteWorkspace(wsId);
      toast.success(t("workspace.settings.deleteSuccess"));
      // Clear the persisted pointer so the next load picks another workspace.
      localStorage.removeItem("agent-studio-workspace-id");
      // Hard reload so stale workspace-scoped state clears everywhere.
      window.location.assign(window.location.pathname);
    } catch (err) {
      const msg =
        err instanceof ApiError && err.body?.error
          ? err.body.error
          : t("workspace.settings.deleteFailed");
      toast.error(msg);
      setDeleting(false);
      setDeleteOpen(false);
    }
  };

  if (!wsId) {
    return (
      <div className="text-xs text-gray-500 dark:text-gray-400 p-4">
        {t("workspace.members.noWorkspace")}
      </div>
    );
  }

  const transferTarget = transferCandidates.find((m) => m.userId === transferTargetId);
  const transferTargetLabel =
    transferTarget?.display_name || transferTarget?.userId || "";

  return (
    <div className="space-y-4">
      {/* Metadata / editable fields */}
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide flex items-center gap-1.5">
            <Settings2 className="w-3.5 h-3.5" /> {t("workspace.settings.title")}
          </h3>
        </div>
        <p className="text-[10px] text-gray-500 dark:text-gray-400 mb-3">
          {t("workspace.settings.subtitle")}
        </p>

        {loading && (
          <div className="text-xs text-gray-500 dark:text-gray-400 py-3">{t("common.loading")}</div>
        )}
        {loadErr && !loading && <div className="text-xs text-red-600 py-3">{loadErr}</div>}

        {!loading && !loadErr && detail && (
          <>
            {!canEdit && (
              <div className="mb-3 rounded-md border border-amber-200 dark:border-amber-900/60 bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-200">
                {t("workspace.settings.readOnly")}
              </div>
            )}

            <div className="space-y-3">
              <div>
                <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                  {t("workspace.settings.nameLabel")}
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("workspace.settings.namePlaceholder")}
                  disabled={!canEdit || saving}
                  maxLength={100}
                  className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200 focus:outline-none focus:border-blue-500 disabled:opacity-60"
                />
                {nameError && canEdit && (
                  <div className="mt-1 text-[10px] text-red-600">{nameError}</div>
                )}
              </div>

              <div>
                <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                  {t("workspace.settings.descriptionLabel")}
                </label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder={t("workspace.settings.descriptionPlaceholder")}
                  disabled={!canEdit || saving}
                  rows={3}
                  className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200 focus:outline-none focus:border-blue-500 disabled:opacity-60 resize-y"
                />
              </div>

              {/* Read-only metadata */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] pt-1">
                <div className="flex justify-between gap-2 border-t border-gray-100 dark:border-gray-800 pt-2">
                  <span className="text-gray-500 dark:text-gray-400">
                    {t("workspace.settings.workspaceId")}
                  </span>
                  <span className="font-mono text-gray-700 dark:text-gray-300 truncate" title={detail.workspaceId}>
                    {detail.workspaceId}
                  </span>
                </div>
                <div className="flex justify-between gap-2 border-t border-gray-100 dark:border-gray-800 pt-2">
                  <span className="text-gray-500 dark:text-gray-400">
                    {t("workspace.settings.owner")}
                  </span>
                  <span className="text-gray-700 dark:text-gray-300 truncate" title={ownerMember?.userId || ""}>
                    {ownerMember?.display_name || ownerMember?.userId || "—"}
                  </span>
                </div>
                <div className="flex justify-between gap-2 border-t border-gray-100 dark:border-gray-800 pt-2">
                  <span className="text-gray-500 dark:text-gray-400">
                    {t("workspace.settings.createdAt")}
                  </span>
                  <span className="text-gray-700 dark:text-gray-300">{formatDate(detail.created_at)}</span>
                </div>
                <div className="flex justify-between gap-2 border-t border-gray-100 dark:border-gray-800 pt-2">
                  <span className="text-gray-500 dark:text-gray-400">
                    {t("workspace.settings.updatedAt")}
                  </span>
                  <span className="text-gray-700 dark:text-gray-300">{formatDate(detail.updated_at)}</span>
                </div>
              </div>

              {canEdit && (
                <div className="flex justify-end pt-1">
                  <button
                    onClick={handleSave}
                    disabled={saving || !dirty || !!nameError}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Save className="w-3.5 h-3.5" />
                    {saving ? t("workspace.settings.saving") : t("workspace.settings.save")}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </section>

      {/* Transfer ownership — owner only */}
      {isOwner && detail && (
        <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3 flex items-center gap-1.5">
            <Crown className="w-3.5 h-3.5" /> {t("workspace.settings.transferTitle")}
          </h3>
          <p className="text-[10px] text-gray-500 dark:text-gray-400 mb-3">
            {t("workspace.settings.transferSubtitle")}
          </p>

          {transferCandidates.length === 0 ? (
            <div className="text-[11px] text-gray-500 dark:text-gray-400">
              {t("workspace.settings.transferNoCandidates")}
            </div>
          ) : (
            <div className="flex flex-col sm:flex-row gap-2 sm:items-end">
              <div className="flex-1">
                <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                  {t("workspace.settings.transferPick")}
                </label>
                <select
                  value={transferTargetId}
                  onChange={(e) => setTransferTargetId(e.target.value)}
                  disabled={transferBusy}
                  className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200"
                >
                  <option value="">—</option>
                  {transferCandidates.map((m) => (
                    <option key={m.userId} value={m.userId}>
                      {(m.display_name || m.userId)} · {t(`workspace.role.${m.role}`)}
                    </option>
                  ))}
                </select>
              </div>
              <button
                onClick={() => setTransferOpen(true)}
                disabled={!transferTargetId || transferBusy}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-amber-300 dark:border-amber-900/60 text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 hover:bg-amber-100 dark:hover:bg-amber-900/40 rounded-lg disabled:opacity-50"
              >
                <Crown className="w-3.5 h-3.5" />
                {t("workspace.settings.transferButton")}
              </button>
            </div>
          )}
        </section>
      )}

      {/* Danger zone — owner only */}
      {isOwner && detail && (
        <section className="border border-red-200 dark:border-red-900/60 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-red-700 dark:text-red-300 uppercase tracking-wide mb-3 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" /> {t("workspace.settings.dangerTitle")}
          </h3>

          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium text-gray-800 dark:text-gray-200">
                {t("workspace.settings.deleteTitle")}
              </div>
              <div className="text-[10px] text-gray-500 dark:text-gray-400 mt-0.5">
                {t("workspace.settings.deleteSubtitle")}
              </div>
              {deleteBlocked && (
                <div className="mt-1.5 text-[10px] text-amber-700 dark:text-amber-300">
                  {t("workspace.settings.deleteBlockedMembers")}
                </div>
              )}
            </div>
            <button
              onClick={() => {
                setDeleteInput("");
                setDeleteOpen(true);
              }}
              disabled={deleteBlocked}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-300 dark:border-red-900/60 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/30 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
            >
              <Trash2 className="w-3.5 h-3.5" />
              {t("workspace.settings.deleteButton")}
            </button>
          </div>
        </section>
      )}

      {/* Transfer confirm dialog */}
      <ConfirmDialog
        open={transferOpen}
        title={t("workspace.settings.transferConfirmTitle")}
        message={t("workspace.settings.transferConfirmBody", { name: transferTargetLabel })}
        confirmLabel={t("workspace.settings.transferButton")}
        onConfirm={handleTransfer}
        onCancel={() => setTransferOpen(false)}
        danger
      />

      {/* Delete dialog — needs typed confirmation, custom modal for text match */}
      {deleteOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => {
              if (!deleting) setDeleteOpen(false);
            }}
          />
          <div className="relative bg-white dark:bg-gray-900 rounded-xl shadow-xl w-[440px] p-5">
            <h3 className="text-sm font-semibold text-red-700 dark:text-red-300 flex items-center gap-1.5">
              <AlertTriangle className="w-4 h-4" />
              {t("workspace.settings.deleteConfirmTitle", { name: deleteConfirmText })}
            </h3>
            <p className="text-xs text-gray-600 dark:text-gray-400 mt-2">
              {t("workspace.settings.deleteConfirmBody")}
            </p>
            <div className="mt-3">
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                {t("workspace.settings.deleteTypeLabel")}
              </label>
              <input
                type="text"
                value={deleteInput}
                onChange={(e) => setDeleteInput(e.target.value)}
                placeholder={deleteConfirmText}
                disabled={deleting}
                className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200 focus:outline-none focus:border-red-500"
                autoFocus
              />
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => setDeleteOpen(false)}
                disabled={deleting}
                className="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg disabled:opacity-50"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting || !deleteMatches}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-white bg-red-600 hover:bg-red-700 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Trash2 className="w-3.5 h-3.5" />
                {deleting ? t("workspace.settings.saving") : t("workspace.settings.deleteButton")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
