/**
 * 通用 SSE 流处理 Hook
 *
 * LangChain 官方格式：
 * - data: {"content": "...", "type": "AIMessageChunk", ...}
 * - data: [DONE]
 */

import { useRef, useCallback } from "react";
import type { ChatMessage } from "../types";

export interface SSEEventHandlers {
  onStart?: () => void;
  onMessage?: (content: string) => void;
  onTask?: (tasks: any[]) => void;
  onCompleted?: (data?: any) => void;
  onError?: (error: string) => void;
  onInterrupt?: (data: {
    review_type: string;
    message: string;
    current_data: any;
  }) => void;
  onEnd?: () => void;
}

export interface SSEStreamOptions {
  url: string;
  method?: string;
  body: any;
  signal?: AbortSignal;
  handlers: SSEEventHandlers;
}

/**
 * 处理 SSE 流式响应（LangChain 官方格式）
 *
 * 格式：
 *   data: {"content": "...", "type": "AIMessageChunk", ...}
 *   data: [DONE]
 */
export async function processSSEStream(options: SSEStreamOptions): Promise<void> {
  const { url, method = "POST", body, signal, handlers } = options;

  try {
    const response = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "include",
      signal,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `HTTP ${response.status}`);
    }

    if (!response.body) {
      throw new Error("响应体为空");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let currentEventType = "message";  // 默认事件类型

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();

        // 解析 event: 行
        if (trimmed.startsWith("event:")) {
          currentEventType = trimmed.slice(6).trim();
          continue;
        }

        // 解析 data: 行
        if (!trimmed.startsWith("data:")) continue;

        const dataStr = trimmed.slice(5).trim();
        if (!dataStr) continue;

        // 结束标记
        if (dataStr === "[DONE]") {
          handlers.onCompleted?.({});
          currentEventType = "message";
          return;
        }

        let eventData: any;
        try {
          eventData = JSON.parse(dataStr);
        } catch {
          currentEventType = "message";
          continue;
        }

        // 根据事件类型处理
        if (currentEventType === "completed") {
          handlers.onCompleted?.(eventData);
          currentEventType = "message";
          continue;
        }

        if (currentEventType === "error") {
          handlers.onError?.(eventData.message || eventData.error || "未知错误");
          currentEventType = "message";
          continue;
        }

        if (currentEventType === "interrupt") {
          handlers.onInterrupt?.(eventData.interrupt_data || eventData);
          currentEventType = "message";
          return;
        }

        // 错误处理（兼容旧格式）
        if (eventData.error) {
          handlers.onError?.(eventData.error);
          currentEventType = "message";
          continue;
        }

        // 中断处理（兼容旧格式）
        if (eventData.interrupt_data) {
          handlers.onInterrupt?.(eventData.interrupt_data);
          currentEventType = "message";
          return;
        }

        // 任务进度（NonStandardContentBlock 格式）
        if (eventData.type === "non_standard" && eventData.value?.type === "task_progress") {
          if (handlers.onTask) {
            handlers.onTask(eventData.value.data);
          }
          currentEventType = "message";
          continue;
        }

        // 任务进度（旧格式兼容）
        if (eventData.type === "task_progress" && handlers.onTask) {
          handlers.onTask(eventData.data);
          currentEventType = "message";
          continue;
        }

        // AI 消息：type 为 "AIMessageChunk" 或 "ai"
        if ((eventData.type === "AIMessageChunk" || eventData.type === "ai") && eventData.content) {
          handlers.onMessage?.(eventData.content);
        }

        currentEventType = "message";
      }
    }
  } finally {
    handlers.onEnd?.();
  }
}

/**
 * SSE Hook - 提供流式处理能力
 */
export function useSSEStream() {
  const abortControllerRef = useRef<AbortController | null>(null);

  const startStream = useCallback(async (options: Omit<SSEStreamOptions, 'signal'>) => {
    // 取消之前的流
    abortControllerRef.current?.abort();

    // 创建新的 AbortController
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      await processSSEStream({
        ...options,
        signal: controller.signal,
      });
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
    }
  }, []);

  const stopStream = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
  }, []);

  return {
    startStream,
    stopStream,
    isStreaming: () => abortControllerRef.current !== null,
  };
}
