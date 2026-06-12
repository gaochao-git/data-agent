import { API_BASE } from "../../../lib/config";

interface DocumentCardProps {
  icon: string; // Font Awesome icon class
  title: string;
  subtitle?: string;
  filename: string;
  threadId: string; // 线程ID，用于构建下载路径
  onPreview?: () => void;
  showPreview?: boolean;
}

export function DocumentCard({
  icon,
  title,
  subtitle,
  filename,
  threadId,
  onPreview,
  showPreview = true,
}: DocumentCardProps) {
  return (
    <div className="border-t border-dashed border-gray-300 pt-2 dark:border-gray-600">
      <div className="flex items-center gap-2">
        <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded bg-blue-500 text-white">
          <i className={`${icon} text-xs`}></i>
        </div>
        <div className="flex-1">
          <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
            {title}
          </div>
          {subtitle && (
            <div className="text-xs text-gray-500 dark:text-gray-400">
              {subtitle}
            </div>
          )}
        </div>
        <div className="flex gap-2">
          {showPreview && onPreview && (
            <button
              onClick={onPreview}
              className="rounded bg-white border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <i className="fa-solid fa-eye mr-1"></i>
              预览
            </button>
          )}
          <a
            href={`${API_BASE}/api/conversations/${threadId}/files/${filename}`}
            download
            className="rounded bg-white border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <i className="fa-solid fa-download mr-1"></i>
            下载
          </a>
        </div>
      </div>
    </div>
  );
}
