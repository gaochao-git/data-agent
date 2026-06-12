import { z } from "zod";
import type { ChatMessage } from "./types";

export const violationFormSchema = z.object({
  violationDescription: z
    .string()
    .min(10, "请详细描述违规问题，至少10个字符"),
});

export const panelHeightClass =
  "h-screen";

export const HISTORY_COLLAPSED_WIDTH = "3.5rem";

export const createInitialChatMessages = (): ChatMessage[] => [];

export const requiredLabelsMap = {
  legalBasis: "违反条款",
  penaltyClauseList: "处罚条款",
  adminPenaltyStandards: "裁量基准",
  penaltySuggestions: "处罚建议",
  rectificationMeasures: "整改措施",
} as const;

// ============ 5个子功能配置 ============

/** 报告类型映射 */
export const REPORT_TYPE_MAP: Record<string, { pinyin: string; name: string }> = {
  'rectification-notice': { pinyin: 'zgtzs', name: '整改通知书' },
  'comprehensive-report': { pinyin: 'jcbg', name: '执法检查报告' },
  'evidence-catalog': { pinyin: 'zjml', name: '证据目录' },
  'info-summary': { pinyin: 'xxjb', name: '信息简表' },
};

/** agentId 到报告类型的映射 */
export const AGENT_ID_TO_REPORT_TYPE: Record<string, string> = {
  'fact_analysis': 'fact-analysis',
  'report_jcbg': 'comprehensive-report',
  'report_zgtzs': 'rectification-notice',
  'report_zjml': 'evidence-catalog',
  'report_xxjb': 'info-summary',
};

/** 子功能配置 */
export interface SubFunctionConfig {
  agentId: string;
  name: string;
  description: string;
  welcomeMessage: string;
  uploadRequirements: string[];
  supportedReportTypes?: string[];
  icon: string;
  color: string;
}

/** 5个子功能配置 */
export const SUB_FUNCTION_CONFIGS: Record<string, SubFunctionConfig> = {
  'fact_analysis': {
    agentId: 'fact_analysis',
    name: '事实认定分析',
    description: '辅助分析执法检查事实认定',
    welcomeMessage: '您好！我可以帮您分析事实认定内容，自动匹配违反条款、处罚依据和整改建议。请上传事实认定书或直接输入事实描述。',
    uploadRequirements: ['事实认定书（Word/PDF格式）', '违法事实描述（可直接输入）'],
    icon: 'fa-scale-balanced',
    color: 'blue',
  },
  'report_jcbg': {
    agentId: 'report_jcbg',
    name: '检查报告撰写',
    description: '自动撰写执法检查报告',
    welcomeMessage: '您好！我可以帮您自动生成规范的执法检查报告。请上传事实认定书，我将为您生成完整的检查报告。',
    uploadRequirements: ['事实认定书（必需，Word/PDF格式）'],
    supportedReportTypes: ['comprehensive-report'],
    icon: 'fa-file-lines',
    color: 'purple',
  },
  'report_zgtzs': {
    agentId: 'report_zgtzs',
    name: '整改通知书撰写',
    description: '自动撰写整改通知书',
    welcomeMessage: '您好！我可以帮您自动生成整改通知书。请上传检查报告或事实认定书，我将为您生成规范的整改通知书。',
    uploadRequirements: ['检查报告或事实认定书（Word/PDF格式）'],
    supportedReportTypes: ['rectification-notice'],
    icon: 'fa-file-contract',
    color: 'indigo',
  },
  'report_zjml': {
    agentId: 'report_zjml',
    name: '证据目录撰写',
    description: '自动撰写检查材料目录',
    welcomeMessage: '您好！我可以帮您自动生成证据材料目录。请上传检查报告，我将为您生成规范的证据目录。',
    uploadRequirements: ['检查报告（Word/PDF格式）', '取证记录表（可选）'],
    supportedReportTypes: ['evidence-catalog'],
    icon: 'fa-list',
    color: 'blue',
  },
  'report_xxjb': {
    agentId: 'report_xxjb',
    name: '信息简表撰写',
    description: '快速撰写检查信息简表',
    welcomeMessage: '您好！我可以帮您快速生成检查信息简表。请上传检查报告，我将为您生成简洁的信息简报。',
    uploadRequirements: ['检查报告（Word/PDF格式）'],
    supportedReportTypes: ['info-summary'],
    icon: 'fa-table',
    color: 'purple',
  },
};
