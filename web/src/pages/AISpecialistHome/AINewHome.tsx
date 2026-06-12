import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router-dom";
import type { ComponentType, LazyExoticComponent } from "react";
import type { AgentConversationProps } from "./Conversations/BaseConversation";
import PolicyPreviewPanel from "./RightPanels/PolicyPreviewPanel";
import { streamChatCompletion } from "@/lib/chatStream";
import { API_BASE } from "@/lib/config";
import type {
  AgentId,
  AgentTab,
  AttachmentInfo,
  ConversationHistory,
  Message,
  ToolTaskUpdate,
  ConversationSummary,
  ParsedCitation,
} from "./types";
import LeftSidebar from "./LeftSidebar";
import { useConversationList } from "./hooks/useConversationList";
import { useSidebarCollapse } from "./hooks/useSidebarCollapse";

const agentTabs: AgentTab[] = [
  {
    id: "case-knowledge",
    label: "AI专家系统",
    description: "",
    icon: "fa-robot",
  },
];

const conversationLoaders: Record<
  AgentId,
  LazyExoticComponent<ComponentType<AgentConversationProps>>
> = {
  "case-knowledge": lazy(
    () => import("./Conversations/CaseKnowledgeConversation")
  ),
};

const highlightNavigationMap: Partial<Record<AgentId, string>> = {
  "case-knowledge": "/case-intelligence",
};

const MIN_CHAT_WIDTH = 320;
const MIN_RIGHT_PANEL_WIDTH = 320;
const RIGHT_PANEL_TRANSITION_DURATION = 250;
const RIGHT_PANEL_RESIZER_WIDTH = 4;

const createInitialAttachmentState = (): Record<AgentId, boolean> => ({
  "case-knowledge": false,
});

const formatDateLabel = (value: string): string => {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value.slice(0, 10);
  }
  const year = parsed.getFullYear();
  const month = `${parsed.getMonth() + 1}`.padStart(2, "0");
  const day = `${parsed.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const shorten = (value: string, max = 60): string => {
  if (!value) {
    return "";
  }
  return value.length > max ? `${value.slice(0, max)}…` : value;
};

interface AINewHomePageProps {
  /** 是否为内嵌模式（禁用路由跳转） */
  isEmbedded?: boolean;
  /** 内嵌模式下，会话创建/更新时的回调 */
  onHistoryRefresh?: () => void;
  /** 内嵌模式下，会话ID变化时的回调 */
  onConversationIdChange?: (conversationId: string) => void;
  /** 内嵌模式下，初始会话ID（用于加载历史会话） */
  initialConversationId?: string;
  /** 内嵌模式下，标题变化时的回调 */
  onTitleChange?: (title: string) => void;
}

export default function AINewHomePage({ isEmbedded = false, onHistoryRefresh, onConversationIdChange, initialConversationId, onTitleChange }: AINewHomePageProps) {
  const layoutContainerRef = useRef<HTMLDivElement | null>(null);
  const {
    isLeftCollapsed,
    setIsLeftCollapsed,
    leftSidebarPreferenceRef,
    leftSidebarStateRef,
    collapseSidebarButtonRef,
    expandSidebarButtonRef,
    expandButtonTop,
    toggleLeftSidebar,
  } = useSidebarCollapse(layoutContainerRef);
  const citationAutoCollapseRef = useRef(false);
  const citationOriginalLeftStateRef = useRef<boolean>(isLeftCollapsed);
  const [isRightVisible, setIsRightVisible] = useState(false);
  const [isRightPanelRendered, setIsRightPanelRendered] = useState(false);
  const [activeAgentId, setActiveAgentId] = useState(agentTabs[0].id);
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [rightPanelWidth, setRightPanelWidth] = useState(0);
  const [isResizingRightPanel, setIsResizingRightPanel] = useState(false);
  const [activeCitation, setActiveCitation] = useState<ParsedCitation | null>(null);
  const [agentAttachmentStates, setAgentAttachmentStates] = useState<
    Record<AgentId, boolean>
  >(() => createInitialAttachmentState());
  const [conversationIdByAgent, setConversationIdByAgent] = useState<
    Record<AgentId, string | null>
  >({
    "case-knowledge": null,
  });
  const {
    conversationSummaries,
    isLoadingConversations,
    conversationListError,
    refreshConversationList,
    upsertConversationSummary,
  } = useConversationList();
  const titleGenerationBufferRef = useRef<Record<string, string>>({});
  const resizeStartXRef = useRef(0);
  const resizeStartWidthRef = useRef(0);
  const messageStreamControllerRef = useRef<AbortController | null>(null);
  const messagesRef = useRef<Message[]>([]);
  const pendingConversationIdRef = useRef<string | null>(null);
  const conversationListWarmupRef = useRef<string | null>(null);
  const rightPanelCloseTimerRef = useRef<number | null>(null);
  const lastConversationIdRef = useRef<string | null>(null);
  const SelectedConversation = conversationLoaders[activeAgentId];

  const navigate = useNavigate();
  const { conversationId: routeConversationId } = useParams<{
    conversationId?: string;
  }>();
  // 内嵌模式下优先使用 props 传入的 initialConversationId，否则使用 URL 参数
  const normalizedRouteConversationId =
    (isEmbedded && initialConversationId)
      ? initialConversationId.trim()
      : (routeConversationId && typeof routeConversationId === "string"
          ? routeConversationId.trim()
          : "");
  const currentConversationId =
    conversationIdByAgent[activeAgentId] ?? null;

  const restoreLeftSidebarAfterCitation = useCallback(() => {
    if (!citationAutoCollapseRef.current) {
      return;
    }
    citationAutoCollapseRef.current = false;
    setIsLeftCollapsed(citationOriginalLeftStateRef.current);
  }, [setIsLeftCollapsed]);

  const closeRightPanel = useCallback(
    ({ clearCitation = true }: { clearCitation?: boolean } = {}) => {
      restoreLeftSidebarAfterCitation();
      setIsRightVisible(false);
      if (clearCitation) {
        setActiveCitation(null);
      }
      if (typeof window === "undefined") {
        setIsRightPanelRendered(false);
        return;
      }
      if (rightPanelCloseTimerRef.current) {
        window.clearTimeout(rightPanelCloseTimerRef.current);
      }
      rightPanelCloseTimerRef.current = window.setTimeout(() => {
        setIsRightPanelRendered(false);
        rightPanelCloseTimerRef.current = null;
      }, RIGHT_PANEL_TRANSITION_DURATION);
    },
    [restoreLeftSidebarAfterCitation]
  );

  const getAvailableContentWidth = useCallback(() => {
    const viewportWidth =
      typeof window !== "undefined"
        ? window.innerWidth
        : MIN_CHAT_WIDTH + MIN_RIGHT_PANEL_WIDTH;
    const containerWidth = layoutContainerRef.current?.clientWidth ?? viewportWidth;
    const boundedWidth = Math.min(containerWidth, viewportWidth);
    const leftSidebarWidth = isLeftCollapsed ? 0 : 320;
    const rawAvailable = boundedWidth - leftSidebarWidth;
    return Math.max(rawAvailable, 0);
  }, [isLeftCollapsed]);

  const clampRightPanelWidth = useCallback(
    (targetWidth: number) => {
      const availableWidth = getAvailableContentWidth();
      const maxAllowed =
        availableWidth - MIN_CHAT_WIDTH - RIGHT_PANEL_RESIZER_WIDTH;
      const cappedMax = Math.max(maxAllowed, 0);
      const minWidth = Math.min(MIN_RIGHT_PANEL_WIDTH, cappedMax);
      const upperWidth = Math.max(minWidth, cappedMax);
      return Math.min(Math.max(targetWidth, minWidth), upperWidth);
    },
    [getAvailableContentWidth]
  );

  const getInitialRightPanelWidth = useCallback(() => {
    const halfWidth = getAvailableContentWidth() / 2;
    return clampRightPanelWidth(halfWidth);
  }, [clampRightPanelWidth, getAvailableContentWidth]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    if (lastConversationIdRef.current !== normalizedRouteConversationId) {
      lastConversationIdRef.current = normalizedRouteConversationId ?? null;
      closeRightPanel();
    }
  }, [normalizedRouteConversationId, closeRightPanel]);

  useEffect(() => {
    if (
      currentConversationId &&
      pendingConversationIdRef.current === currentConversationId
    ) {
      pendingConversationIdRef.current = null;
    }
  }, [currentConversationId]);

  useEffect(() => {
    return () => {
      messageStreamControllerRef.current?.abort();
      if (rightPanelCloseTimerRef.current && typeof window !== "undefined") {
        window.clearTimeout(rightPanelCloseTimerRef.current);
        rightPanelCloseTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    void refreshConversationList({ signal: controller.signal });

    return () => {
      controller.abort();
    };
  }, [refreshConversationList]);

  useEffect(() => {
    if (!isRightPanelRendered) {
      return;
    }
    setRightPanelWidth((current) => clampRightPanelWidth(current));
  }, [isLeftCollapsed, isRightPanelRendered, clampRightPanelWidth]);

  const abortExistingStream = useCallback(() => {
    if (messageStreamControllerRef.current) {
      messageStreamControllerRef.current.abort();
      messageStreamControllerRef.current = null;
    }
  }, []);

  const openRightPanel = useCallback(
    (options?: { collapseLeftTemporarily?: boolean }) => {
      if (typeof window !== "undefined" && rightPanelCloseTimerRef.current) {
        window.clearTimeout(rightPanelCloseTimerRef.current);
        rightPanelCloseTimerRef.current = null;
      }
      const shouldCollapseTemporarily = Boolean(options?.collapseLeftTemporarily);
      let nextLeftCollapsed = leftSidebarPreferenceRef.current;
      if (shouldCollapseTemporarily) {
        if (!leftSidebarStateRef.current) {
          citationOriginalLeftStateRef.current = leftSidebarStateRef.current;
          citationAutoCollapseRef.current = true;
        }
        nextLeftCollapsed = true;
      } else {
        citationAutoCollapseRef.current = false;
        nextLeftCollapsed = leftSidebarPreferenceRef.current;
      }
      setIsLeftCollapsed(nextLeftCollapsed);
      setRightPanelWidth((current) =>
        clampRightPanelWidth(current || getInitialRightPanelWidth())
      );
      setIsRightPanelRendered(true);
      if (typeof window !== "undefined") {
        window.requestAnimationFrame(() => {
          setIsRightVisible(true);
        });
      } else {
        setIsRightVisible(true);
      }
    },
    [leftSidebarPreferenceRef, getInitialRightPanelWidth, clampRightPanelWidth]
  );

  const resetAttachmentForAgent = useCallback(
    (agentId: AgentId) => {
      setAgentAttachmentStates((prev) => {
        if (!prev[agentId]) {
          return prev;
        }
        return { ...prev, [agentId]: false };
      });
    },
    [setAgentAttachmentStates]
  );

  const resetTitleGenerationBuffer = useCallback((conversationId: string | null | undefined) => {
    if (!conversationId) {
      return;
    }
    titleGenerationBufferRef.current[conversationId] = "";
  }, []);

  const appendTitleGenerationFragment = useCallback(
    (conversationId: string, fragment: unknown) => {
      if (!conversationId) {
        return "";
      }
      const safeFragment =
        typeof fragment === "string"
          ? fragment
          : fragment == null
            ? ""
            : String(fragment);
      const current = titleGenerationBufferRef.current[conversationId] ?? "";
      const next = `${current}${safeFragment}`;
      titleGenerationBufferRef.current[conversationId] = next;
      return next;
    },
    []
  );

  useEffect(() => {
    const currentId = normalizedRouteConversationId;

    if (!currentId) {
      if (isStreaming || pendingConversationIdRef.current) {
        return;
      }
      pendingConversationIdRef.current = null;
      abortExistingStream();
      setIsStreaming(false);
      setActiveHistoryId(null);
      if (messagesRef.current.length > 0) {
        setMessages([]);
      }
      closeRightPanel();
      resetAttachmentForAgent(activeAgentId);
      setConversationIdByAgent((prev) => {
        const current = prev[activeAgentId];
        if (current === null) {
          return prev;
        }
        return {
          ...prev,
          [activeAgentId]: null,
        };
      });
      return;
    }

    const matchesCurrent = currentId === currentConversationId;
    const matchesPending = currentId === pendingConversationIdRef.current;
    const hasLocalMessages = messagesRef.current.length > 0;

    setActiveHistoryId((prev) => (prev === currentId ? prev : currentId));
    setConversationIdByAgent((prev) => {
      const current = prev[activeAgentId];
      if (current === currentId) {
        return prev;
      }
      return {
        ...prev,
        [activeAgentId]: currentId,
      };
    });
    if (matchesPending && isStreaming) {
      return;
    }

    if (!matchesCurrent && !matchesPending) {
      abortExistingStream();
      setIsStreaming(false);
    }

    const shouldFetchDetail =
      (!matchesCurrent && !matchesPending) ||
      (!isStreaming && !hasLocalMessages);

    if (!shouldFetchDetail) {
      return;
    }

    const controller = new AbortController();
    setMessages([]);
    resetAttachmentForAgent(activeAgentId);

    const loadConversationDetail = async () => {
      try {
        const response = await fetch(
          `${API_BASE}/api/ai_chat/conversations/${encodeURIComponent(currentId)}`,
          {
            credentials: "include",
            signal: controller.signal,
          }
        );
        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || `请求失败，状态码 ${response.status}`);
        }

        const data: {
          messages?: Array<{
            id?: string;
            role?: string;
            content?: unknown;
            timestamp?: string;
            thinking?: unknown;
          }>;
        } = await response.json();

        const convertThinkingToToolTasks = (
          thinkingValue: unknown,
        ): ToolTaskUpdate[] => {
          if (!Array.isArray(thinkingValue)) {
            return [];
          }
          const normalizeString = (value: unknown) =>
            typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
          const resolveNodeId = (value: unknown, fallback: string) =>
            normalizeString(value) ?? fallback;
          const tryParseJson = (value: string): unknown => {
            try {
              return JSON.parse(value);
            } catch (_error) {
              return null;
            }
          };
          const sortedRecords = thinkingValue
            .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
            .map((item) => item as Record<string, unknown>)
            .sort(
              (a, b) =>
                (typeof a["created"] === "number" ? a["created"] : 0) -
                (typeof b["created"] === "number" ? b["created"] : 0),
            );
          const nodeMap = new Map<string, ToolTaskUpdate>();
          const nodeChildrenMap = new Map<string, Map<string, ToolTaskUpdate>>();
          const nodeLineUpdateMap = new Map<string, Map<string, number>>();
          let childAutoIncrement = 0;
          const ensureNode = (nodeId: string) => {
            let node = nodeMap.get(nodeId);
            if (!node) {
              node = {
                nodeId,
                completed: false,
                updates: [],
              };
              nodeMap.set(nodeId, node);
            }
            return node;
          };
          const ensureChildNode = (parentNodeId: string, childNodeId: string) => {
            const parent = ensureNode(parentNodeId);
            let children = nodeChildrenMap.get(parentNodeId);
            if (!children) {
              children = new Map();
              nodeChildrenMap.set(parentNodeId, children);
            }
            let child = children.get(childNodeId);
            if (!child) {
              child = {
                nodeId: childNodeId,
                completed: false,
                updates: [],
              };
            children.set(childNodeId, child);
            parent.children = Array.from(children.values());
          }
          return child;
        };
        const normalizeLineId = (value: unknown) =>
          typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
        const appendMergedUpdate = (
          targetNodeId: string,
          node: ToolTaskUpdate,
          content: string,
          createdAt: number,
          lineId?: string,
          ephemeral?: boolean,
        ) => {
          if (!content) {
            return;
          }
          const normalizedLineId = normalizeLineId(lineId);
          if (normalizedLineId) {
            let lineMap = nodeLineUpdateMap.get(targetNodeId);
            if (!lineMap) {
              lineMap = new Map();
              nodeLineUpdateMap.set(targetNodeId, lineMap);
            }
            const existingIndex = lineMap.get(normalizedLineId);
            if (existingIndex != null && existingIndex >= 0 && existingIndex < node.updates.length) {
              const existing = node.updates[existingIndex];
              node.updates[existingIndex] = {
                ...existing,
                content: `${existing.content ?? ""}${content}`,
                createdAt:
                  typeof existing.createdAt === "number" ? existing.createdAt : createdAt,
                lineId: normalizedLineId,
                ephemeral: ephemeral ?? existing.ephemeral,
              };
              return;
            }
            lineMap.set(normalizedLineId, node.updates.length);
          }
          node.updates.push({
            id: `${targetNodeId}-hist-${node.updates.length + 1}`,
            content,
            createdAt,
            lineId: normalizedLineId,
            ephemeral,
          });
        };
        const formatContent = (value: unknown): string | undefined => {
          if (typeof value === "string") {
            const trimmed = value.trim();
            return trimmed.length > 0 ? trimmed : undefined;
          }
            if (value == null) {
              return undefined;
            }
            if (typeof value === "object") {
              try {
                const serialized = JSON.stringify(value);
                return serialized === "{}" ? undefined : serialized;
              } catch (_error) {
                return undefined;
              }
            }
            const text = String(value).trim();
            return text.length > 0 ? text : undefined;
          };
          const handleStructuredChildPayload = (
            parentNodeId: string,
            payload: unknown,
            defaultCreatedAt: number,
          ): boolean => {
            const processValue = (value: unknown): boolean => {
              if (!value) {
                return false;
              }
              if (Array.isArray(value)) {
                let handledArray = false;
                value.forEach((entry) => {
                  handledArray = processValue(entry) || handledArray;
                });
                return handledArray;
              }
              if (typeof value === "string") {
                const trimmed = value.trim();
                if (!trimmed) {
                  return false;
                }
                const parsed = tryParseJson(trimmed);
                if (!parsed) {
                  return false;
                }
                return processValue(parsed);
              }
              if (typeof value !== "object") {
                return false;
              }
              const nested = value as Record<string, unknown>;
              const eventName = normalizeString(nested.event)?.toLowerCase();
              if (!eventName || !eventName.startsWith("node_")) {
                return false;
              }
              const tryCreateScopedId = () => {
                const messageId = normalizeString(nested.message_id);
                const responseId = normalizeString(nested.response_id);
                const scoped = messageId ?? responseId;
                if (!scoped) {
                  return undefined;
                }
                const indexValue = nested.index;
                let suffix = "";
                if (
                  typeof indexValue === "number" ||
                  (typeof indexValue === "string" && indexValue.trim().length > 0)
                ) {
                  suffix = `-${String(indexValue).trim()}`;
                }
                return `${parentNodeId}-child-${scoped}${suffix}`;
              };
              const childNodeId =
                resolveNodeId(nested.node_id, "") ||
                tryCreateScopedId() ||
                `${parentNodeId}-child-${++childAutoIncrement}`;
              const childNode = ensureChildNode(parentNodeId, childNodeId);
              const nestedTitle = normalizeString(nested.title);
              const nestedType =
                normalizeString(nested.type) ?? normalizeString(nested.node_type);
              const nestedStatus = normalizeString(nested.status);
              const nestedCreatedAt =
                typeof nested.created === "number" ? nested.created : defaultCreatedAt;
              const nestedLineId = normalizeLineId(nested.line_id);

              if (eventName === "node_start") {
                if (nestedTitle) {
                  childNode.title = nestedTitle;
                }
                if (nestedType) {
                  childNode.nodeType = nestedType;
                }
                childNode.completed = false;
                return true;
              }

              if (eventName === "node_delta") {
                const rawDelta = nested.delta ?? nested.content;
                const handledNested =
                  rawDelta == null
                    ? false
                    : handleStructuredChildPayload(childNodeId, rawDelta, nestedCreatedAt);
                if (!handledNested) {
                  const deltaText = formatContent(rawDelta);
                  if (deltaText) {
                    appendMergedUpdate(
                      childNodeId,
                      childNode,
                      deltaText,
                      nestedCreatedAt,
                      nestedLineId,
                      nested.ephemeral === true,
                    );
                  }
                }
                return true;
              }

              if (eventName === "node_end") {
                if (nestedTitle) {
                  childNode.title = nestedTitle;
                }
                if (nestedStatus) {
                  childNode.status = nestedStatus;
                }
                childNode.completed = true;
                return true;
              }

              return false;
            };

            return processValue(payload);
          };

          sortedRecords.forEach((record, index) => {
            const eventName =
              typeof record["event"] === "string" ? record["event"].trim() : "";
            if (!eventName.startsWith("node_")) {
              return;
            }
            const nodeIdRaw = record["node_id"];
            const nodeId =
              (typeof nodeIdRaw === "string" && nodeIdRaw.trim().length > 0
                ? nodeIdRaw.trim()
                : undefined) ?? `node-${index}`;
            const node = ensureNode(nodeId);
            const nodeType =
              typeof record["node_type"] === "string" && record["node_type"].trim().length > 0
                ? record["node_type"].trim()
                : undefined;
            const title =
              typeof record["title"] === "string" && record["title"].trim().length > 0
                ? record["title"].trim()
                : undefined;
            const status =
              typeof record["status"] === "string" && record["status"].trim().length > 0
                ? record["status"].trim()
                : undefined;
            const lineId = normalizeLineId(record["line_id"]);
            const rawContentValue = record["content"];
            const rawDeltaValue = record["delta"];
            const content =
              typeof rawContentValue === "string" && rawContentValue.trim().length > 0
                ? rawContentValue.trim()
                : typeof rawDeltaValue === "string" && rawDeltaValue.trim().length > 0
                ? rawDeltaValue.trim()
                : undefined;
            const createdAt =
              typeof record["created"] === "number" ? record["created"] : Date.now();

            switch (eventName) {
              case "node_start": {
                node.title = title ?? node.title;
                node.nodeType = nodeType ?? node.nodeType;
                node.completed = false;
                break;
              }
              case "node_delta": {
              if (record["ephemeral"] === true) {
                return;
              }
              const handledChildEvent =
                handleStructuredChildPayload(nodeId, rawContentValue, createdAt) ||
                handleStructuredChildPayload(nodeId, rawDeltaValue, createdAt);
              if (handledChildEvent || !content) {
                return;
              }
              appendMergedUpdate(nodeId, node, content, createdAt, lineId);
              break;
            }
            case "node_end": {
              node.status = status ?? node.status;
              node.completed = true;
                if (title) {
                  node.title = title;
                }
                break;
              }
              default:
                break;
            }
          });

          return Array.from(nodeMap.values()).filter(
            (node) =>
              node.title ||
              node.nodeType ||
              node.updates.length > 0 ||
              node.children?.length ||
              !node.completed,
          );
        };

        const normalizedMessages: Message[] = Array.isArray(data?.messages)
          ? data.messages
              .map((item, index) => {
                if (!item || typeof item !== "object") {
                  return null;
                }
                const roleValue =
                  typeof item.role === "string" ? item.role.trim() : "";
                if (roleValue !== "user" && roleValue !== "assistant") {
                  return null;
                }
                const rawContent = item.content;
                const content =
                  typeof rawContent === "string"
                    ? rawContent
                    : rawContent == null
                    ? ""
                    : String(rawContent);
                const idValue =
                  typeof item.id === "string" && item.id.trim()
                    ? item.id.trim()
                    : `${currentId}-${index}`;
                const timestampValue =
                  typeof item.timestamp === "string" ? item.timestamp : "";
                const toolTasks = convertThinkingToToolTasks(
                  (item as { thinking?: unknown }).thinking,
                );
                return {
                  id: idValue,
                  role: roleValue,
                  content,
                  timestamp: timestampValue,
                  toolTasks: toolTasks.length > 0 ? toolTasks : undefined,
                } satisfies Message;
              })
              .filter((item): item is Message => Boolean(item))
          : [];

        if (!controller.signal.aborted) {
          setMessages(normalizedMessages);
        }
      } catch (error) {
        if (controller.signal.aborted) {
          return;
        }
        console.error("加载会话详情失败", error);
        setMessages([
          {
            id: `${currentId}-error`,
            role: "assistant",
            content: `会话加载失败，请稍后重试。${
              error instanceof Error ? `（${error.message}）` : ""
            }`,
            timestamp: new Date().toLocaleString(),
            isPlaceholder: true,
          },
        ]);
      }
    };

    void loadConversationDetail();

    return () => {
      controller.abort();
    };
  }, [
    normalizedRouteConversationId,
    currentConversationId,
    abortExistingStream,
    activeAgentId,
    isStreaming,
    resetAttachmentForAgent,
    closeRightPanel,
    activeCitation,
  ]);

  const handleAttachmentStateChange = useCallback(
    (agentId: AgentId, hasAttachment: boolean, _info?: AttachmentInfo) => {
      abortExistingStream();
      setIsStreaming(false);
      setAgentAttachmentStates((prev) => {
        if (prev[agentId] === hasAttachment) {
          return prev;
        }
        return { ...prev, [agentId]: hasAttachment };
      });
    },
    [abortExistingStream]
  );

  const handleActiveAttachmentStateChange = useCallback(
    (hasAttachment: boolean, info?: AttachmentInfo) => {
      handleAttachmentStateChange(activeAgentId, hasAttachment, info);
    },
    [activeAgentId, handleAttachmentStateChange]
  );

  const shouldOpenRightPanelOnSubmit = useCallback(() => false, []);

  const activeAgent = useMemo(
    () => agentTabs.find((tab) => tab.id === activeAgentId) ?? agentTabs[0],
    [activeAgentId]
  );

  const activeHistory = useMemo(() => {
    if (!activeHistoryId) {
      return null;
    }
    const summary = conversationSummaries.find(
      (item) => item.id === activeHistoryId
    );
    if (!summary) {
      return null;
    }
    return {
      id: summary.id,
      title:
        shorten(
          summary.title ||
            summary.firstMessagePreview ||
            summary.latestMessagePreview ||
            summary.id,
          30
        ) ||
        `会话 ${summary.id.slice(-4)}`,
      date: formatDateLabel(summary.lastUsedAt || summary.createdAt),
      agentId: activeAgentId,
      agentLabel: activeAgent?.label ?? "",
      summary: summary.firstMessagePreview || summary.latestMessagePreview,
      messages: [],
    } satisfies ConversationHistory;
  }, [activeHistoryId, conversationSummaries, activeAgentId, activeAgent]);

  const isNewConversation = messages.length === 0 && !activeHistoryId;

  const handleInputChange = (value: string) => {
    setInputValue(value);
  };

  const handleSuggestionSelect = (content: string) => {
    setInputValue(content);
  };

  const handleCitationSelect = useCallback(
    (citation: ParsedCitation) => {
      setActiveCitation(citation);
      openRightPanel({ collapseLeftTemporarily: true });
    },
    [openRightPanel]
  );

  const handleCloseRightPanel = useCallback(() => {
    closeRightPanel();
  }, [closeRightPanel]);

  const submitMessage = useCallback(
    async (content: string, assistantReply?: string) => {
      const trimmed = content.trim();
      if (!trimmed) {
        return;
      }

      const targetAgentId = activeAgentId;
      let existingConversationId =
        conversationIdByAgent[targetAgentId] ?? null;

      // 如果没有会话ID，先调用统一接口创建会话
      if (!existingConversationId) {
        try {
          const response = await fetch(`${API_BASE}/api/conversations`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({ agent_id: "expert_agent" }),
          });
          if (response.ok) {
            const data = await response.json();
            existingConversationId = data.data?.id;
            if (existingConversationId) {
              setConversationIdByAgent((prev) => ({
                ...prev,
                [targetAgentId]: existingConversationId,
              }));
              setActiveHistoryId(existingConversationId);
              // 更新 URL
              if (isEmbedded && onConversationIdChange) {
                // 内嵌模式：通知外层更新 URL
                onConversationIdChange(existingConversationId);
              } else if (!isEmbedded) {
                // 独立模式：直接更新 URL
                navigate(`/specialist/c/${existingConversationId}`, { replace: true });
              }
            }
          }
        } catch (error) {
          console.error("创建会话失败:", error);
        }
      }
      const timestamp = new Date().toLocaleString();
      const baseId = Date.now();
      const userMessage: Message = {
        id: `user-${baseId}`,
        role: "user",
        content: trimmed,
        timestamp,
      };
      const assistantMessageId = `assistant-${baseId + 1}`;

      if (assistantReply) {
        setMessages((prev) => [
          ...prev,
          userMessage,
          {
            id: assistantMessageId,
            role: "assistant",
            content: assistantReply,
            timestamp,
            toolTasks: [],
          },
        ]);
        setInputValue("");
        if (shouldOpenRightPanelOnSubmit(targetAgentId)) {
          openRightPanel();
        }
        return;
      }

      if (isStreaming) {
        return;
      }

      const historyPayload = messagesRef.current
        .filter(
          (msg) =>
            (msg.role === "user" || msg.role === "assistant") &&
            !msg.isPlaceholder
        )
        .map((msg) => ({
          role: msg.role,
          content: msg.content,
        }));

      setMessages((prev) => [
        ...prev,
        userMessage,
        {
          id: assistantMessageId,
          role: "assistant",
          content: "",
          timestamp,
          toolTasks: [],
        },
      ]);
      setInputValue("");
      if (shouldOpenRightPanelOnSubmit(targetAgentId)) {
        openRightPanel();
      }

      abortExistingStream();
      const controller = new AbortController();
      messageStreamControllerRef.current = controller;
      setIsStreaming(true);

      let accumulated = "";
      let receivedContent = false;
      const updateAssistantMessage = (updater: (prev: Message) => Message) => {
        setMessages((prev) => {
          let found = false;
          const next = prev.map((msg) => {
            if (msg.id === assistantMessageId) {
              found = true;
              return updater(msg);
            }
            return msg;
          });
          if (!found) {
            next.push(
              updater({
                id: assistantMessageId,
                role: "assistant",
                content: "",
                timestamp,
                toolTasks: [],
              })
            );
          }
          return next;
        });
      };
      const syncAssistantMessage = (nextContent: string) => {
        updateAssistantMessage((prev) => ({
          ...prev,
          content: nextContent,
        }));
      };

      const mutateNode = (
        nodeId: string,
        mutator: (node: ToolTaskUpdate) => ToolTaskUpdate,
        initializer?: () => ToolTaskUpdate,
      ) => {
        updateAssistantMessage((prev) => {
          const nodes = [...(prev.toolTasks ?? [])];
          let index = nodes.findIndex((node) => node.nodeId === nodeId);
          if (index === -1) {
            const created =
              initializer?.() ?? {
                nodeId,
                completed: false,
                updates: [],
              };
            nodes.push(created);
            index = nodes.length - 1;
          }
          nodes[index] = mutator(nodes[index]);
          return {
            ...prev,
            toolTasks: nodes,
          };
        });
      };

      const mutateChildNode = (
        parentNodeId: string,
        childNodeId: string,
        mutator: (node: ToolTaskUpdate) => ToolTaskUpdate,
        initializer?: () => ToolTaskUpdate,
      ) => {
        const createChild = () =>
          initializer?.() ?? {
            nodeId: childNodeId,
            completed: false,
            updates: [],
          };
        mutateNode(
          parentNodeId,
          (parent) => {
            const children = [...(parent.children ?? [])];
            let childIndex = children.findIndex((child) => child.nodeId === childNodeId);
            if (childIndex === -1) {
              children.push(createChild());
              childIndex = children.length - 1;
            }
            children[childIndex] = mutator(children[childIndex]);
            return {
              ...parent,
              children,
            };
          },
          () => ({
            nodeId: parentNodeId,
            completed: false,
            updates: [],
            children: [createChild()],
          }),
        );
      };

      const ensureNodeMetadata = (
        nodeId: string,
        title?: string,
        nodeType?: string,
      ) => {
        mutateNode(
          nodeId,
          (node) => ({
            ...node,
            title: title ?? node.title,
            nodeType: nodeType ?? node.nodeType,
            completed: false,
          }),
          () => ({
            nodeId,
            title,
            nodeType,
            completed: false,
            updates: [],
          }),
        );
      };

      const ensureChildNodeMetadata = (
        parentNodeId: string,
        childNodeId: string,
        title?: string,
        nodeType?: string,
      ) => {
        mutateChildNode(
          parentNodeId,
          childNodeId,
          (node) => ({
            ...node,
            title: title ?? node.title,
            nodeType: nodeType ?? node.nodeType,
            completed: false,
          }),
          () => ({
            nodeId: childNodeId,
            title,
            nodeType,
            completed: false,
            updates: [],
          }),
        );
      };

      const mergeOrAppendUpdate = (
        updates: ToolTaskUpdate["updates"],
        nodeId: string,
        content: string,
        ephemeral?: boolean,
        lineId?: string,
        createdAt?: number,
      ) => {
        if (!content) {
          return updates;
        }
        const baseUpdates = [...updates];
        if (lineId) {
          for (let index = baseUpdates.length - 1; index >= 0; index -= 1) {
            const existing = baseUpdates[index];
            if (existing.lineId === lineId) {
              const nextContent = `${existing.content ?? ""}${content}`;
              baseUpdates[index] = {
                ...existing,
                content: nextContent,
                lineId,
                ephemeral: ephemeral ?? existing.ephemeral,
                createdAt:
                  typeof createdAt === "number"
                    ? createdAt
                    : existing.createdAt,
              };
              return baseUpdates;
            }
          }
        }
        return [
          ...baseUpdates,
          {
            id: `${nodeId}-u-${baseUpdates.length + 1}-${Date.now()}`,
            content,
            ephemeral,
            lineId,
            createdAt,
          },
        ];
      };

      const appendNodeUpdate = (
        nodeId: string,
        content: string,
        ephemeral?: boolean,
        lineId?: string,
        createdAt?: number,
      ) => {
        if (!content) {
          return;
        }
        const appendUpdate = (node: ToolTaskUpdate) => ({
          ...node,
          updates: mergeOrAppendUpdate(
            node.updates,
            nodeId,
            content,
            ephemeral,
            lineId,
            createdAt,
          ),
        });
        mutateNode(
          nodeId,
          appendUpdate,
          () => ({
            nodeId,
            completed: false,
            updates: mergeOrAppendUpdate(
              [],
              nodeId,
              content,
              ephemeral,
              lineId,
              createdAt,
            ),
          }),
        );
      };

      const appendChildNodeUpdate = (
        parentNodeId: string,
        childNodeId: string,
        content: string,
        ephemeral?: boolean,
        lineId?: string,
        createdAt?: number,
      ) => {
        if (!content) {
          return;
        }
        mutateChildNode(
          parentNodeId,
          childNodeId,
          (node) => ({
            ...node,
            updates: mergeOrAppendUpdate(
              node.updates,
              childNodeId,
              content,
              ephemeral,
              lineId,
              createdAt,
            ),
          }),
          () => ({
            nodeId: childNodeId,
            completed: false,
            updates: mergeOrAppendUpdate(
              [],
              childNodeId,
              content,
              ephemeral,
              lineId,
              createdAt,
            ),
          }),
        );
      };

      const completeNode = (nodeId: string, title?: string, status?: string) => {
        mutateNode(nodeId, (node) => ({
          ...node,
          title: title ?? node.title,
          status: status ?? node.status,
          completed: true,
        }));
      };

      const completeChildNode = (
        parentNodeId: string,
        childNodeId: string,
        title?: string,
        status?: string,
      ) => {
        mutateChildNode(parentNodeId, childNodeId, (node) => ({
          ...node,
          title: title ?? node.title,
          status: status ?? node.status,
          completed: true,
        }));
      };

      const stripEphemeralUpdates = (node: ToolTaskUpdate): ToolTaskUpdate => ({
        ...node,
        updates: node.updates.filter((update) => !update.ephemeral),
        children: node.children?.map(stripEphemeralUpdates),
      });

      const flushEphemeralToolTasks = () => {
        updateAssistantMessage((prev) => ({
          ...prev,
          toolTasks: (prev.toolTasks ?? []).map(stripEphemeralUpdates),
        }));
      };

      const resolveNodeId = (value: unknown, fallback: string) => {
        if (typeof value === "string" && value.trim().length > 0) {
          return value.trim();
        }
        return fallback;
      };

      const normalizeString = (value: unknown) =>
        typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;

      const tryHandleStructuredNodeDelta = (
        parentNodeId: string,
        deltaValue: unknown,
        lineId?: string,
        createdAtHint?: number,
      ): boolean => {
        const processValue = (value: unknown): boolean => {
          if (!value) {
            return false;
          }
          if (Array.isArray(value)) {
            let handledArray = false;
            value.forEach((entry) => {
              handledArray = processValue(entry) || handledArray;
            });
            return handledArray;
          }
          if (typeof value === "string") {
            const trimmed = value.trim();
            if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) {
              return false;
            }
            try {
              const parsed = JSON.parse(trimmed);
              return processValue(parsed);
            } catch (_error) {
              return false;
            }
          }
          if (typeof value !== "object") {
            return false;
          }
          const nested = value as Record<string, unknown>;
          const eventName = normalizeString(nested.event)?.toLowerCase();
          if (!eventName || !eventName.startsWith("node_")) {
            return false;
          }
          const nestedLineId = normalizeString(nested.line_id) ?? lineId;
          const nestedCreatedAt =
            typeof nested.created === "number" ? nested.created : createdAtHint;
          const tryCreateMessageScopedId = () => {
            const messageId = normalizeString(nested.message_id);
            const responseId = normalizeString(nested.response_id);
            const scoped = messageId ?? responseId;
            if (!scoped) {
              return undefined;
            }
            const indexValue = nested.index;
            let suffix = "";
            if (
              typeof indexValue === "number" ||
              (typeof indexValue === "string" && indexValue.trim().length > 0)
            ) {
              suffix = `-${String(indexValue).trim()}`;
            }
            return `${parentNodeId}-child-${scoped}${suffix}`;
          };

          const childNodeId =
            resolveNodeId(nested.node_id, "") ||
            tryCreateMessageScopedId() ||
            `${parentNodeId}-child-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

          if (eventName === "node_start") {
            ensureChildNodeMetadata(
              parentNodeId,
              childNodeId,
              normalizeString(nested.title),
              normalizeString(nested.type) ?? normalizeString(nested.node_type),
            );
            return true;
          }

          if (eventName === "node_delta") {
            const rawDelta = nested.delta ?? nested.content;
            const handledNested = processValue(rawDelta);
            if (!handledNested) {
              let deltaText: string | undefined;
              if (typeof rawDelta === "string" && rawDelta.trim().length > 0) {
                deltaText = rawDelta.trim();
              } else if (rawDelta != null) {
                try {
                  deltaText = JSON.stringify(rawDelta);
                } catch (_error) {
                  deltaText = String(rawDelta);
                }
              }
              if (deltaText) {
                appendChildNodeUpdate(
                  parentNodeId,
                  childNodeId,
                  deltaText,
                  nested.ephemeral === true,
                  nestedLineId,
                  nestedCreatedAt,
                );
              }
            }
            return true;
          }

          if (eventName === "node_end") {
            completeChildNode(
              parentNodeId,
              childNodeId,
              normalizeString(nested.title),
              normalizeString(nested.status),
            );
            return true;
          }

          return false;
        };

        return processValue(deltaValue);
      };

      try {
        await streamChatCompletion({
          question: trimmed,
          history: historyPayload,
          agentId: targetAgentId,
          conversationId: existingConversationId ?? undefined,
          signal: controller.signal,
          onEvent: (event) => {
            const name = event.event;
            if (name === "node_start" || name === "node_delta" || name === "node_end") {
              console.debug("[chat] 节点事件", event);
            }
            const nextConversationId =
              typeof event.conversation_id === "string"
                ? event.conversation_id
                : null;
            if (nextConversationId) {
              if (event.event === "message_start") {
                resetTitleGenerationBuffer(nextConversationId);
              }
              pendingConversationIdRef.current = nextConversationId;
              setConversationIdByAgent((prev) => {
                const current = prev[targetAgentId];
                if (current === nextConversationId) {
                  return prev;
                }
                return {
                  ...prev,
                  [targetAgentId]: nextConversationId,
                };
              });
              setActiveHistoryId((prev) =>
                prev === nextConversationId ? prev : nextConversationId
              );
              const nowIso = new Date().toISOString();
              const isNewConversation =
                !existingConversationId || existingConversationId !== nextConversationId;
              upsertConversationSummary(nextConversationId, {
                latestMessagePreview: trimmed,
                firstMessagePreview: isNewConversation
                  ? trimmed
                  : undefined,
                lastUsedAt: nowIso,
                createdAt: isNewConversation ? nowIso : undefined,
              });
              if (conversationListWarmupRef.current !== nextConversationId) {
                conversationListWarmupRef.current = nextConversationId;
                void refreshConversationList({ showLoading: false });
                // 内嵌模式下通知外部刷新历史
                if (isEmbedded && onHistoryRefresh) {
                  onHistoryRefresh();
                }
              }
              if (existingConversationId !== nextConversationId) {
                if (isEmbedded && onConversationIdChange) {
                  onConversationIdChange(nextConversationId);
                } else if (!isEmbedded) {
                  navigate(`/specialist/c/${nextConversationId}`, { replace: true });
                }
              }
            }
          },
          onNodeStart: (event) => {
            const nodeId = resolveNodeId(
              event.node_id,
              `${assistantMessageId}-node-${Date.now()}`,
            );
            const title =
              typeof event.title === "string" && event.title.trim().length > 0
                ? event.title.trim()
                : undefined;
            const nodeType =
              typeof event.type === "string" && event.type.trim().length > 0
                ? event.type.trim()
                : undefined;
            ensureNodeMetadata(nodeId, title, nodeType);
          },
          onNodeDelta: (event) => {
            const nodeId = resolveNodeId(
              event.node_id,
              `${assistantMessageId}-delta-${Date.now()}`,
            );
            const eventLineId =
              typeof event.line_id === "string" && event.line_id.trim().length > 0
                ? event.line_id.trim()
                : undefined;
            const eventCreatedAt =
              typeof event.created === "number" ? event.created : undefined;
            const appendStructuredFallback = (value: unknown) => {
              if (value == null) {
                return false;
              }
              let text: string;
              if (typeof value === "string") {
                text = value.trim();
              } else {
                try {
                  text = JSON.stringify(value);
                } catch (_error) {
                  text = String(value);
                }
              }
              if (!text) {
                return false;
              }
              appendNodeUpdate(nodeId, text, event.ephemeral === true, eventLineId, eventCreatedAt);
              return true;
            };

            if (typeof event.delta === "string") {
              const delta = event.delta.trim();
              if (!delta) {
                return;
              }
              if (
                !tryHandleStructuredNodeDelta(nodeId, delta, eventLineId, eventCreatedAt)
              ) {
                appendNodeUpdate(
                  nodeId,
                  delta,
                  event.ephemeral === true,
                  eventLineId,
                  eventCreatedAt,
                );
              }
              return;
            }

            if (Array.isArray(event.delta)) {
              let handled = false;
              for (const item of event.delta) {
                handled =
                  tryHandleStructuredNodeDelta(nodeId, item, eventLineId, eventCreatedAt) ||
                  handled;
                if (!handled && typeof item === "string" && item.trim()) {
                  appendNodeUpdate(
                    nodeId,
                    item.trim(),
                    event.ephemeral === true,
                    eventLineId,
                    eventCreatedAt,
                  );
                  handled = true;
                }
              }
              if (!handled) {
                appendStructuredFallback(event.delta);
              }
              return;
            }

            if (!tryHandleStructuredNodeDelta(nodeId, event.delta, eventLineId, eventCreatedAt)) {
              appendStructuredFallback(event.delta);
            }
          },
          onNodeEnd: (event) => {
            const nodeId = resolveNodeId(
              event.node_id,
              `${assistantMessageId}-end-${Date.now()}`,
            );
            const title =
              typeof event.title === "string" && event.title.trim().length > 0
                ? event.title.trim()
                : undefined;
            const status =
              typeof event.status === "string" && event.status.trim().length > 0
                ? event.status.trim()
                : undefined;
            completeNode(nodeId, title, status);
          },
          onTitleGeneration: (event) => {
            console.log("[专家系统] 收到标题生成事件:", event);
            const nextConversationId =
              typeof event.conversation_id === "string"
                ? event.conversation_id.trim()
                : null;
            if (!nextConversationId) {
              console.warn("[专家系统] 标题事件缺少 conversation_id");
              return;
            }
            const aggregatedTitle = appendTitleGenerationFragment(
              nextConversationId,
              typeof event.title === "string" ? event.title : "",
            ).trim();
            console.log("[专家系统] 聚合标题:", aggregatedTitle, "isEmbedded:", isEmbedded);
            if (!aggregatedTitle) {
              return;
            }
            upsertConversationSummary(nextConversationId, {
              title: aggregatedTitle,
              lastUsedAt: new Date().toISOString(),
            });
            // 内嵌模式下通知外部更新标题和刷新历史
            if (isEmbedded) {
              console.log("[专家系统] 调用 onTitleChange:", aggregatedTitle);
              onTitleChange?.(aggregatedTitle);
              onHistoryRefresh?.();
            }
          },
          onToken: (token) => {
            accumulated += token;
            if (token.trim().length > 0) {
              receivedContent = true;
            }
            syncAssistantMessage(accumulated);
          },
        });

        if (controller.signal.aborted) {
          syncAssistantMessage("生成已取消。");
          return;
        }

        if (!receivedContent) {
          syncAssistantMessage("抱歉，未能生成有效回复，请稍后再试。");
        }
      } catch (error) {
        if (controller.signal.aborted) {
          syncAssistantMessage("生成已取消。");
        } else {
          const message =
            error instanceof Error ? error.message : "生成回答失败";
          syncAssistantMessage(`抱歉，生成回答失败：${message}`);
        }
      } finally {
        flushEphemeralToolTasks();
        if (messageStreamControllerRef.current === controller) {
          messageStreamControllerRef.current = null;
          setIsStreaming(false);
        }
        void refreshConversationList({ showLoading: false });
        conversationListWarmupRef.current = null;
      }
    },
    [
      abortExistingStream,
      activeAgentId,
      conversationIdByAgent,
      isStreaming,
      navigate,
      openRightPanel,
      refreshConversationList,
      upsertConversationSummary,
      shouldOpenRightPanelOnSubmit,
      resetTitleGenerationBuffer,
      appendTitleGenerationFragment,
      isEmbedded,
      onConversationIdChange,
      onHistoryRefresh,
      onTitleChange,
    ]
  );

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submitMessage(inputValue);
  };

  const handleManualSubmit = useCallback(
    (content: string) => {
      const assistantReply =
        "已接收事实认定信息，系统将匹配法规依据并生成处罚建议，稍后可在右侧查看结果。";
      void submitMessage(content, assistantReply);
    },
    [submitMessage]
  );

  const handleSelectHistory = (history: ConversationSummary) => {
    const targetId = history.id.trim();
    if (!targetId) {
      return;
    }
    const isAlreadyActive =
      targetId === normalizedRouteConversationId &&
      targetId === activeHistoryId;

    if (isAlreadyActive) {
      if (!isRightVisible && activeCitation) {
        openRightPanel();
      }
      return;
    }

    abortExistingStream();
    setIsStreaming(false);
    setActiveHistoryId(targetId);
    setMessages([]);
    closeRightPanel();
    setConversationIdByAgent((prev) => ({
      ...prev,
      [activeAgentId]: targetId,
    }));
    resetAttachmentForAgent(activeAgentId);
    pendingConversationIdRef.current = null;
    if (!isEmbedded) {
      navigate(`/specialist/c/${targetId}`, {
        replace: normalizedRouteConversationId === targetId,
      });
    }
  };

  const handleNavigateToAgent = useCallback(
    (targetAgentId: AgentId) => {
      abortExistingStream();
      setIsStreaming(false);
      setActiveAgentId(targetAgentId);
      setActiveHistoryId(null);
      setMessages([]);
      setInputValue("");
      closeRightPanel();
      setAgentAttachmentStates(createInitialAttachmentState());
      setConversationIdByAgent((prev) => ({
        ...prev,
        [targetAgentId]: null,
      }));
      pendingConversationIdRef.current = null;
      // 不再导航到首页，保持当前页面状态
    },
    [
      abortExistingStream,
      closeRightPanel,
      setActiveAgentId,
      setActiveHistoryId,
      setMessages,
      setInputValue,
      setAgentAttachmentStates,
      setConversationIdByAgent,
    ]
  );

  const handleHighlightNavigation = useCallback(
    (targetAgentId: AgentId) => {
      const targetPath = highlightNavigationMap[targetAgentId];
      if (targetPath) {
        navigate(targetPath);
        return;
      }
      handleNavigateToAgent(targetAgentId);
    },
    [navigate, handleNavigateToAgent]
  );

  const handleStartNewConversation = useCallback(() => {
    handleNavigateToAgent("case-knowledge");
    if (!isEmbedded && normalizedRouteConversationId) {
      navigate("/");
    }
  }, [handleNavigateToAgent, isEmbedded, navigate, normalizedRouteConversationId]);

  useEffect(() => {
    if (normalizedRouteConversationId) {
      return;
    }
    handleStartNewConversation();
  }, [handleStartNewConversation, normalizedRouteConversationId]);

  const handleToggleLeftSidebar = useCallback(() => {
    citationAutoCollapseRef.current = false;
    toggleLeftSidebar();
  }, [toggleLeftSidebar]);

  const handleStartResizeRightPanel = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    resizeStartXRef.current = event.clientX;
    resizeStartWidthRef.current = rightPanelWidth;
    setIsResizingRightPanel(true);
  };

  useEffect(() => {
    if (!isResizingRightPanel) {
      return;
    }

    const handleMouseMove = (event: MouseEvent) => {
      const deltaX = event.clientX - resizeStartXRef.current;
      const nextWidth = resizeStartWidthRef.current - deltaX;
      setRightPanelWidth(clampRightPanelWidth(nextWidth));
    };

    const handleMouseUp = () => {
      setIsResizingRightPanel(false);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, [isResizingRightPanel, clampRightPanelWidth]);

  useEffect(() => {
    if (!isRightVisible || typeof window === "undefined") {
      return;
    }

    const adjustRightPanelWidth = () => {
      setRightPanelWidth((current) => clampRightPanelWidth(current));
    };

    adjustRightPanelWidth();
    window.addEventListener("resize", adjustRightPanelWidth);

    return () => {
      window.removeEventListener("resize", adjustRightPanelWidth);
    };
  }, [isRightVisible, clampRightPanelWidth]);

  const maxRightPanelWidth = clampRightPanelWidth(Number.MAX_SAFE_INTEGER);
  const minRightPanelWidth = clampRightPanelWidth(0);


  return (
    <div ref={layoutContainerRef} className={`relative flex bg-gray-100 ${isEmbedded ? "h-full" : "h-screen"}`}>
      {/* 内嵌模式下隐藏左侧边栏，使用外层统一的历史记录 */}
      {!isEmbedded && (
        <LeftSidebar
          isCollapsed={isLeftCollapsed}
          expandButtonTop={expandButtonTop}
          expandSidebarButtonRef={expandSidebarButtonRef}
          collapseSidebarButtonRef={collapseSidebarButtonRef}
          onToggleCollapse={handleToggleLeftSidebar}
          agentTabs={agentTabs}
          activeAgentId={activeAgentId}
          onStartNewConversation={handleStartNewConversation}
          isLoadingConversations={isLoadingConversations}
          conversationListError={conversationListError}
          conversationSummaries={conversationSummaries}
          activeHistoryId={activeHistoryId}
          onSelectHistory={handleSelectHistory}
          shorten={shorten}
        />
      )}

      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 flex overflow-hidden">
          <Suspense
            fallback={
              <div className="flex-1 flex items-center justify-center bg-gray-50 text-sm text-gray-400">
                <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                正在加载会话界面...
              </div>
            }
          >
            <SelectedConversation
              agent={activeAgent}
              messages={messages}
              inputValue={inputValue}
              isNewConversation={isNewConversation}
              activeHistory={activeHistory}
              onSubmit={handleSubmit}
              onInputChange={handleInputChange}
              onSuggestionSelect={handleSuggestionSelect}
              onManualSubmit={handleManualSubmit}
              onAttachmentStateChange={handleActiveAttachmentStateChange}
              onHighlightSelect={handleHighlightNavigation}
              showSuggestionPills={false}
              isStreaming={isStreaming}
              onCitationSelect={handleCitationSelect}
              isLeftSidebarCollapsed={isLeftCollapsed}
              isEmbedded={isEmbedded}
            />
          </Suspense>
          {isRightPanelRendered && (
            <div
              className={`flex overflow-hidden ${
                isRightVisible ? "opacity-100" : "opacity-0 pointer-events-none"
              }`}
              style={{
                width: isRightVisible
                  ? `${rightPanelWidth + RIGHT_PANEL_RESIZER_WIDTH}px`
                  : "0px",
                maxWidth: `${maxRightPanelWidth + RIGHT_PANEL_RESIZER_WIDTH}px`,
                minWidth: `${minRightPanelWidth + RIGHT_PANEL_RESIZER_WIDTH}px`,
                transition: isResizingRightPanel ? "none" : "width 0.3s ease",
              }}
            >
              <div
                className={`relative flex items-center justify-center w-1 cursor-col-resize select-none ${
                  isResizingRightPanel ? "bg-blue-50" : "bg-transparent"
                }`}
                onMouseDown={handleStartResizeRightPanel}
              >
                <div
                  className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors ${
                    isResizingRightPanel ? "bg-blue-400" : "bg-gray-200"
                  }`}
                ></div>
              </div>
              <aside
                className="border-l border-gray-200 bg-white flex flex-col"
                style={{
                  width: `${rightPanelWidth}px`,
                  maxWidth: `${maxRightPanelWidth}px`,
                  minWidth: `${minRightPanelWidth}px`,
                }}
              >
                <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
                  <div>
                    <p className="text-sm font-semibold text-gray-900">法规依据</p>
                    <p className="text-xs text-gray-400">
                      {activeCitation ? "查看引用原文" : "点击引用可在此查看详情"}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-gray-200 text-gray-500 transition-colors hover:text-gray-700"
                    onClick={handleCloseRightPanel}
                    aria-label="关闭侧栏"
                  >
                    <i className="fa-solid fa-xmark"></i>
                  </button>
                </div>
                <div className="flex-1 overflow-hidden">
                  <PolicyPreviewPanel citation={activeCitation} />
                </div>
              </aside>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
