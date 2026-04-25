import { useTranslation } from "react-i18next";
import { JsonView, darkStyles, defaultStyles } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";
import type { ToolCallRecord } from "../../stores/chat-store";
import useIsDark from "../../hooks/useIsDark";

const MAX_OUTPUT_DISPLAY = 5000;
// Above this the synchronous JSON.parse + tree render can freeze the
// main thread. The upstream tool cap is ~5KB for outputs but input
// payloads and the occasional oversized blob still come through, so
// the guard is defense-in-depth for a UX we'd rather not block on.
const MAX_JSON_PARSE_BYTES = 200_000;

// Parse a value to an object/array, else null. Accepts raw text that may
// be a JSON literal (object/array) — primitives and failed parses return
// null so the caller can fall back to plain-text rendering.
function tryParseJson(raw: string): unknown | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (trimmed.length > MAX_JSON_PARSE_BYTES) return null;
  if (!(trimmed.startsWith("{") || trimmed.startsWith("["))) return null;
  try {
    const p = JSON.parse(trimmed);
    return typeof p === "object" && p !== null ? p : null;
  } catch {
    return null;
  }
}

// Tool outputs frequently wrap another JSON document as a string value
// (e.g. `{"result": "{\"items\": [...]}"}`) because the source tool
// serialized twice. Walk one level deep and re-parse string values that
// look like JSON so the viewer renders the real structure.
function unwrapNestedJson(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(unwrapNestedJson);
  if (node && typeof node === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
      if (typeof v === "string") {
        const parsed = tryParseJson(v);
        out[k] = parsed !== null ? parsed : v;
      } else {
        out[k] = v;
      }
    }
    return out;
  }
  return node;
}

function JsonBlock({ data }: { data: unknown }) {
  const isDark = useIsDark();
  return (
    <div className="rounded border border-gray-200 dark:border-gray-700 overflow-x-auto text-xs bg-gray-50 dark:bg-gray-900 p-2">
      <JsonView
        data={data as object}
        shouldExpandNode={(level) => level < 2}
        style={isDark ? darkStyles : defaultStyles}
      />
    </div>
  );
}

function TextBlock({ text }: { text: string }) {
  const display = text.length > MAX_OUTPUT_DISPLAY
    ? text.slice(0, MAX_OUTPUT_DISPLAY) + "\n... (truncated)"
    : text;
  return (
    <pre className="whitespace-pre-wrap break-words bg-gray-50 dark:bg-gray-900 rounded p-2 overflow-x-auto">
      <code>{display}</code>
    </pre>
  );
}

function MaybeJsonBlock({ raw }: { raw: string }) {
  const parsed = tryParseJson(raw);
  if (parsed !== null) return <JsonBlock data={unwrapNestedJson(parsed)} />;
  return <TextBlock text={raw} />;
}

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
                <MaybeJsonBlock raw={c.input} />
              </>
            )}
            {c.output && !c.isSvg && (
              <>
                <div className="font-medium opacity-70">{t("chat.toolOutput", "Output")}:</div>
                <MaybeJsonBlock raw={c.output} />
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
