/**
 * AI 引导消息组件
 * 新会话时显示，引导用户操作
 */
import { motion } from "framer-motion";
import type { AgentGuideConfig } from "../pages/GeneralAssistant/types";

interface AIGuideMessageProps {
  guide: AgentGuideConfig;
  onActionClick?: () => void;
  onExampleClick?: (prompt: string) => void;
  rightContent?: React.ReactNode;  // 标题右侧自定义内容
}

// 图标颜色映射
const ICON_COLOR_MAP: Record<string, { bg: string; text: string }> = {
  blue: { bg: "bg-blue-100 dark:bg-blue-900/30", text: "text-blue-600 dark:text-blue-400" },
  purple: { bg: "bg-purple-100 dark:bg-purple-900/30", text: "text-purple-600 dark:text-purple-400" },
  indigo: { bg: "bg-indigo-100 dark:bg-indigo-900/30", text: "text-indigo-600 dark:text-indigo-400" },
  green: { bg: "bg-green-100 dark:bg-green-900/30", text: "text-green-600 dark:text-green-400" },
  orange: { bg: "bg-orange-100 dark:bg-orange-900/30", text: "text-orange-600 dark:text-orange-400" },
  teal: { bg: "bg-teal-100 dark:bg-teal-900/30", text: "text-teal-600 dark:text-teal-400" },
  cyan: { bg: "bg-cyan-100 dark:bg-cyan-900/30", text: "text-cyan-600 dark:text-cyan-400" },
};

export function AIGuideMessage({ guide, onActionClick, onExampleClick, rightContent }: AIGuideMessageProps) {
  const colorConfig = ICON_COLOR_MAP[guide.iconColor || "blue"] || ICON_COLOR_MAP.blue;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className="w-full max-w-2xl rounded-xl bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 p-5 shadow-sm"
    >
      {/* 标题行：图标 + 标题 + 右侧内容 */}
      <div className="flex items-center gap-2 mb-1">
        <div className={`flex-shrink-0 w-8 h-8 rounded-lg ${colorConfig.bg} flex items-center justify-center`}>
          <i className={`fa-solid ${guide.icon || "fa-robot"} text-sm ${colorConfig.text}`}></i>
        </div>
        <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          {guide.title}
        </h3>
        {rightContent && (
          <div className="ml-auto flex items-center gap-2">
            {rightContent}
          </div>
        )}
      </div>

      {/* 描述 */}
      <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
        {guide.description}
      </p>

      {/* 操作按钮 */}
      {guide.actionButton && (
        <button
          type="button"
          onClick={onActionClick}
          className="inline-flex items-center gap-2 px-4 py-2.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:border-gray-300 dark:hover:border-gray-600 transition-all shadow-sm mb-4"
        >
          {guide.actionButton.icon && (
            <i className={`fa-solid ${guide.actionButton.icon} text-gray-500 dark:text-gray-400`}></i>
          )}
          <span>{guide.actionButton.label}</span>
          <i className="fa-solid fa-chevron-right text-xs text-gray-400"></i>
        </button>
      )}

      {/* 示例提示 */}
      {guide.examplePrompts && guide.examplePrompts.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">可以试试以下示例：</p>
          <div className="flex flex-col gap-2">
            {guide.examplePrompts.map((prompt, index) => (
              <button
                key={index}
                type="button"
                title={prompt}
                onClick={() => onExampleClick?.(prompt)}
                className="inline-flex items-center justify-between gap-2 px-3 py-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-gray-900 dark:hover:text-gray-200 transition-colors w-fit max-w-md"
              >
                <span>{prompt}</span>
                <i className="fa-solid fa-chevron-right text-xs text-gray-400 flex-shrink-0"></i>
              </button>
            ))}
          </div>
        </div>
      )}
    </motion.div>
  );
}
