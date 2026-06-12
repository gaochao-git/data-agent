/**
 * 专家系统嵌入组件 - 用于嵌入到 GeneralAssistant 的 AgentContainer 中
 *
 * 最小化改造：直接复用现有的 AINewHomePage 组件
 * 支持 URL 参数自动登录
 */

import AiLoginRedirect, { useAutoLoginParams } from "../AiLoginRedirect";
import AINewHomePage from "./AINewHome";

interface EmbeddedLayoutProps {
  agentId?: string;
  toolType?: string;
  initialThreadId?: string;
  onHistoryRefresh?: () => void;
  onConversationIdChange?: (conversationId: string) => void;
  onTitleChange?: (title: string) => void;
}

export function EmbeddedLayout({ initialThreadId, onHistoryRefresh, onConversationIdChange, onTitleChange }: EmbeddedLayoutProps) {
  const { userId, userName } = useAutoLoginParams();
  const hasAutoLoginParams = Boolean(userId && userName);

  // 如果 URL 带有登录参数，先执行登录
  if (hasAutoLoginParams) {
    return <AiLoginRedirect />;
  }

  return (
    <div className="h-full w-full">
      <AINewHomePage
        isEmbedded
        initialConversationId={initialThreadId}
        onHistoryRefresh={onHistoryRefresh}
        onConversationIdChange={onConversationIdChange}
        onTitleChange={onTitleChange}
      />
    </div>
  );
}
