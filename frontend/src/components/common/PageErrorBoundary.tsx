import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";

function PageFallback({ error, resetErrorBoundary }: FallbackProps) {
  const { t } = useTranslation();
  return (
    <div className="p-8">
      <div className="max-w-lg rounded-lg border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 p-5">
        <h2 className="text-base font-semibold text-amber-900 dark:text-amber-200 mb-1">
          {t("common.errorBoundary.pageTitle")}
        </h2>
        <p className="text-sm text-gray-700 dark:text-gray-300 mb-3">
          {t("common.errorBoundary.pageMessage")}
        </p>
        <details className="mb-3 text-xs text-gray-500 dark:text-gray-400">
          <summary className="cursor-pointer">{t("common.errorBoundary.details")}</summary>
          <pre className="mt-2 whitespace-pre-wrap break-words">{String(error)}</pre>
        </details>
        <button
          type="button"
          onClick={resetErrorBoundary}
          className="rounded bg-amber-600 text-white px-3 py-1.5 text-sm font-medium hover:bg-amber-700"
        >
          {t("common.errorBoundary.pageRetry")}
        </button>
      </div>
    </div>
  );
}

export default function PageErrorBoundary({ children }: { children: ReactNode }) {
  return <ErrorBoundary FallbackComponent={PageFallback}>{children}</ErrorBoundary>;
}
