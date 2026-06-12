export type MessageRole = "user" | "assistant";

export type Message = {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  isPlaceholder?: boolean;
  toolTasks?: ToolTaskUpdate[];
};

export type ToolTaskUpdate = {
  nodeId: string;
  title?: string;
  nodeType?: string;
  status?: string;
  completed: boolean;
  updates: Array<{
    id: string;
    content: string;
    ephemeral?: boolean;
    createdAt?: number;
    lineId?: string;
  }>;
  children?: ToolTaskUpdate[];
};

export type AttachmentInfo = {
  fileName: string;
  fileSize: number;
  uploadedAt: string;
};

export type ConversationHistory = {
  id: string;
  title: string;
  date: string;
  agentId: AgentId;
  agentLabel: string;
  summary?: string;
  messages: Message[];
};

export type ConversationSummary = {
  id: string;
  createdAt: string;
  lastUsedAt: string;
  messageCount: number;
  latestMessagePreview: string;
  firstMessagePreview: string;
  title?: string;
};

export type ParsedCitation = {
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
};

export type AgentId = "case-knowledge";

export type AgentTab = {
  id: AgentId;
  label: string;
  description: string;
  icon: string;
  parentId?: AgentId;
  isPrimary?: boolean;
};
