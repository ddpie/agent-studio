import { Outlet } from "react-router";
import AgentList from "../agents/AgentList";
import { useUISettings } from "../../stores/ui-settings-store";
import { useCallback, useRef } from "react";

export default function AgentsLayout() {
  const { sidebarWidth, setSidebarWidth } = useUISettings();
  const dragging = useRef(false);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    const startX = e.clientX;
    const startW = sidebarWidth;

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const newW = Math.min(Math.max(startW + ev.clientX - startX, 56), 400);
      setSidebarWidth(newW);
    };
    const onUp = () => {
      dragging.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [sidebarWidth, setSidebarWidth]);

  return (
    <div className="flex flex-1 overflow-hidden">
      <aside
        style={{ width: sidebarWidth }}
        className="border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 flex-shrink-0 overflow-hidden"
      >
        <AgentList collapsed={sidebarWidth < 100} />
      </aside>
      <div
        onMouseDown={onDragStart}
        onDoubleClick={() => setSidebarWidth(sidebarWidth > 100 ? 56 : 224)}
        className="w-1 cursor-col-resize bg-transparent hover:bg-blue-400/30 active:bg-blue-400/50 flex-shrink-0 transition-colors"
        title="Drag to resize, double-click to toggle"
      />
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
