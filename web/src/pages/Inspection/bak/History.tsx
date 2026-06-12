import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useState, useRef, useCallback } from "react";
import { mergeClassNames } from "../utils";
import { API_BASE } from "../../../lib/config";
import { toast } from "sonner";

interface ConversationThread {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
  threadId: string | null;
  threadTitle: string;
}

interface HistorySidebarProps {
  activeThreadId: string | null;
  isHistoryCollapsed: boolean;
  onToggleCollapse: () => void;
  onSelectThread: (threadId: string, title?: string) => void;
  onStartNewInspection: () => void;
  panelHeightClass: string;
  refreshTrigger?: number;
  onThreadsLoaded?: (threads: ConversationThread[]) => void;
}

export function History({
  activeThreadId,
  isHistoryCollapsed,
  onToggleCollapse,
  onSelectThread,
  onStartNewInspection,
  panelHeightClass,
  refreshTrigger,
  onThreadsLoaded,
}: HistorySidebarProps) {
  const [threads, setThreads] = useState<ConversationThread[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    visible: false,
    x: 0,
    y: 0,
    threadId: null,
    threadTitle: "",
  });
  const [editingThreadId, setEditingThreadId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const editInputRef = useRef<HTMLInputElement>(null);
  const contextMenuRef = useRef<HTMLDivElement>(null);

  // 从后端获取历史会话列表
  const fetchHistory = useCallback(async () => {
    try {
      setIsLoading(true);
      const response = await fetch(`${API_BASE}/api/conversations`, {
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      const threadList = data.data || [];
      setThreads(threadList);
      // 通知父组件 threads 已加载
      onThreadsLoaded?.(threadList);
    } catch (error) {
      console.error("获取历史会话失败:", error);
      setThreads([]);
    } finally {
      setIsLoading(false);
    }
  }, [onThreadsLoaded]);

  useEffect(() => {
    fetchHistory();
  }, [refreshTrigger, fetchHistory]);

  // 点击外部关闭右键菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(event.target as Node)) {
        setContextMenu((prev) => ({ ...prev, visible: false }));
      }
    };

    if (contextMenu.visible) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [contextMenu.visible]);

  // 编辑模式时自动聚焦输入框
  useEffect(() => {
    if (editingThreadId && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingThreadId]);

  // 右键菜单处理
  const handleContextMenu = (e: React.MouseEvent, thread: ConversationThread) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({
      visible: true,
      x: e.clientX,
      y: e.clientY,
      threadId: thread.id,
      threadTitle: thread.title,
    });
  };

  // 开始重命名
  const handleStartRename = () => {
    if (contextMenu.threadId) {
      setEditingThreadId(contextMenu.threadId);
      setEditingTitle(contextMenu.threadTitle);
    }
    setContextMenu((prev) => ({ ...prev, visible: false }));
  };

  // 保存重命名
  const handleSaveRename = async () => {
    if (!editingThreadId || !editingTitle.trim()) {
      setEditingThreadId(null);
      return;
    }

    const newTitle = editingTitle.trim();

    try {
      const response = await fetch(`${API_BASE}/api/conversations/${editingThreadId}/title`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          title: newTitle,
        }),
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("重命名失败");
      }

      // 更新本地状态
      setThreads((prev) =>
        prev.map((t) =>
          t.id === editingThreadId ? { ...t, title: newTitle } : t
        )
      );

      toast.success("重命名成功");
    } catch (error) {
      console.error("重命名失败:", error);
      toast.error("重命名失败");
    } finally {
      setEditingThreadId(null);
    }
  };

  // 取消重命名
  const handleCancelRename = () => {
    setEditingThreadId(null);
    setEditingTitle("");
  };

  // 处理编辑框按键
  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSaveRename();
    } else if (e.key === "Escape") {
      handleCancelRename();
    }
  };

  // 删除会话
  const handleDelete = async () => {
    if (!contextMenu.threadId) return;

    const threadId = contextMenu.threadId;
    setContextMenu((prev) => ({ ...prev, visible: false }));

    if (!confirm("确定要删除这个会话吗？")) {
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/api/conversations/${threadId}`, {
        method: "DELETE",
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("删除失败");
      }

      // 更新本地状态
      setThreads((prev) => prev.filter((t) => t.id !== threadId));
      toast.success("删除成功");
    } catch (error) {
      console.error("删除失败:", error);
      toast.error("删除失败");
    }
  };

  return (
    <motion.aside
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35 }}
      className={mergeClassNames(
        "flex flex-col overflow-hidden bg-white transition-all dark:bg-gray-900",
        isHistoryCollapsed ? "w-14" : "w-[260px] border-r border-gray-200 dark:border-gray-700",
        panelHeightClass,
      )}
    >
      {/* Header - 始终显示 */}
      <div className="flex flex-col">
        {isHistoryCollapsed ? (
          // 折叠状态：只显示新建和展开按钮
          <div className="flex items-center justify-center gap-2 px-3 py-3 h-[52px]">
            <button
              type="button"
              onClick={onStartNewInspection}
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-700 transition-colors hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
              title="新建会话"
            >
              <i className="fa-solid fa-plus"></i>
            </button>
            <button
              type="button"
              onClick={onToggleCollapse}
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-700 transition-colors hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
              title="展开会话历史"
            >
              <i className="fa-solid fa-angles-right"></i>
            </button>
          </div>
        ) : (
          // 展开状态：显示标题和按钮
          <>
            <div className="flex items-center justify-between px-3 py-3">
              <div className="flex items-center gap-2">
                <i className="fa-solid fa-gavel text-lg text-gray-800 dark:text-gray-200"></i>
                <h2 className="text-sm font-semibold text-gray-900 dark:text-white whitespace-nowrap">
                  执法检查AI辅助
                </h2>
              </div>
              <button
                type="button"
                onClick={onToggleCollapse}
                className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-200 dark:text-gray-400 dark:hover:bg-gray-700 ml-2"
                title="收起会话历史"
              >
                <i className="fa-solid fa-angles-left"></i>
              </button>
            </div>
            <div className="flex items-center gap-2 px-3 pb-3">
              <button
                type="button"
                onClick={onStartNewInspection}
                className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-lg border border-gray-300 bg-white px-4 py-1.5 text-xs font-medium text-gray-900 transition-colors hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-white dark:hover:bg-gray-700"
              >
                <i className="fa-solid fa-plus text-xs"></i>
                新建会话
              </button>
            </div>
          </>
        )}
      </div>

      {/* 历史会话列表 - 仅展开时显示 */}
      {!isHistoryCollapsed && (
        <div className="flex-1 overflow-y-auto bg-gray-50/50 dark:bg-gray-900/50">
          {isLoading ? (
            <div className="px-4 py-6 text-center text-sm text-gray-400 dark:text-gray-500">
              <i className="fa-solid fa-spinner fa-spin"></i> 加载中...
            </div>
          ) : threads.length === 0 ? (
            <div className="px-4 py-6 text-sm text-gray-400 dark:text-gray-500">
              暂无历史记录
            </div>
          ) : (
            <ul>
              {threads.map((thread) => {
                const isActive = thread.id === activeThreadId;
                const isEditing = thread.id === editingThreadId;

                return (
                  <li key={thread.id}>
                    <div
                      onContextMenu={(e) => handleContextMenu(e, thread)}
                      onClick={() => !isEditing && onSelectThread(thread.id, thread.title)}
                      className={mergeClassNames(
                        "w-full px-3 py-2 text-left transition-all cursor-pointer group",
                        isActive
                          ? "bg-indigo-50/70 dark:bg-indigo-900/30"
                          : "hover:bg-gray-100/50 dark:hover:bg-gray-800/70",
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          {isEditing ? (
                            <input
                              ref={editInputRef}
                              type="text"
                              value={editingTitle}
                              onChange={(e) => setEditingTitle(e.target.value)}
                              onBlur={handleSaveRename}
                              onKeyDown={handleEditKeyDown}
                              className="w-full px-1 py-0.5 text-xs font-medium text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-800 border border-indigo-400 rounded outline-none"
                              onClick={(e) => e.stopPropagation()}
                            />
                          ) : (
                            <p className="truncate text-xs font-medium text-gray-900 dark:text-gray-100">
                              {thread.title}
                            </p>
                          )}
                          <p className="mt-0.5 text-[11px] text-gray-400 dark:text-gray-500">
                            {new Date(thread.created_at).toLocaleString("zh-CN", {
                              month: "numeric",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                              hour12: false,
                            })}
                          </p>
                        </div>
                        {/* 更多按钮 - hover 时显示 */}
                        {!isEditing && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleContextMenu(e, thread);
                            }}
                            className="opacity-0 group-hover:opacity-100 inline-flex h-6 w-6 items-center justify-center rounded text-gray-400 hover:text-gray-600 hover:bg-gray-200 dark:hover:text-gray-300 dark:hover:bg-gray-700 transition-all"
                            title="更多操作"
                          >
                            <i className="fa-solid fa-ellipsis"></i>
                          </button>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {/* 右键菜单 */}
      <AnimatePresence>
        {contextMenu.visible && (
          <motion.div
            ref={contextMenuRef}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.1 }}
            className="fixed z-50 min-w-[120px] rounded-lg border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            <button
              type="button"
              onClick={handleStartRename}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
            >
              <i className="fa-solid fa-pen w-4"></i>
              重命名
            </button>
            <button
              type="button"
              onClick={handleDelete}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20"
            >
              <i className="fa-solid fa-trash w-4"></i>
              删除
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.aside>
  );
}
