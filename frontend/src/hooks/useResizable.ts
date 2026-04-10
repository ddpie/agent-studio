import { useState, useCallback } from "react";

interface UseResizableOptions {
  min: number;
  max: number;
  direction: "vertical" | "horizontal";
}

export default function useResizable(
  initialSize: number,
  { min, max, direction }: UseResizableOptions,
) {
  const [size, setSize] = useState(initialSize);

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const startPos = direction === "horizontal" ? e.clientX : e.clientY;
      const startSize = size;
      const onMove = (ev: MouseEvent) => {
        const currentPos = direction === "horizontal" ? ev.clientX : ev.clientY;
        const delta = direction === "vertical" ? -(currentPos - startPos) : currentPos - startPos;
        setSize(Math.min(Math.max(startSize + delta, min), max));
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [size, min, max, direction],
  );

  const resetSize = useCallback(() => {
    setSize((prev) => (prev > (min + max) / 2 ? min : Math.round((min + max) / 2)));
  }, [min, max]);

  return {
    size,
    dragHandleProps: { onMouseDown: onDragStart, onDoubleClick: resetSize },
    resetSize,
  };
}
