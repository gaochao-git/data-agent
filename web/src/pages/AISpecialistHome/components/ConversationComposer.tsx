import { useEffect, useLayoutEffect, useRef } from "react";
import type { FormEvent, KeyboardEvent, ReactNode } from "react";

export type ConversationComposerProps = {
  placement: "hero" | "followup";
  composerPlaceholder: string;
  followUpPlaceholder: string;
  renderBeforeComposer?: ReactNode;
  renderAfterComposer?: ReactNode;
  composerFooterStart?: ReactNode;
  showComposerActions: boolean;
  hasPreComposer: boolean;
  hasPostComposer: boolean;
  hasComposerFooterStart: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  inputValue: string;
  onInputChange: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  hasInputValue: boolean;
  isStreaming: boolean;
};

export function ConversationComposer({
  placement,
  composerPlaceholder,
  followUpPlaceholder,
  renderBeforeComposer,
  renderAfterComposer,
  composerFooterStart,
  showComposerActions,
  hasPreComposer,
  hasPostComposer,
  hasComposerFooterStart,
  onSubmit,
  inputValue,
  onInputChange,
  onKeyDown,
  hasInputValue,
  isStreaming,
}: ConversationComposerProps) {
  const formRef = useRef<HTMLFormElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const textareaInitialHeight = 48;
  const formMinHeight = 122;
  const formMaxHeight = 244;
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const intersectionObserverRef = useRef<IntersectionObserver | null>(null);

  // 让输入区在 iframe 首屏就保持“按钮不被挤压、内容多时自然增高”的体验
  const adjustTextareaHeight = (element?: HTMLTextAreaElement | null) => {
    const candidate = element ?? textareaRef.current;
    const textarea = candidate instanceof HTMLTextAreaElement ? candidate : textareaRef.current;
    const form = formRef.current;

    if (!textarea || !form) return;

    const prevFormHeight = form.style.height;

    // 将 form 和 textarea 暂设为 auto 以测量真实占位，避免被上一次强制高度干扰。
    form.style.height = "auto";
    textarea.style.height = "auto";
    // 计算除 textarea 之外的容器占位（padding、按钮等）
    const formOffset = Math.max(0, form.offsetHeight - textarea.offsetHeight);

    const nextFormHeight = Math.max(
      formMinHeight,
      Math.min(formMaxHeight, textarea.scrollHeight + formOffset),
    );
    const nextTextareaHeight = Math.max(0, nextFormHeight - formOffset);

    textarea.style.height = `${nextTextareaHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight + formOffset > formMaxHeight ? "auto" : "hidden";
    form.style.height = `${nextFormHeight}px`;

    // 恢复 form 高度以避免后续测量依赖旧值
    if (prevFormHeight) {
      form.style.height = prevFormHeight;
      // 再次应用计算后的高度，保证与上次写入一致
      form.style.height = `${nextFormHeight}px`;
    }
  };

  useLayoutEffect(() => {
    // 首帧先算一次（避免依赖默认样式），再用 rAF 等待父容器/字体完成布局后重算，修复 iframe/隐藏容器测量为 0 的情况。
    adjustTextareaHeight();
    const frame = requestAnimationFrame(() => adjustTextareaHeight());
    return () => cancelAnimationFrame(frame);
  }, [inputValue]);

  // 当容器尺寸变化或从隐藏变为可见时，重新测量 textarea 高度，避免预加载时的 122px 停留。
  useEffect(() => {
    const form = formRef.current;
    if (!form) return;

    const handleWindowResize = () => adjustTextareaHeight();

    resizeObserverRef.current?.disconnect();
    const resizeObserver = new ResizeObserver(() => adjustTextareaHeight());
    resizeObserver.observe(form);
    resizeObserverRef.current = resizeObserver;

    intersectionObserverRef.current?.disconnect();
    const intersectionObserver = new IntersectionObserver((entries) => {
      const hasVisible = entries.some((entry) => entry.isIntersecting);
      if (hasVisible) {
        adjustTextareaHeight();
      }
    });
    intersectionObserver.observe(form);
    intersectionObserverRef.current = intersectionObserver;

    window.addEventListener("resize", handleWindowResize);
    return () => {
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
      window.removeEventListener("resize", handleWindowResize);
    };
  }, [adjustTextareaHeight]);

  const isHero = placement === "hero";
  const placeholder = isHero ? composerPlaceholder : followUpPlaceholder;
  const wrapperClass =
    "rounded-2xl border border-gray-200 bg-white shadow-sm hover:border-gray-300 focus-within:border-indigo-400 focus-within:shadow-md dark:border-gray-700 dark:bg-gray-800/50";
  const textareaClass =
    "w-full resize-none border-none bg-transparent text-base text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-0";
  const formSizeClass = "w-[776px] max-w-full";
  const formAlignmentClass = "mx-auto";

  return (
    <form
      onSubmit={onSubmit}
      ref={formRef}
      className={`${wrapperClass} ${formSizeClass} ${formAlignmentClass}`}
      style={{ minHeight: `${formMinHeight}px`, maxHeight: `${formMaxHeight}px` }}
    >
      {hasPreComposer && <div className="px-4 pt-4">{renderBeforeComposer}</div>}
      <div className="px-4 pt-4 pb-2">
        <textarea
          className={textareaClass}
          placeholder={placeholder}
          value={inputValue}
          ref={textareaRef}
          onChange={(event) => onInputChange(event.target.value)}
          onInput={(event) => adjustTextareaHeight(event.currentTarget)}
          onKeyDown={onKeyDown}
          style={{ height: `${textareaInitialHeight}px`, overflowY: "hidden" }}
        />
      </div>
      {hasPostComposer && <div className="px-4 pb-4">{renderAfterComposer}</div>}
      <div
        className={`flex flex-wrap items-center gap-4 px-4 py-2 ${
          showComposerActions || hasComposerFooterStart ? "justify-between" : "justify-end"
        }`}
      >
        {(showComposerActions || hasComposerFooterStart) && (
          <div className="flex flex-wrap items-center gap-4">
            {hasComposerFooterStart && <div className="flex flex-wrap items-center gap-2">{composerFooterStart}</div>}
            {showComposerActions && (
              <div className="flex items-center space-x-3 text-gray-400">
                <button type="button" className="hover:text-gray-600">
                  <i className="fa-regular fa-file-lines"></i>
                </button>
                <button type="button" className="hover:text-gray-600">
                  <i className="fa-regular fa-image"></i>
                </button>
                <button type="button" className="hover:text-gray-600">
                  <i className="fa-solid fa-paperclip"></i>
                </button>
              </div>
            )}
          </div>
        )}
        <button
          type="submit"
          disabled={isStreaming || !hasInputValue}
          className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white shadow-sm transition-all hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-gray-300 disabled:text-gray-500 disabled:shadow-none"
          aria-busy={isStreaming}
        >
          {isStreaming ? (
            <>
              <span className="sr-only">生成中...</span>
              <i className="fa-solid fa-spinner fa-spin text-xs"></i>
            </>
          ) : (
            <>
              <span className="sr-only">发送</span>
              <i className="fa-solid fa-arrow-up text-xs"></i>
            </>
          )}
        </button>
      </div>
      {showComposerActions && (
        <div className="flex items-center justify-between text-base text-gray-400">
          <div className="flex items-center space-x-4">
            <button type="button" className="inline-flex items-center space-x-1 hover:text-gray-600">
              <i className="fa-solid fa-paperclip"></i>
              <span>添加材料</span>
            </button>
            <button type="button" className="inline-flex items-center space-x-1 hover:text-gray-600">
              <i className="fa-solid fa-book"></i>
              <span>引用法规</span>
            </button>
            <button type="button" className="inline-flex items-center space-x-1 hover:text-gray-600">
              <i className="fa-solid fa-bolt"></i>
              <span>快速指令</span>
            </button>
          </div>
        </div>
      )}
    </form>
  );
}
