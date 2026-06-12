import { useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { API_BASE } from "@/lib/config";

// import pdfWorkerSrc from "pdfjs-dist/build/pdf.worker.min.js?url";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

interface PDFPreviewProps {
  fileUrl: string;
  fileName?: string | null;
}

export function PDFPreview({ fileUrl, fileName }: PDFPreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState<number>();
  const [numPages, setNumPages] = useState<number>(0);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const resolvedFileUrl = useMemo(() => {
    if (!fileUrl) return fileUrl;
    if (/^https?:\/\//i.test(fileUrl)) return fileUrl;
    if (fileUrl.startsWith("/api/")) {
      const base = API_BASE.replace(/\/$/, "");
      return `${base}${fileUrl}`;
    }
    return fileUrl;
  }, [fileUrl]);

  useEffect(() => {
    setIsLoading(true);
    setLoadError(null);
    setNumPages(0);
  }, [resolvedFileUrl]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const width = entry.contentRect.width;
        setContainerWidth((prev) => (Math.abs((prev ?? 0) - width) > 1 ? width : prev));
      }
    });

    resizeObserver.observe(element);
    return () => resizeObserver.disconnect();
  }, []);

  const pageWidth = useMemo(() => {
    if (!containerWidth) return undefined;
    const horizontalPadding = 48; // px, match px-6 on the inner wrapper
    return Math.max(320, containerWidth - horizontalPadding);
  }, [containerWidth]);

  const handleLoadSuccess = (document: PDFDocumentProxy) => {
    setNumPages(document.numPages);
    setIsLoading(false);
  };

  const handleLoadError = (error: Error) => {
    console.error("PDF 加载失败:", error);
    setLoadError(error?.message || "PDF 加载失败");
    setIsLoading(false);
  };

  return (
    <div
      ref={containerRef}
      data-pdf-container
      className="h-full overflow-auto bg-gray-50 dark:bg-gray-800/60 scrollbar scrollbar-thumb-gray-400 scrollbar-track-gray-50 dark:scrollbar-thumb-gray-500 dark:scrollbar-track-gray-800"
    >
      <div className="min-h-full px-6 py-6 flex flex-col items-center gap-6">
        {loadError ? (
          <div className="w-full max-w-md bg-white/80 dark:bg-gray-900/80 border border-red-200 dark:border-red-700 text-red-600 dark:text-red-300 rounded-xl p-4 text-center">
            <div className="flex items-center justify-center gap-2 text-sm font-medium">
              <i className="fa-solid fa-triangle-exclamation"></i>
              <span>PDF 加载失败</span>
            </div>
            <p className="mt-2 text-xs text-red-500 dark:text-red-400 break-words">
              {loadError}
            </p>
            {fileName ? (
              <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">{fileName}</p>
            ) : null}
          </div>
        ) : (
          <Document
            file={resolvedFileUrl}
            onLoadSuccess={handleLoadSuccess}
            onLoadError={handleLoadError}
            loading={
              <div className="w-full max-w-md bg-white/80 dark:bg-gray-900/80 border border-blue-200 dark:border-blue-700 text-blue-600 dark:text-blue-300 rounded-xl p-4 text-center">
                <div className="flex items-center justify-center gap-2 text-sm font-medium">
                  <i className="fa-solid fa-spinner animate-spin"></i>
                  <span>正在加载 PDF...</span>
                </div>
                {fileName ? (
                  <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">{fileName}</p>
                ) : null}
              </div>
            }
            className="flex flex-col items-center gap-6 w-full"
          >
            {Array.from({ length: numPages }, (_value, index) => (
              <div
                key={`pdf-page-${index + 1}`}
                className="bg-white dark:bg-gray-900 shadow-lg border border-white/60 dark:border-gray-700 overflow-hidden"
              >
                <Page
                  pageNumber={index + 1}
                  width={pageWidth}
                  renderAnnotationLayer={false}
                  renderTextLayer
                  loading={null}
                />
                <div className="px-4 py-2 text-xs text-gray-500 dark:text-gray-400 text-center bg-gray-50 dark:bg-gray-800">
                  第 {index + 1} 页 / 共 {numPages} 页
                </div>
              </div>
            ))}
            {isLoading && numPages === 0 ? (
              <div className="text-sm text-gray-500 dark:text-gray-400">正在解析文档...</div>
            ) : null}
          </Document>
        )}
      </div>
    </div>
  );
}
