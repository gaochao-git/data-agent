import { Suspense } from "react";
import type { ActiveAgentState } from "../types";
import { getAgentById } from "../agentRegistry";

interface AgentContainerProps {
  activeAgent: ActiveAgentState;
  onBack: () => void;
  onHistoryRefresh?: () => void;
  onConversationIdChange?: (conversationId: string) => void;
  onTitleChange?: (title: string) => void;
}

/**
 * 加载中占位组件
 */
function LoadingFallback() {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-center">
        <i className="fa-solid fa-spinner fa-spin text-3xl text-indigo-500 mb-3"></i>
        <p className="text-sm text-gray-500 dark:text-gray-400">加载中...</p>
      </div>
    </div>
  );
}

/**
 * 未注册智能体占位组件
 */
function UnknownAgent({ agentId }: { agentId: string }) {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-center">
        <i className="fa-solid fa-circle-exclamation text-4xl text-gray-400 mb-4"></i>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          智能体 "{agentId}" 尚未实现
        </p>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">
          请在 agentRegistry.ts 中配置该智能体的组件
        </p>
      </div>
    </div>
  );
}

/**
 * 智能体容器组件
 *
 * 负责根据选中的智能体动态加载和渲染对应的智能体功能组件
 * 组件通过 agentRegistry.ts 配置，支持动态加载
 */
export function AgentContainer({ activeAgent, onBack, onHistoryRefresh, onConversationIdChange, onTitleChange }: AgentContainerProps) {
  // 从注册表获取智能体配置
  const agent = getAgentById(activeAgent.agentId);

  // 渲染智能体组件
  const renderAgentComponent = () => {
    if (!agent?.component) {
      return <UnknownAgent agentId={activeAgent.agentId} />;
    }

    const Component = agent.component;

    return (
      <Suspense fallback={<LoadingFallback />}>
        <Component
          key={activeAgent.key}
          {...activeAgent.componentProps}
          onHistoryRefresh={onHistoryRefresh}
          onConversationIdChange={onConversationIdChange}
          onTitleChange={onTitleChange}
        />
      </Suspense>
    );
  };

  return (
    <div className="flex flex-col h-full">
      {/* 智能体内容区域 */}
      <div className="flex-1 overflow-hidden">
        {renderAgentComponent()}
      </div>
    </div>
  );
}
