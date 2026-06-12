import { useState, useMemo, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { ConversationHistoryItem } from "../types";

// agent_id 到显示名称和颜色的映射
const AGENT_LABELS: Record<string, { name: string; color: string }> = {
  general: { name: "通用", color: "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300" },
  chat_data: { name: "问数", color: "bg-cyan-100 text-cyan-600 dark:bg-cyan-900/30 dark:text-cyan-400" },
  fact_analysis: { name: "事实", color: "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400" },
  report_jcbg: { name: "检查", color: "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400" },
  report_zgtzs: { name: "整改", color: "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400" },
  report_zjml: { name: "证据", color: "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400" },
  report_xxjb: { name: "简表", color: "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400" },
  complaint_agent: { name: "投诉", color: "bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400" },
  expert_agent: { name: "专家", color: "bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400" },
};

// 时间分组类型
type TimeGroup = "hour1" | "hour5" | "today" | "week" | "month" | "older";

// 时间分组配置
const TIME_GROUP_LABELS: Record<TimeGroup, string> = {
  hour1: "最近 1 小时",
  hour5: "最近 5 小时",
  today: "今天",
  week: "7 天内",
  month: "30 天内",
  older: "更早",
};

// 判断日期属于哪个时间分组
function getTimeGroup(date: Date | undefined): TimeGroup {
  if (!date || !(date instanceof Date) || isNaN(date.getTime())) {
    return "older";
  }

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffHours = diffMs / (1000 * 60 * 60);
  const diffDays = diffMs / (1000 * 60 * 60 * 24);

  if (diffHours <= 1) return "hour1";
  if (diffHours <= 5) return "hour5";
  if (diffDays < 1) return "today";
  if (diffDays <= 7) return "week";
  if (diffDays <= 30) return "month";
  return "older";
}

// 按时间分组历史记录
function groupHistoryByTime(history: ConversationHistoryItem[]): Map<TimeGroup, ConversationHistoryItem[]> {
  const groups = new Map<TimeGroup, ConversationHistoryItem[]>();

  // 初始化所有分组
  const groupOrder: TimeGroup[] = ["hour1", "hour5", "today", "week", "month", "older"];
  groupOrder.forEach(group => groups.set(group, []));

  // 分配每条记录到对应分组
  history.forEach(item => {
    const group = getTimeGroup(item.update_time);
    groups.get(group)!.push(item);
  });

  return groups;
}

interface HistorySidebarProps {
  isCollapsed: boolean;
  setIsCollapsed: (collapsed: boolean) => void;
  history: ConversationHistoryItem[];
  activeHistoryId: string | null;
  onSelectHistory: (id: string) => void;
  onNewConversation: () => void;
  onUpdateTitle?: (id: string, newTitle: string) => void;
  onToggleFavorite?: (id: string) => void;
  onDeleteConversation?: (id: string) => void;
  panelHeightClass: string;
  authError?: string | null;
  isAgentView?: boolean;
  isLoading?: boolean;
}

export function HistorySidebar({
  isCollapsed,
  setIsCollapsed,
  history,
  activeHistoryId,
  onSelectHistory,
  onNewConversation,
  onUpdateTitle,
  onToggleFavorite,
  onDeleteConversation,
  panelHeightClass,
  authError,
  isAgentView = false,
  isLoading = false,
}: HistorySidebarProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);  // 控制哪个菜单打开
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [filterMode, setFilterMode] = useState<"all" | "favorited">("all");

  // 点击外部关闭菜单
  useEffect(() => {
    const handleClickOutside = () => {
      if (menuOpenId) {
        setMenuOpenId(null);
      }
    };
    document.addEventListener("click", handleClickOutside);
    return () => document.removeEventListener("click", handleClickOutside);
  }, [menuOpenId]);

  // 根据筛选模式过滤历史记录
  const filteredHistory = filterMode === "favorited"
    ? history.filter(item => item.is_favorited)
    : history;

  // 按时间分组
  const groupedHistory = useMemo(() => groupHistoryByTime(filteredHistory), [filteredHistory]);
  const groupOrder: TimeGroup[] = ["hour1", "hour5", "today", "week", "month", "older"];

  return (
    <motion.aside
      className={`flex flex-col overflow-hidden transition-all dark:bg-gray-900 ${
        isCollapsed
          ? `w-14 ${isAgentView ? "bg-[#F7F9FA]" : "bg-transparent"}`
          : "w-[280px] border-r border-gray-200 dark:border-gray-700 bg-gray-50"
      } ${panelHeightClass}`}
    >
      {isCollapsed ? (
        // 折叠状态：占位保持高度一致
        <div className="h-[52px]"></div>
      ) : (
        // 展开状态：显示标题和新建按钮
        <>
          <div className="flex items-center justify-center px-3 py-3 h-[52px]">
            <h2
              className="font-semibold whitespace-nowrap"
              style={{
                background: 'linear-gradient(112deg, #007EA7 1%, #1F3A56 100%)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
                backgroundClip: 'text',
                fontSize: '18px',
              }}
            >
              智能体中心
            </h2>
          </div>

          {/* 新建会话按钮 */}
          <div className="px-3 py-3">
            <button
              onClick={onNewConversation}
              style={{
                borderRadius: '20px',
                border: '1px solid #007EA7',
              }}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium transition-all hover:bg-cyan-50"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#007EA7" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
                <path d="M12 8v4M10 10h4"></path>
              </svg>
              <span style={{ color: '#007EA7' }}>新建会话</span>
            </button>
          </div>

          {/* 筛选按钮 */}
          <div className="border-t border-gray-200 dark:border-gray-700 px-3 py-2">
            <div className="flex">
              <button
                onClick={() => setFilterMode("all")}
                className={`flex-1 relative px-3 py-2 text-sm font-medium transition-all ${
                  filterMode === "all"
                    ? "text-cyan-600 dark:text-cyan-400"
                    : "text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
                }`}
              >
                <i className="fa-solid fa-list mr-1.5"></i>
                全部会话
                {filterMode === "all" && (
                  <span className="absolute bottom-0 left-1/2 -translate-x-1/2 w-12 h-0.5 bg-cyan-600 dark:bg-cyan-400 rounded-full"></span>
                )}
              </button>
              <button
                onClick={() => setFilterMode("favorited")}
                className={`flex-1 relative px-3 py-2 text-sm font-medium transition-all ${
                  filterMode === "favorited"
                    ? "text-cyan-600 dark:text-cyan-400"
                    : "text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
                }`}
              >
                <i className="fa-regular fa-star mr-1.5"></i>
                收藏
                {filterMode === "favorited" && (
                  <span className="absolute bottom-0 left-1/2 -translate-x-1/2 w-8 h-0.5 bg-cyan-600 dark:bg-cyan-400 rounded-full"></span>
                )}
              </button>
            </div>
          </div>

          {/* 历史对话列表 */}
          <div className="flex-1 overflow-y-auto px-3 pb-4">
            {authError ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <i className="fa-solid fa-lock text-4xl mb-3 text-orange-400 dark:text-orange-500"></i>
                <p className="text-sm text-orange-600 dark:text-orange-400 font-medium">
                  {authError}
                </p>
              </div>
            ) : isLoading ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <i className="fa-solid fa-spinner fa-spin text-3xl mb-3 text-cyan-500 dark:text-cyan-400"></i>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  加载中...
                </p>
              </div>
            ) : filteredHistory.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <i className={`text-4xl mb-3 ${
                  filterMode === "favorited"
                    ? "fa-regular fa-star text-yellow-300 dark:text-yellow-600"
                    : "fa-regular fa-comments text-gray-300 dark:text-gray-600"
                }`}></i>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  {filterMode === "favorited" ? "暂无收藏对话" : "暂无历史对话"}
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {groupOrder.map((group) => {
                  const items = groupedHistory.get(group) || [];
                  if (items.length === 0) return null;

                  return (
                    <div key={group}>
                      {/* 分组标题 */}
                      <div className="px-1 py-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
                        {TIME_GROUP_LABELS[group]}
                      </div>
                      {/* 分组内容 */}
                      <div className="space-y-1">
                        {items.map((item) => (
                          <motion.div
                            key={item.id}
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            onMouseEnter={() => setHoveredId(item.id)}
                            onMouseLeave={() => setHoveredId(null)}
                            className={`group relative w-full rounded-lg px-3 py-2.5 transition-all ${
                              activeHistoryId === item.id
                                ? "bg-indigo-50 dark:bg-indigo-900/20"
                                : "hover:bg-gray-50 dark:hover:bg-gray-800"
                            }`}
                          >
                            <div className="relative">
                              <div
                                className="cursor-pointer"
                                onClick={() => editingId !== item.id && onSelectHistory(item.id)}
                              >
                                {editingId === item.id ? (
                                  <input
                                    type="text"
                                    value={editingTitle}
                                    onChange={(e) => setEditingTitle(e.target.value)}
                                    onBlur={() => {
                                      if (editingTitle.trim() && editingTitle !== item.title) {
                                        onUpdateTitle?.(item.id, editingTitle.trim());
                                      }
                                      setEditingId(null);
                                    }}
                                    onKeyDown={(e) => {
                                      if (e.key === 'Enter') {
                                        if (editingTitle.trim() && editingTitle !== item.title) {
                                          onUpdateTitle?.(item.id, editingTitle.trim());
                                        }
                                        setEditingId(null);
                                      } else if (e.key === 'Escape') {
                                        setEditingId(null);
                                      }
                                    }}
                                    autoFocus
                                    className="w-full text-sm font-medium px-2 py-1 border border-indigo-400 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                                    onClick={(e) => e.stopPropagation()}
                                  />
                                ) : (
                                  <>
                                    <div className="flex items-center gap-1.5">
                                      {item.is_favorited && (
                                        <i className="fa-solid fa-star text-xs text-yellow-500"></i>
                                      )}
                                      <p
                                        className={`text-sm font-medium ${
                                          activeHistoryId === item.id
                                            ? "text-indigo-700 dark:text-indigo-400"
                                            : "text-gray-900 dark:text-gray-100"
                                        }`}
                                        title={item.title}
                                      >
                                        {item.title.length > 15 ? `${item.title.slice(0, 15)}...` : item.title}
                                      </p>
                                    </div>
                                    <div className="mt-1 flex items-center gap-2">
                                      {/* 智能体标签 */}
                                      {item.agent_id && AGENT_LABELS[item.agent_id] && (
                                        <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${AGENT_LABELS[item.agent_id].color}`}>
                                          {AGENT_LABELS[item.agent_id].name}
                                        </span>
                                      )}
                                      <span className="text-xs text-gray-500 dark:text-gray-400">
                                        {formatDate(item.update_time)}
                                      </span>
                                    </div>
                                  </>
                                )}
                              </div>

                              {/* 三点菜单按钮 - 绝对定位悬浮在右上角 */}
                              {(hoveredId === item.id || menuOpenId === item.id) && editingId !== item.id && (
                                <div className="absolute right-0 top-0 flex-shrink-0">
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setMenuOpenId(menuOpenId === item.id ? null : item.id);
                                    }}
                                    className="flex h-6 w-6 items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300 cursor-pointer"
                                  >
                                    <i className="fa-solid fa-ellipsis text-sm"></i>
                                  </button>

                                  {/* 下拉菜单 */}
                                  <AnimatePresence>
                                    {menuOpenId === item.id && (
                                      <motion.div
                                        initial={{ opacity: 0, scale: 0.95, y: -5 }}
                                        animate={{ opacity: 1, scale: 1, y: 0 }}
                                        exit={{ opacity: 0, scale: 0.95, y: -5 }}
                                        transition={{ duration: 0.1 }}
                                        className="absolute right-0 top-7 z-50 w-32 rounded-lg bg-white shadow-lg border border-gray-200 dark:bg-gray-800 dark:border-gray-700 py-1"
                                      >
                                        {/* 重命名 */}
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            setEditingId(item.id);
                                            setEditingTitle(item.title);
                                            setMenuOpenId(null);
                                          }}
                                          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-700"
                                        >
                                          <i className="fa-solid fa-pen text-xs text-gray-400"></i>
                                          重命名
                                        </button>

                                        {/* 收藏/取消收藏 */}
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            onToggleFavorite?.(item.id);
                                            setMenuOpenId(null);
                                          }}
                                          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-700"
                                        >
                                          <i className={`text-xs ${item.is_favorited ? "fa-solid fa-star text-yellow-500" : "fa-regular fa-star text-gray-400"}`}></i>
                                          {item.is_favorited ? "取消收藏" : "收藏"}
                                        </button>

                                        {/* 分割线 */}
                                        <div className="my-1 border-t border-gray-200 dark:border-gray-700"></div>

                                        {/* 删除 */}
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            if (confirm(`确定要删除会话 "${item.title}" 吗？`)) {
                                              onDeleteConversation?.(item.id);
                                            }
                                            setMenuOpenId(null);
                                          }}
                                          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20"
                                        >
                                          <i className="fa-solid fa-trash text-xs"></i>
                                          删除
                                        </button>
                                      </motion.div>
                                    )}
                                  </AnimatePresence>
                                </div>
                              )}
                            </div>
                          </motion.div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </>
      )}
    </motion.aside>
  );
}

function formatDate(date: Date | undefined): string {
  // 防御性检查：确保 date 是有效的 Date 对象
  if (!date || !(date instanceof Date) || isNaN(date.getTime())) {
    return "未知时间";
  }

  // 格式化为 YYYY-MM-DD HH:mm:ss
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');

  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}
