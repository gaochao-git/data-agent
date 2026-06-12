import { API_BASE } from "./config";

export type ChatHistoryItem = {
  role: "user" | "assistant" | "system";
  content: string;
};

type BaseStreamEvent = {
  event: string;
  response_id: string;
  message_id?: string;
  created: number;
  conversation_id?: string;
  node_id?: string | null;
  [key: string]: unknown;
};

export type MessageStartEvent = BaseStreamEvent & {
  event: "message_start";
  role?: string;
  model?: string;
};

export type ContentDeltaEvent = BaseStreamEvent & {
  event: "content_delta";
  index: number;
  delta: string;
  role?: string;
};

export type NodeStartEvent = BaseStreamEvent & {
  event: "node_start";
  node_id: string;
  type?: string;
  title?: string;
};

export type NodeDeltaEvent = BaseStreamEvent & {
  event: "node_delta";
  node_id: string;
  delta?: unknown;
  ephemeral?: boolean;
  line_id?: string;
};

export type NodeEndEvent = BaseStreamEvent & {
  event: "node_end";
  node_id: string;
  status?: string;
  title?: string;
};

export type MessageEndEvent = BaseStreamEvent & {
  event: "message_end";
  finish_reason?: string;
  usage?: Record<string, unknown> | null;
};

export type ErrorEvent = BaseStreamEvent & {
  event: "error";
  code?: string;
  message?: string;
  fatal?: boolean;
};

export type KeepAliveEvent = BaseStreamEvent & {
  event: "keepalive";
};

export type DoneEvent = BaseStreamEvent & {
  event: "done";
};

export type TitleGenerationEvent = BaseStreamEvent & {
  event: "title_generation";
  title?: string;
};

export type StreamEvent =
  | MessageStartEvent
  | ContentDeltaEvent
  | NodeStartEvent
  | NodeDeltaEvent
  | NodeEndEvent
  | MessageEndEvent
  | ErrorEvent
  | KeepAliveEvent
  | DoneEvent
  | TitleGenerationEvent;

type StreamChatOptions = {
  question: string;
  history?: ChatHistoryItem[];
  agentId?: string;
  systemPrompt?: string;
  temperature?: number;
  topP?: number;
  enableTestTools?: boolean;
  conversationId?: string;
  signal?: AbortSignal;
  onEvent?: (event: StreamEvent) => void;
  onMessageStart?: (event: MessageStartEvent) => void;
  onContentDelta?: (event: ContentDeltaEvent) => void;
  onNodeStart?: (event: NodeStartEvent) => void;
  onNodeDelta?: (event: NodeDeltaEvent) => void;
  onNodeEnd?: (event: NodeEndEvent) => void;
  onMessageEnd?: (event: MessageEndEvent) => void;
  onTitleGeneration?: (event: TitleGenerationEvent) => void;
  onError?: (event: ErrorEvent) => void;
  onToken?: (token: string) => void;
  onDone?: () => void;
  onConversationId?: (conversationId: string) => void;
};

const decoder = new TextDecoder();

const isAbortError = (error: unknown, signal?: AbortSignal) => {
  if (signal?.aborted) {
    return true;
  }
  if (error instanceof DOMException) {
    return error.name === "AbortError";
  }
  if (error instanceof Error) {
    return error.name === "AbortError";
  }
  return false;
};

export async function streamChatCompletion({
  question,
  history = [],
  agentId,
  systemPrompt,
  temperature,
  topP,
  enableTestTools,
  conversationId,
  signal,
  onEvent,
  onMessageStart,
  onContentDelta,
  onNodeStart,
  onNodeDelta,
  onNodeEnd,
  onMessageEnd,
  onTitleGeneration,
  onError,
  onToken,
  onDone,
  onConversationId,
}: StreamChatOptions): Promise<void> {
  const payload: Record<string, unknown> = {
    question,
    history,
    agent_id: agentId,
    system_prompt: systemPrompt,
    temperature,
    top_p: topP,
    enable_test_tools: enableTestTools,
  };

  if (conversationId) {
    payload.conversation_id = conversationId;
  }

  const response = await fetch(`${API_BASE}/api/ai_chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
    credentials: "include",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(
      text ? `请求失败：${text}` : `请求失败，状态码 ${response.status}`
    );
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("无法建立流式连接");
  }

  let buffer = "";
  let doneEmitted = false;

  const markDone = () => {
    if (!doneEmitted) {
      doneEmitted = true;
      onDone?.();
    }
  };

  const handleEvent = (data: Record<string, unknown>): boolean => {
    const eventName = data.event;
    if (typeof eventName !== "string") {
      return false;
    }

    const event = data as StreamEvent;
    const conversationIdValue =
      typeof (data as BaseStreamEvent).conversation_id === "string"
        ? (data as BaseStreamEvent).conversation_id
        : null;

    if (conversationIdValue) {
      onConversationId?.(conversationIdValue);
    }

    onEvent?.(event);

    switch (eventName) {
      case "message_start": {
        onMessageStart?.(event as MessageStartEvent);
        return false;
      }
      case "content_delta": {
        const contentEvent = event as ContentDeltaEvent;
        if (typeof contentEvent.delta === "string") {
          onContentDelta?.(contentEvent);
          onToken?.(contentEvent.delta);
        }
        return false;
      }
      case "node_start": {
        onNodeStart?.(event as NodeStartEvent);
        return false;
      }
      case "node_delta": {
        onNodeDelta?.(event as NodeDeltaEvent);
        return false;
      }
      case "node_end": {
        onNodeEnd?.(event as NodeEndEvent);
        return false;
      }
      case "message_end": {
        onMessageEnd?.(event as MessageEndEvent);
        return false;
      }
      case "title_generation": {
        onTitleGeneration?.(event as TitleGenerationEvent);
        return false;
      }
      case "keepalive": {
        return false;
      }
      case "error": {
        const errorEvent = event as ErrorEvent;
        onError?.(errorEvent);
        const message =
          (typeof errorEvent.message === "string" && errorEvent.message) ||
          (typeof data.error === "string" && data.error) ||
          "聊天流出现错误";
        throw new Error(message);
      }
      case "done": {
        markDone();
        return true;
      }
      default:
        return false;
    }
  };

  const processPayload = (payload: string): boolean => {
    if (!payload) {
      return false;
    }

    if (payload === "[DONE]") {
      markDone();
      return true;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch (error) {
      console.warn("聊天流数据解析失败", error, payload);
      return false;
    }

    if (typeof parsed !== "object" || parsed === null) {
      return false;
    }

    const data = parsed as Record<string, unknown>;
    if (typeof data.event === "string") {
      return handleEvent(data);
    }

    if (typeof data.error === "string" && data.error) {
      throw new Error(data.error);
    }

    if (typeof data.content === "string") {
      onToken?.(data.content);
    }

    return false;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const rawEvent = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);

        const lines = rawEvent.split("\n");
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data:")) {
            continue;
          }

          const payload = trimmed.slice(5).trim();
          if (processPayload(payload)) {
            return;
          }
        }

        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (error) {
    if (isAbortError(error, signal)) {
      return;
    }
    throw error instanceof Error ? error : new Error(String(error));
  }

  if (buffer.trim()) {
    const remaining = buffer.trim();
    try {
      if (processPayload(remaining)) {
        return;
      }
    } catch (error) {
      console.warn("聊天流尾部解析失败", error, remaining);
      throw error instanceof Error ? error : new Error(String(error));
    }
  }

  markDone();
}
