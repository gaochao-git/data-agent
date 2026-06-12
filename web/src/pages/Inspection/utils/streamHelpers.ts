/**
 * SSE 流处理辅助函数
 */

import type { ChatMessage } from "../types";

/**
 * 线程创建锁，防止并发创建多个线程
 */
let threadCreationPromise: Promise<string | null> | null = null;

/**
 * 确保线程 ID 存在,如果不存在则创建新线程
 * 使用锁机制防止并发创建
 */
export async function ensureThreadId(
  currentThreadId: string,
  createNewThread: () => Promise<string | null>,
  context: string
): Promise<string | null> {
  if (currentThreadId) return currentThreadId;

  // 如果正在创建线程，等待完成
  if (threadCreationPromise) {
    console.log(`[${context}] 检测到线程正在创建，等待完成...`);
    return await threadCreationPromise;
  }

  console.log(`[${context}] 未找到线程ID，创建新线程...`);
  threadCreationPromise = createNewThread();

  try {
    const newThreadId = await threadCreationPromise;
    if (!newThreadId) {
      console.error(`[${context}] 创建线程失败`);
    }
    return newThreadId;
  } finally {
    // 创建完成后清除锁
    threadCreationPromise = null;
  }
}

/**
 * 更新指定 ID 的消息
 */
export function updateMessageById(
  messages: ChatMessage[],
  messageId: string,
  updater: (msg: ChatMessage) => ChatMessage
): ChatMessage[] {
  return messages.map(msg =>
    msg.id === messageId ? updater(msg) : msg
  );
}

/**
 * 向指定 ID 的消息追加内容
 */
export function appendMessageContent(
  messages: ChatMessage[],
  messageId: string,
  content: string
): ChatMessage[] {
  return updateMessageById(messages, messageId, msg => ({
    ...msg,
    content: msg.content + content,
    timestamp: new Date(),
  }));
}

/**
 * 更新指定 ID 消息的错误状态
 */
export function setMessageError(
  messages: ChatMessage[],
  messageId: string,
  error: string
): ChatMessage[] {
  return updateMessageById(messages, messageId, msg => ({
    ...msg,
    content: error,
    timestamp: new Date(),
  }));
}

/**
 * 解析 SSE 行
 */
export type SSELine =
  | { type: 'event'; name: string }
  | { type: 'data'; payload: string }
  | { type: 'skip' };

export function parseSSELine(line: string): SSELine {
  const trimmed = line.trim();

  if (trimmed.startsWith("event:")) {
    return { type: 'event', name: trimmed.slice(6).trim() };
  }

  if (trimmed.startsWith("data:")) {
    return { type: 'data', payload: trimmed.slice(5).trim() };
  }

  return { type: 'skip' };
}

/**
 * 清理流式状态
 */
export interface StreamingState {
  setIsChatStreaming: (value: boolean) => void;
  setChatStreamingStatus: (value: string) => void;
  setActiveAssistantMessageId: (value: string | null) => void;
  abortControllerRef: React.MutableRefObject<AbortController | null>;
}

export function cleanupStreamingState(state: StreamingState) {
  state.abortControllerRef.current?.abort();
  state.abortControllerRef.current = null;
  state.setIsChatStreaming(false);
  state.setChatStreamingStatus("");
  state.setActiveAssistantMessageId(null);
}
