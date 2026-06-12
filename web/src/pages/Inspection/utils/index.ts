/**
 * 工具函数统一导出
 */

// 格式化工具
export {
  mergeClassNames,
  formatPromptPreview,
  formatLawItem,
  formatDetailItem,
  toStringSafe,
  joinChineseSemicolon,
  stringifyLawItems,
  createMessageId,
  formatTimestamp,
} from "./formatters";

// 消息解析
export {
  parseMessageContent,
  extractTextFromContent,
  extractFilesFromContent,
  extractNonStandardData,
  type ParsedMessageContent,
} from "./messageParser";

// 流处理辅助
export {
  ensureThreadId,
  updateMessageById,
  appendMessageContent,
  setMessageError,
  parseSSELine,
  cleanupStreamingState,
  type SSELine,
  type StreamingState,
} from "./streamHelpers";
