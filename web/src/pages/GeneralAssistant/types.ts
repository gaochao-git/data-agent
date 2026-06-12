/**
 * 通用助手类型定义
 */

// 上传文件状态
export interface UploadedFile {
  file_id: string;
  original_filename: string;
  file_type: string;
  file_size: number;
  status: "uploading" | "parsing" | "completed" | "failed" | "timeout";
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string | any[];
  timestamp: Date;
  attachments?: UploadedFile[];  // 附带的文件
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
}

export interface ConversationHistoryItem {
  id: string;
  title: string;
  timestamp: Date; // 兼容旧代码
  preview: string;
  is_favorited?: boolean; // 是否收藏
  agent_id?: string; // 智能体ID，用于跳转
  // 可选的额外字段
  create_time?: Date;
  update_time?: Date;
  message_count?: number;
}

export type ModelType = "deepseek-r1" | "deepseek-v3" | "qwen3-30b";

export interface AgentCategory {
  id: string;
  name: string;
  icon: string;
  description?: string; // 功能描述（用于轮播展示）
}

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

// AI 引导消息配置
export interface AgentGuideConfig {
  title: string;           // 标题，如"事实认定分析"
  description: string;     // 描述文字
  icon?: string;           // 图标 class，如 "fa-scale-balanced"
  iconColor?: string;      // 图标颜色，如 "blue"
  actionButton?: {         // 操作按钮（可选）
    label: string;         // 按钮文字，如 "上传文档"
    icon?: string;         // 按钮图标
  };
  examplePrompts?: string[]; // 示例提示语（可选）
}

export interface AgentInputAction {
  id: string;
  label: string;
  kind?: "model" | "toggle" | "custom";
  icon?: "deep-think";
  defaultSelected?: boolean;
}

export interface AgentInputControls {
  upload?: boolean | {
    enabled?: boolean;
    accept?: string;
    maxSizeMb?: number;
    maxCount?: number;
  };
}

export interface ChatMockMessage {
  message_id: string;
  role: "user" | "assistant";
  time?: string;
  text?: string;
  attachment?: {
    id: string;
    name: string;
  };
  steps?: Array<{
    id: string;
    label: string;
    status: "done" | "thinking";
    label_hidden?: boolean;
    flatten_children?: boolean;
    detail?: string;
    children?: Array<{
      id: string;
      label: string;
      status: "done" | "thinking";
      label_hidden?: boolean;
      flatten_children?: boolean;
      detail?: string;
      children?: any[];
    }>;
  }>;
  content?: string;
  references?: Array<{
    id: string;
    title: string;
    summary: string;
  }>;
  suggestions?: Array<{ tool_id: string; name: string }>;
}

export interface ChatSseEvent {
  event: string;
  data: any;
}

// 嵌入组件的 props 类型（基础类型，具体组件可扩展）
export interface EmbeddedComponentProps {
  [key: string]: any;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  color: "orange" | "blue" | "purple" | "indigo" | "green" | "teal" | "cyan";
  // 智能体展示模式：inline-当前页面内展示 | navigate-跳转到新页面
  displayMode?: "inline" | "navigate";
  // 跳转路径（当displayMode为navigate时使用）
  navigatePath?: string;
  // 嵌入组件（displayMode为inline时使用，支持动态加载）
  component?: React.ComponentType<EmbeddedComponentProps>;
  // 传递给嵌入组件的 props
  componentProps?: Record<string, any>;
  // 初始化状态下聊天窗口的开场文案
  opening_message?: AgentOpeningMessage;
  // AI 引导消息配置（新会话时显示）
  guide?: AgentGuideConfig;
  // 输入框下方按钮区配置
  input_actions?: AgentInputAction[];
  // 输入框右侧操作按钮控制
  input_controls?: AgentInputControls;
  // 输入框 placeholder 文案
  input_placeholder?: string;
  // 输入框底部提示文案
  footer_tag?: string;
  // AI 回复头部角色名
  assistant_role_label?: string;
  // Mock 消息（用于布局预览）
  mock_messages?: ChatMockMessage[];
  // Mock SSE 事件（用于解析后渲染）
  mock_sse_events?: ChatSseEvent[];
  // Mock SSE 解析后的补充信息
  mock_assistant_meta?: Pick<ChatMockMessage, "references" | "suggestions">;
  // 工作台配置
  workbench?: {
    enabled: boolean;
    variant: "complaint" | "expert";
  };
}

// 视图状态：home-主页 | agent-智能体视图
export type ViewMode = "home" | "agent";

// 激活的智能体信息
export interface ActiveAgentState {
  agentId: string;
  agentName: string;
  category: string;
  componentProps?: Record<string, any>;  // 传递给组件的 props
  key?: number;  // 用于强制重新渲染组件
}
