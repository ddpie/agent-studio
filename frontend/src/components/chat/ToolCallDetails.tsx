import { useTranslation } from "react-i18next";
import type { ToolCallRecord } from "../../stores/chat-store";

const MAX_OUTPUT_DISPLAY = 2000;

export default function ToolCallDetails({ calls }: { calls: ToolCallRecord[] }) {
  const { t } = useTranslation();
  if (!calls.length) return null;
  return (
    <div className="mt-2 space-y-1">
      {calls.map((c) => (
        <details key={c.id} className="tool-call">
          <summary>
            {t("chat.calledTool", "Called")} <strong>{c.name}</strong>
          </summary>
          <div className="px-2 pb-2 text-xs space-y-1">
            {c.input && (
              <>
                <div className="font-medium opacity-70">{t("chat.toolInput", "Input")}:</div>
                <pre className="whitespace-pre-wrap break-words bg-gray-50 dark:bg-gray-900 rounded p-2 overflow-x-auto">
                  <code>{c.input}</code>
                </pre>
              </>
            )}
            {c.output && !c.isSvg && (
              <>
                <div className="font-medium opacity-70">{t("chat.toolOutput", "Output")}:</div>
                <pre className="whitespace-pre-wrap break-words bg-gray-50 dark:bg-gray-900 rounded p-2 overflow-x-auto">
                  <code>
                    {c.output.length > MAX_OUTPUT_DISPLAY
                      ? c.output.slice(0, MAX_OUTPUT_DISPLAY) + "\n... (truncated)"
                      : c.output}
                  </code>
                </pre>
              </>
            )}
            {c.isSvg && (
              <>
                <div className="font-medium opacity-70">{t("chat.toolOutput", "Output")}:</div>
                <ChartOutput svg={c.output} />
              </>
            )}
          </div>
        </details>
      ))}
    </div>
  );
}

/**
 * Render tool-emitted SVG. We inject via dangerouslySetInnerHTML because the
 * upstream tool is trusted (same-origin sub-agent inside our account) and the
 * SVG is rendered inside a contained block — not exposed to cross-message
 * script injection since every ChatMessage wraps its own isolated DOM.
 */
function ChartOutput({ svg }: { svg: string }) {
  return (
    <div
      className="tool-rich-output"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
