/**
 * 消息内容块解析工具
 * 符合 LangChain 官方规范，不兼容旧格式
 *
 * 数据来源（全部在 content 数组中）：
 * - TextContentBlock: type="text"
 * - FileContentBlock: type="file"
 * - NonStandardContentBlock: type="non_standard"
 *   - value.type="task_progress" -> 任务进度
 *   - value.type="analysis_data" -> 分析结果
 *   - value.type="excel_data" -> Excel 预览数据
 *   - value.type="file_title" -> 文件标题
 *   - value.type="report_source_data" -> 报告源数据（用于编辑后重新生成）
 */

import type {
  ChatMessage,
  ContentBlock,
  TextContentBlock,
  FileContentBlock,
  NonStandardContentBlock,
  AnalysisTask,
  AnalysisResult
} from "../types";
import { mapViolationsAnalysisResult } from "./dataMapper";

// 报告源数据类型（用于编辑后重新生成）
export interface ReportSourceData {
  report_type: string;
  inspection_content?: string;
  violation_facts?: string[];
  legal_basis?: string[];
  penalty_clauses?: string[];
}

export interface ParsedMessageContent {
  textContent: string;
  files: Array<FileContentBlock & { title?: string }>;  // 扩展 title 字段（来自 NonStandardContentBlock）
  tasks: AnalysisTask[];
  analysisData: AnalysisResult | null;
  excelData: any | null;
  reportSourceData: ReportSourceData | null;  // 报告源数据
}

/**
 * 解析消息内容（仅支持新格式）
 *
 * 所有数据都从 content 数组中提取：
 * - textContent: TextContentBlock
 * - files: FileContentBlock + file_title（来自 NonStandardContentBlock）
 * - tasks: NonStandardContentBlock (value.type="task_progress")
 * - analysisData: NonStandardContentBlock (value.type="analysis_data")
 * - excelData: NonStandardContentBlock (value.type="excel_data")
 */
export function parseMessageContent(message: ChatMessage): ParsedMessageContent {
  const result: ParsedMessageContent = {
    textContent: "",
    files: [],
    tasks: [],
    analysisData: null,
    excelData: null,
    reportSourceData: null,
  };

  // 字符串格式：直接作为文本
  if (typeof message.content === "string") {
    result.textContent = message.content;
    return result;
  }

  // 临时存储 file_title（可能在 NonStandardContentBlock 中）
  let fileTitle: string | undefined;

  // 数组格式：解析内容块（符合 LangChain 官方规范）
  if (Array.isArray(message.content)) {
    message.content.forEach((block: ContentBlock) => {
      switch (block.type) {
        case "text":
          result.textContent += (block as TextContentBlock).text;
          break;

        case "file":
          result.files.push(block as FileContentBlock);
          break;

        case "non_standard": {
          // 解析 NonStandardContentBlock
          const nsBlock = block as NonStandardContentBlock;
          const customType = nsBlock.value?.type;
          const customData = nsBlock.value?.data;

          switch (customType) {
            case "task_progress":
              result.tasks = customData || [];
              break;

            case "analysis_data":
              // ⭐ 使用数据映射层转换后端格式到前端格式
              if (customData) {
                const mapped = mapViolationsAnalysisResult(customData);
                result.analysisData = mapped;
              } else {
                result.analysisData = null;
              }
              break;

            case "excel_data":
              result.excelData = customData || null;
              break;

            case "file_title":
              fileTitle = customData;
              break;

            case "report_source_data":
              result.reportSourceData = customData || null;
              break;

            default:
              console.warn(`Unknown non_standard content type: ${customType}`);
          }
          break;
        }

        default:
          console.warn(`Unknown content block type: ${(block as any).type}`);
      }
    });
  }

  // 将 file_title 附加到文件列表
  if (fileTitle && result.files.length > 0) {
    result.files = result.files.map(file => ({
      ...file,
      title: fileTitle
    }));
  }

  return result;
}

/**
 * 从内容块中提取所有文本
 */
export function extractTextFromContent(content: string | ContentBlock[]): string {
  if (typeof content === "string") {
    return content;
  }

  return content
    .filter((block): block is TextContentBlock => block.type === "text")
    .map(block => block.text)
    .join("");
}

/**
 * 从内容块中提取所有文件
 */
export function extractFilesFromContent(content: string | ContentBlock[]): FileContentBlock[] {
  if (typeof content === "string") {
    return [];
  }

  return content.filter((block): block is FileContentBlock => block.type === "file");
}

/**
 * 从内容块中提取 NonStandardContentBlock 的自定义数据
 */
export function extractNonStandardData(
  content: string | ContentBlock[],
  customType: string
): any | null {
  if (typeof content === "string") {
    return null;
  }

  const block = content.find(
    (b): b is NonStandardContentBlock =>
      b.type === "non_standard" && (b as NonStandardContentBlock).value?.type === customType
  );

  return block?.value?.data || null;
}
