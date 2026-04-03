import { Outlet } from "react-router";
import IconNav from "./IconNav";

interface AppShellProps {
  signOut?: () => void;
  user?: { signInDetails?: { loginId?: string } };
}

export default function AppShell({ signOut, user }: AppShellProps) {
  return (
    <div className="h-screen flex flex-col bg-white dark:bg-gray-950">
      <header className="flex items-center justify-between px-4 py-2 bg-gray-900 text-white">
        <div className="flex items-center gap-2">
          <img src="/logo.svg" alt="Agent Studio" className="w-5 h-5" />
          <span className="font-semibold text-sm">Agent Studio</span>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-gray-400">{user?.signInDetails?.loginId}</span>
          <button onClick={signOut} className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600">Sign out</button>
        </div>
      </header>
      <div className="flex flex-1 overflow-hidden">
        <IconNav />
        <div className="flex-1 min-w-0 overflow-hidden flex flex-col">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
