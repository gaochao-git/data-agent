import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { API_BASE, MAX_FILE_SIZE_MB, TRACE_PANEL_ENABLED } from "@/lib/config";
import type { ParsedCitation } from "@/pages/AISpecialistHome/types";
import { Conversation } from "./unified/Conversation";
import { Workbench } from "./unified/Workbench";

const safeRandomId = (): string => {
  const c: any = globalThis?.crypto;
  if (typeof c?.randomUUID === "function") {
    return c.randomUUID();
  }
  if (typeof c?.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    c.getRandomValues(bytes);
    return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  return `id_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
};

export type ThinkingStatus = "done" | "thinking";

export interface ThinkingItem {
  id: string;
  label: string;
  status: ThinkingStatus;
  label_hidden?: boolean;
  flatten_children?: boolean;
  detail?: string;
  detailLines?: Array<{
    lineId?: string;
    content: string;
  }>;
  children?: ThinkingItem[];
}

export interface AssistantReference {
  id: string;
  title: string;
  summary: string;
  sourceId?: string;
  anchorId?: string;
  clause?: string;
  description?: string;
  displayLabel?: string;
}

export interface SuggestionItem {
  tool_id: string;
  name: string;
}

export interface ChatMessage {
  message_id: string;
  role: "user" | "assistant";
  time?: string;
  text?: string;
  reference_blocks?: Array<{
    id: string;
    text: string;
  }>;
  attachment?: {
    id: string;
    name: string;
  };
  attachments?: Array<{
    id: string;
    name: string;
  }>;
  steps?: ThinkingItem[];
  content?: string;
  references?: AssistantReference[];
  suggestions?: SuggestionItem[];
}

interface UnifiedEmbeddedContentProps {
  agentId?: string;
  initialThreadId?: string;
  messages?: ChatMessage[];
  onStreamDone?: () => void;
  inputActions?: Array<{
    id: string;
    label: string;
    icon?: "deep-think";
    defaultSelected?: boolean;
    kind?: "model" | "toggle" | "custom";
  }>;
  historyArtifacts?: {
    rawFile?: {
      filePreviewUrl?: string;
      serverFiles?: Array<{ order?: number; name?: string; url?: string }>;
      selectedFileIndex?: number;
    };
    rawText?: {
      files?: Array<{ order?: number; name?: string; pages?: Array<{ page?: number; content?: string }> }>;
    };
  };
  inputControls?: {
    upload?: boolean;
  };
  inputPlaceholder?: string;
  footerTag?: string;
  assistantRoleLabel?: string;
  openingMessage?: AgentOpeningMessage;
  workbench?: {
    enabled: boolean;
    variant: "complaint" | "expert";
  };
  onConversationIdChange?: (conversationId: string) => void;
  onTitleChange?: (title: string) => void;
}


export function UnifiedEmbeddedContent({ agentId, initialThreadId, messages = [], inputActions, inputControls, inputPlaceholder, footerTag, assistantRoleLabel, openingMessage, workbench, onConversationIdChange, onTitleChange, historyArtifacts, onStreamDone }: UnifiedEmbeddedContentProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollContainerRef = useRef<HTMLElement | null>(null);
  const shouldAutoScrollRef = useRef(true);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(messages);
  const [inputValue, setInputValue] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [referenceBlocks, setReferenceBlocks] = useState<Array<{ id: string; text: string }>>([]);
  const [isSending, setIsSending] = useState(false);
  const [isWorkbenchVisible, setIsWorkbenchVisible] = useState(false);
  const [activeWorkbenchPanel, setActiveWorkbenchPanel] = useState<string | null>(null);
  const layoutContainerRef = useRef<HTMLDivElement | null>(null);
  const resizeStartXRef = useRef(0);
  const resizeStartWidthRef = useRef(0);
  const hasManualResizeRef = useRef(false);
  const [isResizing, setIsResizing] = useState(false);
  const [workbenchWidth, setWorkbenchWidth] = useState<number | null>(null);
  const [complaintFiles, setComplaintFiles] = useState<
    Array<{ order: number; name: string; url: string }>
  >([]);
  const [complaintSelectedIndex, setComplaintSelectedIndex] = useState(0);
  const [complaintRawTextFiles, setComplaintRawTextFiles] = useState<
    Array<{ order: number; name: string; pages: Array<{ page: number; content: string }> }>
  >([]);
  const [activeExpertCitation, setActiveExpertCitation] = useState<ParsedCitation | null>(null);
  const [currentConversationId, setCurrentConversationId] = useState<string>("");
  const abortControllerRef = useRef<AbortController | null>(null);
  const conversationIdRef = useRef<string>("");
  const titleBufferRef = useRef<string>("");
  const hasTriggeredHistoryRefreshRef = useRef(false);
  const historyRefreshIdsRef = useRef<Set<string>>(new Set());
  const hasSyncedConversationIdRef = useRef(false);
  const messageIdCounterRef = useRef(0);
  const showUpload = Boolean(inputControls?.upload);
  const actionButtons = inputActions ?? [];
  const [selectedActionIds, setSelectedActionIds] = useState<Set<string>>(new Set());
  const placeholderText = inputPlaceholder || "请输入内容...";
  const buildAttachmentPrompt = (files: File[]) => {
    if (!files.length) return "";
    return `${placeholderText}：${files.map((file) => file.name).join("，")}`;
  };
  const footerTagText = footerTag || (agentId === "expert_agent" ? "法律检索" : "举报件办理");
  const assistantRole = assistantRoleLabel || (agentId === "expert_agent" ? "法律AI" : "举报件AI");
  const showOpening = chatMessages.length === 0 && Boolean(openingMessage);
  const showWorkbench = Boolean(workbench?.enabled) && isWorkbenchVisible;
  const workbenchVariant = workbench?.variant;
  const tracePanelEnabled = TRACE_PANEL_ENABLED && workbenchVariant === "expert";
  const handleAskFromRawText = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setReferenceBlocks((prev) => {
      if (prev.some((item) => item.text === trimmed)) {
        return prev;
      }
      return [...prev, { id: safeRandomId(), text: trimmed }];
    });
    requestAnimationFrame(() => textareaRef.current?.focus());
  };
  const handleRemoveReferenceBlock = (id: string) => {
    setReferenceBlocks((prev) => prev.filter((item) => item.id !== id));
  };
  const uploadConfig = useMemo(() => {
    if (!inputControls?.upload) return null;
    if (typeof inputControls.upload === "boolean") {
      return inputControls.upload ? { enabled: true } : null;
    }
    return inputControls.upload.enabled === false ? null : inputControls.upload;
  }, [inputControls]);
  const canSend = inputValue.trim().length > 0 || selectedFiles.length > 0 || referenceBlocks.length > 0;

  const handleToggleAction = (actionId: string) => {
    const action = actionButtons.find((item) => item.id === actionId);
    if (!action) return;
    if (action.defaultSelected) return;
    setSelectedActionIds((prev) => {
      const next = new Set(prev);
      if (action.kind === "model") {
        actionButtons
          .filter((item) => item.kind === "model")
          .forEach((item) => next.delete(item.id));
        next.add(actionId);
        return next;
      }
      if (next.has(actionId)) {
        next.delete(actionId);
      } else {
        next.add(actionId);
      }
      return next;
    });
  };

  const selectedModel = actionButtons.find(
    (action) => action.kind === "model" && selectedActionIds.has(action.id)
  );
  const useDeepThink = selectedActionIds.has("deep-think");

  useEffect(() => {
    setChatMessages(messages);
  }, [agentId, messages]);

  useEffect(() => {
    if (!initialThreadId) return;
    if (conversationIdRef.current !== initialThreadId) {
      conversationIdRef.current = initialThreadId;
      setCurrentConversationId(initialThreadId);
      hasSyncedConversationIdRef.current = true;
      titleBufferRef.current = "";
    }
  }, [initialThreadId]);

  useEffect(() => {
    if (!historyArtifacts) return;
    const rawFile = historyArtifacts.rawFile;
    if (rawFile && Array.isArray(rawFile.serverFiles)) {
      setComplaintFiles(
        rawFile.serverFiles
          .filter((file: any) => file && file.url)
          .map((file: any) => ({
            order: Number(file.order) || 0,
            name: String(file.name || ""),
            url: String(file.url || ""),
          }))
      );
      const nextIndex = Number(rawFile.selectedFileIndex) || 0;
      setComplaintSelectedIndex(nextIndex);
    }
    const rawTextFiles = historyArtifacts.rawText?.files;
    if (Array.isArray(rawTextFiles)) {
      setComplaintRawTextFiles(
        rawTextFiles
          .filter((file: any) => file && Array.isArray(file.pages))
          .map((file: any) => ({
            order: Number(file.order) || 0,
            name: String(file.name || ""),
            pages: file.pages
              .filter((page: any) => page && typeof page.content === "string")
              .map((page: any) => ({
                page: Number(page.page) || 0,
                content: String(page.content || ""),
              })),
          }))
      );
    }
  }, [historyArtifacts]);

  useEffect(() => {
    const nextSelected = new Set<string>();
    let hasModelSelected = false;
    actionButtons.forEach((action) => {
      if (action.defaultSelected) {
        nextSelected.add(action.id);
        if (action.kind === "model") {
          hasModelSelected = true;
        }
      }
    });
    if (!hasModelSelected) {
      const fallbackModel = actionButtons.find((action) => action.kind === "model");
      if (fallbackModel) {
        nextSelected.add(fallbackModel.id);
      }
    }
    setSelectedActionIds(nextSelected);
  }, [agentId, actionButtons]);

  useEffect(() => {
    if (!showWorkbench) return;
    const container = layoutContainerRef.current;
    if (!container) return;
    const MIN_LEFT_WIDTH = 550;
    const MIN_RIGHT_WIDTH = 320;
    const RESIZER_WIDTH = 22;
    const getContentWidth = () => {
      const style = window.getComputedStyle(container);
      const paddingLeft = Number.parseFloat(style.paddingLeft || "0");
      const paddingRight = Number.parseFloat(style.paddingRight || "0");
      return Math.max(container.clientWidth - paddingLeft - paddingRight, 0);
    };
    const clampWidth = (target: number, containerWidth: number) => {
      const maxAllowed = containerWidth - MIN_LEFT_WIDTH - RESIZER_WIDTH;
      const cappedMax = Math.max(maxAllowed, 0);
      const minWidth = Math.min(MIN_RIGHT_WIDTH, cappedMax);
      const upperWidth = Math.max(minWidth, cappedMax);
      return Math.min(Math.max(target, minWidth), upperWidth);
    };
    const updateWidth = (containerWidth: number) => {
      if (!containerWidth || !Number.isFinite(containerWidth)) return;
      setWorkbenchWidth((prev) => {
        if (hasManualResizeRef.current && prev) {
          return clampWidth(prev, containerWidth);
        }
        const ratio = workbenchVariant === "complaint" ? 2 / 3 : 1 / 2;
        const target = containerWidth * ratio;
        return clampWidth(target, containerWidth);
      });
    };
    updateWidth(getContentWidth());
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      updateWidth(getContentWidth());
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [showWorkbench]);

  useEffect(() => {
    if (!isResizing) return;
    const handleMouseMove = (event: MouseEvent) => {
      const container = layoutContainerRef.current;
      if (!container) return;
      const MIN_LEFT_WIDTH = 550;
      const MIN_RIGHT_WIDTH = 320;
      const RESIZER_WIDTH = 22;
      const style = window.getComputedStyle(container);
      const paddingLeft = Number.parseFloat(style.paddingLeft || "0");
      const paddingRight = Number.parseFloat(style.paddingRight || "0");
      const contentWidth = Math.max(container.clientWidth - paddingLeft - paddingRight, 0);
      const deltaX = event.clientX - resizeStartXRef.current;
      const target = resizeStartWidthRef.current - deltaX;
      const maxAllowed = contentWidth - MIN_LEFT_WIDTH - RESIZER_WIDTH;
      const cappedMax = Math.max(maxAllowed, 0);
      const minWidth = Math.min(MIN_RIGHT_WIDTH, cappedMax);
      const upperWidth = Math.max(minWidth, cappedMax);
      const clamped = Math.min(Math.max(target, minWidth), upperWidth);
      setWorkbenchWidth(clamped);
    };
    const handleMouseUp = () => setIsResizing(false);
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isResizing]);

  useEffect(() => {
    if (!shouldAutoScrollRef.current) return;
    const container = scrollContainerRef.current;
    if (!container) return;
    container.scrollTop = container.scrollHeight;
  }, [chatMessages]);

  const handleScroll = () => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    shouldAutoScrollRef.current = distanceToBottom <= 24;
  };

  const scrollToBottom = () => {
    const container = scrollContainerRef.current;
    if (!container) return;
    container.scrollTop = container.scrollHeight;
  };

  const createLocalMessageId = () => {
    messageIdCounterRef.current += 1;
    return `${agentId ?? "agent"}-${Date.now()}-${messageIdCounterRef.current}`;
  };

  const triggerHistoryRefreshOnce = () => {
    const conversationId = conversationIdRef.current;
    if (!conversationId) return;
    if (historyRefreshIdsRef.current.has(conversationId)) return;
    historyRefreshIdsRef.current.add(conversationId);
    onStreamDone?.();
  };

  const ensureConversationId = () => {
    if (!conversationIdRef.current) {
      conversationIdRef.current = safeRandomId();
      setCurrentConversationId(conversationIdRef.current);
    }
    return conversationIdRef.current;
  };

  const isFileAllowed = (file: File, accept?: string) => {
    if (!accept) return true;
    const mime = (file.type || "").toLowerCase();
    const name = (file.name || "").toLowerCase();
    return accept
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .some((rule) => {
        if (!rule) return false;
        if (rule.endsWith("/*")) {
          const prefix = rule.replace("/*", "");
          return mime.startsWith(`${prefix}/`);
        }
        if (rule.startsWith(".")) {
          return name.endsWith(rule);
        }
        return mime === rule;
      });
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    const accept = uploadConfig?.accept;
    const maxSizeMb = uploadConfig?.maxSizeMb ?? MAX_FILE_SIZE_MB;
    const maxSize = maxSizeMb * 1024 * 1024;
    const maxCount = uploadConfig?.maxCount ?? 5;
    const nextFiles: File[] = [];

    for (const file of files) {
      if (!isFileAllowed(file, accept)) {
        toast.error("不支持的文件类型");
        event.target.value = "";
        return;
      }
      if (file.size > maxSize) {
        toast.error(`文件大小不能超过${maxSizeMb}MB`);
        event.target.value = "";
        return;
      }
      nextFiles.push(file);
    }

    const existingNames = new Set(selectedFiles.map((file) => `${file.name}-${file.size}`));
    const deduped = nextFiles.filter((file) => !existingNames.has(`${file.name}-${file.size}`));
    const combined = [...selectedFiles, ...deduped];
    if (combined.length > maxCount) {
      toast.error(`最多上传 ${maxCount} 个文件`);
      event.target.value = "";
      return;
    }
    setSelectedFiles(combined);
    setInputValue(buildAttachmentPrompt(combined));
    event.target.value = "";
  };

  const sendMessage = async (options?: { textOverride?: string; suggestion?: SuggestionItem }) => {
    if (isSending) return;
    if (!conversationIdRef.current) {
      titleBufferRef.current = "";
    }
    hasTriggeredHistoryRefreshRef.current = false;
    shouldAutoScrollRef.current = true;
    requestAnimationFrame(scrollToBottom);
    const text = (options?.textOverride ?? inputValue).trim();
    const filesToUpload = [...selectedFiles];
    const hasUploads = filesToUpload.length > 0;
    if (!text && !hasUploads && referenceBlocks.length === 0) {
      toast.error("请输入内容");
      return;
    }

    const effectiveText = text || (hasUploads ? "帮我分析举报件" : "");
    const displayText = effectiveText || (referenceBlocks.length ? "（引用原文）" : "");
    const userMessage: ChatMessage = {
      message_id: createLocalMessageId(),
      role: "user",
      text: displayText,
      time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    };
    if (referenceBlocks.length) {
      userMessage.reference_blocks = referenceBlocks.map((item) => ({ ...item }));
    }
    if (hasUploads && agentId === "complaint_agent") {
      userMessage.attachments = filesToUpload.map((file) => ({
        id: `${ensureConversationId()}:${file.name}`,
        name: file.name,
      }));
    }
    setChatMessages((prev) => [...prev, userMessage]);
    setInputValue("");
    setSelectedFiles([]);
    setReferenceBlocks([]);
    setIsSending(true);

    const selectedCustomActions = actionButtons
      .filter((action) => action.kind === "custom" && selectedActionIds.has(action.id))
      .map((action) => action.id);

    const extPayload: Record<string, any> = {};
    if (selectedCustomActions.length) {
      extPayload.actions = selectedCustomActions;
    }
    if (options?.suggestion) {
      extPayload.suggestion = {
        tool_id: options.suggestion.tool_id,
        name: options.suggestion.name,
      };
    }

    const payload = {
      client_meta: {
        conversation_id: ensureConversationId(),
        agent_id: agentId,
        last_message_index: 0,
      },
      messages: [
        {
          content_block: [
            ...referenceBlocks.map((item) => ({
              block_type: 10000,
              content: {
                reference_block: {
                  text: {
                    text: item.text,
                  },
                },
              },
              block_id: item.id,
              meta_info: [],
            })),
            ...(effectiveText
              ? [
                  {
                    block_type: 10001,
                    content: {
                      text_block: {
                        text: effectiveText,
                        icon_url: "",
                        icon_url_dark: "",
                        summary: "",
                      },
                    },
                    block_id: safeRandomId(),
                    meta_info: [],
                  },
                ]
              : []),
          ],
          model_id: selectedModel?.label || "DeepSeek-R1",
          model_name: selectedModel?.label || "DeepSeek-R1",
          need_deep_think: useDeepThink ? 1 : 0,
        },
      ],
      option: { answer_with_suggest: false },
      chat_ability: { ability_type: 6, ability_param: "{\"action\":3}" },
      ...(Object.keys(extPayload).length ? { ext: extPayload } : {}),
    };

    try {
      abortControllerRef.current?.abort();
      const controller = new AbortController();
      abortControllerRef.current = controller;
      let response: Response;
      if (agentId === "complaint_agent" && hasUploads) {
        const formData = new FormData();
        formData.append("payload", JSON.stringify(payload));
        filesToUpload.forEach((file) => {
          formData.append("file", file);
        });
        response = await fetch(`${API_BASE}/api/chat/completion`, {
          method: "POST",
          credentials: "include",
          signal: controller.signal,
          body: formData,
        });
      } else {
        response = await fetch(`${API_BASE}/api/chat/completion`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          signal: controller.signal,
          body: JSON.stringify(payload),
        });
      }

      if (!response.ok || !response.body) {
        throw new Error(`请求失败: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let currentMessageId = "";

      const updateAssistantMessage = (messageId: string, updater: (msg: ChatMessage) => ChatMessage) => {
        setChatMessages((prev) => {
          const next = [...prev];
          const idx = next.findIndex((msg) => msg.message_id === messageId);
          if (idx >= 0) {
            next[idx] = updater(next[idx]);
            return next;
          }
          next.push(updater({ message_id: messageId, role: "assistant", steps: [], content: "" }));
          return next;
        });
      };

      const ensureAssistantMessage = (messageId: string) => {
        updateAssistantMessage(messageId, (msg) => msg);
      };

      const updateStepInTree = (
        steps: ThinkingItem[],
        stepId: string,
        updater: (step: ThinkingItem) => ThinkingItem
      ): { steps: ThinkingItem[]; found: boolean } => {
        let found = false;
        const next = steps.map((step) => {
          if (step.id === stepId) {
            found = true;
            return updater(step);
          }
          if (step.children?.length) {
            const result = updateStepInTree(step.children, stepId, updater);
            if (result.found) {
              found = true;
              return { ...step, children: result.steps };
            }
          }
          return step;
        });
        return { steps: next, found };
      };

      const ensureStep = (steps: ThinkingItem[], stepId: string, initial: ThinkingItem) => {
        const result = updateStepInTree(steps, stepId, (step) => step);
        if (result.found) return result.steps;
        return [...steps, initial];
      };

      const ensureChildStep = (
        steps: ThinkingItem[],
        parentId: string,
        childId: string,
        initial: ThinkingItem
      ) => {
        const result = updateStepInTree(steps, parentId, (step) => {
          const children = step.children ? [...step.children] : [];
          const updatedChildren = ensureStep(children, childId, initial);
          return { ...step, children: updatedChildren };
        });
        if (result.found) return result.steps;
        return ensureStep(steps, parentId, {
          id: parentId,
          label: "处理中",
          status: "thinking",
          children: [initial],
        });
      };

      const handleChatMessageEvent = (data: any) => {
        const eventType = data.event;
        if (!eventType) return;

        if (eventType === "message_start") {
          if (typeof data.conversation_id === "string" && data.conversation_id) {
            const nextConversationId = data.conversation_id;
            const isDifferent = conversationIdRef.current !== nextConversationId;
            if (isDifferent) {
              conversationIdRef.current = nextConversationId;
              setCurrentConversationId(nextConversationId);
            }
            if (isDifferent || !hasSyncedConversationIdRef.current) {
              onConversationIdChange?.(nextConversationId);
              hasSyncedConversationIdRef.current = true;
            }
          }
          currentMessageId = data.message_id || currentMessageId || createLocalMessageId();
          ensureAssistantMessage(currentMessageId);
          return;
        }

        if (eventType === "node_start") {
          if (!data.node_id) return;
          const msgId = data.message_id || currentMessageId || createLocalMessageId();
          currentMessageId = msgId;
          const labelHidden = Boolean(data.label_hidden);
          const flattenChildren = Boolean(data.flatten_children);
          updateAssistantMessage(msgId, (msg) => {
            const steps = msg.steps ? [...msg.steps] : [];
            const nextSteps = ensureStep(steps, data.node_id, {
              id: data.node_id,
              label: labelHidden ? "" : (data.title || "处理中"),
              status: "thinking",
              label_hidden: labelHidden,
              flatten_children: flattenChildren,
            });
            return { ...msg, steps: nextSteps };
          });
          return;
        }

        if (eventType === "node_end") {
          if (!data.node_id) return;
          const msgId = data.message_id || currentMessageId;
          if (!msgId) return;
          const labelHidden = Boolean(data.label_hidden);
          const flattenChildren = Boolean(data.flatten_children);
          updateAssistantMessage(msgId, (msg) => {
            const steps = msg.steps ? [...msg.steps] : [];
            const result = updateStepInTree(steps, data.node_id, (step) => ({
              ...step,
              status: "done",
              label: (step.label_hidden || labelHidden) ? "" : (data.title || step.label),
              label_hidden: step.label_hidden || labelHidden,
              flatten_children: step.flatten_children || flattenChildren,
            }));
            return { ...msg, steps: result.steps };
          });
          return;
        }

        if (eventType === "node_delta") {
          if (!data.node_id) return;
          const msgId = data.message_id || currentMessageId || createLocalMessageId();
          currentMessageId = msgId;
          const deltaValue = data.delta;
          if (typeof deltaValue === "string") {
            const labelHidden = Boolean(data.label_hidden);
            const flattenChildren = Boolean(data.flatten_children);
            let handledNested = false;
            const trimmed = deltaValue.trim();
            if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
              try {
                const nested = JSON.parse(trimmed);
                const nestedEvent = nested?.event;
                const childId = nested?.node_id;
                if (nestedEvent && childId) {
                  updateAssistantMessage(msgId, (msg) => {
                    const steps = msg.steps ? [...msg.steps] : [];
                    if (nestedEvent === "node_start") {
                      const nextSteps = ensureChildStep(steps, data.node_id, childId, {
                        id: childId,
                        label: nested.title || "处理中",
                        status: "thinking",
                        label_hidden: Boolean(nested.label_hidden),
                        flatten_children: Boolean(nested.flatten_children),
                      });
                      return { ...msg, steps: nextSteps };
                    }
                    if (nestedEvent === "node_end") {
                      const nextSteps = ensureChildStep(steps, data.node_id, childId, {
                        id: childId,
                        label: nested.title || "处理中",
                        status: "done",
                        label_hidden: Boolean(nested.label_hidden),
                        flatten_children: Boolean(nested.flatten_children),
                      });
                      const finalSteps = updateStepInTree(nextSteps, childId, (step) => ({
                        ...step,
                        status: "done",
                        label: nested.title || step.label,
                        label_hidden: step.label_hidden || Boolean(nested.label_hidden),
                        flatten_children: step.flatten_children || Boolean(nested.flatten_children),
                      })).steps;
                      return { ...msg, steps: finalSteps };
                    }
                    if (nestedEvent === "node_delta") {
                      const deltaText = typeof nested.delta === "string" ? nested.delta : "";
                      const lineId = typeof nested.line_id === "string" ? nested.line_id : undefined;
                      const nextSteps = ensureChildStep(steps, data.node_id, childId, {
                        id: childId,
                        label: nested.title || "处理中",
                        status: "thinking",
                        label_hidden: Boolean(nested.label_hidden),
                        flatten_children: Boolean(nested.flatten_children),
                      });
                      const updatedSteps = updateStepInTree(nextSteps, childId, (step) => {
                        if (!deltaText) {
                          return step;
                        }
                        const existingLines = step.detailLines
                          ? [...step.detailLines]
                          : step.detail
                          ? step.detail.split("\n").map((content) => ({ content }))
                          : [];
                        if (lineId) {
                          const idx = existingLines.findIndex((line) => line.lineId === lineId);
                          if (idx >= 0) {
                            existingLines[idx] = {
                              lineId,
                              content: `${existingLines[idx]?.content ?? ""}${deltaText}`,
                            };
                          } else {
                            existingLines.push({ lineId, content: deltaText });
                          }
                        } else {
                          existingLines.push({ content: deltaText });
                        }
                        return {
                          ...step,
                          detailLines: existingLines,
                          label_hidden: step.label_hidden || Boolean(nested.label_hidden),
                          flatten_children: step.flatten_children || Boolean(nested.flatten_children),
                        };
                      });
                      return { ...msg, steps: updatedSteps.steps };
                    }
                    return { ...msg, steps };
                  });
                  handledNested = true;
                }
              } catch (error) {
                console.error("parse nested node delta failed", error);
              }
            }
            if (!handledNested) {
              updateAssistantMessage(msgId, (msg) => {
                const steps = msg.steps ? [...msg.steps] : [];
                const nextSteps = ensureStep(steps, data.node_id, {
                  id: data.node_id,
                  label: labelHidden ? "" : (data.title || "处理中"),
                  status: "thinking",
                  label_hidden: labelHidden,
                  flatten_children: flattenChildren,
                });
                const result = updateStepInTree(nextSteps, data.node_id, (step) => ({
                  ...step,
                  label_hidden: step.label_hidden || labelHidden,
                  flatten_children: step.flatten_children || flattenChildren,
                  ...(typeof data.line_id === "string" && data.line_id
                    ? (() => {
                        const existingLines = step.detailLines
                          ? [...step.detailLines]
                          : step.detail
                          ? step.detail.split("\n").map((content) => ({ content }))
                          : [];
                        const idx = existingLines.findIndex((line) => line.lineId === data.line_id);
                        if (idx >= 0) {
                          existingLines[idx] = {
                            lineId: data.line_id,
                            content: `${existingLines[idx]?.content ?? ""}${deltaValue}`,
                          };
                        } else {
                          existingLines.push({ lineId: data.line_id, content: deltaValue });
                        }
                        return { detailLines: existingLines };
                      })()
                    : { detail: `${step.detail || ""}${deltaValue}` }),
                }));
                return { ...msg, steps: result.steps };
              });
            }
          }
          return;
        }

        if (eventType === "content_delta") {
          const msgId = data.message_id || currentMessageId || createLocalMessageId();
          currentMessageId = msgId;
          const delta = typeof data.delta === "string" ? data.delta : "";
          updateAssistantMessage(msgId, (msg) => ({
            ...msg,
            content: `${msg.content || ""}${delta}`,
            time: msg.time || new Date().toLocaleTimeString("zh-CN", { hour12: false }),
          }));
        }

        if (eventType === "references") {
          const msgId = data.message_id || currentMessageId || createLocalMessageId();
          currentMessageId = msgId;
          const references = Array.isArray(data.references) ? data.references : [];
          updateAssistantMessage(msgId, (msg) => ({
            ...msg,
            references,
          }));
        }

        if (eventType === "suggestions") {
          const msgId = data.message_id || currentMessageId || createLocalMessageId();
          currentMessageId = msgId;
          let suggestions: SuggestionItem[] = [];
          if (typeof data.delta === "string" && data.delta.trim()) {
            try {
              const parsed = JSON.parse(data.delta);
              if (Array.isArray(parsed?.suggestions)) {
                suggestions = parsed.suggestions
                  .filter(
                    (item: any) =>
                      item && typeof item.tool_id === "string" && typeof item.name === "string"
                  )
                  .map((item: any) => ({
                    tool_id: item.tool_id,
                    name: item.name,
                  }));
              }
            } catch (error) {
              console.error("parse suggestions failed", error);
            }
          }
          updateAssistantMessage(msgId, (msg) => ({
            ...msg,
            suggestions,
          }));
        }
      };

      let currentEventType = "";
      const currentDataLines: string[] = [];
      const flushEvent = () => {
        if (!currentDataLines.length && !currentEventType) return;
        const dataStr = currentDataLines.join("\n");
        if (currentEventType === "chat_message") {
          try {
            const data = JSON.parse(dataStr);
            handleChatMessageEvent(data);
          } catch (error) {
            console.error("parse chat_message failed", error);
          }
        }
        if (currentEventType === "system") {
          try {
            const data = JSON.parse(dataStr);
            if (data?.event === "title_generation" && data?.title) {
              titleBufferRef.current = `${titleBufferRef.current}${String(data.title || "")}`;
              onTitleChange?.(titleBufferRef.current);
            }
            if (data?.event === "title_generation_done") {
              if (typeof data?.title === "string" && data.title) {
                titleBufferRef.current = data.title;
                onTitleChange?.(titleBufferRef.current);
              }
              if (!hasTriggeredHistoryRefreshRef.current) {
                hasTriggeredHistoryRefreshRef.current = true;
                triggerHistoryRefreshOnce();
              }
            }
            if (data?.event === "artifacts") {
              const rawFile = data?.artifacts?.rawFile;
              if (rawFile && Array.isArray(rawFile.serverFiles)) {
                setComplaintFiles(
                  rawFile.serverFiles
                    .filter((file: any) => file && file.url)
                    .map((file: any) => ({
                      order: Number(file.order) || 0,
                      name: String(file.name || ""),
                      url: String(file.url || ""),
                    }))
                );
                const nextIndex = Number(rawFile.selectedFileIndex) || 0;
                setComplaintSelectedIndex(nextIndex);
              }
              const rawTextFiles = data?.artifacts?.rawText?.files;
              if (Array.isArray(rawTextFiles)) {
                setComplaintRawTextFiles(
                  rawTextFiles
                    .filter((file: any) => file && Array.isArray(file.pages))
                    .map((file: any) => ({
                      order: Number(file.order) || 0,
                      name: String(file.name || ""),
                      pages: file.pages
                        .filter((page: any) => page && typeof page.content === "string")
                        .map((page: any) => ({
                          page: Number(page.page) || 0,
                          content: String(page.content || ""),
                        })),
                    }))
                );
              }
            }
          } catch (error) {
            console.error("parse system event failed", error);
          }
        }
        if (currentEventType === "DONE") {
          if (!hasTriggeredHistoryRefreshRef.current) {
            hasTriggeredHistoryRefreshRef.current = true;
            triggerHistoryRefreshOnce();
          }
          return true;
        }
        currentEventType = "";
        currentDataLines.length = 0;
        return false;
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const rawLine of lines) {
          const line = rawLine.replace(/\r$/, "");
          if (line === "") {
            const shouldStop = flushEvent();
            if (shouldStop) {
              buffer = "";
              break;
            }
            continue;
          }
          if (line.startsWith("event:")) {
            currentEventType = line.slice(6).trim();
            continue;
          }
          if (line.startsWith("data:")) {
            currentDataLines.push(line.slice(5).trim());
          }
        }
      }
    } catch (error: any) {
      if (error?.name === "AbortError") {
        return;
      }
      console.error(error);
      toast.error("发送失败");
    } finally {
      setSelectedFiles([]);
      setIsSending(false);
      abortControllerRef.current = null;
    }
  };

  const handleSend = async () => {
    await sendMessage();
  };

  const handleStop = () => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setIsSending(false);
  };

  const handleInputKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter") return;
    if (event.shiftKey) return;
    event.preventDefault();
    handleSend();
  };

  return (
    <div className="h-full w-full">
      <div className="flex h-full w-full flex-col rounded-[28px] pt-3">
        <div
          ref={layoutContainerRef}
          className={`relative mx-auto flex h-full w-full ${showWorkbench ? "max-w-[1905px] flex-row items-stretch gap-0 px-6" : "max-w-[1063px] flex-col gap-6 px-6"}`}
        >
          {tracePanelEnabled && !isWorkbenchVisible && (
            <button
              type="button"
              className="absolute right-8 top-6 z-10 inline-flex items-center gap-2 rounded-full border border-[#E5EAF0] bg-white px-4 py-2 text-xs font-semibold text-[#5B6B7C] shadow-sm transition-colors hover:border-[#D3DAE3] hover:text-[#2E3A48] disabled:cursor-not-allowed disabled:opacity-60"
              onClick={() => {
                setActiveWorkbenchPanel("trace");
                setIsWorkbenchVisible(true);
              }}
              disabled={!currentConversationId}
            >
              <i className="fa-solid fa-bug"></i>
              调试
            </button>
          )}
          <div className="flex min-h-0 flex-1 min-w-[550px]">
            <Conversation
              assistantRole={assistantRole}
              footerTagText={footerTagText}
              openingMessage={openingMessage}
              showOpening={showOpening}
              chatMessages={chatMessages}
              scrollContainerRef={scrollContainerRef}
              onScroll={handleScroll}
              inputValue={inputValue}
              placeholderText={placeholderText}
              onInputValueChange={setInputValue}
              onPromptClick={(prompt) => {
                setInputValue(prompt);
                textareaRef.current?.focus();
              }}
              onSuggestionClick={(suggestion) => {
                sendMessage({ textOverride: suggestion.name, suggestion });
              }}
              onReferenceClick={(ref) => {
                if (workbenchVariant === "expert") {
                  const sourceId = ref?.sourceId || ref?.anchorId || ref?.id;
                  const anchorId = ref?.anchorId || ref?.sourceId || ref?.id;
                  const citation: ParsedCitation = {
                    id: ref?.id || sourceId || "",
                    normalizedId: ref?.id || sourceId || "",
                    anchorId: anchorId || "",
                    displayLabel: ref?.displayLabel || ref?.id || "引用",
                    title: ref?.title || "",
                    clause: ref?.clause,
                    description: ref?.description || ref?.summary,
                    sourceId,
                  };
                  setActiveExpertCitation(citation);
                  setActiveWorkbenchPanel("reference");
                  setIsWorkbenchVisible(true);
                }
              }}
              onAttachmentClick={(attachment) => {
                if (workbenchVariant === "complaint") {
                  if (attachment && complaintFiles.length > 0) {
                    const index = complaintFiles.findIndex((file) => file.name === attachment.name);
                    if (index >= 0) {
                      setComplaintSelectedIndex(index);
                    }
                  }
                  setActiveWorkbenchPanel("original");
                  setIsWorkbenchVisible(true);
                }
              }}
              onUploadClick={handleUploadClick}
              onFocusInput={() => textareaRef.current?.focus()}
              textareaRef={textareaRef}
              selectedFiles={selectedFiles}
              onRemoveFile={(index) => {
                setSelectedFiles((prev) => {
                  const next = prev.filter((_, idx) => idx !== index);
                  setInputValue(buildAttachmentPrompt(next));
                  return next;
                });
              }}
              referenceBlocks={referenceBlocks}
              onRemoveReferenceBlock={handleRemoveReferenceBlock}
              inputActions={actionButtons}
              selectedActionIds={selectedActionIds}
              onToggleAction={handleToggleAction}
              showUpload={showUpload && Boolean(uploadConfig)}
              uploadAccept={uploadConfig?.accept}
              onFileChange={handleFileChange}
              fileInputRef={fileInputRef}
              canSend={canSend}
              isSending={isSending}
              onSend={handleSend}
              onStop={handleStop}
              onInputKeyDown={handleInputKeyDown}
            />
          </div>
          {showWorkbench ? (
            <div className="flex items-stretch px-[5px]">
              <div
                className="relative flex w-3 cursor-col-resize select-none items-stretch"
                role="separator"
                aria-orientation="vertical"
                onMouseDown={(event) => {
                  setIsResizing(true);
                  hasManualResizeRef.current = true;
                  resizeStartXRef.current = event.clientX;
                  resizeStartWidthRef.current = workbenchWidth ?? 0;
                }}
              >
                <div className="mx-auto h-full w-px bg-[#E5EAF0]" />
              </div>
            </div>
          ) : null}
            {showWorkbench ? (
              <div className="flex-none min-w-[320px]" style={{ width: workbenchWidth ?? undefined }}>
                <Workbench
                  workbench={workbench}
                  isVisible={showWorkbench}
                  activePanelId={activeWorkbenchPanel}
                  onPanelChange={setActiveWorkbenchPanel}
                  complaintFiles={complaintFiles}
                  complaintSelectedIndex={complaintSelectedIndex}
                  onComplaintSelectIndex={setComplaintSelectedIndex}
                  complaintRawTextFiles={complaintRawTextFiles}
                  onAskFromRawText={handleAskFromRawText}
                  expertCitation={activeExpertCitation}
                  onClose={() => setIsWorkbenchVisible(false)}
                  expertConversationId={currentConversationId}
                  expertTraceEnabled={tracePanelEnabled}
                />
              </div>
            ) : null}
        </div>
      </div>
    </div>
  );
}
