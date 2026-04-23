import ImageLightbox from "../ui/ImageLightbox";
import S3DownloadButton from "../ui/S3DownloadButton";
import { agentConfig } from "../../config";
import type { S3Download } from "../../stores/chat-store";

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;

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
            return <ImageLightbox key={d.key} src={s3Url} alt={d.filename} />;
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
