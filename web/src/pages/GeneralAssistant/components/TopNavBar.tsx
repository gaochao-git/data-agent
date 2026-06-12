/**
 * 公共顶部导航栏组件
 *
 * 所有页面统一使用，包含：
 * - 新建按钮
 * - 折叠按钮（控制侧边栏）
 * - 返回按钮（返回首页，仅非首页显示）
 * - 标题（当前智能体名称）
 */

interface TopNavBarProps {
  title: string;
  onBack: () => void;
  onNewConversation: () => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  showBackButton?: boolean;  // 是否显示返回按钮
}

export function TopNavBar({ title, onBack, onNewConversation, isCollapsed, onToggleCollapse, showBackButton = true }: TopNavBarProps) {
  return (
    <div className="flex items-center justify-center py-3 h-[52px] relative bg-transparent">
      <div className="absolute left-3 flex items-center">
        {/* 新建按钮 - 仅侧边栏折叠时显示 */}
        {isCollapsed && (
          <button
            onClick={onNewConversation}
            className="flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-white/50"
            title="新建会话"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#8592A6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
              <path d="M12 8v4M10 10h4"></path>
            </svg>
          </button>
        )}
        {/* 折叠按钮 */}
        <button
          onClick={onToggleCollapse}
          className="flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
          title={isCollapsed ? "展开侧边栏" : "收起侧边栏"}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
            <line x1="9" y1="3" x2="9" y2="21"></line>
          </svg>
        </button>
        {/* 返回首页按钮 - 仅非首页显示 */}
        {showBackButton && (
          <button
            onClick={onBack}
            className="flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
            title="返回首页"
          >
            <i className="fa-solid fa-house text-sm"></i>
          </button>
        )}
      </div>
      <h2 className="text-sm font-medium text-gray-700 dark:text-gray-300">
        {title}
      </h2>
    </div>
  );
}
