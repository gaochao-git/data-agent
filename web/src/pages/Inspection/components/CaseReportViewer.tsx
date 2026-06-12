import { motion } from "framer-motion";
import { mergeClassNames } from "../utils";

interface CaseReportViewerProps {
  report: {
    name: string;
    content: string;
  };
  onClose: () => void;
  panelHeightClass: string;
}

export function CaseReportViewer({
  report,
  onClose,
  panelHeightClass,
}: CaseReportViewerProps) {
  return (
    <motion.aside
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ duration: 0.3 }}
      className={mergeClassNames(
        "flex flex-col border-l border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900 w-full min-w-0",
        panelHeightClass
      )}
    >
      {/* 头部 */}
      <div className="flex items-center justify-between gap-2 px-3 py-3 flex-shrink-0 h-[52px] border-b border-gray-100 dark:border-gray-800">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <i className="fa-solid fa-file-lines text-amber-600 text-lg flex-shrink-0"></i>
          <h2 className="text-sm font-medium text-gray-900 dark:text-white truncate">
            {report.name}
          </h2>
        </div>
        <button
          onClick={onClose}
          className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-200 dark:text-gray-400 dark:hover:bg-gray-700"
          title="关闭"
        >
          <i className="fa-solid fa-xmark text-sm"></i>
        </button>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto bg-white px-4 py-4 dark:bg-gray-900">
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800">
          <pre className="text-sm leading-relaxed text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-sans">
            {report.content}
          </pre>
        </div>
      </div>
    </motion.aside>
  );
}
