/**
 * 数据映射层 - 将后端统一数据结构转换为前端展示格式
 *
 * 后端使用统一的 violations_analysis_result 结构：
 * - inspected_person: 被检查人
 * - violations: 违法行为列表
 * - source_files: 源文件元数据
 *
 * 前端使用中文字段的 ViolationAnalysis 结构
 */

import type {
  AnalysisResult,
  ViolationAnalysis,
  LawItem,
  HistoricalCase,
  EvidenceItem,
} from "../types";

// 简单的日志工具
const logger = {
  info: (...args: any[]) => console.log("[dataMapper]", ...args),
  warn: (...args: any[]) => console.warn("[dataMapper]", ...args),
  error: (...args: any[]) => console.error("[dataMapper]", ...args),
};

/**
 * 后端统一数据结构（violations_analysis_result）
 */
interface BackendViolation {
  violation_id: number;
  title: string;
  facts: string;
  violated_provisions: Array<{
    law_name: string;
    law_item_name: string;
    law_item_content: string;
    relevance_score?: number;
  }>;
  penalty_provisions: Array<{
    law_name: string;
    law_item_name: string;
    law_item_content: string;
    relevance_score?: number;
  }>;
  discretionary_benchmarks: string[];
  rectification_measures: string[];
  penalty_recommendations: string[];
  historical_cases?: Array<{
    case_id: string;
    case_title: string;
    case_summary: string;
    violation_facts: string;
    penalty_result: string;
    reference_value: string;
    similarity_score: number;
  }>;
  evidence_list?: Array<{
    evidence_name_code: string;
    evidence_source: string;
    evidence_form: string;
    evidence_truth: string;
    provider_sign?: string;
    remark?: string;
  }>;
  evidence_process?: string;
}

interface BackendAnalysisData {
  // 案件基本信息
  case_number?: string;
  inspected_person?: string;
  inspector?: string;
  inspection_date?: string;
  inspection_location?: string;

  // 违法行为数据
  violations: BackendViolation[];

  // 元数据
  source_files?: any[];
}

/**
 * 将后端的 violations_analysis_result 映射为前端展示格式
 *
 * @param backendData - 后端返回的分析数据
 * @returns 前端 AnalysisResult 格式
 */
export function mapViolationsAnalysisResult(
  backendData: any
): AnalysisResult | null {
  // 数据验证
  if (!backendData) {
    logger.warn("[数据映射] backendData 为空");
    return null;
  }

  // 兼容直接传入 violations 数组的情况（旧格式）
  const violations = Array.isArray(backendData)
    ? backendData
    : backendData.violations;

  if (!violations || !Array.isArray(violations)) {
    logger.warn("[数据映射] violations 不是数组", backendData);
    return null;
  }

  logger.info(`[数据映射] 开始映射 ${violations.length} 个违法行为`);

  try {
    const mapped: AnalysisResult = {
      // 案件基本信息
      案件编号: backendData.case_number,
      被检查人: backendData.inspected_person || "未知",
      检查人: backendData.inspector,
      检查时间: backendData.inspection_date,
      检查地点: backendData.inspection_location,

      // 违法行为数据
      违法行为数量: violations.length,
      违法行为分析: violations.map((v: BackendViolation) =>
        mapSingleViolation(v)
      ),
    };

    logger.info("[数据映射] 映射完成");
    return mapped;
  } catch (error) {
    logger.error("[数据映射] 映射失败", error);
    return null;
  }
}

/**
 * 映射单个违法行为
 */
function mapSingleViolation(v: BackendViolation): ViolationAnalysis {
  return {
    违法行为编号: v.violation_id || 0,
    原始事实认定: v.facts || "",
    AI扩充: v.title || "", // title 作为 AI 扩充内容
    违反条款: mapLawItems(v.violated_provisions),
    处罚条款: mapLawItems(v.penalty_provisions),
    裁量基准: v.discretionary_benchmarks || [],
    整改措施: v.rectification_measures || [],
    处罚建议: v.penalty_recommendations || [],
    历史案例: mapHistoricalCases(v.historical_cases),
    证据列表: mapEvidenceList(v.evidence_list),
    取证过程: v.evidence_process,
  };
}

/**
 * 映射法律条款列表
 */
function mapLawItems(
  items: Array<{
    law_name: string;
    law_item_name: string;
    law_item_content: string;
    relevance_score?: number;
  }> | undefined
): LawItem[] {
  if (!items || !Array.isArray(items)) {
    return [];
  }

  return items.map((item) => ({
    law_name: item.law_name || "",
    law_item_name: item.law_item_name || "",
    law_item_content: item.law_item_content || "",
    relevance_score: item.relevance_score,
  }));
}

/**
 * 映射历史案例列表（包含完整执法要素）
 */
function mapHistoricalCases(
  cases: Array<{
    case_id: string;
    case_title: string;
    case_summary: string;
    inspected_person?: string;
    inspector?: string;
    inspection_date?: string;
    inspection_location?: string;
    violation_facts: string;
    violated_provisions?: any[];
    penalty_provisions?: any[];
    discretionary_benchmarks?: string[];
    rectification_measures?: string[];
    penalty_result: string;
    reference_value: string;
    similarity_score: number;
  }> | undefined
): HistoricalCase[] {
  if (!cases || !Array.isArray(cases)) {
    return [];
  }

  return cases.map((c) => ({
    // 基本信息
    case_id: c.case_id || "",
    case_title: c.case_title || "",
    case_summary: c.case_summary || "",

    // 执法主体信息
    inspected_person: c.inspected_person,
    inspector: c.inspector,
    inspection_date: c.inspection_date,
    inspection_location: c.inspection_location,

    // 违法事实和法律依据
    violation_facts: c.violation_facts || "",
    violated_provisions: c.violated_provisions
      ? c.violated_provisions.map(p => ({
          law_name: p.law_name || "",
          law_item_name: p.law_item_name || "",
          law_item_content: p.law_item_content || "",
          relevance_score: p.relevance_score
        }))
      : undefined,
    penalty_provisions: c.penalty_provisions
      ? c.penalty_provisions.map(p => ({
          law_name: p.law_name || "",
          law_item_name: p.law_item_name || "",
          law_item_content: p.law_item_content || "",
          relevance_score: p.relevance_score
        }))
      : undefined,

    // 裁量和处罚
    discretionary_benchmarks: c.discretionary_benchmarks,
    rectification_measures: c.rectification_measures,
    penalty_result: c.penalty_result || "",

    // 参考价值
    reference_value: c.reference_value || "",
    similarity_score: c.similarity_score || 0,
  }));
}

/**
 * 映射证据列表
 */
function mapEvidenceList(
  evidence: Array<{
    evidence_name_code: string;
    evidence_source: string;
    evidence_form: string;
    evidence_truth: string;
    provider_sign?: string;
    remark?: string;
  }> | undefined
): EvidenceItem[] | undefined {
  if (!evidence || !Array.isArray(evidence) || evidence.length === 0) {
    return undefined;
  }

  return evidence.map((e) => ({
    evidence_name_code: e.evidence_name_code || "",
    evidence_source: e.evidence_source || "",
    evidence_form: e.evidence_form || "",
    evidence_truth: e.evidence_truth || "",
    provider_sign: e.provider_sign,
    remark: e.remark,
  }));
}

/**
 * 将前端修改后的数据转换回后端格式
 * 用于编辑后保存
 */
export function mapToBackendFormat(
  frontendData: AnalysisResult
): BackendAnalysisData {
  return {
    violations: frontendData.违法行为分析.map((v) => ({
      violation_id: v.违法行为编号,
      title: v.AI扩充,
      facts: v.原始事实认定,
      violated_provisions: v.违反条款.map((item) => ({
        law_name: item.law_name,
        law_item_name: item.law_item_name,
        law_item_content: item.law_item_content,
        relevance_score: item.relevance_score,
      })),
      penalty_provisions: v.处罚条款.map((item) => ({
        law_name: item.law_name,
        law_item_name: item.law_item_name,
        law_item_content: item.law_item_content,
        relevance_score: item.relevance_score,
      })),
      discretionary_benchmarks: v.裁量基准,
      rectification_measures: v.整改措施,
      penalty_recommendations: v.处罚建议,
      historical_cases: v.历史案例,
      evidence_list: v.证据列表,
      evidence_process: v.取证过程,
    })),
  };
}
