import { useEffect, useState } from "react";
import { Outlet } from "react-router";
import { useTranslation } from "react-i18next";
import { fetchUserAttributes } from "aws-amplify/auth";
import IconNav from "./IconNav";
import WorkspaceSwitcher from "./WorkspaceSwitcher";
import OfflineBanner from "../common/OfflineBanner";
import InvitationAcceptor from "./InvitationAcceptor";

interface AppShellProps {
  signOut?: () => void;
  user?: { signInDetails?: { loginId?: string } };
}

export default function AppShell({ signOut, user }: AppShellProps) {
  const { t } = useTranslation();
  const loginId = user?.signInDetails?.loginId || "";
  // Amplify's `user` prop doesn't carry the `name` attribute — fetch it
  // separately so the header can render "Display Name (email)". Refetch
  // when the user changes so sign-out → sign-in as a different user
  // doesn't stick on stale data.
  const [displayName, setDisplayName] = useState<string>("");
  useEffect(() => {
    let alive = true;
    fetchUserAttributes()
      .then((attrs) => { if (alive) setDisplayName(attrs.name || ""); })
      .catch(() => { if (alive) setDisplayName(""); });
    return () => { alive = false; };
  }, [loginId]);
  const userLabel = displayName && loginId
    ? `${displayName} (${loginId})`
    : (displayName || loginId);
  return (
    <div className="h-screen flex flex-col bg-white dark:bg-gray-950">
      <OfflineBanner />
      <header className="flex items-center justify-between px-4 py-2 bg-gray-900 text-white">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <img src="/logo.svg" alt="Agent Studio" className="w-5 h-5" />
            <span className="font-semibold text-sm">{t("appShell.title")}</span>
          </div>
          <WorkspaceSwitcher />
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-gray-400 truncate max-w-[320px]" title={userLabel}>{userLabel}</span>
          <button onClick={signOut} className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600">{t("appShell.signOut")}</button>
        </div>
      </header>
      <InvitationAcceptor />
      <div className="flex flex-1 overflow-hidden">
        <IconNav />
        <div className="flex-1 min-w-0 overflow-hidden flex flex-col">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
