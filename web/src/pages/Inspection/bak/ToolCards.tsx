import { motion } from "framer-motion";
import type { ToolType, ReportSubType } from "../types";

interface ToolCard {
  id: ToolType;
  subType?: ReportSubType;
  name: string;
  description: string;
  icon: string;
  iconBg: string;
  iconColor: string;
}

const toolCards: ToolCard[] = [
  {
    id: "violation-analysis",
    name: "事实认定分析",
    description: "分析违反条款、处罚条款、裁量基准、处罚建议、整改措施",
    icon: "fa-solid fa-scale-balanced",
    iconBg: "bg-indigo-100 dark:bg-indigo-900/40",
    iconColor: "text-indigo-600 dark:text-indigo-400",
  },
  {
    id: "report-generation",
    subType: "comprehensive-report",
    name: "检查报告生成",
    description: "生成完整的执法检查报告",
    icon: "fa-solid fa-file-word",
    iconBg: "bg-emerald-100 dark:bg-emerald-900/40",
    iconColor: "text-emerald-600 dark:text-emerald-400",
  },
  {
    id: "report-generation",
    subType: "rectification-notice",
    name: "整改通知书生成",
    description: "生成责令整改通知文书",
    icon: "fa-solid fa-file-word",
    iconBg: "bg-amber-100 dark:bg-amber-900/40",
    iconColor: "text-amber-600 dark:text-amber-400",
  },
  {
    id: "report-generation",
    subType: "evidence-catalog",
    name: "证据目录生成",
    description: "生成证据材料目录清单",
    icon: "fa-solid fa-file-excel",
    iconBg: "bg-blue-100 dark:bg-blue-900/40",
    iconColor: "text-blue-600 dark:text-blue-400",
  },
  {
    id: "report-generation",
    subType: "info-summary",
    name: "信息简表生成",
    description: "生成执法检查信息简表",
    icon: "fa-solid fa-file-excel",
    iconBg: "bg-purple-100 dark:bg-purple-900/40",
    iconColor: "text-purple-600 dark:text-purple-400",
  },
];

interface ToolCardsProps {
  onSelectTool: (toolId: ToolType, subType?: ReportSubType) => void;
}

export function ToolCards({ onSelectTool }: ToolCardsProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.1 }}
      className="w-full mt-4"
    >
      {/* 第一行：事实认定分析（突出显示） */}
      <div className="mb-2.5">
        <button
          type="button"
          onClick={() => onSelectTool(toolCards[0].id, toolCards[0].subType)}
          className="w-full group rounded-xl border border-gray-200 bg-white p-3 text-left transition-all hover:border-indigo-300 hover:bg-indigo-50/50 hover:shadow-md dark:border-gray-700 dark:bg-gray-800/50 dark:hover:border-indigo-600 dark:hover:bg-indigo-900/20"
        >
          <div className="flex items-center gap-3">
            <div className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg ${toolCards[0].iconBg}`}>
              <i className={`${toolCards[0].icon} text-base ${toolCards[0].iconColor}`}></i>
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-gray-900 dark:text-white">
                {toolCards[0].name}
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                {toolCards[0].description}
              </div>
            </div>
          </div>
        </button>
      </div>

      {/* 第二行：4个报告生成功能 */}
      <div className="grid grid-cols-4 gap-2.5">
        {toolCards.slice(1).map((tool, index) => {
          // 所有报告都已开放
          const isDisabled = false;

          return (
            <motion.button
              key={`${tool.id}-${tool.subType}`}
              type="button"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.15 + index * 0.05 }}
              onClick={() => !isDisabled && onSelectTool(tool.id, tool.subType)}
              disabled={isDisabled}
              className={`group rounded-xl border p-2.5 text-left transition-all ${
                isDisabled
                  ? 'border-gray-200 bg-gray-100 cursor-not-allowed dark:border-gray-700 dark:bg-gray-800/30'
                  : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50 hover:shadow-md dark:border-gray-700 dark:bg-gray-800/50 dark:hover:border-gray-600 dark:hover:bg-gray-700/50'
              }`}
              title={isDisabled ? "该功能暂未开放" : undefined}
            >
              <div className="flex flex-col items-center text-center gap-1.5">
                <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${tool.iconBg} ${isDisabled ? 'opacity-50' : ''}`}>
                  <i className={`${tool.icon} text-sm ${tool.iconColor}`}></i>
                </div>
                <div className={`text-xs font-medium ${isDisabled ? 'text-gray-400 dark:text-gray-500' : 'text-gray-900 dark:text-white'}`}>
                  {tool.name}
                  {isDisabled && <span className="block text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">暂未开放</span>}
                </div>
              </div>
            </motion.button>
          );
        })}
      </div>
    </motion.div>
  );
}
