import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import type { Components } from "react-markdown";
import AgentProposalCard from "./AgentProposalCard";

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
        <span className="text-[10px] text-gray-400 bg-white/80 dark:bg-gray-800/80 px-1 rounded">{language}</span>
        <button onClick={handleCopy} className="p-1 bg-white/80 dark:bg-gray-800/80 hover:bg-white dark:hover:bg-gray-800 rounded border border-gray-200 dark:border-gray-700" title={t("chat.copyCode")}>
          {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3 text-gray-400" />}
        </button>
      </div>
      <SyntaxHighlighter style={oneLight} language={language} PreTag="div" customStyle={{ margin: 0, borderRadius: "0.375rem", fontSize: "0.8em" }}>
        {code}
      </SyntaxHighlighter>
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
