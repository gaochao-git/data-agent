import type { AnalysisTask, FileContentBlock } from "../types";
import { TaskManage } from "./TaskManage";
import { AnalysisResultMessage } from "./AnalysisResultMessage";
import { DocumentCard } from "./DocumentCard";
import type { CaseReport } from "./AnalysisResultMessage";
import type { ReportSourceData } from "../utils/messageParser";

// 扩展 FileContentBlock，添加 title 字段（来自 NonStandardContentBlock file_title）
type FileWithTitle = FileContentBlock & { title?: string };

interface AnalysisProgressMessageProps {
  tasks: AnalysisTask[];
  data?: any; // 分析结果数据（可以是 AnalysisResult 或包含 excel_data 的对象）
  files: FileWithTitle[]; // 文件列表（title 来自 NonStandardContentBlock file_title）
  reportSourceData?: ReportSourceData | null; // 报告源数据（用于编辑后重新生成）
  threadId: string; // 线程ID，用于构建下载路径
  updatedWordFilename?: string; // 编辑后更新的Word文件名（覆盖 files 中的原始文件名）
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void;
  onWordDocumentView?: (doc: { filename: string; title: string; sourceData?: ReportSourceData }) => void;
  onExcelDocumentView?: (doc: { filename: string; title: string; excelData: any }) => void;
  onReportClick?: (report: CaseReport) => void; // 点击相似案例报告时在右侧预览
}

export function AnalysisProgressMessage({
  tasks,
  data,
  files,
  reportSourceData,
  threadId,
  updatedWordFilename,
  onLawClick,
  onWordDocumentView,
  onExcelDocumentView,
  onReportClick,
}: AnalysisProgressMessageProps) {
  // 计算是否已完成（所有任务都完成、出错或取消，或没有任务时默认完成）
  const isCompleted = tasks.length === 0 || tasks.every(t =>
    t.status === 'completed' || t.status === 'error' || t.status === 'cancelled'
  );

  // 从 files 中查找文档
  const wordDocument = files.find(f => f.mime_type?.includes('wordprocessing'));
  const excelDocument = files.find(f => f.mime_type?.includes('spreadsheet'));

  // 使用更新后的文件名（如果有），否则使用原始文件名
  const wordFilename = updatedWordFilename || wordDocument?.file_id || '';
  return (
    <div className="rounded-2xl border border-gray-200 bg-gradient-to-br from-gray-50 to-white px-5 pt-5 pb-3 shadow-lg dark:border-gray-700 dark:from-gray-800/50 dark:to-gray-900/50">
      <div className="space-y-4">
        {/* 任务进度区域 */}
        <TaskManage tasks={tasks} />

        {/* Word文档下载区域 */}
        {wordDocument && isCompleted && (
          <DocumentCard
            icon="fa-solid fa-file-word"
            title={wordDocument.title || '文档'}
            filename={wordFilename}
            threadId={threadId}
            onPreview={() => onWordDocumentView?.({
              filename: wordFilename,
              title: wordDocument.title || '文档',
              sourceData: reportSourceData || undefined
            })}
          />
        )}

        {/* Excel文档下载区域 */}
        {excelDocument && isCompleted && (
          <DocumentCard
            icon="fa-solid fa-file-excel"
            title={excelDocument.title || 'Excel文档'}
            filename={excelDocument.file_id || ''}
            threadId={threadId}
            onPreview={
              data?.excel_data && onExcelDocumentView
                ? () => {
                    const title = (excelDocument.file_id || '').includes('证据目录') ? '证据目录预览' : '信息简表预览';
                    onExcelDocumentView({
                      filename: excelDocument.file_id || '',
                      title,
                      excelData: data.excel_data
                    });
                  }
                : undefined
            }
            showPreview={!!data?.excel_data && !!onExcelDocumentView}
          />
        )}

        {/* 分析结果区域（如果有） */}
        {data && data.违法行为分析 && (
          <div className="pt-1">
            <AnalysisResultMessage
              analysisResult={data}
              isCompleted={isCompleted}
              onLawClick={onLawClick}
              onReportClick={onReportClick}
            />
          </div>
        )}
      </div>
    </div>
  );
}
