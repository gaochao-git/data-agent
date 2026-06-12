export type LawItem = {
  law_name: string;
  law_item_name: string;
  law_item_content: string;
  relevance_score?: number;  // 相关性评分（可选）
};

// 历史案例类型（完整执法案例）
export interface HistoricalCase {
  // 基本信息
  case_id: string;
  case_title: string;
  case_summary: string;

  // 执法主体信息
  inspected_person?: string;
  inspector?: string;
  inspection_date?: string;
  inspection_location?: string;

  // 违法事实和法律依据
  violation_facts: string;
  violated_provisions?: LawItem[];
  penalty_provisions?: LawItem[];

  // 裁量和处罚
  discretionary_benchmarks?: string[];
  rectification_measures?: string[];
  penalty_result: string;

  // 参考价值
  reference_value: string;
  similarity_score: number;
}

// 证据项类型
export interface EvidenceItem {
  evidence_name_code: string;   // 证据名称及编号
  evidence_source: string;       // 证据来源
  evidence_form: string;         // 证据表现形式
  evidence_truth: string;        // 真实性说明
  provider_sign?: string;        // 提供人签名（可选）
  remark?: string;               // 备注（可选）
}

// 单个违法行为的分析结果
export interface ViolationAnalysis {
  违法行为编号: number;
  原始事实认定: string;
  AI扩充: string;
  违反条款: LawItem[];
  处罚条款: LawItem[];
  裁量基准: string[];
  整改措施: string[];
  处罚建议: string[];
  历史案例: HistoricalCase[];    // ⭐ 新增：历史案例参考
  证据列表?: EvidenceItem[];     // ⭐ 新增：证据列表（可选）
  取证过程?: string;             // ⭐ 新增：取证过程描述（可选）
}

// 完整分析结果（支持多个违法行为 + 案件基本信息）
export interface AnalysisResult {
  // 案件基本信息
  案件编号?: string;
  被检查人: string;
  检查人?: string;
  检查时间?: string;
  检查地点?: string;

  // 违法行为数据
  违法行为数量: number;
  违法行为分析: ViolationAnalysis[];
}

export interface SSETaskStatus {
  taskId: string;
  status: "pending" | "running" | "completed" | "error";
  progress: number;
  currentStep: string;
  result?: AnalysisResult;
  error?: string;
}

export type ChatRole = "assistant" | "user" | "system";

// ============ LangChain 标准内容块类型（符合官方规范）============

/**
 * 文本内容块（标准类型）
 * 官方规范: https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/messages/content.py
 */
export interface TextContentBlock {
  type: "text";
  text: string;
  id?: string;
  annotations?: any[];
  index?: number | string;
  extras?: Record<string, any>;
}

/**
 * 文件内容块（标准类型）
 * 官方规范: https://reference.langchain.com/python/langchain/messages/#langchain.messages.FileContentBlock
 * - type: "file" (必需)
 * - id: 内容块标识符
 * - file_id: 文件存储系统中的文件 ID
 * - mime_type: 文件 MIME 类型
 * - index: 流式响应中的块索引
 * - url: 文件 URL
 * - base64: Base64 编码的文件数据
 * - extras: 提供商特定的元数据
 */
export interface FileContentBlock {
  type: "file";
  id?: string;                    // 内容块标识符
  file_id?: string;               // 文件存储系统中的文件 ID
  mime_type?: string;             // 文件 MIME 类型
  index?: number | string;        // 流式响应中的块索引
  url?: string;                   // 文件 URL
  base64?: string;                // Base64 编码的文件数据
  extras?: Record<string, any>;   // 提供商特定的元数据
}

/**
 * 非标准内容块（用于自定义类型）
 * 官方规范: https://reference.langchain.com/python/langchain/messages/#langchain.messages.NonStandardContentBlock
 * 用于存放自定义业务数据，如 task_progress, analysis_data, excel_data 等
 */
export interface NonStandardContentBlock {
  type: "non_standard";
  id?: string;           // 内容块标识符
  value: {
    type: string;        // 自定义类型（如 "task_progress", "analysis_data", "excel_data", "file_title"）
    data: any;           // 自定义数据
  };
  index?: number;        // 在消息内容块中的位置
}

// 内容块联合类型（包含官方标准类型 + NonStandardContentBlock）
export type ContentBlock = TextContentBlock | FileContentBlock | NonStandardContentBlock;

// ============ 消息类型（符合 LangChain 规范）============

/**
 * additional_kwargs 结构
 * 仅用于用户消息中的任务信息（符合 LangChain 官方规范）
 * AI 消息的业务数据已全部迁移到 NonStandardContentBlock
 */
export interface MessageAdditionalKwargs {
  // 用户消息相关（发送任务请求时携带）
  task?: {
    task_type?: string;
    files?: Array<{ file_id: string; file_name: string; file_type: string }>;
    [key: string]: any;
  };
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string | ContentBlock[];  // 支持结构化内容
  timestamp: Date;
  additional_kwargs?: MessageAdditionalKwargs;
}

// 兼容旧代码的类型别名（已废弃，使用 FileContentBlock）
/** @deprecated 使用 FileContentBlock 替代 */
export interface Attachment {
  filename: string;
  title: string;
  type: "word" | "pdf" | "excel" | "image" | string;
  url?: string;
  size?: number;
}

export type AnalysisStage = "initial" | "analyzing" | "completed" | "error";

export interface AnalysisHistoryItem {
  id: string;
  title?: string; // 对话标题（由大模型生成）
  inspectionContent: string;
  violationDescription: string;
  startedAt: Date;
  updatedAt: Date;
  status: "draft" | "analyzing" | "completed" | "error";
  progress: number;
  result: AnalysisResult | null;
  error?: string;
}

export interface ExtractedDocumentContent {
  inspection_content: string;
  violation_facts: string;
}

export interface ViolationFormValues {
  violationDescription: string;
  uploadedFiles?: Array<{file_id: string; file_name: string; file_type: string}>;
}

export type TaskStatus = "pending" | "in_progress" | "completed" | "error" | "cancelled" | "awaiting_confirm";

export interface AnalysisTask {
  id: string;
  title: string;
  status: TaskStatus;
  // Human-in-the-Loop: 待确认时的审核数据
  reviewData?: HumanReviewData;
}

// 任务类型定义
export type TaskType = "violation_analysis" | "qa" | "report";

// 后端任务参数（支持扩展）
export interface TaskPayload {
  task_type: TaskType;
  [key: string]: any; // 支持扩展字段，如 report_name 等
}

// ============ Human-in-the-Loop 审核相关类型 ============

/**
 * 违法行为审核数据（用于拆分结果审核）
 */
export interface ReviewViolationItem {
  violation_id: number;
  original_fact: string;
  ai_expansion: string;
}

/**
 * 检索结果审核数据（包含法律条款）
 */
export interface ReviewViolationWithResults extends ReviewViolationItem {
  legal_basis?: LawItem[];
  penalty_clause?: LawItem[];
  discretion_standard?: string[];
  rectification_measures?: string[];
  penalty_suggestion?: string[];
}

/**
 * 解析结果审核数据（事实认定书解析）
 */
export interface ReviewExtractData {
  inspection_content: string;
  violation_facts: string;
}

/**
 * 人工审核数据
 * - split: 拆分结果审核（违法行为分析中）
 * - retrieve: 检索结果审核（违法行为分析中）
 * - extract: 解析结果审核（报告生成中 - 事实认定书）
 * - analyze: 分析结果审核（报告生成中）
 * - evidence: 取证记录表审核（证据目录报告中）
 */
export interface HumanReviewData {
  review_type: "split" | "retrieve" | "extract" | "analyze" | "evidence";
  message: string;
  current_data: {
    violations?: ReviewViolationItem[] | ReviewViolationWithResults[];
    inspection_content?: string;
    violation_facts?: string;
    evidence_list?: EvidenceItem[];
    basic_info?: Record<string, string>;
  };
  requires_evidence_upload?: boolean;  // 是否需要上传取证记录表（证据目录报告）
  violation_upload_status?: Record<number, boolean>;  // 每个违法行为的上传状态 {violation_id: has_uploaded}
  all_evidence_uploaded?: boolean;     // 是否所有违法行为都已上传取证记录表
}

/**
 * 用户审核操作
 */
export type ReviewAction = "approve" | "edit" | "reject";

/**
 * Resume 请求参数
 */
export interface ResumeInput {
  action: ReviewAction;
  data?: {
    violations: any[];
  };
}

/**
 * 工具类型
 */
export type ToolType = "violation-analysis" | "report-generation" | null;
export type ReportSubType = "evidence-catalog" | "rectification-notice" | "comprehensive-report" | "info-summary" | null;

// ============ 违法行为文件分组（前端数据结构）============

/**
 * 违法行为文件分组
 * 业务规则：1个事实认定书 = 1个违法行为（最多10个分组）
 */
export interface ViolationFileGroup {
  group_id: string;  // 分组唯一ID（UUID）

  fact_determination: {
    file_id: string;
    filename: string;
    upload_time: string;
    file_type: string;
  };

  evidence_records?: {  // 可选
    file_id: string;
    filename: string;
    upload_time: string;
    file_type: string;
  };

  created_at: string;  // ISO格式
  order: number;  // 排序序号
}
