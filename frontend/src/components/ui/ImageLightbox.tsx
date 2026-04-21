import { useState, useEffect, useRef, useCallback } from "react";
import { X, ZoomIn, ZoomOut, RotateCcw } from "lucide-react";
import { fetchSignedS3 } from "../../lib/s3-utils";

export default function ImageLightbox({ src, alt }: { src: string; alt?: string }) {
  const [open, setOpen] = useState(false);
  const [displayUrl, setDisplayUrl] = useState(src);
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ dragging: boolean; startX: number; startY: number; origX: number; origY: number }>({
    dragging: false, startX: 0, startY: 0, origX: 0, origY: 0,
  });

  useEffect(() => {
    if (src.startsWith("data:")) {
      setDisplayUrl(src);
      return;
    }
    let revoked = false;
    fetchSignedS3(src).then((url) => {
      if (!revoked) setDisplayUrl(url);
    }).catch(() => {
      setDisplayUrl(src);
    });
    return () => { revoked = true; };
  }, [src]);

  const resetView = useCallback(() => {
    setScale(1);
    setTranslate({ x: 0, y: 0 });
  }, []);

  const handleOpen = () => {
    resetView();
    setOpen(true);
  };

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.stopPropagation();
    setScale((s) => Math.min(Math.max(s + (e.deltaY < 0 ? 0.25 : -0.25), 0.25), 10));
  }, []);

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    if (scale <= 1) return;
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = { dragging: true, startX: e.clientX, startY: e.clientY, origX: translate.x, origY: translate.y };
  }, [scale, translate]);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragRef.current.dragging) return;
    e.stopPropagation();
    const { startX, startY, origX, origY } = dragRef.current;
    setTranslate({ x: origX + e.clientX - startX, y: origY + e.clientY - startY });
  }, []);

  const handlePointerUp = useCallback(() => {
    dragRef.current.dragging = false;
  }, []);

  return (
    <>
      <img
        src={displayUrl}
        alt={alt || ""}
        className="max-w-48 max-h-48 rounded-lg object-contain cursor-pointer hover:opacity-80 transition-opacity"
        onClick={handleOpen}
      />
      {open && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center"
          onClick={() => setOpen(false)}
          onWheel={handleWheel}
        >
          <div className="absolute top-4 right-4 flex items-center gap-2">
            <button className="text-white/70 hover:text-white p-1" onClick={(e) => { e.stopPropagation(); setScale((s) => Math.min(s + 0.5, 10)); }} title="Zoom in">
              <ZoomIn className="w-6 h-6" />
            </button>
            <button className="text-white/70 hover:text-white p-1" onClick={(e) => { e.stopPropagation(); setScale((s) => Math.max(s - 0.5, 0.25)); }} title="Zoom out">
              <ZoomOut className="w-6 h-6" />
            </button>
            <button className="text-white/70 hover:text-white p-1" onClick={(e) => { e.stopPropagation(); resetView(); }} title="Reset">
              <RotateCcw className="w-6 h-6" />
            </button>
            <span className="text-white/50 text-sm min-w-[3rem] text-center">{Math.round(scale * 100)}%</span>
            <button className="text-white hover:text-gray-300 p-1" onClick={() => setOpen(false)}>
              <X className="w-7 h-7" />
            </button>
          </div>
          <img
            src={displayUrl}
            alt={alt || ""}
            className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg select-none"
            style={{
              transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`,
              cursor: scale > 1 ? "grab" : "default",
              transition: dragRef.current.dragging ? "none" : "transform 0.15s ease-out",
            }}
            draggable={false}
            onClick={(e) => e.stopPropagation()}
            onWheel={handleWheel}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
          />
        </div>
      )}
    </>
  );
}
