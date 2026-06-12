import { motion } from "framer-motion";
import { useEffect, useState, useRef } from "react";
import { mergeClassNames } from "../utils";
import { API_BASE } from "../../../lib/config";

// Excel 表头定义（移到组件外部避免重复创建）
const CATALOG_HEADERS = [
  { key: 'sequence_number', label: '序号', width: '60px' },
  { key: 'violation_behavior', label: '违规行为', width: '200px' },
  { key: 'evidence_code', label: '证据编号', width: '120px' },
  { key: 'evidence_name', label: '证据名称', width: '150px' },
  { key: 'main_content', label: '主要内容', width: '300px' },
  { key: 'summary', label: '汇总说明', width: '200px' },
  { key: 'legal_regulation', label: '法律规定', width: '200px' },
  { key: 'suggestion', label: '处理建议', width: '200px' },
  { key: 'remark', label: '备注', width: '120px' },
];

const APPENDIX_HEADERS = [
  { key: 'specialty', label: '检查专业', width: '120px' },
  { key: 'problem_number', label: '问题序号', width: '100px' },
  { key: 'violation_behavior', label: '违规行为', width: '200px' },
  { key: 'legal_regulation', label: '法律规定', width: '200px' },
  { key: 'law_original_text', label: '法条原文', width: '400px' },
];

const INFO_SUMMARY_HEADERS = [
  { key: 'sequence_number', label: '编号', width: '60px' },
  { key: 'business_area', label: '业务领域', width: '120px' },
  { key: 'violation_type_level1', label: '违法行为类型(一级)', width: '120px' },
  { key: 'violation_type_level2', label: '违法行为类型(二级)', width: '120px' },
  { key: 'violation_facts', label: '具体违法行为概要', width: '300px' },
  { key: 'legal_basis', label: '违反条款', width: '200px' },
  { key: 'penalty_clause', label: '处罚条款', width: '200px' },
  { key: 'penalty_method', label: '计罚方式', width: '100px' },
  { key: 'penalty_min', label: '单次处罚下限', width: '100px' },
  { key: 'penalty_max', label: '单次处罚上限', width: '100px' },
  { key: 'behavior_count', label: '行为数量', width: '80px' },
  { key: 'org_suggestion_unit', label: '机构处理建议(检查实施单位)', width: '150px' },
  { key: 'org_suggestion_dept', label: '机构处理建议(相关司局)', width: '150px' },
  { key: 'org_suggestion_legal', label: '机构处理建议(条法司)', width: '150px' },
  { key: 'person_suggestion_unit', label: '个人处理建议(检查实施单位)', width: '150px' },
  { key: 'person_suggestion_dept', label: '个人处理建议(相关司局)', width: '150px' },
  { key: 'person_suggestion_legal', label: '个人处理建议(条法司)', width: '150px' },
  { key: 'remark', label: '备注', width: '150px' },
];

// 报告源数据类型
interface ReportSourceData {
  report_type: string;
  inspection_content?: string;
  violation_facts?: string[];
  legal_basis?: string[];
  penalty_clauses?: string[];
}

interface DocumentViewerProps {
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
  onClose: () => void;
  onRegenerate?: (filename: string, sourceData: ReportSourceData) => void;
  panelHeightClass: string;
  threadId: string;
}

export function DocumentViewer({
  document,
  wordDocument,
  excelDocument,
  onClose,
  onRegenerate,
  panelHeightClass,
  threadId,
}: DocumentViewerProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string>("");
  const docxContainerRef = useRef<HTMLDivElement>(null);

  // 移除了在线编辑功能，用户可下载后自行修改

  // Word 文档样式
  useEffect(() => {
    if (!wordDocument) return;

    const styleElement = window.document.createElement('style');
    styleElement.id = 'docx-preview-custom-styles';
    styleElement.textContent = `
      .docx-preview-container {
        width: 100%;
      }
      .docx-preview-container section.docx {
        padding: 0 !important;
        margin: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
        box-sizing: border-box !important;
      }
      .docx-preview-container section.docx > article {
        margin: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
        box-sizing: border-box !important;
      }
      .docx-preview-container section.docx .docx-wrapper {
        width: 100% !important;
        max-width: 100% !important;
      }
    `;

    if (!window.document.getElementById('docx-preview-custom-styles')) {
      window.document.head.appendChild(styleElement);
    }

    return () => {
      const existingStyle = window.document.getElementById('docx-preview-custom-styles');
      if (existingStyle) {
        existingStyle.remove();
      }
    };
  }, [wordDocument]);

  // 加载并渲染 Word 文档
  useEffect(() => {
    if (!wordDocument || excelDocument) {
      return;
    }

    const loadWordDocument = async () => {
      setIsLoading(true);
      setError("");

      try {
        const response = await fetch(`${API_BASE}/api/conversations/${threadId}/files/${wordDocument.filename}`);
        if (!response.ok) {
          throw new Error("文档加载失败");
        }

        const arrayBuffer = await response.arrayBuffer();
        const { renderAsync } = await import('docx-preview');

        let retries = 10;
        while (!docxContainerRef.current && retries > 0) {
          await new Promise(resolve => setTimeout(resolve, 50));
          retries--;
        }

        if (!docxContainerRef.current) {
          throw new Error("文档容器未就绪");
        }

        docxContainerRef.current.innerHTML = '';
        docxContainerRef.current.contentEditable = 'false';

        await renderAsync(arrayBuffer, docxContainerRef.current, undefined, {
          className: 'docx-preview-wrapper',
          inWrapper: false,
          ignoreWidth: true,
          ignoreHeight: false,
          renderHeaders: true,
          renderFooters: true,
        });
      } catch (err) {
        console.error("Word 文档加载失败:", err);
        setError(err instanceof Error ? err.message : "文档加载失败");
      } finally {
        setIsLoading(false);
      }
    };

    loadWordDocument();
  }, [wordDocument?.filename, excelDocument, threadId]);

  if (!document && !wordDocument && !excelDocument) {
    return null;
  }

  const isEvidenceCatalog = excelDocument && typeof excelDocument.excelData === 'object' && 'catalog' in excelDocument.excelData;

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
      <div className="flex items-center justify-between gap-2 px-3 py-2 flex-shrink-0 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <i className={`fa-solid ${excelDocument ? 'fa-file-excel text-green-600' : wordDocument ? 'fa-file-word text-blue-600' : 'fa-file-lines text-indigo-600'} text-lg flex-shrink-0`}></i>
          <h2 className="text-sm font-medium text-gray-900 dark:text-white truncate">
            {excelDocument ? excelDocument.title : wordDocument ? wordDocument.title : '检查报告'}
          </h2>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onClose}
            className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-200 dark:text-gray-400 dark:hover:bg-gray-700"
            title="关闭"
          >
            <i className="fa-solid fa-xmark text-sm"></i>
          </button>
        </div>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto bg-gray-100 dark:bg-gray-800/50 p-4">
        {excelDocument ? (
          // Excel 预览（保持原有逻辑）
          <div className="space-y-4">
            {isEvidenceCatalog ? (
              <>
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">Sheet 1: 证据目录</h3>
                  <div className="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 overflow-hidden">
                    <div className="overflow-x-auto">
                      <table className="w-full border-collapse text-xs">
                        <thead className="bg-gray-50 dark:bg-gray-800">
                          <tr>
                            {CATALOG_HEADERS.map((header) => (
                              <th key={header.key} style={{ minWidth: header.width }} className="px-2 py-2 text-left text-[10px] font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider border-b border-gray-300 dark:border-gray-600 whitespace-nowrap">
                                {header.label}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="bg-white dark:bg-gray-900 divide-y divide-gray-200 dark:divide-gray-700">
                          {(excelDocument.excelData as any).catalog?.map((row: any, rowIndex: number) => (
                            <tr key={rowIndex} className="hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                              {CATALOG_HEADERS.map((header) => (
                                <td key={header.key} style={{ maxWidth: header.width }} className={`px-2 py-2 border-b border-gray-200 dark:border-gray-700 ${header.key === 'sequence_number' ? 'text-center font-medium text-gray-900 dark:text-gray-100' : 'text-gray-700 dark:text-gray-300'}`}>
                                  <div className={header.key === 'sequence_number' ? '' : 'whitespace-pre-wrap break-words'}>
                                    {row[header.key] || ''}
                                  </div>
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">Sheet 2: 证据目录附件</h3>
                  <div className="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 overflow-hidden">
                    <div className="overflow-x-auto">
                      <table className="w-full border-collapse text-xs">
                        <thead className="bg-gray-50 dark:bg-gray-800">
                          <tr>
                            {APPENDIX_HEADERS.map((header) => (
                              <th key={header.key} style={{ minWidth: header.width }} className="px-2 py-2 text-left text-[10px] font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider border-b border-gray-300 dark:border-gray-600 whitespace-nowrap">
                                {header.label}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="bg-white dark:bg-gray-900 divide-y divide-gray-200 dark:divide-gray-700">
                          {(excelDocument.excelData as any).appendix?.map((row: any, rowIndex: number) => (
                            <tr key={rowIndex} className="hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                              {APPENDIX_HEADERS.map((header) => (
                                <td key={header.key} style={{ maxWidth: header.width }} className="px-2 py-2 border-b border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300">
                                  <div className="whitespace-pre-wrap break-words">{row[header.key] || ''}</div>
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-xs">
                    <thead className="bg-gray-50 dark:bg-gray-800">
                      <tr>
                        {INFO_SUMMARY_HEADERS.map((header) => (
                          <th key={header.key} style={{ minWidth: header.width }} className="px-2 py-2 text-left text-[10px] font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider border-b border-gray-300 dark:border-gray-600 whitespace-nowrap">
                            {header.label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="bg-white dark:bg-gray-900 divide-y divide-gray-200 dark:divide-gray-700">
                      {Array.isArray(excelDocument.excelData) && excelDocument.excelData.map((row: any, rowIndex: number) => (
                        <tr key={rowIndex} className="hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                          {INFO_SUMMARY_HEADERS.map((header) => (
                            <td key={header.key} style={{ maxWidth: header.width }} className={`px-2 py-2 border-b border-gray-200 dark:border-gray-700 ${header.key === 'sequence_number' ? 'text-center font-medium text-gray-900 dark:text-gray-100' : 'text-gray-700 dark:text-gray-300'}`}>
                              <div className={header.key === 'sequence_number' ? '' : 'whitespace-pre-wrap break-words'}>
                                {row[header.key] || ''}
                              </div>
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        ) : wordDocument ? (
          // Word 文档预览
          <div className="h-full">
            {isLoading && (
              <div className="flex items-center justify-center py-12">
                <div className="text-center">
                  <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-indigo-600 mx-auto mb-3"></div>
                  <p className="text-sm text-gray-500 dark:text-gray-400">加载文档中...</p>
                </div>
              </div>
            )}
            {error && (
              <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-900/20">
                <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
              </div>
            )}
            <div className={mergeClassNames(
              "mx-auto bg-white dark:bg-gray-800 shadow-lg rounded-lg overflow-hidden",
              isLoading || error ? "hidden" : ""
            )}
            style={{ maxWidth: '210mm' }}
            >
              <div
                ref={docxContainerRef}
                className="docx-preview-container min-h-[500px] p-6"
                style={{
                  fontFamily: '"FangSong", "STFangsong", "FangSong_GB2312", serif',
                }}
              />
            </div>
          </div>
        ) : document ? (
          // 法律条款预览
          <div className="space-y-4">
            <div>
              <h3 className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">法律名称</h3>
              <p className="text-base font-semibold text-gray-900 dark:text-white">{document.law_name}</p>
            </div>
            <div>
              <h3 className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">条款</h3>
              <p className="text-sm font-medium text-indigo-700 dark:text-indigo-300">{document.law_item_name}</p>
            </div>
            <div>
              <h3 className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">条款内容</h3>
              <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
                <p className="text-sm leading-relaxed text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
                  {document.law_item_content}
                </p>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </motion.aside>
  );
}
