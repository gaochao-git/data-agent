import type { RefObject } from "react";
import type { AgentId, AgentTab, ConversationSummary } from "./types";

type LeftSidebarProps = {
  isCollapsed: boolean;
  expandButtonTop: number | null;
  expandSidebarButtonRef: RefObject<HTMLButtonElement>;
  collapseSidebarButtonRef: RefObject<HTMLButtonElement>;
  onToggleCollapse: () => void;
  agentTabs: AgentTab[];
  activeAgentId: AgentId;
  onStartNewConversation: () => void;
  isLoadingConversations: boolean;
  conversationListError: string | null;
  conversationSummaries: ConversationSummary[];
  activeHistoryId: string | null;
  onSelectHistory: (history: ConversationSummary) => void;
  shorten: (value: string, max?: number) => string;
};

export default function LeftSidebar({
  isCollapsed,
  expandButtonTop,
  expandSidebarButtonRef,
  collapseSidebarButtonRef,
  onToggleCollapse,
  agentTabs,
  activeAgentId,
  onStartNewConversation,
  isLoadingConversations,
  conversationListError,
  conversationSummaries,
  activeHistoryId,
  onSelectHistory,
  shorten,
}: LeftSidebarProps) {
  return (
    <>
      {isCollapsed && (
        <button
          type="button"
          ref={expandSidebarButtonRef}
          className="absolute left-6 z-20 inline-flex h-9 w-11 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-500 shadow-md hover:text-gray-700"
          style={{ top: expandButtonTop ?? 16 }}
          onClick={onToggleCollapse}
          title="展开侧边栏"
        >
          <i className="fa-solid fa-angles-right"></i>
        </button>
      )}
      <aside
        className={`relative transition-all duration-300 flex flex-col overflow-hidden bg-white ${
          isCollapsed ? "w-0 min-w-0" : "w-80 border-r border-gray-200"
        }`}
      >
        <div className="flex items-center justify-between px-4 py-4 border-b border-gray-200">
          <div className="flex items-center space-x-2 text-blue-600 font-semibold">
            <i className="fa-solid fa-robot text-black dark:text-black text-base"></i>
            {!isCollapsed && (
              <span className="text-sm font-semibold text-gray-900 dark:text-white">
                AI专家系统
              </span>
            )}
          </div>
          <button
            type="button"
            className="text-gray-400 hover:text-gray-600"
            ref={collapseSidebarButtonRef}
            onClick={onToggleCollapse}
            title={isCollapsed ? "展开侧边栏" : "收起侧边栏"}
          >
            <i
              className={`fa-solid ${
                isCollapsed ? "fa-angles-right" : "fa-angles-left"
              }`}
            ></i>
          </button>
        </div>

        <div className="px-3 py-4 space-y-3 overflow-y-auto flex-1">
          <div>
            <nav className="space-y-2">
              {agentTabs.map((agent) => {
                const isActive = agent.id === activeAgentId;

                return (
                  <button
                    key={agent.id}
                    type="button"
                    className={`w-full flex items-center ${
                      isCollapsed ? "justify-center" : "justify-between"
                    } rounded-xl px-3 py-3 transition-all ${
                      isActive
                        ? "bg-blue-50 text-blue-600"
                        : "hover:bg-gray-100 text-gray-700"
                    }`}
                    onClick={onStartNewConversation}
                    title={agent.label}
                  >
                    <span className="flex items-center space-x-2">
                      <i
                        className={`fa-solid fa-plus ${
                          isCollapsed ? "text-lg" : "text-base"
                        }`}
                      ></i>
                      {!isCollapsed && (
                        <span className="text-sm font-medium">新会话</span>
                      )}
                    </span>
                    {!isCollapsed && (
                      <i
                        className={`fa-solid fa-circle ${
                          isActive ? "text-blue-400" : "text-gray-200"
                        } text-[10px]`}
                      ></i>
                    )}
                  </button>
                );
              })}
            </nav>
          </div>

          <div>
            {!isCollapsed && (
              <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
                历史会话
              </h2>
            )}
            <div className="space-y-2">
              {isLoadingConversations ? (
                <div className="text-xs text-gray-400 px-3 py-8 text-center">
                  <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                  正在加载历史会话...
                </div>
              ) : conversationListError ? (
                <div className="text-xs text-red-500 px-3 py-8 text-center border border-red-100 bg-red-50 rounded-xl">
                  {conversationListError}
                </div>
              ) : conversationSummaries.length === 0 ? (
                <div className="text-xs text-gray-400 px-3 py-8 text-center border border-gray-100 bg-white rounded-xl">
                  暂无历史会话
                </div>
              ) : (
                conversationSummaries.map((history) => {
                  const isActive = history.id === activeHistoryId;
                  const previewSource =
                    history.title ||
                    history.firstMessagePreview ||
                    history.latestMessagePreview ||
                    `会话 ${history.id.slice(-4)}`;
                  const preview = shorten(previewSource, isCollapsed ? 24 : 48);
                  return (
                    <button
                      key={history.id}
                      type="button"
                      className={`w-full rounded-xl px-3 py-3 text-left transition-all ${
                        isActive
                          ? "bg-blue-50 text-blue-600 border border-blue-200"
                          : "hover:bg-gray-100 text-gray-700 border border-transparent"
                      }`}
                      onClick={() => onSelectHistory(history)}
                      title={previewSource}
                    >
                      {isCollapsed ? (
                        <div className="flex items-center justify-center">
                          <i className="fa-solid fa-comments text-lg"></i>
                        </div>
                      ) : (
                        <div className="text-sm font-semibold truncate">
                          {preview}
                        </div>
                      )}
                    </button>
                  );
                })
              )}
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
