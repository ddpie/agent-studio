import type { TraceSpan } from "../../lib/api-client";

interface SpanRowProps {
  span: TraceSpan;
  depth: number;
  totalMs: number;
  startMs: number;
}

function SpanRow({ span, depth, totalMs, startMs }: SpanRowProps) {
  const offset = totalMs > 0 ? ((span.startMs - startMs) / totalMs) * 100 : 0;
  const width = totalMs > 0 ? Math.max(0.5, (span.durationMs / totalMs) * 100) : 100;
  const statusColor =
    span.status === "ERROR"
      ? "bg-red-500"
      : span.status === "OK" || !span.status
        ? "bg-blue-500"
        : "bg-gray-400";
  return (
    <>
      <div
        className="flex items-center gap-2 py-1 text-xs"
        style={{ paddingLeft: `${depth * 14}px` }}
        data-testid={`span-row-${span.spanId}`}
      >
        <span
          className="font-mono text-gray-700 dark:text-gray-300 truncate min-w-0 flex-1"
          title={span.name}
        >
          {span.name}
        </span>
        <span className="font-mono tabular-nums text-gray-500 dark:text-gray-400 w-16 text-right">
          {span.durationMs}ms
        </span>
        <div className="w-48 relative h-3 bg-gray-100 dark:bg-gray-800 rounded">
          <div
            className={`absolute top-0 bottom-0 ${statusColor} rounded`}
            style={{ left: `${offset}%`, width: `${width}%` }}
          />
        </div>
      </div>
      {span.children.map((c) => (
        <SpanRow
          key={c.spanId}
          span={c}
          depth={depth + 1}
          totalMs={totalMs}
          startMs={startMs}
        />
      ))}
    </>
  );
}

export default function SpanTree({ root }: { root: TraceSpan }) {
  function latestEnd(s: TraceSpan): number {
    let m = s.startMs + s.durationMs;
    for (const c of s.children) m = Math.max(m, latestEnd(c));
    return m;
  }
  const totalMs = latestEnd(root) - root.startMs;
  return (
    <div
      className="bg-white dark:bg-gray-900 border dark:border-gray-800 rounded p-2"
      data-testid="span-tree"
    >
      <SpanRow span={root} depth={0} totalMs={totalMs} startMs={root.startMs} />
    </div>
  );
}
