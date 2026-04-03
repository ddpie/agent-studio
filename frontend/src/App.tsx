// src/App.tsx
import { useMemo } from "react";
import { Authenticator } from "@aws-amplify/ui-react";
import "@aws-amplify/ui-react/styles.css";
import { RouterProvider } from "react-router";
import { createRouter } from "./router";

export default function App() {
  return (
    <Authenticator
      components={{
        Header() {
          return (
            <div className="flex flex-col items-center pt-16 pb-6">
              <div className="relative mb-6 animate-[fadeSlideIn_0.6s_ease-out]">
                <div className="w-20 h-20 rounded-2xl shadow-xl shadow-blue-500/20 overflow-hidden">
                  <img src="/logo.svg" alt="Agent Studio" className="w-full h-full" />
                </div>
                <div className="absolute -inset-3 animate-[spin_8s_linear_infinite]">
                  <div className="absolute top-0 left-1/2 -translate-x-1/2 w-2.5 h-2.5 bg-blue-400 rounded-full shadow-lg shadow-blue-400/50" />
                </div>
                <div className="absolute -inset-5 animate-[spin_12s_linear_infinite_reverse]">
                  <div className="absolute bottom-0 right-0 w-2 h-2 bg-purple-400 rounded-full shadow-lg shadow-purple-400/50" />
                </div>
                <div className="absolute -inset-4 animate-[spin_10s_linear_infinite]">
                  <div className="absolute top-1/2 right-0 w-2 h-2 bg-indigo-400 rounded-full shadow-lg shadow-indigo-400/50" />
                </div>
                <div className="absolute -inset-6 animate-[spin_15s_linear_infinite]">
                  <div className="absolute top-1/4 left-0 w-1.5 h-1.5 bg-cyan-400 rounded-full shadow-lg shadow-cyan-400/50" />
                </div>
                <div className="absolute -inset-7 animate-[spin_18s_linear_infinite_reverse]">
                  <div className="absolute bottom-1/4 right-1/4 w-1.5 h-1.5 bg-emerald-400 rounded-full shadow-lg shadow-emerald-400/50" />
                </div>
                <div className="absolute -inset-8 animate-[spin_20s_linear_infinite]">
                  <div className="absolute top-0 right-1/3 w-1 h-1 bg-amber-400 rounded-full shadow-lg shadow-amber-400/50" />
                </div>
                <div className="absolute -inset-5 animate-[spin_14s_linear_infinite_reverse]">
                  <div className="absolute bottom-0 left-1/4 w-1 h-1 bg-rose-400 rounded-full shadow-lg shadow-rose-400/50" />
                </div>
              </div>
              <h1 className="text-2xl font-bold text-gray-900 animate-[fadeSlideIn_0.6s_ease-out_0.15s_both]">Agent Studio</h1>
              <p className="text-sm text-gray-500 mt-1 animate-[fadeSlideIn_0.6s_ease-out_0.25s_both]">Build and orchestrate AI agents on AWS</p>
            </div>
          );
        },
        Footer() {
          return (
            <div className="text-center py-4 text-[11px] text-gray-400">
              Powered by AWS Bedrock AgentCore
            </div>
          );
        },
      }}
    >
      {({ signOut, user }) => <AuthenticatedApp signOut={signOut} user={user} />}
    </Authenticator>
  );
}

function AuthenticatedApp({ signOut, user }: { signOut?: () => void; user?: { signInDetails?: { loginId?: string } } }) {
  const router = useMemo(() => createRouter(signOut, user), [signOut, user]);
  return <RouterProvider router={router} />;
}
