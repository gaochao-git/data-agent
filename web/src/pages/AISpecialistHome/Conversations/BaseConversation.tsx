import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { visit } from "unist-util-visit";
import type { Root, Text } from "mdast";
import styles from "./BaseConversation.module.css";
import { ConversationComposer } from "../components/ConversationComposer";
import type {
  AgentId,
  AgentTab,
  AttachmentInfo,
  ConversationHistory,
  Message,
  ToolTaskUpdate,
  ParsedCitation,
} from "../types";

export type SuggestionPill = {
  id: string;
  label: string;
  content: string;
};

export type ConversationHighlight = {
  title: string;
  description?: string;
  targetAgentId?: AgentId;
  icon?: string;
};

export type BaseConversationProps = {
  agent: AgentTab;
  messages: Message[];
  inputValue: string;
  isNewConversation: boolean;
  activeHistory: ConversationHistory | null;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onInputChange: (value: string) => void;
  onSuggestionSelect: (content: string) => void;
  introDescription: string;
  composerPlaceholder?: string;
  followUpPlaceholder?: string;
  suggestionPills: SuggestionPill[];
  highlights: ConversationHighlight[];
  renderBeforeComposer?: ReactNode;
  renderAfterComposer?: ReactNode;
  composerFooterStart?: ReactNode;
  showSuggestionPills?: boolean;
  showHighlights?: boolean;
  showNewConversationComposer?: boolean;
  showComposerActions?: boolean;
  onHighlightSelect?: (targetAgentId: AgentId) => void;
  onManualSubmit?: (content: string) => Promise<void> | void;
  isStreaming?: boolean;
  onCitationSelect?: (citation: ParsedCitation) => void;
  isLeftSidebarCollapsed?: boolean;
  /** 是否为嵌入模式（隐藏内部标题栏） */
  isEmbedded?: boolean;
};

export type AgentConversationProps =
  Pick<
    BaseConversationProps,
    | "agent"
    | "messages"
    | "inputValue"
    | "isNewConversation"
    | "activeHistory"
    | "onSubmit"
    | "onInputChange"
    | "onSuggestionSelect"
    | "showSuggestionPills"
    | "isStreaming"
    | "onCitationSelect"
    | "isLeftSidebarCollapsed"
    | "isEmbedded"
  > & {
    onAttachmentStateChange?: (
      hasAttachment: boolean,
      info?: AttachmentInfo
    ) => void;
    onHighlightSelect?: (targetAgentId: AgentId) => void;
    onManualSubmit?: (content: string) => Promise<void> | void;
};

type ParsedAssistantContent = {
  markdownBody: string;
  citations: ParsedCitation[];
  citationMap: Record<string, ParsedCitation>;
};

const CITATION_HEADING_MARKER = "### 引用";

const normalizeCitationId = (value: string) => value.replace(/\s+/g, "");

const extractCitationLabel = (value: string, fallbackIndex: number) => {
  const numeric = value.match(/\d+/);
  return numeric?.[0] ?? String(fallbackIndex + 1);
};

const createAnchorId = (sourceId: string, fallbackIndex: number) => {
  const normalized = normalizeCitationId(sourceId);
  return `${normalized || fallbackIndex + 1}`;
};

const parseCitationLine = (line: string, index: number): ParsedCitation | null => {
  const matches = Array.from(line.matchAll(/\[([^\]]+)\]/g)).map((match) =>
    match[1].trim(),
  );

  if (matches.length < 6) {
    return null;
  }

  const [kind, title, clause, description, sourceId, citeIdRaw] = matches;
  const normalizedId = normalizeCitationId(citeIdRaw) || `ref${index + 1}`;
  const baseAnchorId = createAnchorId(sourceId, index);
  const anchorId = [baseAnchorId, normalizedId, index + 1].filter(Boolean).join("__");
  const displayLabel = extractCitationLabel(citeIdRaw, index);
  const href = sourceId?.startsWith("http") ? sourceId : undefined;

  return {
    id: citeIdRaw,
    normalizedId,
    anchorId,
    displayLabel,
    title,
    clause: clause || undefined,
    description: description || undefined,
    sourceId: sourceId || undefined,
    href,
    kind,
  };
};

const parseAssistantMessageContent = (content: string): ParsedAssistantContent => {
  const citations: ParsedCitation[] = [];
  const fenceRegex = /```link([\s\S]*?)```/g;
  let fenceMatch: RegExpExecArray | null;
  let firstFenceIndex = -1;

  while ((fenceMatch = fenceRegex.exec(content)) !== null) {
    if (firstFenceIndex === -1 && typeof fenceMatch.index === "number") {
      firstFenceIndex = fenceMatch.index;
    }

    const block = fenceMatch[1].trim();
    if (!block) continue;

    block
      .split(/\n+/)
      .map((line) => line.trim())
      .filter(Boolean)
      .forEach((line) => {
        const citation = parseCitationLine(line, citations.length);
        if (citation) {
          citations.push(citation);
        }
      });
  }

  if (!citations.length) {
    return {
      markdownBody: content,
      citations: [],
      citationMap: {},
    };
  }

  let markdownBody: string;
  const headingIndex = content.indexOf(CITATION_HEADING_MARKER);
  if (headingIndex !== -1) {
    markdownBody = content.slice(0, headingIndex).trimEnd() || content;
  } else if (firstFenceIndex !== -1) {
    markdownBody = content.slice(0, firstFenceIndex).trimEnd() || content;
  } else {
    markdownBody = content;
  }

  const citationMap = citations.reduce<Record<string, ParsedCitation>>((acc, citation) => {
    acc[citation.normalizedId] = citation;
    return acc;
  }, {});

  return {
    markdownBody,
    citations,
    citationMap,
  };
};

const createCitationRemarkPlugin = (citationMap: Record<string, ParsedCitation>) => {
  const plugin = () => (tree: Root) => {
    visit(tree, "text", (node, index, parent) => {
      if (!parent || typeof index !== "number") {
        return;
      }

      const textNode = node as Text;
      const value = textNode.value;
      const matcher = /\[(引用\s*\d+)\]/g;
      let match: RegExpExecArray | null;
      let lastIndex = 0;
      const newNodes: Array<Text | any> = [];

      while ((match = matcher.exec(value)) !== null) {
        const [fullMatch, citeIdRaw] = match;
        const normalized = normalizeCitationId(citeIdRaw);
        const citation = citationMap[normalized];

        if (match.index > lastIndex) {
          newNodes.push({
            type: "text",
            value: value.slice(lastIndex, match.index),
          });
        }

        if (citation) {
          newNodes.push({
            type: "link",
            url: `#${citation.anchorId}`,
            data: {
              hProperties: {
                className: "ai-citation-link",
                "data-citation-id": citation.anchorId,
                "data-citation-label": citation.displayLabel,
              },
            },
            children: [{ type: "text", value: citation.displayLabel }],
          });
        } else {
          newNodes.push({
            type: "text",
            value: fullMatch,
          });
        }

        lastIndex = match.index + fullMatch.length;
      }

      if (!newNodes.length) {
        return;
      }

      if (lastIndex < value.length) {
        newNodes.push({
          type: "text",
          value: value.slice(lastIndex),
        });
      }

      parent.children.splice(index, 1, ...newNodes);
      return index + newNodes.length;
    });
  };

  return plugin;
};

const markdownComponents: Components = {
  a: ({ node, children, ...props }) => {
    const elementNode = node as { properties?: Record<string, unknown> } | undefined;
    const citationId = elementNode?.properties?.["data-citation-id"];
    const citationLabel = elementNode?.properties?.["data-citation-label"] as
      | string
      | undefined;
    const isCitationLink = Boolean(citationId);

    if (isCitationLink) {
      return (
        <a
          {...props}
          className={`ml-1 inline-flex h-5 min-w-[1.2rem] items-center justify-center rounded-full bg-gray-200 px-1 text-[11px] font-semibold text-gray-600 no-underline align-text-top transition-colors hover:bg-gray-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-200 ${props.className ?? ""}`}
          aria-label={citationLabel ? `跳转到引用 ${citationLabel}` : "跳转到引用"}
        >
          {children}
        </a>
      );
    }

    return <a {...props}>{children}</a>;
  },
};

export function BaseConversation({
  agent,
  messages,
  inputValue,
  isNewConversation,
  activeHistory,
  onSubmit,
  onInputChange,
  onSuggestionSelect,
  introDescription,
  composerPlaceholder = "输入支付结算问题，例如：惩戒期间账户发生非柜面交易。",
  followUpPlaceholder = "继续提问，支持上传附件、引用法规等多种方式",
  suggestionPills,
  highlights,
  renderBeforeComposer,
  renderAfterComposer,
  composerFooterStart,
  showSuggestionPills = true,
  showHighlights = true,
  showNewConversationComposer = true,
  showComposerActions = true,
  onHighlightSelect,
  onManualSubmit: _onManualSubmit,
  isStreaming = false,
  onCitationSelect,
  isLeftSidebarCollapsed = false,
  isEmbedded = false,
}: BaseConversationProps) {
  const hasPreComposer = Boolean(renderBeforeComposer);
  const hasPostComposer = Boolean(renderAfterComposer);
  const hasComposerFooterStart = Boolean(composerFooterStart);
  const shouldRenderStandalonePreComposer =
    hasPreComposer && !showNewConversationComposer;
  const contentWidthClass = "max-w-5xl"; // 调整此类名可控制内容区域宽度
  const assistantBubbleWidthClass = "max-w-[640px]";
  const userBubbleWidthClass = "max-w-[640px]";
  const [expandedTaskMap, setExpandedTaskMap] = useState<Record<string, boolean>>(
    {},
  );
  const [expandedNodeMap, setExpandedNodeMap] = useState<Record<string, boolean>>(
    {},
  );
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const autoScrollRef = useRef(true);
  const scrollTickingRef = useRef(false);
  const latestAssistantMessageId = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const candidate = messages[index];
      if (candidate?.role === "assistant") {
        return candidate.id;
      }
    }
    return null;
  }, [messages]);
  const conversationTitle =
    activeHistory?.title?.trim() || agent.label || "当前会话";

  const scrollToBottom = useCallback(() => {
    const node = scrollContainerRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, []);

  const handleScroll = useCallback(() => {
    const node = scrollContainerRef.current;
    if (!node || scrollTickingRef.current) {
      return;
    }

    scrollTickingRef.current = true;
    requestAnimationFrame(() => {
      const distanceToBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
      autoScrollRef.current = distanceToBottom <= 40;
      scrollTickingRef.current = false;
    });
  }, []);

  const attachScrollContainer = useCallback(
    (node: HTMLDivElement | null) => {
      if (scrollContainerRef.current) {
        scrollContainerRef.current.removeEventListener("scroll", handleScroll);
      }

      if (node) {
        node.addEventListener("scroll", handleScroll);
      }

      scrollContainerRef.current = node;
    },
    [handleScroll],
  );

  useEffect(() => {
    if (isNewConversation) {
      return;
    }

    autoScrollRef.current = true;
    const frame = requestAnimationFrame(() => scrollToBottom());
    return () => cancelAnimationFrame(frame);
  }, [isNewConversation, scrollToBottom]);

  useEffect(() => {
    if (isNewConversation || !autoScrollRef.current) {
      return;
    }

    const frame = requestAnimationFrame(() => scrollToBottom());
    return () => cancelAnimationFrame(frame);
  }, [messages, isStreaming, isNewConversation, scrollToBottom]);

  const hasInputValue = inputValue.trim().length > 0;

  const submitForm = (form?: HTMLFormElement | null) => {
    if (!form) {
      return;
    }
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
      return;
    }
    const submitButton = form.querySelector<HTMLButtonElement>('button[type="submit"]');
    submitButton?.click();
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    if (!hasInputValue || isStreaming) {
      return;
    }
    submitForm(event.currentTarget.form);
  };

  const renderComposer = (placement: "hero" | "followup") => (
    <ConversationComposer
      placement={placement}
      composerPlaceholder={composerPlaceholder}
      followUpPlaceholder={followUpPlaceholder}
      renderBeforeComposer={renderBeforeComposer}
      renderAfterComposer={renderAfterComposer}
      composerFooterStart={composerFooterStart}
      showComposerActions={showComposerActions}
      hasPreComposer={hasPreComposer}
      hasPostComposer={hasPostComposer}
      hasComposerFooterStart={hasComposerFooterStart}
      onSubmit={onSubmit}
      inputValue={inputValue}
      onInputChange={onInputChange}
      onKeyDown={handleComposerKeyDown}
      hasInputValue={hasInputValue}
      isStreaming={isStreaming}
    />
  );

  const shouldShowHeroComposer = isNewConversation && showNewConversationComposer;
  const shouldShowFollowupComposer = !shouldShowHeroComposer;

  const toggleTaskVisibility = (messageId: string) => {
    setExpandedTaskMap((prev) => {
      const current = prev[messageId] ?? true;
      return {
        ...prev,
        [messageId]: !current,
      };
    });
  };

  const toggleNodeVisibility = (nodeKey: string, defaultExpanded: boolean) => {
    setExpandedNodeMap((prev) => {
      const current = prev[nodeKey] ?? defaultExpanded;
      return {
        ...prev,
        [nodeKey]: !current,
      };
    });
  };

  const resolveNodeExpanded = (nodeKey: string, defaultExpanded: boolean) =>
    expandedNodeMap[nodeKey] ?? defaultExpanded;

  const createNodeKey = (messageId: string, path: string[]) =>
    `${messageId}-${path.join("::")}`;
  const truncate = (str: string, max = 50) =>
    str.length > max ? str.slice(0, max) + "..." : str;
  return (
    <section className="flex-1 min-h-0 flex flex-col bg-gray-50">
      {/* 内部标题栏 - 嵌入模式下隐藏 */}
      {!isNewConversation && !isEmbedded && (
        <header
          className={`sticky top-0 z-10 w-full border-b border-gray-100 bg-white/90 py-3 backdrop-blur ${
            isLeftSidebarCollapsed ? "px-20" : "px-20"
          }`}
        >
          <div className="flex flex-col gap-1">
            <span className="truncate text-lg font-semibold text-gray-900">
              {conversationTitle}
            </span>
          </div>
        </header>
      )}
      <div className={`flex-1 min-h-0 flex flex-col w-full mx-auto ${contentWidthClass}`}>
        {isNewConversation ? (
          <div className="flex-1 flex flex-col items-center justify-center px-10">
            <div className="text-center max-w-2xl">
              <h1 className="mt-6 text-2xl font-semibold text-gray-900">{agent.label}</h1>
              <p className="mt-3 text-gray-500">{introDescription}</p>
            </div>

            <div className="">
              {shouldShowHeroComposer
                ? renderComposer("hero")
                : shouldRenderStandalonePreComposer && (
                    <div className="">{renderBeforeComposer}</div>
                  )}

            {showSuggestionPills && suggestionPills.length > 0 && (
              <div className="mt-6 flex flex-wrap items-center gap-3">
                {suggestionPills.map((pill) => (
                  <button
                    key={pill.id}
                    type="button"
                    className={`px-4 py-2 rounded-full border text-sm transition-colors ${
                      inputValue === pill.content
                        ? "border-blue-500 bg-blue-50 text-blue-600"
                        : "border-gray-200 bg-white text-gray-600 hover:border-blue-400"
                    }`}
                    onClick={() => onSuggestionSelect(pill.content)}
                  >
                    <span className="flex items-center space-x-2">
                      <i className="fa-solid fa-sparkles text-xs text-blue-400"></i>
                      <span className="font-medium">{pill.label}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {showHighlights && highlights.length > 0 && (
            <div className="mt-16 w-full max-w-4xl">
              <div className="flex flex-col md:flex-row md:justify-center gap-3">
                {highlights.map((highlight) => {
                  const isNavigable = Boolean(
                    highlight.targetAgentId && onHighlightSelect
                  );
                  const iconClass = highlight.icon ?? "fa-solid fa-robot";

                  if (isNavigable && highlight.targetAgentId) {
                    return (
                      <button
                        key={highlight.title}
                        type="button"
                        onClick={() =>
                          onHighlightSelect?.(highlight.targetAgentId!)
                        }
                        className="group inline-flex flex-col items-start rounded-2xl border border-blue-200 bg-white px-4 py-3 text-left text-blue-600 shadow-sm transition-all hover:border-blue-300 hover:bg-blue-50"
                      >
                          <span className="inline-flex items-center space-x-2 text-sm font-semibold">
                            <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-blue-50 text-blue-500">
                              <i className={iconClass}></i>
                            </span>
                            <span>{highlight.title}</span>
                            <i className="fa-solid fa-arrow-right-long text-xs transition-transform group-hover:translate-x-0.5"></i>
                          </span>
                          {highlight.description && (
                            <p className="mt-2 text-xs text-gray-500 leading-relaxed">
                              {highlight.description}
                            </p>
                          )}
                        </button>
                      );
                  }

                    return (
                      <div
                        key={highlight.title}
                        className="inline-flex flex-col items-start rounded-2xl border border-gray-200 bg-white px-4 py-3 text-left text-gray-700 shadow-sm"
                      >
                        <span className="inline-flex items-center space-x-2 text-sm font-semibold">
                          <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-blue-50 text-blue-500">
                            <i className={iconClass}></i>
                          </span>
                          <span>{highlight.title}</span>
                        </span>
                        {highlight.description && (
                          <p className="mt-2 text-xs text-gray-500 leading-relaxed">
                            {highlight.description}
                          </p>
                        )}
                      </div>
                    );
                })}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="flex-1 min-h-0 flex flex-col">
          <div
            ref={attachScrollContainer}
            className="flex-1 overflow-y-auto w-full max-w-[820px] mx-auto px-6 py-8 space-y-6"
          >
            {messages.map((message) => {
              const isAssistant = message.role === "assistant";
              const trimmedContent = message.content.trim();
              const showMessageBubble = !isAssistant || trimmedContent.length > 0;
              const isTaskExpanded = expandedTaskMap[message.id] ?? true;
              const nodeTraces = message.toolTasks ?? [];
              const summarizeNodeHeader = (node: ToolTaskUpdate) => {
                const baseTitle = node.title || node.nodeType || "节点";
                if (!node.completed) {
                  return baseTitle;
                }
                if (!node.status || node.status === "completed") {
                  return baseTitle;
                }
                return `${baseTitle}（${node.status}）`;
              };
              const hasNodeTraces = nodeTraces.length > 0;
              const latestTaskDescription = hasNodeTraces
                ? summarizeNodeHeader(nodeTraces[nodeTraces.length - 1])
                : null;
              const isLatestAssistantMessage =
                isAssistant && message.id === latestAssistantMessageId;
              const hasAssistantContent = isAssistant && trimmedContent.length > 0;
              const isThinking = isAssistant && isLatestAssistantMessage && isStreaming;
              const timelineLabel = isTaskExpanded
                ? isThinking
                  ? "正在思考..."
                  : "思考"
                : hasAssistantContent
                  ? "思考"
                  : truncate(latestTaskDescription ?? "正在思考...");
              const assistantContent = isAssistant
                ? parseAssistantMessageContent(message.content)
                : null;
              const assistantRemarkPlugins = isAssistant
                ? [
                    remarkGfm,
                    ...(assistantContent && assistantContent.citations.length > 0
                      ? [createCitationRemarkPlugin(assistantContent.citationMap)]
                      : []),
                  ]
                : [remarkGfm];

              const resolveLatestUpdateText = (
                node?: ToolTaskUpdate,
              ): string | null => {
                if (!node) {
                  return null;
                }
                for (let index = node.updates.length - 1; index >= 0; index -= 1) {
                  const candidate = node.updates[index]?.content;
                  if (typeof candidate === "string" && candidate.trim().length > 0) {
                    return candidate.trim();
                  }
                }
                if (node.children && node.children.length > 0) {
                  for (let index = node.children.length - 1; index >= 0; index -= 1) {
                    const nested = resolveLatestUpdateText(node.children[index]);
                    if (nested) {
                      return nested;
                    }
                  }
                }
                return null;
              };

              const renderTimelineNode = (
                node: ToolTaskUpdate,
                path: string[],
                ancestorActive: boolean,
              ): ReactNode => {
                const nodeKey = createNodeKey(message.id, path);
                const defaultExpandedForNode = !node.completed;
                const nodeExpanded = resolveNodeExpanded(nodeKey, defaultExpandedForNode);
                const updates = node.updates.filter(
                  (update) =>
                    typeof update.content === "string" && update.content.trim().length > 0,
                );
                const childNodes = node.children ?? [];
                const canExpand =
                  updates.length > 0 || childNodes.length > 0 || !node.completed;
                const isActiveNode =
                  ancestorActive && isLatestAssistantMessage && isStreaming && !node.completed;
                const isAgentNode = node.nodeType === "agent";
                const agentPreview = isAgentNode
                  ? resolveLatestUpdateText(node) ??
                    (node.status && node.status !== "completed"
                      ? node.status
                      : node.completed
                        ? "子 Agent 执行完成"
                        : "子 Agent 执行中")
                  : null;
                const panelClass = `${styles.nodePanel} ${
                  nodeExpanded ? styles.nodePanelExpanded : styles.nodePanelCollapsed
                }`;
                return (
                  <div key={nodeKey} className="space-y-1">
                    <button
                      type="button"
                      className="flex items-start space-x-2 text-left text-blue-500 transition-colors duration-200"
                      onClick={() => toggleNodeVisibility(nodeKey, defaultExpandedForNode)}
                    >
                      <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-blue-50">
                        <i className="fa-solid fa-circle-dot text-[8px]"></i>
                      </span>
                      <span className="flex flex-col space-y-1">
                        <span
                          className={`font-medium tracking-wide ${
                            isActiveNode ? "text-blue-600" : ""
                          }`}
                        >
                          {summarizeNodeHeader(node)}
                        </span>
                        {isAgentNode && !nodeExpanded && agentPreview ? (
                          <span className="text-blue-300">
                            {truncate(agentPreview, 80)}
                          </span>
                        ) : null}
                      </span>
                      {canExpand ? (
                        <i
                          className={`fa-solid ${
                            nodeExpanded ? "fa-chevron-up" : "fa-chevron-down"
                          } text-[8px] opacity-70 transition-transform duration-200`}
                        ></i>
                      ) : null}
                    </button>
                    <div className={`ml-6 text-blue-400 ${panelClass}`}>
                      {nodeExpanded ? (
                        <>
                          {updates.length > 0 ? (
                            <div className="space-y-1">
                              {updates.map((update, index) => {
                                const isActiveUpdate =
                                  isActiveNode && index === updates.length - 1;
                                return (
                                  <div
                                    key={update.id ?? `${nodeKey}-update-${index}`}
                                    className={`flex items-start space-x-2 ${styles.taskRow} ${
                                      isActiveUpdate ? styles.activeTaskRow : ""
                                    }`}
                                  >
                                    <span
                                      className={`mt-1 inline-flex h-1 w-1 flex-none rounded-full bg-blue-300 ${
                                        isActiveUpdate ? styles.activeTaskDot : ""
                                      }`}
                                    ></span>
                                    <span
                                      className={`${styles.taskText} ${
                                        isActiveUpdate ? styles.activeTaskText : ""
                                      }`}
                                    >
                                      {update.content}
                                    </span>
                                  </div>
                                );
                              })}
                            </div>
                          ) : childNodes.length === 0 ? (
                            <div className="text-blue-300">暂无详细记录</div>
                          ) : null}
                          {childNodes.length > 0 ? (
                            <div className="mt-2 space-y-2 border-l border-blue-100 pl-3">
                              {isAgentNode ? (
                                <div className="uppercase tracking-[0.2em] text-blue-300">
                                  子 Agent 轨迹
                                </div>
                              ) : null}
                              {childNodes.map((child, childIndex) =>
                                renderTimelineNode(
                                  child,
                                  [...path, child.nodeId || `child-${childIndex}`],
                                  isActiveNode,
                                ),
                              )}
                            </div>
                          ) : isAgentNode ? (
                            <div className="text-blue-300">子 Agent 暂无更多记录</div>
                          ) : null}
                        </>
                      ) : null}
                    </div>
                  </div>
                );
              };

              return (
                <div key={message.id} className="flex flex-col space-y-2">
                  {isAssistant && (
                    <div className="space-y-2 text-sm leading-6">
                      <button
                        type="button"
                        className="flex items-center space-x-2 text-blue-500"
                        onClick={() => toggleTaskVisibility(message.id)}
                        disabled={!hasNodeTraces}
                      >
                        <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-blue-50">
                          <i className="fa-solid fa-person-running text-xs"></i>
                        </span>
                      <span className="font-medium tracking-wider text-left">
                        {timelineLabel}
                      </span>
                      {hasNodeTraces ? (
                        <i
                            className={`fa-solid ${
                              isTaskExpanded ? "fa-chevron-up" : "fa-chevron-down"
                            } text-[10px] opacity-80`}
                          ></i>
                        ) : null}
                      </button>
                      {hasNodeTraces && (
                        <div
                          className={`ml-8 text-blue-400 ${styles.timelinePanel} ${
                            isTaskExpanded
                              ? styles.timelinePanelExpanded
                              : styles.timelinePanelCollapsed
                          }`}
                        >
                          <div className="space-y-2">
                            {nodeTraces.map((node, nodeIndex) =>
                              renderTimelineNode(
                                node,
                                [node.nodeId || `node-${nodeIndex}`],
                                isLatestAssistantMessage && isStreaming,
                              ),
                            )}
                          </div>
                        </div>
                      )}
                  </div>
                )}
                {showMessageBubble && (
                  <div
                    className={`rounded-2xl px-6 py-4 text-sm leading-7 shadow-sm ${
                      isAssistant
                        ? `bg-white border border-blue-100 text-gray-700 ${assistantBubbleWidthClass}`
                        : `bg-blue-600 text-white ml-auto ${userBubbleWidthClass}`
                    }`}
                  >
                    {isAssistant ? (
                      <ReactMarkdown
                        remarkPlugins={assistantRemarkPlugins}
                        components={markdownComponents}
                        className="markdown-content space-y-3 [&>p]:m-0 [&>p+*]:mt-3 [&>ul]:list-disc [&>ul]:pl-5 [&>ol]:list-decimal [&>ol]:pl-5 [&>li]:leading-6 [&>blockquote]:border-l-4 [&>blockquote]:border-blue-100 [&>blockquote]:bg-blue-50 [&>blockquote]:px-3 [&>blockquote]:py-2 [&>blockquote]:text-sm [&>table]:w-full [&>table]:text-left [&>th]:border [&>th]:border-blue-100 [&>td]:border [&>td]:border-blue-100 [&>th]:bg-blue-50 [&>code]:font-mono [&>code]:text-xs [&>code]:bg-slate-100 [&>code]:px-1.5 [&>code]:py-0.5 [&>pre]:overflow-x-auto [&>pre]:rounded-xl [&>pre]:bg-slate-900 [&>pre]:p-4 [&>pre>code]:text-xs [&_a]:text-blue-600 [&_a]:underline"
                      >
                        {(assistantContent?.markdownBody ?? message.content) || ""}
                      </ReactMarkdown>
                    ) : (
                      <span className="whitespace-pre-wrap">{message.content}</span>
                    )}
                    {assistantContent && assistantContent.citations.length > 0 && (
                      <div className="mt-4 border-t border-dashed border-gray-200 pt-4">
                      <div className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                          检索到的法律条款
                        </div>
                        <div className="mt-3 space-y-3">
                          {assistantContent.citations.map((citation) => {
                            const isExternal = Boolean(citation.href);
                            const handleCitationClick = () => {
                              if (!isExternal) {
                                onCitationSelect?.(citation);
                              }
                            };

                            if (isExternal) {
                              return (
                                <a
                                  key={citation.anchorId}
                                  href={citation.href}
                                  target="_blank"
                                  rel="noreferrer noopener"
                                  id={citation.anchorId}
                                  className="block rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-gray-700 transition-colors hover:border-blue-300 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-200 scroll-mt-24"
                                >
                                  <div className="flex items-center gap-2 text-xs font-semibold text-gray-500">
                                    <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-gray-200 text-gray-700">
                                      {citation.displayLabel}
                                    </span>
                                    <span>{citation.title}</span>
                                  </div>
                                  {citation.clause && (
                                    <div className="mt-1 text-sm font-semibold text-gray-700">
                                      {citation.clause}
                                    </div>
                                  )}
                                  {citation.description && (
                                    <p className="mt-1 text-xs leading-relaxed text-gray-600">
                                      {citation.description}
                                    </p>
                                  )}
                                </a>
                              );
                            }

                            return (
                              <button
                                key={citation.anchorId}
                                type="button"
                                onClick={handleCitationClick}
                                id={citation.anchorId}
                                className="block w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-left text-gray-700 transition-colors hover:border-blue-300 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-200 scroll-mt-24"
                              >
                                <div className="flex items-center gap-2 text-xs font-semibold text-gray-500">
                                  <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-gray-200 text-gray-700">
                                    {citation.displayLabel}
                                  </span>
                                  <span>{citation.title}</span>
                                </div>
                                {citation.clause && (
                                  <div className="mt-1 text-sm font-semibold text-gray-700">
                                    {citation.clause}
                                  </div>
                                )}
                                {citation.description && (
                                  <p
                                    className={`mt-1 text-xs leading-relaxed text-gray-600 ${styles.citationDescription}`}
                                    title={citation.description}
                                  >
                                    {citation.description}
                                  </p>
                                )}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}
                </div>
              );
            })}
          </div>
          {shouldShowFollowupComposer && (
            <div className="px-10 py-6">
              {renderComposer("followup")}
              <div className="mt-4 text-[13px] text-center">
                <div>
                  <p className="text-gray-400 dark:text-gray-500 font-bold">
                    内容由AI生成，仅作为辅助参考，请认真核对！
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
      </div>
    </section>
  );
}
