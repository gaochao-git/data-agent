interface ImagePreviewProps {
  fileUrl: string;
  fileName?: string;
}

export function ImagePreview({ fileUrl, fileName }: ImagePreviewProps) {
  return (
    <div className="flex h-full w-full items-center justify-center overflow-auto rounded-[12px] bg-white p-4">
      <img
        src={fileUrl}
        alt={fileName || "原件图片"}
        className="max-h-full max-w-full rounded-[12px] object-contain"
      />
    </div>
  );
}
