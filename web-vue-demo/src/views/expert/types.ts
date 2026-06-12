/**
 * 法律助手类型定义
 * 重新导出 types/index.ts 中的所有类型
 */

export type {
  // 基础类型
  UploadedFile,
  MessageRole,
  StepStatus,
  ThinkingItem,
  AssistantReference,
  ChatMessage,
  ConversationHistoryItem,
  ModelType,

  // 智能体配置类型
  AgentCategory,
  AgentOpeningMessage,
  AgentGuideConfig,
  AgentInputAction,
  AgentInputControls,
  ChatSseEvent,
  EmbeddedComponentProps,
  Agent,
  ChatMockMessage,

  // AISpecialistHome 类型
  ParsedCitation,
  PolicyResponse,

  // 其他类型
  ViewMode,
  ActiveAgentState,
} from './types/index'
