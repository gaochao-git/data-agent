import { DocumentViewer } from "./DocumentViewer";
import { CaseReportViewer } from "./CaseReportViewer";
import { ReviewEditor } from "./ReviewEditor";
import type { HumanReviewData } from "../types";

// 审核编辑数据类型
type ReviewEditData =
  | { violations: any[] }
  | { inspection_content: string; violation_facts: string }
  | { files: Array<{ file_index: number; inspection_content: string; violation_facts: string }> }
  | { evidence_list: any[]; basic_info: Record<string, string> };

// 报告源数据类型
interface ReportSourceData {
  report_type: string;
  inspection_content?: string;
  violation_facts?: string[];
  legal_basis?: string[];
  penalty_clauses?: string[];
}

interface ConsoleProps {
  document: {
    law_name: string;
    law_item_name: string;
    law_item_content: string;
  } | null;
  wordDocument?: {
    filename: string;
    title: string;
    sourceData?: ReportSourceData;
  } | null;
  excelDocument?: {
    filename: string;
    title: string;
    excelData: any[] | { catalog: any[]; appendix: any[] };
  } | null;
  caseReport?: {
    name: string;
    content: string;
  } | null;
  reviewEditor?: {
    reviewData: HumanReviewData;
    onConfirm: (data: ReviewEditData, isEdited: boolean) => void;
    onCancel: () => void;
  } | null;
  onClose: () => void;
  onRegenerate?: (filename: string, sourceData: ReportSourceData) => void; // 报告重新生成回调
  panelHeightClass: string;
  threadId: string; // 线程ID，用于构建下载路径
}

export function Console({
  document,
  wordDocument,
  excelDocument,
  caseReport,
  reviewEditor,
  onClose,
  onRegenerate,
  panelHeightClass,
  threadId,
}: ConsoleProps) {
  // 优先显示审核编辑器（人工审核）
  if (reviewEditor) {
    return (
      <ReviewEditor
        reviewData={reviewEditor.reviewData}
        onConfirm={reviewEditor.onConfirm}
        onCancel={reviewEditor.onCancel}
        threadId={threadId}
        panelHeightClass={panelHeightClass}
      />
    );
  }

  // 其次显示相似案例报告
  if (caseReport) {
    return (
      <CaseReportViewer
        key={caseReport.name}
        report={caseReport}
        onClose={onClose}
        panelHeightClass={panelHeightClass}
      />
    );
  }

  return (
    <DocumentViewer
      document={document}
      wordDocument={wordDocument}
      excelDocument={excelDocument}
      onClose={onClose}
      onRegenerate={onRegenerate}
      panelHeightClass={panelHeightClass}
      threadId={threadId}
    />
  );
}
