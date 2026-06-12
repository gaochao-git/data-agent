import type { ChatMockMessage, ChatSseEvent } from "../types";

type StepStatus = "done" | "thinking";

export function parseSseEventsToMessages(events: ChatSseEvent[]): ChatMockMessage[] {
  const messages: ChatMockMessage[] = [];
  const stepIndex = new Map<string, number>();
  const messageById = new Map<string, ChatMockMessage>();

  const ensureMessage = (messageId: string) => {
    if (!messageById.has(messageId)) {
      const msg: ChatMockMessage = {
        message_id: messageId,
        role: "assistant",
        steps: [],
        content: "",
      };
      messageById.set(messageId, msg);
      messages.push(msg);
    }
    return messageById.get(messageId)!;
  };

  events.forEach((evt) => {
    if (evt.event !== "chat_message") return;
    const data = evt.data || {};
    const eventType = data.event;
    if (!eventType) return;

    if (eventType === "message_start") {
      if (data.message_id) {
        ensureMessage(data.message_id);
      }
      return;
    }

    if (eventType === "node_start") {
      if (!data.message_id || !data.node_id) return;
      const msg = ensureMessage(data.message_id);
      const steps = msg.steps ?? [];
      const labelHidden = Boolean(data.label_hidden);
      const flattenChildren = Boolean(data.flatten_children);
      const step = {
        id: data.node_id,
        label: labelHidden ? "" : (data.title || "处理中"),
        status: "thinking" as StepStatus,
        label_hidden: labelHidden,
        flatten_children: flattenChildren,
      };
      stepIndex.set(data.node_id, steps.length);
      steps.push(step);
      msg.steps = steps;
      return;
    }

    if (eventType === "node_end") {
      if (!data.message_id || !data.node_id) return;
      const msg = ensureMessage(data.message_id);
      const steps = msg.steps ?? [];
      const idx = stepIndex.get(data.node_id);
      if (typeof idx === "number" && steps[idx]) {
        steps[idx].status = "done";
      }
      msg.steps = steps;
      return;
    }

    if (eventType === "content_delta") {
      if (!data.message_id) return;
      const msg = ensureMessage(data.message_id);
      const delta = typeof data.delta === "string" ? data.delta : "";
      msg.content = `${msg.content || ""}${delta}`;
      msg.time = data.created ? String(data.created) : msg.time;
    }
  });

  return messages;
}
