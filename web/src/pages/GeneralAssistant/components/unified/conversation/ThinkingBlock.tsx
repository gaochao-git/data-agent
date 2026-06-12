import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import Lottie from "lottie-react";
import thinkingAnimation from "@/assets/thinking.json";
import checkIcon from "@/assets/check.svg";
import chevronRightIcon from "@/assets/chevron-right.svg";
import chevronDownIcon from "@/assets/chevron-down.svg";
import deepThinkIcon from "@/assets/deep-think.svg";
import type { ThinkingItem } from "../../UnifiedEmbeddedContent";

export function DeepThinkIcon({ className }: { className?: string }) {
  return (
    <img
      src={deepThinkIcon}
      alt=""
      className={className ?? "h-[18px] w-[18px] text-slate-500"}
    />
  );
}

function StepStatusIcon({ status }: { status: "done" | "thinking" }) {
  if (status === "thinking") {
    return (
      <div className="h-[19.26px] w-[20px]">
        <Lottie
          animationData={thinkingAnimation}
          loop
          rendererSettings={{ preserveAspectRatio: "xMidYMid slice" }}
          style={{ width: "100%", height: "100%" }}
        />
      </div>
    );
  }

  return (
    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-[#31C48D] text-white">
      <img src={checkIcon} alt="" className="h-3 w-3" />
    </div>
  );
}

export function ThinkingBlock({ items, indent = 0 }: { items: ThinkingItem[]; indent?: number }) {
  const [collapsedMap, setCollapsedMap] = useState<Record<string, boolean>>({});
  const detailClassName =
    "prose prose-slate max-w-none text-[14px] font-[350] leading-[25px] text-[rgba(31,58,86,1)] prose-p:my-2 prose-ul:my-2 prose-li:my-1 prose-li:text-[14px] prose-li:leading-[25px]";

  const flattenSteps = (steps: ThinkingItem[]): ThinkingItem[] => {
    return steps.flatMap((step) => {
      const normalizedChildren = step.children ? flattenSteps(step.children) : step.children;
      if (step.flatten_children) {
        return normalizedChildren ?? [];
      }
      if (normalizedChildren && normalizedChildren !== step.children) {
        return [{ ...step, children: normalizedChildren }];
      }
      return [step];
    });
  };
  const flattenedItems = flattenSteps(items);

  const toggleItem = (id: string, current: boolean) => {
    setCollapsedMap((prev) => ({ ...prev, [id]: !current }));
  };

  const renderDetail = (step: ThinkingItem) => {
    if (step.detailLines && step.detailLines.length > 0) {
      return step.detailLines.map((line, index) => (
        <ReactMarkdown
          key={line.lineId ?? index}
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeRaw]}
          className={detailClassName}
        >
          {line.content}
        </ReactMarkdown>
      ));
    }

    if (step.detail) {
      return (
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeRaw]}
          className={detailClassName}
        >
          {step.detail}
        </ReactMarkdown>
      );
    }

    return null;
  };

  const renderStepLabel = (label: string) => {
    const match = label.match(/[（(]/);
    if (!match || match.index === undefined) {
      return <span>{label}</span>;
    }

    const splitIndex = match.index;
    const prefix = label.slice(0, splitIndex);
    const suffix = label.slice(splitIndex);

    return (
      <span>
        <span className="font-semibold">{prefix}</span>
        <span>{suffix}</span>
      </span>
    );
  };

  return (
    <div className="relative">
      <div className="flex flex-col gap-3">
        {flattenedItems.map((step) => {
          const hasChildren = Boolean(step.children && step.children.length > 0);
          const hasDetail = Boolean(step.detail || (step.detailLines && step.detailLines.length > 0));
          const hasContent = hasChildren || hasDetail;
          const hideLabel = Boolean(step.label_hidden);
          const isCollapsed =
            hideLabel ? false : (collapsedMap[step.id] ?? (step.status === "done" && hasContent));

          if (hideLabel) {
            return (
              <div key={step.id} className="flex flex-col gap-0">
                {hasDetail ? renderDetail(step) : null}
                {hasChildren ? (
                  <div className="pt-2">
                    <ThinkingBlock items={step.children!} indent={0} />
                  </div>
                ) : null}
              </div>
            );
          }

          return (
            <div key={step.id} className="flex flex-col gap-0">
              <div
                className={`flex items-center gap-2 text-[14px] text-black ${hasContent ? "cursor-pointer" : ""}`}
                onClick={() => hasContent && toggleItem(step.id, isCollapsed)}
                role={hasContent ? "button" : undefined}
                tabIndex={hasContent ? 0 : undefined}
                onKeyDown={(event) => {
                  if (!hasContent) return;
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    toggleItem(step.id, isCollapsed);
                  }
                }}
              >
                <StepStatusIcon status={step.status} />
                <span className="inline-flex items-center gap-1.5">
                  {renderStepLabel(step.label)}
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      if (hasContent) toggleItem(step.id, isCollapsed);
                    }}
                    className={`inline-flex h-4 w-4 items-center justify-center text-black ${
                      hasContent ? "" : "opacity-40 cursor-default"
                    }`}
                    aria-label={isCollapsed ? "展开" : "收起"}
                  >
                    {isCollapsed || !hasContent ? (
                      <img src={chevronRightIcon} alt="" className="h-3 w-3" />
                    ) : (
                      <img src={chevronDownIcon} alt="" className="h-3 w-3" />
                    )}
                  </button>
                </span>
              </div>
              {hasDetail && !isCollapsed && <div className="pl-8 mt-[11px]">{renderDetail(step)}</div>}
              {hasChildren && !isCollapsed && (
                <div className="pt-2">
                  <ThinkingBlock items={step.children!} indent={0} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
