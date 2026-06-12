import { useEffect, useRef, useState } from "react";
import { UnifiedEmbeddedContent } from "./components/UnifiedEmbeddedContent";
import { AGENTS } from "./agentRegistry";
import { API_BASE } from "@/lib/config";
import type { ChatMessage } from "./components/UnifiedEmbeddedContent";

interface EmbeddedLayoutProps {
  agentId?: string;
  toolType?: string;
  initialThreadId?: string;
  onHistoryRefresh?: () => void;
  onConversationIdChange?: (conversationId: string) => void;
  onTitleChange?: (title: string) => void;
}

export function UnifiedEmbeddedLayout(props: EmbeddedLayoutProps) {
  const agentConfig = AGENTS.find(agent => agent.componentProps?.agentId === props.agentId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyArtifacts, setHistoryArtifacts] = useState<any | null>(null);
  const lastLoadedIdRef = useRef<string>("");
  const inFlightRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let ignore = false;
    const conversationId = props.initialThreadId;
    if (!conversationId) {
      setMessages([]);
      setHistoryArtifacts(null);
      return;
    }

    if (lastLoadedIdRef.current === conversationId) {
      return;
    }
    inFlightRef.current?.abort();
    const controller = new AbortController();
    inFlightRef.current = controller;

    const loadHistory = async () => {
      try {
        const url = `${API_BASE}/api/chat/conversations/${encodeURIComponent(conversationId)}/messages?agent_id=${encodeURIComponent(props.agentId || "")}`;
        const response = await fetch(url, { credentials: "include", signal: controller.signal });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        if (ignore) return;
        const nextMessages = Array.isArray(data?.messages) ? data.messages : [];
        setMessages(nextMessages);
        setHistoryArtifacts(data?.artifacts || null);
        lastLoadedIdRef.current = conversationId;
      } catch (error: any) {
        if (error?.name === "AbortError") {
          return;
        }
        if (ignore) return;
        console.error("加载历史对话失败:", error);
        setMessages([]);
        setHistoryArtifacts(null);
      }
    };

    loadHistory();
    return () => {
      ignore = true;
      controller.abort();
    };
  }, [props.agentId, props.initialThreadId]);

  return (
    <div className="h-full w-full">
      <UnifiedEmbeddedContent
        agentId={props.agentId}
        initialThreadId={props.initialThreadId}
        inputActions={agentConfig?.input_actions}
        inputControls={agentConfig?.input_controls}
        inputPlaceholder={agentConfig?.input_placeholder}
        footerTag={agentConfig?.footer_tag}
        assistantRoleLabel={agentConfig?.assistant_role_label}
        openingMessage={agentConfig?.opening_message}
        workbench={agentConfig?.workbench}
        messages={messages}
        historyArtifacts={historyArtifacts}
        onConversationIdChange={(conversationId) => {
          props.onConversationIdChange?.(conversationId);
        }}
        onStreamDone={() => {
          props.onHistoryRefresh?.();
        }}
        onTitleChange={props.onTitleChange}
      />
    </div>
  );
}
