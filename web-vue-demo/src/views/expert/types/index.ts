/**
 * 法律助手类型定义
 * 迁移自 React 版本: web/src/pages/GeneralAssistant/types.ts 和 web/src/pages/AISpecialistHome/types.ts
 * 注意：保持字段名不变，不进行驼峰转换
 */

// ============ 基础类型 ============

/**
 * 上传文件状态
 */
export interface UploadedFile {
  file_id: string;
  original_filename: string;
  file_type: string;
  file_size: number;
  status: "uploading" | "parsing" | "completed" | "failed" | "timeout";
  name?: string;  // 兼容 attachment.name
}

/**
 * 消息角色
 */
export type MessageRole = "user" | "assistant";

/**
 * 思考步骤状态
 */
export type StepStatus = "done" | "thinking";

/**
 * 思考步骤项
 */
export interface ThinkingItem {
  id: string;
  label: string;
  status: StepStatus;
  label_hidden?: boolean;
  flatten_children?: boolean;
  detail?: string;
  detailLines?: Array<{
    lineId?: string;
    content: string;
  }>;
  children?: ThinkingItem[];
}

/**
 * 法规引用信息
 */
export interface AssistantReference {
  id: string;
  title: string;
  summary: string;
  sourceId?: string;
  anchorId?: string;
  clause?: string;
  description?: string;
}

/**
 * 聊天消息
 */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string | any[];
  timestamp: Date;
  attachments?: UploadedFile[];
  additional_kwargs?: {
    task?: {
      task_type?: string;
      uploaded_files?: Array<{
        file_id: string;
        file_name: string;
        file_type: string;
      }>;
    };
  };
  // 流式消息相关字段
  text?: string;
  time?: string;
  message_id?: string;
  // 引用块（用于引用原文功能）
  reference_blocks?: Array<{
    id: string;
    text: string;
  }>;
  // 思考步骤（用于展示 AI 思考过程）
  steps?: ThinkingItem[];
  // 法规引用
  references?: AssistantReference[];
  // 工具建议
  suggestions?: Array<{ tool_id: string; name: string }>;
  // 工具调用进度
  toolCalls?: Array<{
    id: string;
    name: string;
    status: 'running' | 'completed' | 'error';
    resultPreview: string;
    timestamp: number;
  }>;
}

/**
 * 对话历史项
 */
export interface ConversationHistoryItem {
  id: string;
  title: string;
  timestamp: Date;
  preview: string;
  is_favorited?: boolean;
  agent_id?: string;
  create_time?: Date;
  update_time?: Date;
  message_count?: number;
}

/**
 * 模型类型
 */
export type ModelType = "deepseek-r1" | "deepseek-v3" | "qwen3-30b";

// ============ 智能体配置类型 ============

/**
 * 智能体分类
 */
export interface AgentCategory {
  id: string;
  name: string;
  icon: string;
  description?: string;
}

/**
 * 智能体开场消息
 */
export interface AgentOpeningMessage {
  title: string;
  content: string;
  icon?: string;
  actions?: Array<{
    id: string;
    label: string;
    description?: string;
    kind: "upload" | "input";
  }>;
  prompts?: string[];
}

/**
 * AI 引导消息配置
 */
export interface AgentGuideConfig {
  title: string;
  description: string;
  icon?: string;
  iconColor?: string;
  actionButton?: {
    label: string;
    icon?: string;
  };
  examplePrompts?: string[];
}

/**
 * 输入区域操作
 */
export interface AgentInputAction {
  id: string;
  label: string;
  kind?: "model" | "toggle" | "custom";
  icon?: "deep-think";
  defaultSelected?: boolean;
}

/**
 * 输入控件配置
 */
export interface AgentInputControls {
  upload?: boolean | {
    enabled?: boolean;
    accept?: string;
    maxSizeMb?: number;
    maxCount?: number;
  };
}

/**
 * SSE 事件
 */
export interface ChatSseEvent {
  event: string;
  data: any;
}

/**
 * 嵌入组件的 props 类型
 */
export interface EmbeddedComponentProps {
  [key: string]: any;
}

/**
 * 智能体配置
 */
export interface Agent {
  id: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  color: "orange" | "blue" | "purple" | "indigo" | "green" | "teal" | "cyan";
  displayMode?: "inline" | "navigate";
  navigatePath?: string;
  // Vue3 中不需要 React Component 类型，使用字符串引用
  component?: string;
  componentProps?: Record<string, any>;
  opening_message?: AgentOpeningMessage;
  guide?: AgentGuideConfig;
  input_actions?: AgentInputAction[];
  input_controls?: AgentInputControls;
  input_placeholder?: string;
  footer_tag?: string;
  assistant_role_label?: string;
  mock_messages?: ChatMockMessage[];
  mock_sse_events?: ChatSseEvent[];
  mock_assistant_meta?: Pick<ChatMockMessage, "references" | "suggestions">;
  workbench?: {
    enabled: boolean;
    variant: "complaint" | "expert";
  };
}

/**
 * Mock 消息（用于布局预览）
 */
export interface ChatMockMessage {
  message_id: string;
  role: MessageRole;
  time?: string;
  text?: string;
  attachment?: {
    id: string;
    name: string;
  };
  steps?: ThinkingItem[];
  content?: string;
  references?: Array<{
    id: string;
    title: string;
    summary: string;
  }>;
  suggestions?: Array<{ tool_id: string; name: string }>;
}

// ============ AISpecialistHome 类型 ============

/**
 * 法规引用解析结果
 */
export interface ParsedCitation {
  id: string;
  normalizedId: string;
  anchorId: string;
  displayLabel: string;
  title: string;
  clause?: string;
  description?: string;
  sourceId?: string;
  href?: string;
  kind?: string;
}

/**
 * 法规响应
 * 匹配后端实际返回格式
 */
export interface PolicyResponse {
  text?: string;
  title?: string;
  [key: string]: unknown;
}

// ============ 其他类型 ============

/**
 * 视图模式
 */
export type ViewMode = "home" | "agent";

/**
 * 激活的智能体状态
 */
export interface ActiveAgentState {
  agentId: string;
  agentName: string;
  category: string;
  componentProps?: Record<string, any>;
  key?: number;
}
