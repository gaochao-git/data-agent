import { useEffect, useMemo, useRef, useState } from "react";
import { Transformer } from "markmap-lib";
import { Markmap } from "markmap-view";

export interface MindmapNode {
  id: string;
  title: string;
  children?: MindmapNode[];
  emphasize?: boolean;
}

interface MindmapViewerProps {
  data: MindmapNode;
  className?: string;
  minHeight?: number;
}

interface MindmapCanvasProps extends MindmapViewerProps {
  containerClassName?: string;
  autoFit?: boolean;
  enableZoom?: boolean;
}

const transformer = new Transformer();

function buildMarkdown(node: MindmapNode, depth = 1): string {
  const headingLevel = Math.min(depth, 6);
  const rawTitle = (node.title ?? "").replace(/\s+/g, " ").trim() || "未命名节点";
  const normalizedTitle = rawTitle.replace(/\*\*/g, "");
  const title = node.emphasize ? `**${normalizedTitle}**` : normalizedTitle;
  const currentLine = `${"#".repeat(headingLevel)} ${title}`;
  if (!node.children || node.children.length === 0) {
    return currentLine;
  }
  const childLines = node.children.map((child) => buildMarkdown(child, depth + 1));
  return [currentLine, ...childLines].join("\n\n");
}

function MindmapCanvas({
  data,
  className,
  minHeight = 360,
  containerClassName,
  autoFit = true,
  enableZoom = false,
}: MindmapCanvasProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const markmapRef = useRef<Markmap | null>(null);

  const markdown = useMemo(() => buildMarkdown(data), [data]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const svgElement = svgRef.current;
    if (!svgElement) {
      return;
    }

    const { root } = transformer.transform(markdown);
    const instance = Markmap.create(
      svgElement,
      { autoFit, fitRatio: 0.92, zoom: enableZoom },
      root,
    );
    markmapRef.current = instance;

    return () => {
      instance.destroy();
      markmapRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const handleResize = () => {
      if (autoFit) {
        markmapRef.current?.fit?.();
      }
    };

    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    if (!markmapRef.current) {
      return;
    }

    const { root } = transformer.transform(markdown);
    markmapRef.current.setData(root);
    if (autoFit) {
      markmapRef.current.fit();
    }
  }, [markdown]);

  return (
    <div
      className={`relative w-full overflow-hidden rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900/40 ${
        className ?? ""
      } ${containerClassName ?? ""}`}
      style={{ minHeight }}
    >
      <svg ref={svgRef} className="absolute inset-0 h-full w-full" />
    </div>
  );
}

export default function MindmapViewer({ data, className, minHeight = 360 }: MindmapViewerProps) {
  const [showPreview, setShowPreview] = useState(false);

  return (
    <>
      <div className="relative">
        <MindmapCanvas data={data} className={className} minHeight={minHeight} />
        <button
          type="button"
          onClick={() => setShowPreview(true)}
          className="absolute bottom-3 right-3 inline-flex h-11 w-11 items-center justify-center rounded-full border border-gray-200 bg-white/90 text-gray-600 shadow-md backdrop-blur transition-all hover:scale-105 hover:border-gray-300 hover:text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-200 dark:hover:border-gray-500"
          aria-label="放大查看思维导图"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-5 w-5"
          >
            <path d="M15 3h6v6" />
            <path d="M9 21H3v-6" />
            <path d="M21 3l-7 7" />
            <path d="M3 21l7-7" />
          </svg>
        </button>
      </div>

      {showPreview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setShowPreview(false)}
            aria-hidden
          />
          <div
            className="relative z-10 w-full max-w-6xl px-4"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="relative flex max-h-[80vh] flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900">
              <div className="flex justify-end gap-2 border-b border-gray-100/80 px-6 py-4 dark:border-gray-700/60">
                <button
                  type="button"
                  onClick={() => setShowPreview(false)}
                  className="inline-flex h-9 min-w-[2.25rem] items-center justify-center rounded-full border border-transparent bg-gray-100 px-3 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-200 hover:text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
                  aria-label="关闭放大预览"
                >
                  关闭
                </button>
              </div>
              <div className="flex-1 overflow-auto p-6">
                <MindmapCanvas
                  data={data}
                  minHeight={Math.max(minHeight, 540)}
                  className={className}
                  containerClassName="border-transparent bg-transparent shadow-none"
                  autoFit
                  enableZoom
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
