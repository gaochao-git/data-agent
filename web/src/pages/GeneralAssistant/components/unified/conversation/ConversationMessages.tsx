import type { AgentOpeningMessage } from "../../../types";
import type { ChatMessage } from "../../UnifiedEmbeddedContent";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { createPortal } from "react-dom";
import uploadIcon from "@/assets/upload.png";
import chevronRightIcon from "@/assets/chevron-right.svg";
import { ThinkingBlock } from "./ThinkingBlock";

interface ConversationMessagesProps {
  assistantRole: string;
  openingMessage?: AgentOpeningMessage;
  showOpening: boolean;
  chatMessages: ChatMessage[];
  scrollContainerRef: React.RefObject<HTMLElement>;
  onScroll: () => void;
  onPromptClick: (prompt: string) => void;
  onUploadClick: () => void;
  onFocusInput: () => void;
  onReferenceClick?: (ref: ChatMessage["references"][number]) => void;
  onAttachmentClick?: (attachment: ChatMessage["attachment"]) => void;
  onSuggestionClick?: (suggestion: NonNullable<ChatMessage["suggestions"]>[number]) => void;
}

export function ConversationMessages({
  assistantRole,
  openingMessage,
  showOpening,
  chatMessages,
  scrollContainerRef,
  onScroll,
  onPromptClick,
  onUploadClick,
  onFocusInput,
  onReferenceClick,
  onAttachmentClick,
  onSuggestionClick,
}: ConversationMessagesProps) {
  const [hoveredRef, setHoveredRef] = useState<{ anchor: DOMRect; text: string } | null>(null);

  useEffect(() => {
    if (!hoveredRef) return;
    const handleMove = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target?.closest("[data-ref]")) {
        setHoveredRef(null);
      }
    };
    document.addEventListener("mousemove", handleMove);
    return () => {
      document.removeEventListener("mousemove", handleMove);
    };
  }, [hoveredRef]);
  const replaceReferenceLabels = (content: string) => {
    if (!content) return content;
    return content.replace(/【引用([0-9]+)】/g, (_match, num) => {
      return `<span data-ref="引用${num}" class="ref-inline"><i class="fa-solid fa-link"></i></span>`;
    });
  };

  const ReferenceTooltip = ({ anchor, text }: { anchor: DOMRect | null; text: string }) => {
    if (!anchor) return null;
    const style: React.CSSProperties = {
      position: "fixed",
      left: anchor.left + anchor.width / 2,
      top: anchor.bottom + 8,
      transform: "translateX(-50%)",
      zIndex: 2000,
      maxWidth: 320,
      pointerEvents: "none",
    };
    return createPortal(
      <div style={style}>
        <div className="rounded-md bg-[#1F3A56] px-2 py-1 text-[11px] text-white shadow-sm">
          {text}
        </div>
      </div>,
      document.body
    );
  };
  return (
    <main ref={scrollContainerRef} onScroll={onScroll} className="flex min-h-0 flex-1 overflow-y-auto">
      <div className="flex w-full flex-col gap-6">
        {showOpening ? (
          <div className="w-full rounded-[20px] border border-[#EEF2F5] bg-white px-6 py-5 shadow-[0_4px_25px_1px_rgba(0,0,0,0.06)]">
            <div className="flex items-center gap-2 text-[16px] font-[500] leading-[24px] text-[#1F3A56]">
              {openingMessage?.icon ? (
                <i className={`fa-solid ${openingMessage.icon} text-[16px] text-[#1F3A56]`} />
              ) : null}
              <span>{openingMessage?.title}</span>
            </div>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeRaw]}
              className="mt-1 whitespace-pre-line text-[14px] leading-[22px] text-[#8592A6]"
            >
              {openingMessage?.content || ""}
            </ReactMarkdown>
            {openingMessage?.actions?.length ? (
              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                {openingMessage.actions.map((action) => (
                  <button
                    key={action.id}
                    type="button"
                    onClick={() => {
                      if (action.kind === "upload") {
                        onUploadClick();
                        return;
                      }
                      onFocusInput();
                    }}
                    className="flex items-center justify-between rounded-[12px] bg-[#F6F8FA] px-4 py-3 text-left"
                  >
                    <div>
                      <div className="text-[14px] font-[500] text-[#1F3A56]">{action.label}</div>
                      {action.description ? (
                        <div className="mt-1 text-[12px] text-[#A3AAB5]">{action.description}</div>
                      ) : null}
                    </div>
                    <img src={chevronRightIcon} alt="" className="h-4 w-4 opacity-60" />
                  </button>
                ))}
              </div>
            ) : null}
            {openingMessage?.prompts?.length ? (
              <div className="mt-4 flex flex-col gap-2">
                <div className="text-[12px] text-[#8592A6]">可以试试以下示例:</div>
                {openingMessage.prompts.map((prompt, index) => (
                  <button
                    key={`${prompt}-${index}`}
                    type="button"
                    onClick={() => onPromptClick(prompt)}
                    className="inline-flex w-fit items-center justify-between gap-2 rounded-[12px] bg-[#F6F8FA] px-4 py-3 text-left text-[14px] text-[#1F3A56]"
                  >
                    <span>{prompt}</span>
                    <img src={chevronRightIcon} alt="" className="h-4 w-4 opacity-60" />
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
        {chatMessages.map((message) => {
          if (message.role === "user") {
            const displayText = message.text || (message.reference_blocks?.length ? "（引用原文）" : "");
            return (
              <div key={message.message_id} className="flex justify-end">
                <div className="flex max-w-[70%] flex-col items-end gap-2">
                  {message.reference_blocks?.length ? (
                    <div className="flex w-full flex-col gap-2">
                      {message.reference_blocks.map((block) => (
                        <div
                          key={block.id}
                          className="rounded-[12px] border border-[#E5EAF0] bg-white px-4 py-3 text-[14px] leading-[22px] text-[#1F3A56]"
                        >
                          <div className="mb-1 text-[12px] text-[#8592A6]">引用原文</div>
                          <div className="whitespace-pre-wrap">{block.text}</div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="rounded-2xl bg-[rgba(0,126,167,0.1)] px-5 py-3 text-[16px] font-[350] leading-[24px] text-[#1F3A56]">
                    <div>{displayText}</div>
                  </div>
                  {message.attachments && message.attachments.length > 0 ? (
                    <div className="flex flex-wrap items-center gap-2 self-start">
                      {message.attachments.map((attachment) => (
                        <button
                          key={attachment.id}
                          type="button"
                          onClick={() => onAttachmentClick?.(attachment)}
                          className="flex items-center gap-2 text-[16px] font-[350] leading-[24px] text-[#007EA7]"
                        >
                          <img src={uploadIcon} alt="附件" className="h-4 w-4" />
                          <span data-attachment-id={attachment.id}>{attachment.name}</span>
                        </button>
                      ))}
                    </div>
                  ) : message.attachment ? (
                    <button
                      type="button"
                      onClick={() => onAttachmentClick?.(message.attachment)}
                      className="flex items-center gap-2 text-[16px] font-[350] leading-[24px] text-[#007EA7] self-start"
                    >
                      <img src={uploadIcon} alt="附件" className="h-4 w-4" />
                      <span data-attachment-id={message.attachment.id}>{message.attachment.name}</span>
                    </button>
                  ) : null}
                  <div className="text-[16px] leading-[24px] text-[#A3AAB5] self-end">
                    我 {message.time}
                  </div>
                </div>
              </div>
            );
          }

          return (
            <div key={message.message_id} className="flex flex-col gap-4">
              <div className="flex max-w-[629px] flex-col gap-4">
                <div className="text-[16px] leading-[24px] text-[#A3AAB5]">
                  {assistantRole} {message.time}
                </div>
                {message.steps && message.steps.length > 0 ? <ThinkingBlock items={message.steps} /> : null}
                {message.content ? (
                  <div className="prose prose-slate max-w-none text-[16px] font-[350] leading-[30px] text-[rgba(0,0,0,0.88)] prose-headings:mb-2 prose-headings:mt-4 prose-h2:text-[16px] prose-h2:font-[350] prose-p:my-2 prose-ul:my-2 prose-li:my-1 prose-li:text-[16px] prose-li:leading-[30px]">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      rehypePlugins={[rehypeRaw]}
                      components={{
                        span: ({ children, ...props }) => {
                          const rawLabel =
                            ((props as any)["data-ref"] as string | undefined) ||
                            ((props as any)["dataRef"] as string | undefined);
                          const normalizeLabel = (value: string) =>
                            value.replace(/[０-９]/g, (digit) =>
                              String.fromCharCode(digit.charCodeAt(0) - 0xfee0)
                            );
                          const refLabel = rawLabel ? normalizeLabel(rawLabel) : "";
                          if (!refLabel) {
                            return <span {...props}>{children}</span>;
                          }
                          const reference = message.references?.find(
                            (ref) => normalizeLabel(ref.displayLabel || "") === refLabel
                          );
                          const tooltip = reference?.title || refLabel;
                          return (
                            <span
                              {...props}
                              className="ref-inline relative cursor-pointer text-[#007EA7] hover:text-[#005F86]"
                              onClick={() => reference && onReferenceClick?.(reference)}
                              role="button"
                              aria-label={refLabel}
                              onMouseEnter={(event) => {
                                setHoveredRef({
                                  anchor: event.currentTarget.getBoundingClientRect(),
                                  text: tooltip,
                                });
                              }}
                              onMouseLeave={() => setHoveredRef(null)}
                            >
                              {children}
                            </span>
                          );
                        },
                      }}
                    >
                      {replaceReferenceLabels(message.content)}
                    </ReactMarkdown>
                  </div>
                ) : null}
                {message.suggestions?.length ? (
                  <div className="rounded-[16px] bg-[#F3F5F7] p-4">
                    <div className="mb-2 text-[14px] text-[#8592A6]">建议</div>
                    <div className="flex flex-col items-start gap-2 text-[14px] text-[#1F3A56]">
                      {message.suggestions.map((item, index) => (
                        <button
                          key={`${item.tool_id}-${index}`}
                          type="button"
                          onClick={() => onSuggestionClick?.(item)}
                          className="inline-flex items-center justify-between gap-3 rounded-[12px] border border-[#E2E6EA] bg-white px-4 py-3 text-left transition hover:border-[#007EA7] hover:shadow-sm"
                        >
                          <div className="flex items-center gap-2">
                            <span className="text-[18px] leading-none text-[#A3AAB5]">•</span>
                            <span>{item.name}</span>
                          </div>
                          <i className="fa-solid fa-chevron-right text-[#C2CAD4]"></i>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
              {message.references?.length ? (
                <div>
                  <div className="mb-4 h-px w-full bg-[rgba(216,216,216,1)]" />
                  <div className="mb-3 text-[16px] font-[350] leading-[30px] text-[rgba(0,0,0,0.45)]">检索到的法律条款</div>
                  <div className="flex flex-col gap-3">
                    {message.references.map((ref, index) => (
                      <button
                        key={`${ref.id}-${index}`}
                        type="button"
                        onClick={() => onReferenceClick?.(ref)}
                        className="flex w-full items-center gap-3 rounded-[12px] bg-white px-4 py-3 text-left text-[14px] text-[rgba(31,58,86,1)]"
                        data-ref-id={ref.id}
                      >
                        <div className="flex h-[30px] w-[30px] items-center justify-center rounded-full bg-[#F3F5F7] text-[12px] text-[#8592A6]">
                          {index + 1}
                        </div>
                        <div className="flex-1 text-[14px] font-[350] text-[rgba(61,61,61,1)]">
                          <div className="leading-[30px]">{ref.title}</div>
                          <div className="leading-[24px]">{ref.summary}</div>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
        {hoveredRef ? (
          <ReferenceTooltip anchor={hoveredRef.anchor} text={hoveredRef.text} />
        ) : null}
      </div>
    </main>
  );
}
