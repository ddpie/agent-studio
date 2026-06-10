import ImageLightbox from "../ui/ImageLightbox";
import S3DownloadButton from "../ui/S3DownloadButton";
import { agentConfig } from "../../config";
import type { S3Download } from "../../stores/chat-store";

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;

function ReviewBadge({ badge }: { badge?: "pass" | "fail" }) {
  if (!badge) return null;
  if (badge === "fail") {
    return (
      <div className="absolute top-2 left-2 px-2 py-0.5 rounded text-xs font-medium bg-red-500/90 text-white backdrop-blur-sm">
        ✗ 设定冲突
      </div>
    );
  }
  return (
    <div className="absolute top-2 left-2 px-2 py-0.5 rounded text-xs font-medium bg-green-500/90 text-white backdrop-blur-sm">
      ✓ 已通过
    </div>
  );
}

export default function S3DownloadList({ downloads }: { downloads: S3Download[] }) {
  if (downloads.length === 0) return null;
  const images = downloads.filter((d) => IMAGE_EXT_RE.test(d.filename));
  const files = downloads.filter((d) => !IMAGE_EXT_RE.test(d.filename));
  return (
    <>
      {images.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-3">
          {images.map((d) => {
            const s3Url = `https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}/${d.key}`;
            return (
              <div key={d.key} className="relative">
                <ImageLightbox src={s3Url} alt={d.filename} />
                <ReviewBadge badge={d.badge} />
              </div>
            );
          })}
        </div>
      )}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-3">
          {files.map((d) => (
            <S3DownloadButton key={d.key} s3Key={d.key} filename={d.filename} size="sm" />
          ))}
        </div>
      )}
    </>
  );
}
