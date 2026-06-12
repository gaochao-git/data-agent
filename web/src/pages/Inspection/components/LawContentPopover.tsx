import { useState, useRef, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";

interface LawDocumentViewerProps {
  lawName: string;
  lawItemName: string;
  isOpen: boolean;
  onClose: () => void;
  // 初始位置
  initialPosition?: { x: number; y: number };
  // 加载法律文档内容的回调
  onLoadContent?: (lawName: string, lawItemName: string) => Promise<string>;
}

export function LawDocumentViewer({
  lawName,
  lawItemName,
  isOpen,
  onClose,
  initialPosition,
  onLoadContent,
}: LawDocumentViewerProps) {
  // 卡片位置
  const [position, setPosition] = useState({ x: 100, y: 100 });
  // 卡片尺寸
  const [size, setSize] = useState({ width: 700, height: 600 });
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
  // 文档内容
  const [content, setContent] = useState<string>("");
  // 加载状态
  const [isLoading, setIsLoading] = useState(false);

  // 最小尺寸
  const MIN_WIDTH = 400;
  const MIN_HEIGHT = 300;

  // 初始化位置
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
  }, [isOpen, initialPosition]);

  // 加载法律文档内容
  useEffect(() => {
    if (isOpen && lawName) {
      setIsLoading(true);
      // 模拟加载，实际应该从后端获取
      if (onLoadContent) {
        onLoadContent(lawName, lawItemName)
          .then(setContent)
          .catch(() => setContent("加载失败，请稍后重试"))
          .finally(() => setIsLoading(false));
      } else {
        // 默认显示提示
        setTimeout(() => {
          setContent(`该功能需要连接法律法规数据库，请配置后端接口以获取完整法规原文。`);
          setIsLoading(false);
        }, 300);
      }
    }
  }, [isOpen, lawName, lawItemName, onLoadContent]);

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

  // 拖动中
  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = e.clientX - dragStartRef.current.x;
      const deltaY = e.clientY - dragStartRef.current.y;

      const newX = dragStartRef.current.posX + deltaX;
      const newY = dragStartRef.current.posY + deltaY;

      // 限制在视口内
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;

      setPosition({
        x: Math.max(0, Math.min(newX, viewportWidth - size.width)),
        y: Math.max(0, Math.min(newY, viewportHeight - size.height)),
      });
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, size]);

  // 调整大小中
  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = e.clientX - dragStartRef.current.x;
      const deltaY = e.clientY - dragStartRef.current.y;

      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;

      let newWidth = dragStartRef.current.width;
      let newHeight = dragStartRef.current.height;
      let newX = dragStartRef.current.posX;
      let newY = dragStartRef.current.posY;

      // 根据方向调整
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

      // 限制不超出视口
      if (newX + newWidth > viewportWidth) {
        newWidth = viewportWidth - newX;
      }
      if (newY + newHeight > viewportHeight) {
        newHeight = viewportHeight - newY;
      }
      if (newX < 0) {
        newWidth += newX;
        newX = 0;
      }
      if (newY < 0) {
        newHeight += newY;
        newY = 0;
      }

      setSize({ width: newWidth, height: newHeight });
      setPosition({ x: newX, y: newY });
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      setResizeDirection("");
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isResizing, resizeDirection]);

  if (!isOpen) return null;

  const popoverContent = (
    <AnimatePresence>
      <motion.div
        ref={cardRef}
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        transition={{ duration: 0.15 }}
        className="fixed z-[9999] bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 flex flex-col"
        style={{
          left: position.x,
          top: position.y,
          width: size.width,
          height: size.height,
        }}
      >
        {/* 头部 - 可拖动区域 */}
        <div
          className="flex items-center justify-between px-4 py-3 bg-indigo-50 dark:bg-indigo-900/30 border-b border-gray-200 dark:border-gray-700 select-none flex-shrink-0 rounded-t-xl"
          style={{ cursor: isDragging ? "grabbing" : "grab" }}
          onMouseDown={handleDragStart}
          title="拖动此处移动窗口"
        >
          <div className="flex items-center gap-2 min-w-0">
            <i className="fa-solid fa-scale-balanced text-indigo-600 dark:text-indigo-400 text-sm"></i>
            <span className="text-sm font-semibold text-gray-900 dark:text-white truncate">
              {lawName}
            </span>
            <span className="text-xs text-gray-400 ml-2">| 点击拖动位置</span>
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            {/* 关闭按钮 */}
            <button
              type="button"
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onClose();
              }}
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            >
              <i className="fa-solid fa-xmark"></i>
            </button>
          </div>
        </div>

        {/* 内容区域 - 模拟A4纸样式 */}
        <div className="flex-1 overflow-auto p-6 bg-gray-100 dark:bg-gray-900">
          <div className="bg-white dark:bg-gray-800 mx-auto max-w-[595px] min-h-[842px] p-10 shadow-lg rounded">
            {isLoading ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center">
                  <i className="fa-solid fa-spinner fa-spin text-2xl text-indigo-500 mb-3"></i>
                  <p className="text-sm text-gray-500">正在加载法规原文...</p>
                </div>
              </div>
            ) : (
              <div className="text-sm text-gray-800 dark:text-gray-200 leading-relaxed whitespace-pre-wrap">
                {content}
              </div>
            )}
          </div>
        </div>

        {/* 底部提示 */}
        <div className="flex items-center justify-between px-4 py-2 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 text-xs text-gray-500 dark:text-gray-400 flex-shrink-0 rounded-b-xl">
          <span>
            <i className="fa-solid fa-arrows-up-down-left-right mr-1"></i>
            拖动边缘调整大小
          </span>
          <span>
            <i className="fa-solid fa-file-pdf mr-1"></i>
            法规原文预览
          </span>
        </div>

        {/* 调整大小的手柄 */}
        {/* 四条边 */}
        <div className="absolute top-0 left-2 right-2 h-1 cursor-n-resize" onMouseDown={(e) => handleResizeStart(e, "n")} />
        <div className="absolute bottom-0 left-2 right-2 h-1 cursor-s-resize" onMouseDown={(e) => handleResizeStart(e, "s")} />
        <div className="absolute left-0 top-2 bottom-2 w-1 cursor-w-resize" onMouseDown={(e) => handleResizeStart(e, "w")} />
        <div className="absolute right-0 top-2 bottom-2 w-1 cursor-e-resize" onMouseDown={(e) => handleResizeStart(e, "e")} />
        {/* 四个角 */}
        <div className="absolute top-0 left-0 w-3 h-3 cursor-nw-resize" onMouseDown={(e) => handleResizeStart(e, "nw")} />
        <div className="absolute top-0 right-0 w-3 h-3 cursor-ne-resize" onMouseDown={(e) => handleResizeStart(e, "ne")} />
        <div className="absolute bottom-0 left-0 w-3 h-3 cursor-sw-resize" onMouseDown={(e) => handleResizeStart(e, "sw")} />
        <div className="absolute bottom-0 right-0 w-3 h-3 cursor-se-resize" onMouseDown={(e) => handleResizeStart(e, "se")} />
      </motion.div>
    </AnimatePresence>
  );

  // 使用 Portal 渲染到 body，避免被父元素的 overflow 裁剪
  return createPortal(popoverContent, document.body);
}

// 用于管理法律文档查看器的 Hook
export function useLawDocumentViewer() {
  const [viewerState, setViewerState] = useState<{
    isOpen: boolean;
    lawName: string;
    lawItemName: string;
    position: { x: number; y: number };
  } | null>(null);

  const openViewer = useCallback((
    lawName: string,
    lawItemName: string,
    triggerElement?: HTMLElement
  ) => {
    const position = triggerElement
      ? {
          x: triggerElement.getBoundingClientRect().left - 350,
          y: triggerElement.getBoundingClientRect().top - 200,
        }
      : { x: 100, y: 100 };

    setViewerState({
      isOpen: true,
      lawName,
      lawItemName,
      position,
    });
  }, []);

  const closeViewer = useCallback(() => {
    setViewerState(null);
  }, []);

  return {
    viewerState,
    openViewer,
    closeViewer,
  };
}

// 保留旧的导出名称以保持兼容性
export const LawContentPopover = LawDocumentViewer;
export const useLawContentPopover = useLawDocumentViewer;
