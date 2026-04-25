import { useEffect, useRef } from "react";
import { useTranslation, Trans } from "react-i18next";
import { Loader2, RefreshCw } from "lucide-react";
import type { Message } from "../../stores/chat-store";
import ChatMessage from "./ChatMessage";

interface MessageListProps {
  messages: Message[];
  isStreaming: boolean;
  statusText: string | null;
  activeTool: string | null;
  autoContinue: { n: number; max: number } | null;
  onRegenerate: () => void;
  emptyState: React.ReactNode;
}

export default function MessageList({ messages, isStreaming, statusText, activeTool, autoContinue, onRegenerate, emptyState }: MessageListProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      userScrolledUp.current = !atBottom;
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (!isStreaming) {
      userScrolledUp.current = false;
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    } else if (!userScrolledUp.current) {
      el.scrollTo({ top: el.scrollHeight, behavior: "instant" });
    }
  }, [messages, isStreaming]);

  const lastAssistantIdx = messages.findLastIndex((m) => m.role === "assistant");

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
      {messages.length === 0 && emptyState}
      {messages.map((msg, idx) => (
        <ChatMessage
          key={msg.id}
          message={msg}
          isLastAssistant={idx === lastAssistantIdx}
          isStreaming={isStreaming}
        />
      ))}
      {messages.length > 0 && !isStreaming && messages[messages.length - 1]?.role === "assistant" && messages[messages.length - 1]?.content && (
        <div className="flex justify-start mb-4 -mt-2">
          <button
            onClick={onRegenerate}
            className="flex items-center gap-1 text-[11px] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 px-2 py-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            title={t("chat.regenerate")}
          >
            <RefreshCw className="w-3 h-3" />
            {t("common.regenerate")}
          </button>
        </div>
      )}
      {statusText && (
        <div className="flex justify-start mb-4">
          <div className="rounded-2xl px-4 py-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-300">
            <div className="flex items-center gap-2 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>{statusText}</span>
            </div>
          </div>
        </div>
      )}
      {activeTool && !statusText && (
        <div className="flex justify-start mb-4">
          <div className="rounded-xl px-3 py-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-300">
            <div className="flex items-center gap-2 text-xs">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span><Trans i18nKey="chat.calling" values={{ tool: activeTool }} components={[<strong />]} /></span>
            </div>
          </div>
        </div>
      )}
      {autoContinue && !statusText && !activeTool && (
        <div className="flex justify-start mb-4">
          <div className="rounded-xl px-3 py-2 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 text-purple-700 dark:text-purple-300">
            <div className="flex items-center gap-2 text-xs">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>{t("chat.autoContinuing", "自动继续 ({{n}}/{{max}})", { n: autoContinue.n, max: autoContinue.max })}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
