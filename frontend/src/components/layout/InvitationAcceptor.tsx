import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { acceptInvitation, verifyInvitation, setWorkspaceId, ApiError } from "../../lib/api-client";
import { useWorkspaceStore } from "../../stores/workspace-store";
import { toast } from "../../lib/toast";

/** Reads the invite token from the hash query (e.g. #/path?invite=xxx) or search. */
function readInviteToken(): string | null {
  // React Router v7 hash mode keeps actual search in the hash portion after a ?.
  const hash = window.location.hash || "";
  const qIndex = hash.indexOf("?");
  if (qIndex >= 0) {
    const params = new URLSearchParams(hash.slice(qIndex + 1));
    const t = params.get("invite");
    if (t) return t;
  }
  const search = new URLSearchParams(window.location.search);
  return search.get("invite");
}

function clearInviteFromUrl() {
  // Clean both possible locations so the dialog won't re-open on refresh.
  const search = new URLSearchParams(window.location.search);
  if (search.has("invite")) {
    search.delete("invite");
    const newSearch = search.toString();
    const newUrl =
      window.location.pathname +
      (newSearch ? `?${newSearch}` : "") +
      window.location.hash;
    window.history.replaceState(null, "", newUrl);
  }
  const hash = window.location.hash || "";
  const qIndex = hash.indexOf("?");
  if (qIndex >= 0) {
    const hashBase = hash.slice(0, qIndex);
    const hashSearch = new URLSearchParams(hash.slice(qIndex + 1));
    if (hashSearch.has("invite")) {
      hashSearch.delete("invite");
      const newHashSearch = hashSearch.toString();
      const newHash = hashBase + (newHashSearch ? `?${newHashSearch}` : "");
      window.history.replaceState(null, "", window.location.pathname + window.location.search + newHash);
    }
  }
}

export default function InvitationAcceptor() {
  const { t } = useTranslation();
  const [token, setToken] = useState<string | null>(null);
  const [workspaceName, setWorkspaceName] = useState<string>("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const tok = readInviteToken();
    if (!tok) return;
    setToken(tok);
    verifyInvitation(tok)
      .then((r) => setWorkspaceName(r.workspaceName || ""))
      .catch(() => setWorkspaceName(""));
  }, []);

  if (!token) return null;

  const handleAccept = async () => {
    setBusy(true);
    try {
      const result = await acceptInvitation(token);
      // Await so the chat-store localStorage clear finishes before the
      // reload — otherwise the previous workspace's chat history survives
      // into the newly-joined workspace.
      await setWorkspaceId(result.workspaceId);
      toast.success(t("workspace.invite.acceptSuccess"));
      clearInviteFromUrl();
      // Refresh the list + reload so the newly-joined workspace is selected.
      await useWorkspaceStore.getState().refreshWorkspaces();
      window.location.assign(window.location.pathname);
    } catch (err) {
      const msg =
        err instanceof ApiError && err.body?.error
          ? err.body.error
          : t("workspace.invite.acceptFailed");
      toast.error(msg);
      setBusy(false);
      setToken(null);
      clearInviteFromUrl();
    }
  };

  const handleDecline = () => {
    clearInviteFromUrl();
    setToken(null);
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={handleDecline} />
      <div className="relative bg-white dark:bg-gray-900 rounded-xl shadow-xl w-[420px] p-5">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          {t("workspace.invite.dialogTitle")}
        </h3>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-2 leading-relaxed">
          {workspaceName
            ? t("workspace.invite.dialogBody", { name: workspaceName })
            : t("workspace.invite.dialogBodyUnknown")}
        </p>
        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={handleDecline}
            disabled={busy}
            className="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg disabled:opacity-50"
          >
            {t("workspace.invite.decline")}
          </button>
          <button
            onClick={handleAccept}
            disabled={busy}
            className="px-3 py-1.5 text-xs text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50"
          >
            {busy ? t("common.loading") : t("workspace.invite.accept")}
          </button>
        </div>
      </div>
    </div>
  );
}
