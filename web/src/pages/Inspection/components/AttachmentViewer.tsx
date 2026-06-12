import { useState, useRef, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { renderAsync } from "docx-preview";
import { API_BASE } from "@/lib/config";

interface AttachmentViewerProps {
  fileName: string;
  fileId: string;
  threadId: string;
  isOpen: boolean;
  onClose: () => void;
  initialPosition?: { x: number; y: number };
}

export function AttachmentViewer({
  fileName,
  fileId,
  threadId,
  isOpen,
  onClose,
  initialPosition,
}: AttachmentViewerProps) {
  // 卡片位置
  const [position, setPosition] = useState({ x: 100, y: 100 });
  // 卡片尺寸
  const [size, setSize] = useState({ width: 800, height: 600 });
  // 是否正在拖动位置
  const [isDragging, setIsDragging] = useState(false);
  // 是否正在调整大小
  const [isResizing, setIsResizing] = useState(false);
  // 调整大小的方向
  const [resizeDirection, setResizeDirection] = useState<string>("");
  // 拖动/调整起始点
  const dragStartRef = useRef({ x: 0, y: 0, posX: 0, posY: 0, width: 0, height: 0 });
  // 卡片 ref
  const cardRef = useRef<HTMLDivElement>(null);
  // 文档容器 ref
  const docContainerRef = useRef<HTMLDivElement>(null);
  // 加载状态
  const [isLoading, setIsLoading] = useState(false);
  // 错误信息
  const [error, setError] = useState<string>("");

  // 最小尺寸
  const MIN_WIDTH = 500;
  const MIN_HEIGHT = 400;

  // 初始化位置（仅在首次打开时执行）
  useEffect(() => {
    if (isOpen) {
      // 居中显示
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const cardWidth = size.width;
      const cardHeight = size.height;

      let x = initialPosition?.x ?? (viewportWidth - cardWidth) / 2;
      let y = initialPosition?.y ?? (viewportHeight - cardHeight) / 2;

      // 确保在视口内
      x = Math.max(20, Math.min(x, viewportWidth - cardWidth - 20));
      y = Math.max(20, Math.min(y, viewportHeight - cardHeight - 20));

      setPosition({ x, y });
    }
    // 注意：只在 isOpen 变化时执行，不依赖 size，避免调整大小时重新定位
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // 拖动开始
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      posX: position.x,
      posY: position.y,
      width: size.width,
      height: size.height,
    };
  }, [position, size]);

  // 调整大小开始
  const handleResizeStart = useCallback((e: React.MouseEvent, direction: string) => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);
    setResizeDirection(direction);
    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      posX: position.x,
      posY: position.y,
      width: size.width,
      height: size.height,
    };
  }, [position, size]);

  // 鼠标移动
  useEffect(() => {
    let rafId: number | null = null;

    const handleMouseMove = (e: MouseEvent) => {
      // 使用 requestAnimationFrame 优化渲染性能
      if (rafId) {
        cancelAnimationFrame(rafId);
      }

      rafId = requestAnimationFrame(() => {
        if (isDragging) {
          const deltaX = e.clientX - dragStartRef.current.x;
          const deltaY = e.clientY - dragStartRef.current.y;
          setPosition({
            x: dragStartRef.current.posX + deltaX,
            y: dragStartRef.current.posY + deltaY,
          });
        } else if (isResizing) {
          const deltaX = e.clientX - dragStartRef.current.x;
          const deltaY = e.clientY - dragStartRef.current.y;

          let newWidth = dragStartRef.current.width;
          let newHeight = dragStartRef.current.height;
          let newX = dragStartRef.current.posX;
          let newY = dragStartRef.current.posY;

          if (resizeDirection.includes("e")) {
            newWidth = Math.max(MIN_WIDTH, dragStartRef.current.width + deltaX);
          }
          if (resizeDirection.includes("w")) {
            const potentialWidth = dragStartRef.current.width - deltaX;
            if (potentialWidth >= MIN_WIDTH) {
              newWidth = potentialWidth;
              newX = dragStartRef.current.posX + deltaX;
            }
          }
          if (resizeDirection.includes("s")) {
            newHeight = Math.max(MIN_HEIGHT, dragStartRef.current.height + deltaY);
          }
          if (resizeDirection.includes("n")) {
            const potentialHeight = dragStartRef.current.height - deltaY;
            if (potentialHeight >= MIN_HEIGHT) {
              newHeight = potentialHeight;
              newY = dragStartRef.current.posY + deltaY;
            }
          }

          // 批量更新，减少重渲染
          setSize({ width: newWidth, height: newHeight });
          if (newX !== dragStartRef.current.posX || newY !== dragStartRef.current.posY) {
            setPosition({ x: newX, y: newY });
          }
        }
      });
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      setIsResizing(false);
      setResizeDirection("");
    };

    if (isDragging || isResizing) {
      window.addEventListener("mousemove", handleMouseMove);
      window.addEventListener("mouseup", handleMouseUp);
      return () => {
        window.removeEventListener("mousemove", handleMouseMove);
        window.removeEventListener("mouseup", handleMouseUp);
      };
    }
  }, [isDragging, isResizing, resizeDirection]);

  // 加载并渲染 Word 文档
  useEffect(() => {
    if (!isOpen || !docContainerRef.current) return;

    const loadDocument = async () => {
      setIsLoading(true);
      setError("");

      try {
        const downloadUrl = `${API_BASE}/api/conversations/${threadId}/files/${fileId}`;

        // 下载文档
        const response = await fetch(downloadUrl);
        if (!response.ok) {
          throw new Error(`文件加载失败: ${response.statusText}`);
        }

        const blob = await response.blob();

        console.log("文件信息:", {
          type: blob.type,
          size: blob.size,
          fileName: fileId
        });

        // 验证文件类型
        if (!blob.type.includes('wordprocessing') && !blob.type.includes('octet-stream')) {
          throw new Error(`不支持的文件类型: ${blob.type}`);
        }

        // 清空容器
        if (docContainerRef.current) {
          docContainerRef.current.innerHTML = "";

          // 使用 docx-preview 渲染
          await renderAsync(blob, docContainerRef.current, undefined, {
            className: "docx-preview-container",
            inWrapper: true,
            ignoreWidth: false,
            ignoreHeight: false,
            ignoreFonts: false,
            breakPages: true,
            ignoreLastRenderedPageBreak: false,
            experimental: false,
            trimXmlDeclaration: true,
            useBase64URL: false,
            debug: true,
          });
        }

        setIsLoading(false);
      } catch (err) {
        console.error("文档加载失败:", err);
        setError(err instanceof Error ? err.message : "文档加载失败");
        setIsLoading(false);
      }
    };

    loadDocument();
  }, [isOpen, fileId, threadId]);

  // ESC 关闭
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  // 获取文件下载 URL
  const downloadUrl = `${API_BASE}/api/conversations/${threadId}/files/${fileId}`;

  return createPortal(
    <>
      {/* 添加自定义样式覆盖 docx-preview 默认样式 */}
      <style>{`
        .docx-wrapper {
          background: #fff !important;
          padding: 0 !important;
          margin: 0 !important;
        }
        .docx-wrapper > div {
          background: #fff !important;
          padding: 0 !important;
          margin: 0 !important;
        }
        .docx-wrapper .docx {
          background: #fff !important;
          padding: 0 !important;
          margin: 0 !important;
          box-shadow: none !important;
        }
        .docx-wrapper section.docx {
          background: #fff !important;
          margin: 0 !important;
          padding: 20px !important;
          max-width: 100%;
        }
        .docx-wrapper article {
          background: #fff !important;
          padding: 0 !important;
          margin: 0 !important;
        }
      `}</style>
    <AnimatePresence>
      {isOpen && (
        <motion.div
            ref={cardRef}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{
              opacity: 1,
              scale: 1,
              // 在拖拽或调整大小时禁用动画过渡
              transition: (isDragging || isResizing) ? { duration: 0 } : { type: "spring", damping: 20 }
            }}
            exit={{ opacity: 0, scale: 0.95 }}
            style={{
              position: "fixed",
              left: position.x,
              top: position.y,
              width: size.width,
              height: size.height,
              zIndex: 9999,
              boxShadow: '0 20px 60px rgba(0, 0, 0, 0.3), 0 0 0 1px rgba(99, 102, 241, 0.2)'
            }}
            drag={false}
            className="bg-white dark:bg-gray-800 rounded-lg flex flex-col overflow-hidden border-2 border-indigo-500 dark:border-indigo-400"
          >
            {/* 标题栏（可拖动） */}
            <div
              className="flex items-center justify-between px-4 py-3 bg-gradient-to-r from-indigo-500 to-indigo-600 text-white cursor-move select-none"
              onMouseDown={handleDragStart}
            >
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <i className="fa-solid fa-file-alt text-sm flex-shrink-0"></i>
                <span className="font-medium truncate text-sm">{fileName}</span>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <a
                  href={downloadUrl}
                  download={fileName}
                  className="p-1.5 hover:bg-white/20 rounded transition-colors"
                  title="下载文件"
                  onClick={(e) => e.stopPropagation()}
                >
                  <i className="fa-solid fa-download text-sm"></i>
                </a>
                <button
                  onClick={onClose}
                  className="p-1.5 hover:bg-white/20 rounded transition-colors"
                  title="关闭 (ESC)"
                >
                  <i className="fa-solid fa-times text-sm"></i>
                </button>
              </div>
            </div>

            {/* 内容区域 */}
            <div className="flex-1 overflow-auto bg-white dark:bg-gray-800">
              {isLoading && (
                <div className="flex items-center justify-center h-full">
                  <div className="text-center">
                    <i className="fa-solid fa-spinner fa-spin text-3xl text-indigo-500 mb-2"></i>
                    <p className="text-gray-600 dark:text-gray-400">加载中...</p>
                  </div>
                </div>
              )}
              {error && (
                <div className="flex items-center justify-center h-full">
                  <div className="text-center">
                    <i className="fa-solid fa-exclamation-circle text-3xl text-red-500 mb-2"></i>
                    <p className="text-red-600 dark:text-red-400">{error}</p>
                  </div>
                </div>
              )}
              <div
                ref={docContainerRef}
                className="docx-wrapper"
                style={{
                  display: isLoading || error ? 'none' : 'block',
                  minHeight: '100%',
                  background: '#fff',
                  padding: 0,
                  margin: 0
                }}
              />
            </div>

            {/* 调整大小手柄 */}
            <div
              className="absolute bottom-0 right-0 w-4 h-4 cursor-nwse-resize"
              onMouseDown={(e) => handleResizeStart(e, "se")}
            >
              <div className="absolute bottom-0 right-0 w-3 h-3 border-r-2 border-b-2 border-gray-400 dark:border-gray-600"></div>
            </div>
            {/* 边缘调整手柄 */}
            <div
              className="absolute top-0 left-0 right-0 h-1 cursor-ns-resize"
              onMouseDown={(e) => handleResizeStart(e, "n")}
            />
            <div
              className="absolute bottom-0 left-0 right-0 h-1 cursor-ns-resize"
              onMouseDown={(e) => handleResizeStart(e, "s")}
            />
            <div
              className="absolute top-0 left-0 bottom-0 w-1 cursor-ew-resize"
              onMouseDown={(e) => handleResizeStart(e, "w")}
            />
            <div
              className="absolute top-0 right-0 bottom-0 w-1 cursor-ew-resize"
              onMouseDown={(e) => handleResizeStart(e, "e")}
            />
          </motion.div>
      )}
    </AnimatePresence>
    </>,
    document.body
  );
}

// Hook for managing attachment viewer state
export function useAttachmentViewer() {
  const [viewerState, setViewerState] = useState<{
    isOpen: boolean;
    fileName: string;
    fileId: string;
    threadId: string;
  } | null>(null);

  const openViewer = (fileName: string, fileId: string, threadId: string) => {
    setViewerState({ isOpen: true, fileName, fileId, threadId });
  };

  const closeViewer = () => {
    setViewerState(null);
  };

  return {
    viewerState,
    openViewer,
    closeViewer,
  };
}
