import type { ViolationFileGroup } from "../types";

interface ViolationFileGroupsDisplayProps {
  fileGroups: ViolationFileGroup[];
  onDeleteGroup: (groupId: string) => Promise<void>;
  onClearAll?: () => Promise<void>;  // 清空所有分组
}

export function ViolationFileGroupsDisplay({
  fileGroups,
  onDeleteGroup,
  onClearAll
}: ViolationFileGroupsDisplayProps) {
  if (fileGroups.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      {/* 分组数量提示 */}
      <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 px-1">
        <span>已上传 {fileGroups.length}/10 个违法行为</span>
        <div className="flex items-center gap-2">
          {fileGroups.length >= 10 && (
            <span className="text-amber-600 dark:text-amber-400">
              <i className="fa-solid fa-triangle-exclamation mr-1"></i>
              已达上限
            </span>
          )}
          {onClearAll && fileGroups.length > 0 && (
            <button
              type="button"
              onClick={onClearAll}
              className="text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 transition-colors hover:underline"
              title="清空全部"
            >
              <i className="fa-solid fa-trash-can mr-1"></i>
              清空全部
            </button>
          )}
        </div>
      </div>

      {/* 文件列表 */}
      <div className="space-y-2">
        {fileGroups.map((group, index) => (
          <div
            key={group.group_id}
            className="group relative flex items-center gap-3 p-3 bg-white dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-lg hover:border-gray-300 dark:hover:border-gray-600 hover:shadow-sm transition-all"
          >
            {/* 序号标识 */}
            <div className="flex-shrink-0 w-6 h-6 rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 flex items-center justify-center text-xs font-semibold">
              {index + 1}
            </div>

            {/* 文件图标和信息 */}
            <div className="flex-shrink-0 w-9 h-9 bg-blue-50 dark:bg-blue-900/20 rounded flex items-center justify-center">
              <i className="fa-solid fa-file-word text-blue-600 dark:text-blue-400 text-lg"></i>
            </div>

            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                {group.fact_determination.filename}
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                {new Date(group.fact_determination.upload_time).toLocaleString('zh-CN', {
                  year: 'numeric',
                  month: '2-digit',
                  day: '2-digit',
                  hour: '2-digit',
                  minute: '2-digit'
                })}
              </div>
            </div>

            {/* 删除按钮 */}
            <button
              type="button"
              onClick={() => onDeleteGroup(group.group_id)}
              className="flex-shrink-0 opacity-0 group-hover:opacity-100 p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-all"
              title="删除此违法行为"
            >
              <i className="fa-solid fa-trash text-sm"></i>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
