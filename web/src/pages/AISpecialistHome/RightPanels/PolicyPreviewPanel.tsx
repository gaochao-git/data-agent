import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE } from "@/lib/config";
import type { ParsedCitation } from "../types";

type PolicyResponse = {
  text?: string;
  title?: string;
  [key: string]: unknown;
};

type PolicyPreviewPanelProps = {
  citation: ParsedCitation | null;
};

export default function PolicyPreviewPanel({ citation }: PolicyPreviewPanelProps) {
  const policyKey = useMemo(
    () => citation?.sourceId ?? citation?.anchorId ?? "",
    [citation?.anchorId, citation?.sourceId],
  );
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const highlightRef = useRef<HTMLSpanElement | null>(null);
  const [hasRenderedHighlight, setHasRenderedHighlight] = useState(false);

  useEffect(() => {
    if (!policyKey) {
      setContent("");
      setError(null);
      setLoading(false);
      setHasRenderedHighlight(false);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setContent("");
    setHasRenderedHighlight(false);

    const fetchPolicy = async () => {
      try {
        const response = await fetch(
          `${API_BASE}/api/policies/${encodeURIComponent(policyKey)}`,
          {
            credentials: "include",
            signal: controller.signal,
          }
        );
        if (!response.ok) {
          throw new Error(`加载失败：${response.status}`);
        }
        const data: PolicyResponse = await response.json();
        if (!controller.signal.aborted) {
          setContent(typeof data?.text === "string" ? data.text : "");
        }
      } catch (err) {
        if (controller.signal.aborted) {
          return;
        }
        const message = err instanceof Error ? err.message : "未知错误";
        setError(message);
        setContent("");
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    };

    void fetchPolicy();

    return () => {
      controller.abort();
    };
  }, [policyKey]);

  const highlightTarget = useMemo(() => {
    const candidate =
      citation?.clause?.trim() ||
      citation?.description?.trim() ||
      "";
    if (!candidate) {
      return "";
    }
    if (!content.includes(candidate)) {
      return "";
    }
    return candidate;
  }, [citation?.clause, citation?.description, content]);

  useEffect(() => {
    if (!highlightTarget || !hasRenderedHighlight) {
      return;
    }
    const timer = window.setTimeout(() => {
      highlightRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }, 200);
    return () => window.clearTimeout(timer);
  }, [highlightTarget, hasRenderedHighlight]);

  const highlightedContent = useMemo(() => {
    if (!highlightTarget) {
      return content;
    }
    const segments = content.split(highlightTarget);
    const nodes = segments.flatMap((segment, index) => {
      const items = [
        <span key={`segment-${index}`}>{segment}</span>,
      ];
      if (index < segments.length - 1) {
        items.push(
          <mark
            key={`highlight-${index}`}
            ref={index === 0 ? highlightRef : undefined}
            className="rounded bg-yellow-200/70 px-1 py-0.5 text-gray-900"
          >
            {highlightTarget}
          </mark>,
        );
      }
      return items;
    });
    return nodes;
  }, [content, highlightTarget]);

  useEffect(() => {
    if (highlightTarget && Array.isArray(highlightedContent)) {
      setHasRenderedHighlight(true);
    } else {
      setHasRenderedHighlight(false);
    }
  }, [highlightTarget, highlightedContent]);

  if (!citation) {
    return (
      <div className="px-6 py-10 text-center text-sm text-gray-400">
        选择引用后将在此处显示法规原文。
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-1 px-6 py-4">
        <p className="text-sm font-semibold text-gray-900">{citation.title}</p>
        {citation.clause && (
          <p className="text-xs text-blue-600 truncate" title={citation.clause}>
            {citation.clause}
          </p>
        )}
        {citation.description && (
          <p
            className="text-xs text-gray-500 truncate"
            title={citation.description}
          >
            {citation.description}
          </p>
        )}
      </div>
      <div className="flex-1 overflow-y-auto border-t border-gray-100">
        {loading && (
          <div className="px-6 py-6 text-sm text-gray-400">
            <i className="fa-solid fa-spinner fa-spin mr-2"></i>
            正在加载法规内容...
          </div>
        )}
        {!loading && error && (
          <div className="px-6 py-6 text-sm text-red-500">
            加载失败：{error}
          </div>
        )}
        {!loading && !error && content && (
          <div className="whitespace-pre-wrap break-words px-6 py-6 text-sm leading-6 text-gray-800">
            {highlightedContent}
          </div>
        )}
        {!loading && !error && !content && (
          <div className="px-6 py-6 text-sm text-gray-400">暂无内容</div>
        )}
      </div>
    </div>
  );
}
