import { useCallback, useState } from "react";
import { API_BASE } from "@/lib/config";
import type { ConversationSummary } from "../types";

type RefreshOptions = {
  signal?: AbortSignal;
  showLoading?: boolean;
};

export function useConversationList() {
  const [conversationSummaries, setConversationSummaries] = useState<ConversationSummary[]>([]);
  const [isLoadingConversations, setIsLoadingConversations] = useState(false);
  const [conversationListError, setConversationListError] = useState<string | null>(null);

  const refreshConversationList = useCallback(
    async ({ signal, showLoading = true }: RefreshOptions = {}) => {
      const shouldManageLoading = showLoading;
      if (shouldManageLoading) {
        setIsLoadingConversations(true);
        setConversationListError(null);
      }
      try {
        const response = await fetch(`${API_BASE}/api/ai_chat/conversations`, {
          credentials: "include",
          signal,
        });
        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || `请求失败，状态码 ${response.status}`);
        }

        const data: {
          conversations?: Array<{
            conversation_id?: string;
            created_at?: string;
            last_used_at?: string;
            message_count?: number;
            latest_message_preview?: string;
            first_message_preview?: string;
          }>;
        } = await response.json();

        const normalized: ConversationSummary[] = (Array.isArray(data?.conversations)
          ? data.conversations
          : []
        )
          .map((item) => {
            const conversationId =
              typeof item?.conversation_id === "string"
                ? item.conversation_id.trim()
                : "";
            if (!conversationId) {
              return null;
            }
            const createdAt =
              typeof item?.created_at === "string"
                ? item.created_at
                : undefined;
            const lastUsedAt =
              typeof item?.last_used_at === "string"
                ? item.last_used_at
                : createdAt;
            const title =
              typeof item?.title === "string" && item.title.trim().length > 0
                ? item.title.trim()
                : undefined;
            return {
              id: conversationId,
              createdAt: createdAt ?? "",
              lastUsedAt: lastUsedAt ?? "",
              messageCount: typeof item?.message_count === "number"
                ? item.message_count
                : 0,
              latestMessagePreview:
                typeof item?.latest_message_preview === "string"
                  ? item.latest_message_preview
                  : "",
              firstMessagePreview:
                typeof item?.first_message_preview === "string"
                  ? item.first_message_preview
                  : "",
              title,
            };
          })
          .filter(Boolean) as ConversationSummary[];

        if (signal?.aborted) {
          return;
        }
        setConversationSummaries(normalized);
        setConversationListError(null);
      } catch (error) {
        if (signal?.aborted) {
          return;
        }
        const message =
          error instanceof Error ? error.message : "无法获取历史会话列表";
        setConversationListError(message);
      } finally {
        if (shouldManageLoading && !signal?.aborted) {
          setIsLoadingConversations(false);
        }
      }
    },
    []
  );

  const upsertConversationSummary = useCallback(
    (
      conversationId: string,
      summary: Partial<ConversationSummary> = {}
    ) => {
      const fallbackTimestamp = new Date().toISOString();
      setConversationSummaries((prev) => {
        let found = false;
        let changed = false;
        const next = prev.map((item) => {
          if (item.id !== conversationId) {
            return item;
          }
          found = true;
          const merged: ConversationSummary = {
            ...item,
            ...summary,
            lastUsedAt: summary.lastUsedAt ?? item.lastUsedAt ?? fallbackTimestamp,
            latestMessagePreview:
              summary.latestMessagePreview ?? item.latestMessagePreview,
            firstMessagePreview:
              summary.firstMessagePreview ?? item.firstMessagePreview,
            title: summary.title ?? item.title,
            messageCount: summary.messageCount ?? item.messageCount,
            createdAt: summary.createdAt ?? item.createdAt ?? fallbackTimestamp,
          };
          if (
            merged.lastUsedAt !== item.lastUsedAt ||
            merged.latestMessagePreview !== item.latestMessagePreview ||
            merged.firstMessagePreview !== item.firstMessagePreview ||
            merged.title !== item.title ||
            merged.messageCount !== item.messageCount ||
            merged.createdAt !== item.createdAt
          ) {
            changed = true;
          }
          return merged;
        });
        if (!found) {
          const createdAt = summary.createdAt ?? fallbackTimestamp;
          const lastUsedAt = summary.lastUsedAt ?? createdAt;
          next.push({
            id: conversationId,
            createdAt,
            lastUsedAt,
            messageCount: summary.messageCount ?? 1,
            latestMessagePreview: summary.latestMessagePreview ?? "",
            firstMessagePreview: summary.firstMessagePreview ?? "",
            title: summary.title,
          });
          changed = true;
        }
        if (!changed) {
          return prev;
        }
        next.sort((a, b) => {
          const left = a.lastUsedAt || "";
          const right = b.lastUsedAt || "";
          return right.localeCompare(left);
        });
        return next;
      });
    },
    []
  );

  return {
    conversationSummaries,
    isLoadingConversations,
    conversationListError,
    refreshConversationList,
    upsertConversationSummary,
  };
}
