import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";

function RootFallback({ error }: FallbackProps) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-gray-950 p-6">
      <div className="max-w-md w-full rounded-lg border border-red-200 dark:border-red-900 bg-white dark:bg-gray-900 p-6 shadow">
        <h1 className="text-lg font-semibold text-red-700 dark:text-red-400 mb-2">
          {t("common.errorBoundary.rootTitle")}
        </h1>
        <p className="text-sm text-gray-700 dark:text-gray-300 mb-4">
          {t("common.errorBoundary.rootMessage")}
        </p>
        <details className="mb-4 text-xs text-gray-500 dark:text-gray-400">
          <summary className="cursor-pointer">{t("common.errorBoundary.details")}</summary>
          <pre className="mt-2 whitespace-pre-wrap break-words">{String(error)}</pre>
        </details>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded bg-red-600 text-white px-4 py-2 text-sm font-medium hover:bg-red-700"
        >
          {t("common.errorBoundary.rootReload")}
        </button>
      </div>
    </div>
  );
}

export default function RootErrorBoundary({ children }: { children: ReactNode }) {
  return <ErrorBoundary FallbackComponent={RootFallback}>{children}</ErrorBoundary>;
}
