import { useState, useEffect } from "react";
import { X } from "lucide-react";
import { getSignedImageUrl } from "../../lib/image-upload";

export default function ImageLightbox({ src, alt }: { src: string; alt?: string }) {
  const [open, setOpen] = useState(false);
  const [displayUrl, setDisplayUrl] = useState(src);

  // If src is an S3 URL (not base64), fetch a signed version
  useEffect(() => {
    if (src.startsWith("data:")) {
      setDisplayUrl(src);
      return;
    }
    let revoked = false;
    getSignedImageUrl(src).then((url) => {
      if (!revoked) setDisplayUrl(url);
    }).catch(() => {
      setDisplayUrl(src); // fallback to original
    });
    return () => { revoked = true; };
  }, [src]);

  return (
    <>
      <img
        src={displayUrl}
        alt={alt || ""}
        className="max-w-48 max-h-48 rounded-lg object-contain cursor-pointer hover:opacity-80 transition-opacity"
        onClick={() => setOpen(true)}
      />
      {open && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-8"
          onClick={() => setOpen(false)}
        >
          <button
            className="absolute top-4 right-4 text-white hover:text-gray-300"
            onClick={() => setOpen(false)}
          >
            <X className="w-8 h-8" />
          </button>
          <img
            src={displayUrl}
            alt={alt || ""}
            className="max-w-full max-h-full object-contain rounded-lg"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
}
