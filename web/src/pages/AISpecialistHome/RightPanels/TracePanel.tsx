import { useCallback, useEffect, useMemo, useState } from "react";
import { API_BASE } from "@/lib/config";

type TraceEvent = {
  ts_ms?: number;
  event?: string;
  payload?: unknown;
};

type TracePanelProps = {
  conversationId: string | null;
};

const formatTimestamp = (value?: number) => {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString();
};

export default function TracePanel({ conversationId }: TracePanelProps) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canFetch = Boolean(conversationId);

  const fetchTrace = useCallback(async () => {
    if (!conversationId) {
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `${API_BASE}/api/ai_chat/traces/${encodeURIComponent(conversationId)}`,
        {
          credentials: "include",
        }
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `请求失败，状态码 ${response.status}`);
      }
      const data: { events?: TraceEvent[] } = await response.json();
      setEvents(Array.isArray(data.events) ? data.events : []);
    } catch (err) {
      const message = err instanceof Error ? err.message : "加载 trace 失败";
      setError(message);
      setEvents([]);
    } finally {
      setIsLoading(false);
    }
  }, [conversationId]);

  useEffect(() => {
    if (!canFetch) {
      setEvents([]);
      setError(null);
      return;
    }
    void fetchTrace();
  }, [canFetch, fetchTrace]);

  const summaryText = useMemo(() => {
    if (!conversationId) {
      return "暂无会话可用。";
    }
    if (isLoading) {
      return "正在加载 trace...";
    }
    if (error) {
      return error;
    }
    if (!events.length) {
      return "没有 trace 记录。";
    }
    return `共 ${events.length} 条记录。`;
  }, [conversationId, isLoading, error, events.length]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
        <div>
          <p className="text-sm font-semibold text-gray-900">调试 Trace</p>
          <p className="text-xs text-gray-400">{summaryText}</p>
        </div>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-600 transition-colors hover:border-gray-300 hover:text-gray-800 disabled:cursor-not-allowed disabled:text-gray-400"
          onClick={() => void fetchTrace()}
          disabled={!canFetch || isLoading}
        >
          <i className="fa-solid fa-rotate-right text-[11px]"></i>
          刷新
        </button>
      </div>
      <div className="flex-1 overflow-auto px-5 py-4">
        {events.length === 0 ? (
          <div className="text-sm text-gray-500">{summaryText}</div>
        ) : (
          <div className="space-y-4">
            {events.map((item, index) => (
              <div
                key={`${item.event ?? "event"}-${item.ts_ms ?? index}-${index}`}
                className="rounded-xl border border-gray-200 bg-white p-4 text-xs text-gray-600 shadow-sm"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-700">
                    {item.event || "event"}
                  </span>
                  <span className="text-[11px] text-gray-400">
                    {formatTimestamp(item.ts_ms)}
                  </span>
                </div>
                <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-[11px] text-gray-700">
                  {JSON.stringify(item.payload ?? {}, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
