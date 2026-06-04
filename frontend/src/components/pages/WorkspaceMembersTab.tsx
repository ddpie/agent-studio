import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchUserAttributes } from "aws-amplify/auth";
import { UserPlus, Trash2, LogOut, Users, Copy, X } from "lucide-react";
import {
  ApiError,
  fetchWorkspaceDetail,
  inviteWorkspaceMember,
  leaveWorkspace,
  listWorkspaceInvitations,
  removeWorkspaceMember,
  revokeWorkspaceInvitation,
  updateWorkspaceMemberRole,
  type WorkspaceDetail,
  type WorkspaceInvitation,
  type WorkspaceMember,
} from "../../lib/api-client";
import { toast } from "../../lib/toast";
import { useWorkspaceStore, type WorkspaceRole } from "../../stores/workspace-store";
import ConfirmDialog from "../ui/ConfirmDialog";
import { formatDate } from "../../lib/date-format";

const ROLE_LEVEL: Record<WorkspaceRole, number> = {
  viewer: 1,
  editor: 2,
  admin: 3,
  owner: 4,
};

function canManage(role: WorkspaceRole | undefined): boolean {
  if (!role) return false;
  return ROLE_LEVEL[role] >= ROLE_LEVEL.admin;
}

export default function WorkspaceMembersTab() {
  const { t } = useTranslation();
  const { currentWorkspace, refreshWorkspaces } = useWorkspaceStore();
  const [detail, setDetail] = useState<WorkspaceDetail | null>(null);
  const [invites, setInvites] = useState<WorkspaceInvitation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);

  // Invite modal
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"viewer" | "editor" | "admin">("viewer");
  const [inviteBusy, setInviteBusy] = useState(false);
  const [lastInviteToken, setLastInviteToken] = useState<string | null>(null);

  // Confirm dialogs
  const [removeTarget, setRemoveTarget] = useState<WorkspaceMember | null>(null);
  const [leaveOpen, setLeaveOpen] = useState(false);

  const role = currentWorkspace?.role;
  const isAdmin = canManage(role);
  const isOwner = role === "owner";

  const wsId = currentWorkspace?.workspaceId;

  const load = useCallback(async () => {
    if (!wsId) return;
    setLoading(true);
    setError(null);
    try {
      const d = await fetchWorkspaceDetail(wsId);
      setDetail(d);
      if (canManage(d.members.find((m) => m.userId === currentUserId)?.role as WorkspaceRole | undefined) || isAdmin) {
        try {
          const inv = await listWorkspaceInvitations(wsId);
          setInvites(inv.items || []);
        } catch {
          setInvites([]);
        }
      } else {
        setInvites([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "load failed");
    } finally {
      setLoading(false);
    }
  }, [wsId, isAdmin, currentUserId]);

  useEffect(() => {
    // Resolve current user's Cognito sub for self-detection (used to disable destructive actions on self).
    fetchUserAttributes()
      .then((attrs) => setCurrentUserId(attrs.sub || null))
      .catch(() => setCurrentUserId(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleInvite = async () => {
    if (!wsId) return;
    const email = inviteEmail.trim();
    if (!email) {
      toast.error(t("workspace.members.emailRequired"));
      return;
    }
    setInviteBusy(true);
    try {
      const resp = await inviteWorkspaceMember(wsId, email, inviteRole);
      setLastInviteToken(resp.token);
      toast.success(t("workspace.members.inviteCreated"));
      setInviteEmail("");
      await load();
    } catch (err) {
      const msg =
        err instanceof ApiError && err.body?.error
          ? err.body.error
          : t("workspace.members.inviteFailed");
      toast.error(msg);
    } finally {
      setInviteBusy(false);
    }
  };

  const handleRoleChange = async (member: WorkspaceMember, newRole: "viewer" | "editor" | "admin") => {
    if (!wsId) return;
    try {
      await updateWorkspaceMemberRole(wsId, member.userId, newRole);
      toast.success(t("workspace.members.roleUpdated"));
      await load();
    } catch (err) {
      const msg =
        err instanceof ApiError && err.body?.error
          ? err.body.error
          : t("workspace.members.roleUpdateFailed");
      toast.error(msg);
    }
  };

  const handleRemove = async () => {
    if (!wsId || !removeTarget) return;
    try {
      await removeWorkspaceMember(wsId, removeTarget.userId);
      toast.success(t("workspace.members.removed"));
      setRemoveTarget(null);
      await load();
    } catch (err) {
      const msg =
        err instanceof ApiError && err.body?.error
          ? err.body.error
          : t("workspace.members.removeFailed");
      toast.error(msg);
      setRemoveTarget(null);
    }
  };

  const handleLeave = async () => {
    if (!wsId) return;
    try {
      await leaveWorkspace(wsId);
      toast.success(t("workspace.members.leftSuccess"));
      setLeaveOpen(false);
      await refreshWorkspaces();
      // Reload so stale workspace data (agents, chats) clears.
      window.location.assign(window.location.pathname);
    } catch (err) {
      const msg =
        err instanceof ApiError && err.body?.error
          ? err.body.error
          : t("workspace.members.leaveFailed");
      toast.error(msg);
      setLeaveOpen(false);
    }
  };

  const handleRevokeInvite = async (token: string) => {
    if (!wsId) return;
    try {
      await revokeWorkspaceInvitation(wsId, token);
      toast.success(t("workspace.members.inviteRevoked"));
      await load();
    } catch {
      toast.error(t("workspace.members.inviteRevokeFailed"));
    }
  };

  const copyInviteLink = (token: string) => {
    const url = `${window.location.origin}${window.location.pathname}?invite=${encodeURIComponent(token)}`;
    navigator.clipboard.writeText(url).then(
      () => toast.success(t("workspace.members.linkCopied")),
      () => toast.error(t("workspace.members.linkCopyFailed"))
    );
  };

  if (!wsId) {
    return (
      <div className="text-xs text-gray-500 dark:text-gray-400 p-4">
        {t("workspace.members.noWorkspace")}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header / summary */}
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide flex items-center gap-1.5">
            <Users className="w-3.5 h-3.5" /> {t("workspace.members.title")}
          </h3>
          <div className="flex items-center gap-2">
            {!isOwner && role && (
              <button
                onClick={() => setLeaveOpen(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20"
              >
                <LogOut className="w-3.5 h-3.5" /> {t("workspace.members.leave")}
              </button>
            )}
            {isAdmin && (
              <button
                onClick={() => setInviteOpen(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg"
              >
                <UserPlus className="w-3.5 h-3.5" /> {t("workspace.members.invite")}
              </button>
            )}
          </div>
        </div>

        <div className="text-[10px] text-gray-500 dark:text-gray-400 mb-3">
          {t("workspace.members.subtitle")}
        </div>

        {loading && (
          <div className="text-xs text-gray-500 dark:text-gray-400 py-3">{t("common.loading")}</div>
        )}
        {error && !loading && (
          <div className="text-xs text-red-600 dark:text-red-400 py-3">{error}</div>
        )}

        {!loading && !error && detail && (
          <div className="overflow-hidden border border-gray-100 dark:border-gray-800 rounded-lg">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 dark:bg-gray-900/60 text-gray-500 dark:text-gray-400">
                <tr>
                  <th className="text-left font-medium px-3 py-2">{t("workspace.members.colUser")}</th>
                  <th className="text-left font-medium px-3 py-2">{t("workspace.members.colRole")}</th>
                  <th className="text-left font-medium px-3 py-2">{t("workspace.members.colJoined")}</th>
                  {isAdmin && <th className="text-right font-medium px-3 py-2">{t("workspace.members.colActions")}</th>}
                </tr>
              </thead>
              <tbody>
                {detail.members.map((m) => {
                  const isSelf = currentUserId && m.userId === currentUserId;
                  const targetRole = m.role as WorkspaceRole;
                  const canChangeRole =
                    isAdmin &&
                    targetRole !== "owner" &&
                    !isSelf &&
                    // Admin can only demote admins if caller is owner.
                    (isOwner || targetRole !== "admin");
                  const canRemove =
                    isAdmin && targetRole !== "owner" && !isSelf && (isOwner || targetRole !== "admin");
                  const roleOptions: ("viewer" | "editor" | "admin")[] = isOwner
                    ? ["viewer", "editor", "admin"]
                    : ["viewer", "editor"];
                  return (
                    <tr
                      key={m.userId}
                      className="border-t border-gray-100 dark:border-gray-800 text-gray-700 dark:text-gray-200"
                    >
                      <td className="px-3 py-2">
                        <div className="font-medium truncate max-w-[220px]">
                          {m.display_name || m.email || m.userId}
                        </div>
                        {m.display_name && m.email && (
                          <div className="text-[10px] text-gray-400 dark:text-gray-500 truncate max-w-[220px]">
                            {m.email}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {canChangeRole ? (
                          <select
                            value={targetRole}
                            onChange={(e) =>
                              handleRoleChange(m, e.target.value as "viewer" | "editor" | "admin")
                            }
                            className="text-xs px-2 py-1 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200"
                          >
                            {roleOptions.map((r) => (
                              <option key={r} value={r}>
                                {t(`workspace.role.${r}`)}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span
                            className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${
                              targetRole === "owner"
                                ? "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
                                : targetRole === "admin"
                                  ? "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300"
                                  : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
                            }`}
                          >
                            {t(`workspace.role.${targetRole}`)}
                          </span>
                        )}
                        {isSelf && (
                          <span className="ml-1.5 text-[10px] text-gray-400 dark:text-gray-500">
                            ({t("workspace.members.you")})
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-gray-500 dark:text-gray-400">
                        {formatDate(m.joined_at)}
                      </td>
                      {isAdmin && (
                        <td className="px-3 py-2 text-right">
                          {canRemove && (
                            <button
                              onClick={() => setRemoveTarget(m)}
                              className="p-1 text-gray-400 dark:text-gray-500 hover:text-red-600 dark:hover:text-red-400 rounded"
                              title={t("workspace.members.remove")}
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Pending invitations */}
      {isAdmin && invites.length > 0 && (
        <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3">
            {t("workspace.members.pendingTitle")}
          </h3>
          <ul className="space-y-2">
            {invites.map((inv) => (
              <li
                key={inv.token}
                className="flex items-center justify-between gap-2 text-xs border border-gray-100 dark:border-gray-800 rounded-lg px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-gray-700 dark:text-gray-200 truncate">{inv.email}</div>
                  <div className="text-[10px] text-gray-500 dark:text-gray-400">
                    {t(`workspace.role.${inv.role}`)}
                    {inv.created_at ? ` · ${formatDate(inv.created_at, "")}` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => copyInviteLink(inv.token)}
                    className="p-1 text-gray-400 dark:text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 rounded"
                    title={t("workspace.members.copyLink")}
                  >
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => handleRevokeInvite(inv.token)}
                    className="p-1 text-gray-400 dark:text-gray-500 hover:text-red-600 dark:hover:text-red-400 rounded"
                    title={t("workspace.members.revokeInvite")}
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Invite modal */}
      {inviteOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => {
              if (!inviteBusy) {
                setInviteOpen(false);
                setLastInviteToken(null);
              }
            }}
          />
          <div className="relative bg-white dark:bg-gray-900 rounded-xl shadow-xl w-[440px] p-5">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              {t("workspace.members.inviteTitle")}
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1.5">
              {t("workspace.members.inviteSubtitle")}
            </p>

            <div className="mt-4 space-y-3">
              <div>
                <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                  {t("workspace.members.emailLabel")}
                </label>
                <input
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="user@example.com"
                  disabled={inviteBusy}
                  className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
                  {t("workspace.members.roleLabel")}
                </label>
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value as "viewer" | "editor" | "admin")}
                  disabled={inviteBusy}
                  className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200"
                >
                  <option value="viewer">{t("workspace.role.viewer")}</option>
                  <option value="editor">{t("workspace.role.editor")}</option>
                  {isOwner && <option value="admin">{t("workspace.role.admin")}</option>}
                </select>
              </div>

              {lastInviteToken && (
                <div className="border border-blue-200 dark:border-blue-900/60 bg-blue-50 dark:bg-blue-950/40 rounded-lg p-3">
                  <div className="text-[11px] font-semibold text-blue-800 dark:text-blue-200 mb-1">
                    {t("workspace.members.inviteLinkTitle")}
                  </div>
                  <div className="text-[10px] text-blue-700/80 dark:text-blue-300/80 mb-2">
                    {t("workspace.members.inviteLinkHint")}
                  </div>
                  <div className="flex gap-2">
                    <input
                      readOnly
                      value={`${window.location.origin}${window.location.pathname}?invite=${lastInviteToken}`}
                      className="flex-1 px-2 py-1 text-[10px] border border-blue-200 dark:border-blue-900/60 rounded bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200 font-mono"
                    />
                    <button
                      onClick={() => copyInviteLink(lastInviteToken)}
                      className="flex items-center gap-1 px-2 py-1 text-[10px] text-white bg-blue-600 hover:bg-blue-700 rounded"
                    >
                      <Copy className="w-3 h-3" /> {t("common.copy")}
                    </button>
                  </div>
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => {
                  setInviteOpen(false);
                  setLastInviteToken(null);
                }}
                disabled={inviteBusy}
                className="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg disabled:opacity-50"
              >
                {t("common.close")}
              </button>
              <button
                onClick={handleInvite}
                disabled={inviteBusy || !inviteEmail.trim()}
                className="px-3 py-1.5 text-xs text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50"
              >
                {inviteBusy ? t("common.loading") : t("workspace.members.sendInvite")}
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!removeTarget}
        title={t("workspace.members.removeConfirmTitle")}
        message={t("workspace.members.removeConfirmBody", {
          name: removeTarget?.display_name || removeTarget?.email || removeTarget?.userId || "",
        })}
        confirmLabel={t("workspace.members.remove")}
        onConfirm={handleRemove}
        onCancel={() => setRemoveTarget(null)}
        danger
      />

      <ConfirmDialog
        open={leaveOpen}
        title={t("workspace.members.leaveConfirmTitle")}
        message={t("workspace.members.leaveConfirmBody", {
          name: currentWorkspace?.name || "",
        })}
        confirmLabel={t("workspace.members.leave")}
        onConfirm={handleLeave}
        onCancel={() => setLeaveOpen(false)}
        danger
      />
    </div>
  );
}
