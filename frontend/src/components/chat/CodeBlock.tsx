import { useState, useEffect, lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check } from "lucide-react";
import type { Components } from "react-markdown";
import AgentProposalCard from "./AgentProposalCard";

// Lazy-load react-syntax-highlighter (~300KB) — only needed when code blocks appear in chat
const SyntaxHighlighter = lazy(() =>
  import("react-syntax-highlighter").then((mod) => ({ default: mod.Prism }))
);

// Module-level style cache so it loads once across all CodeBlock instances
let cachedStyle: Record<string, React.CSSProperties> | null = null;
let stylePromise: Promise<Record<string, React.CSSProperties>> | null = null;
function getStyle(): Promise<Record<string, React.CSSProperties>> {
  if (!stylePromise) {
    stylePromise = import("react-syntax-highlighter/dist/esm/styles/prism").then((mod) => {
      cachedStyle = mod.oneLight;
      return mod.oneLight;
    });
  }
  return stylePromise;
}

function CodeFallback({ code }: { code: string }) {
  return (
    <pre className="rounded-md bg-gray-50 dark:bg-gray-900 p-3 text-xs overflow-auto" style={{ fontSize: "0.8em" }}>
      <code>{code}</code>
    </pre>
  );
}

function HighlightedCode({ language, code }: { language: string; code: string }) {
  const [style, setStyle] = useState<Record<string, React.CSSProperties> | null>(cachedStyle);
  useEffect(() => {
    if (!style) {
      getStyle().then(setStyle);
    }
  }, [style]);
  if (!style) return <CodeFallback code={code} />;
  return (
    <SyntaxHighlighter style={style} language={language} PreTag="div" customStyle={{ margin: 0, borderRadius: "0.375rem", fontSize: "0.8em" }}>
      {code}
    </SyntaxHighlighter>
  );
}

export default function CodeBlock({ language, code }: { language: string; code: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="relative group/code">
      <div className="absolute top-1 right-1 flex items-center gap-1 opacity-0 group-hover/code:opacity-100 transition-opacity z-10">
        <span className="text-[10px] text-gray-500 dark:text-gray-400 bg-white/80 dark:bg-gray-800/80 px-1 rounded">{language}</span>
        <button onClick={handleCopy} className="p-1 bg-white/80 dark:bg-gray-800/80 hover:bg-white dark:hover:bg-gray-800 rounded border border-gray-200 dark:border-gray-700" title={t("chat.copyCode")}>
          {copied ? <Check className="w-3 h-3 text-green-500 dark:text-green-400" /> : <Copy className="w-3 h-3 text-gray-400 dark:text-gray-500" />}
        </button>
      </div>
      <Suspense fallback={<CodeFallback code={code} />}>
        <HighlightedCode language={language} code={code} />
      </Suspense>
    </div>
  );
}

export const mdComponents: Components = {
  code({ className, children, ...props }) {
    const match = /language-([\w-]+)/.exec(className || "");
    const code = String(children).replace(/\n$/, "");
    if (match) {
      if (match[1] === "agent-proposal") {
        return <AgentProposalCard json={code} />;
      }
      return <CodeBlock language={match[1]} code={code} />;
    }
    return <code className={className} {...props}>{children}</code>;
  },
};
