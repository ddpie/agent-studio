import { useState, useEffect, useRef, useCallback } from "react";
import { X, ZoomIn, ZoomOut, RotateCcw, ImageOff } from "lucide-react";
import { fetchSignedS3 } from "../../lib/s3-utils";

export default function ImageLightbox({ src, alt }: { src: string; alt?: string }) {
  const [open, setOpen] = useState(false);
  // Start empty: rendering <img src="https://s3.../foo.png"> before the
  // signed URL resolves triggers a 403 and the "broken image" icon flashes
  // for a couple hundred ms. Keep the box blank until we actually have a
  // fetchable URL (data: URL or blob: URL from fetchSignedS3).
  const [displayUrl, setDisplayUrl] = useState<string>(src.startsWith("data:") ? src : "");
  const [failed, setFailed] = useState(false);
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ dragging: boolean; startX: number; startY: number; origX: number; origY: number }>({
    dragging: false, startX: 0, startY: 0, origX: 0, origY: 0,
  });

  useEffect(() => {
    if (src.startsWith("data:")) {
      setDisplayUrl(src);
      setFailed(false);
      return;
    }
    setDisplayUrl("");
    setFailed(false);
    let revoked = false;
    fetchSignedS3(src).then((url) => {
      if (revoked) return;
      // fetchSignedS3 falls back to the original URL on failure — that
      // path would 403 against S3, so treat a non-blob/non-data result as
      // a failure rather than rendering a broken <img>.
      if (url.startsWith("blob:") || url.startsWith("data:")) {
        setDisplayUrl(url);
      } else {
        setFailed(true);
      }
    }).catch(() => {
      if (!revoked) setFailed(true);
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
      {displayUrl ? (
        <img
          src={displayUrl}
          alt={alt || ""}
          className="max-w-48 max-h-48 rounded-lg object-contain cursor-pointer hover:opacity-80 transition-opacity"
          onClick={handleOpen}
        />
      ) : failed ? (
        <div
          title={alt || ""}
          className="w-48 h-36 rounded-lg border border-dashed border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 flex items-center justify-center text-gray-400 dark:text-gray-500"
        >
          <ImageOff className="w-6 h-6" />
        </div>
      ) : (
        <div
          aria-hidden
          className="w-48 h-36 rounded-lg bg-gray-200/70 dark:bg-gray-800/70 animate-pulse"
        />
      )}
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
