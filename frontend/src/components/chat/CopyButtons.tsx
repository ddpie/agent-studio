import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, FileText, Check, Image as ImageIcon } from "lucide-react";

// tool-call <details> blocks and tool-rich-output divs are no longer written
// into message.content — they live on message.toolCalls sidecar. Legacy
// messages are cleaned in chat-store's v1→v2 migration, so this helper only
// needs to handle agent-proposal code fences going forward.
const stripNonContent = (s: string) => s
  .replace(/```agent-proposal\n[\s\S]*?```/g, "")
  .replace(/\n{3,}/g, "\n\n")
  .trim();

const svgToPng = (svgEl: SVGSVGElement): Promise<string> =>
  new Promise((resolve) => {
    const svgStr = new XMLSerializer().serializeToString(svgEl);
    const vb = svgEl.getAttribute("viewBox")?.split(/[\s,]+/).map(Number);
    const svgW = vb && vb.length >= 4 ? vb[2] : svgEl.clientWidth || 800;
    const svgH = vb && vb.length >= 4 ? vb[3] : svgEl.clientHeight || 400;
    const scale = 2;
    const blob = new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const img = new window.Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = svgW * scale;
      c.height = svgH * scale;
      const ctx = c.getContext("2d")!;
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, svgW, svgH);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/png"));
    };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(""); };
    img.src = url;
  });

export default function CopyButtons({ content, contentRef }: { content: string; contentRef?: React.RefObject<HTMLDivElement | null> }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState<"text" | "md" | "rich" | null>(null);

  const copyAs = async (mode: "text" | "md" | "rich") => {
    if (mode === "text") {
      const text = stripNonContent(content)
        .replace(/[#*`_~\[\]()>|\\-]/g, "")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
      await navigator.clipboard.writeText(text);
    } else if (mode === "md") {
      await navigator.clipboard.writeText(stripNonContent(content));
    } else {
      if (!contentRef?.current) {
        await navigator.clipboard.writeText(content);
        return;
      }
      const clone = contentRef.current.cloneNode(true) as HTMLDivElement;
      clone.querySelectorAll("details.tool-call").forEach(el => el.remove());
      const svgs = clone.querySelectorAll("svg");
      for (const svg of svgs) {
        const png = await svgToPng(svg);
        if (png) {
          const img = document.createElement("img");
          img.src = png;
          img.style.maxWidth = "100%";
          svg.parentElement?.replaceChild(img, svg);
        }
      }
      const html = clone.innerHTML;
      const plainText = clone.textContent || "";
      try {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/plain": new Blob([plainText], { type: "text/plain" }),
            "text/html": new Blob([html], { type: "text/html" }),
          }),
        ]);
      } catch {
        await navigator.clipboard.writeText(plainText);
      }
    }
    setCopied(mode);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div className="absolute -top-1 right-2 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity bg-white dark:bg-gray-800 rounded-md shadow-sm border border-gray-200 dark:border-gray-700 p-0.5">
      <button onClick={() => copyAs("text")} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded" title={t("chat.copyPlain")} aria-label={t("chat.copyPlain")}>
        {copied === "text" ? <Check className="w-3 h-3 text-green-500 dark:text-green-400" /> : <Copy className="w-3 h-3 text-gray-400 dark:text-gray-500" />}
      </button>
      <button onClick={() => copyAs("md")} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded" title={t("chat.copyMarkdown")} aria-label={t("chat.copyMarkdown")}>
        {copied === "md" ? <Check className="w-3 h-3 text-green-500 dark:text-green-400" /> : <FileText className="w-3 h-3 text-gray-400 dark:text-gray-500" />}
      </button>
      <button onClick={() => copyAs("rich")} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded" title={t("chat.copyRich")} aria-label={t("chat.copyRich")}>
        {copied === "rich" ? <Check className="w-3 h-3 text-green-500 dark:text-green-400" /> : <ImageIcon className="w-3 h-3 text-gray-400 dark:text-gray-500" />}
      </button>
    </div>
  );
}
