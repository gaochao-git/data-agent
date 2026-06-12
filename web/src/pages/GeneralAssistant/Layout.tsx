import { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate, useLocation, useParams } from "react-router-dom";
import { toast } from "sonner";
import { HistorySidebar } from "./components/HistorySidebar";
import { Conversation } from "./components/Conversation";
import { AgentContainer } from "./components/AgentContainer";
import { TopNavBar } from "./components/TopNavBar";
import { panelHeightClass } from "./constants";
import { AGENTS } from "./agentRegistry";
import { useGeneralAssistant } from "./hooks";
import { API_BASE } from "../../lib/config";
import type { ViewMode, ActiveAgentState, Agent } from "./types";

// agent_id 配置：定义每个智能体的展示方式和路由
// displayMode: "current_page" 在当前页面展示 | "new_page" 跳转到新页面
const AGENT_CONFIG: Record<string, { displayMode: "current_page" | "new_page"; route: string; name: string }> = {
  general: { displayMode: "current_page", route: "/general", name: "通用助手" },
  chat_data: { displayMode: "current_page", route: "/chatdata", name: "智能问数" },
  fact_analysis: { displayMode: "current_page", route: "/fact_analysis", name: "事实认定分析" },
  report_jcbg: { displayMode: "current_page", route: "/report_jcbg", name: "检查报告" },
  report_zgtzs: { displayMode: "current_page", route: "/report_zgtzs", name: "整改通知书" },
  report_zjml: { displayMode: "current_page", route: "/report_zjml", name: "证据目录" },
  report_xxjb: { displayMode: "current_page", route: "/report_xxjb", name: "信息简表" },
  complaint_agent: { displayMode: "current_page", route: "/complaint", name: "投诉举报" },
  expert_agent: { displayMode: "current_page", route: "/expert", name: "专家系统" },
};

export function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { conversationId: urlConversationId } = useParams<{ conversationId?: string }>();

  // 使用通用助手 Hook
  const {
    chatMessages,
    chatInput,
    setChatInput,
    isChatStreaming,
    activeAssistantMessageId,
    conversationHistory,
    activeHistoryId,
    setActiveHistoryId,
    isHistoryCollapsed,
    setIsHistoryCollapsed,
    isHistoryLoading,
    chatThreadId,
    historyRefreshTrigger,
    chatContainerRef,
    sendChatMessage,
    stopChatStreaming,
    loadConversationHistory,
    switchToThread,
    resetChatHistory,
    clearChatState,
    uploadedFiles,
    handleFileUpload,
    removeUploadedFile,
    authError,
  } = useGeneralAssistant();

  // 视图模式状态
  const [viewMode, setViewMode] = useState<ViewMode>("home");
  const [activeAgent, setActiveAgent] = useState<ActiveAgentState | null>(null);
  // 当前智能体的会话标题（由内嵌组件生成标题后回调更新）
  const [agentTitle, setAgentTitle] = useState<string | null>(null);
  // 用于触发返回首页导航的标志
  const [pendingNavigateHome, setPendingNavigateHome] = useState(false);

  // 根据 URL 路径同步智能体视图（支持前进/后退）
  useEffect(() => {
    const pathname = location.pathname;

    // 路由匹配配置
    const routeMatches = [
      { pattern: /^(\/ai)?\/fact_analysis/, agentId: "fact_analysis" },
      { pattern: /^(\/ai)?\/report_jcbg/, agentId: "report_jcbg" },
      { pattern: /^(\/ai)?\/report_zgtzs/, agentId: "report_zgtzs" },
      { pattern: /^(\/ai)?\/report_zjml/, agentId: "report_zjml" },
      { pattern: /^(\/ai)?\/report_xxjb/, agentId: "report_xxjb" },
      { pattern: /^(\/ai)?\/complaint/, agentId: "complaint_agent" },
      { pattern: /^(\/ai)?\/expert/, agentId: "expert_agent" },
      { pattern: /^(\/ai)?\/chatdata/, agentId: "chat_data" },
    ];

    for (const { pattern, agentId } of routeMatches) {
      if (pattern.test(pathname)) {
        const config = AGENT_CONFIG[agentId];
        const agent = AGENTS.find(a => a.componentProps?.agentId === agentId);
        if (agent?.component && config) {
          // 检查当前 activeAgent 是否已经是同一个 agentId 和 conversationId
          setActiveAgent(prev => {
            const prevAgentId = prev?.componentProps?.agentId;
            const prevThreadId = prev?.componentProps?.initialThreadId;
            if (prevAgentId === agentId && prevThreadId === urlConversationId) {
              // 同一个 agent 和会话，保持不变
              return prev;
            }
            // 切换 agent 或会话，使用新配置并添加 key 强制组件重新挂载
            return {
              agentId: agent.id,
              agentName: config.name,
              category: agent.category,
              key: `${agentId}-${urlConversationId || 'new'}-${Date.now()}`,
              componentProps: {
                ...agent.componentProps,
                initialThreadId: urlConversationId,
              },
            };
          });
          setActiveHistoryId(urlConversationId ?? null);
          setViewMode("agent");
          setAgentTitle(null);
          return;
        }
      }
    }
    // 未命中智能体路由：回到通用助手视图
    setViewMode("home");
    setActiveAgent(null);
    setAgentTitle(null);
  }, [location.pathname, urlConversationId, setActiveHistoryId]);

  // 初始加载历史记录
  useEffect(() => {
    loadConversationHistory();
  }, [loadConversationHistory]);

  // 监听历史刷新触发器
  useEffect(() => {
    if (historyRefreshTrigger > 0) {
      loadConversationHistory();
    }
  }, [historyRefreshTrigger, loadConversationHistory]);

  // 处理返回首页导航（使用 useEffect 确保在状态更新完成后执行导航）
  useEffect(() => {
    if (pendingNavigateHome) {
      setPendingNavigateHome(false);
      navigate("/general", { replace: true });
    }
  }, [pendingNavigateHome, navigate]);

  // 获取当前会话标题
  const currentConversationTitle = useMemo(() => {
    if (viewMode === "agent") {
      if (agentTitle) return agentTitle;
      const embeddedId = activeHistoryId || activeAgent?.componentProps?.initialThreadId || urlConversationId;
      if (embeddedId) {
        const currentConversation = conversationHistory.find(h => h.id === embeddedId);
        return currentConversation?.title || "新会话";
      }
      return "新会话";
    }

    if (!chatThreadId) return "新会话";
    const currentConversation = conversationHistory.find(h => h.id === chatThreadId);
    return currentConversation?.title || "新会话";
  }, [
    viewMode,
    agentTitle,
    activeHistoryId,
    activeAgent?.componentProps?.initialThreadId,
    urlConversationId,
    chatThreadId,
    conversationHistory,
  ]);

  useEffect(() => {
    if (viewMode !== "agent") return;
    if (agentTitle) return;
    const embeddedId = activeHistoryId || activeAgent?.componentProps?.initialThreadId || urlConversationId;
    if (!embeddedId) return;
    const currentConversation = conversationHistory.find(h => h.id === embeddedId);
    if (currentConversation?.title) {
      setAgentTitle(currentConversation.title);
    }
  }, [
    viewMode,
    agentTitle,
    activeHistoryId,
    activeAgent?.componentProps?.initialThreadId,
    urlConversationId,
    conversationHistory,
  ]);

  // 新建会话
  const handleNewConversation = () => {
    if (viewMode === "home") {
      // 首页：重置通用助手聊天
      resetChatHistory();
    } else if (activeAgent) {
      // 智能体页面：在当前智能体内新建会话（清除 threadId）
      // 使用新的 key 强制组件重新挂载
      setActiveAgent({
        ...activeAgent,
        key: Date.now(),  // 添加唯一 key 强制重新渲染
        componentProps: {
          ...activeAgent.componentProps,
          initialThreadId: undefined,  // 清除会话ID，触发新建
        },
      });
      // 清除标题
      setAgentTitle(null);
      setActiveHistoryId(null);
      // 更新 URL，移除 threadId（使用路由导航以同步 params）
      const agentId = activeAgent.componentProps?.agentId;
      const config = agentId ? AGENT_CONFIG[agentId] : null;
      if (config?.route) {
        navigate(config.route, { replace: true });
      }
    }
  };

  // 选择历史对话
  const handleSelectHistory = (id: string) => {
    // 从历史记录中找到对应的会话，获取 agent_id
    const conversation = conversationHistory.find(h => h.id === id);
    const agentId = conversation?.agent_id;
    const config = agentId ? AGENT_CONFIG[agentId] : null;

    if (config?.displayMode === "new_page") {
      // 在新标签页打开对应智能体页面
      const newWindow = window.open(`/ai${config.route}/${id}`, "_blank");
      // 如果弹窗被拦截，回退到当前页面跳转（navigate 会自动添加 basename）
      if (!newWindow) {
        navigate(`${config.route}/${id}`);
      }
    } else if (config?.displayMode === "current_page" && agentId !== "general") {
      // 内嵌智能体：在当前页面内嵌显示，并加载历史会话
      // 查找对应的智能体配置（使用第一个匹配的智能体作为组件载体）
      const agent = AGENTS.find(a => a.componentProps?.agentId === agentId);
      if (agent?.component) {
        setActiveAgent({
          agentId: agent.id,
          agentName: config.name,
          category: agent.category,
          key: Date.now(),  // 添加唯一 key 强制重新渲染
          componentProps: {
            agentId: agent.componentProps?.agentId,
            initialThreadId: id,  // 传递要加载的会话ID
          },
        });
        setViewMode("agent");
        setActiveHistoryId(id);  // 更新当前选中的历史ID
        // 设置标题（从历史记录获取）
        setAgentTitle(conversation?.title || null);
        // 更新 URL
        window.history.replaceState(null, "", `/ai${config.route}/${id}`);
      } else {
        // 找不到智能体配置，回退到通用处理
        switchToThread(id);
        setViewMode("home");
        setActiveAgent(null);
        setAgentTitle(null);
      }
    } else {
      // 通用助手：在当前页面展示
      switchToThread(id);
      setViewMode("home");
      setActiveAgent(null);
      setAgentTitle(null);
    }
  };

  // 处理智能体选择
  const handleAgentSelect = (agent: Agent) => {
    // 根据智能体的展示模式决定行为
    if (agent.displayMode === "inline") {
      // 在当前页面内展示
      setActiveAgent({
        agentId: agent.id,
        agentName: agent.name,
        category: agent.category,
        componentProps: agent.componentProps,  // 传递组件 props
      });
      setViewMode("agent");
      // 清除标题（新会话）
      setAgentTitle(null);
      // 如果配置了导航路径，使用路由跳转以保留浏览器返回栈
      if (agent.navigatePath) {
        navigate(agent.navigatePath);
      }
    } else if (agent.displayMode === "navigate" && agent.navigatePath) {
      // 跳转到新页面（由 Conversation 组件处理）
      // 这里不做处理，让 Conversation 的 handleAgentSelect 处理
    }
  };

  // 返回首页
  const handleBackToHome = useCallback(() => {
    // 重置视图状态
    setViewMode("home");
    setActiveAgent(null);
    setAgentTitle(null);
    // 清空聊天状态
    clearChatState();
    // 设置标志，触发 useEffect 中的导航（确保在状态更新完成后执行）
    setPendingNavigateHome(true);
  }, [clearChatState]);

  // 更新会话标题
  const handleUpdateTitle = useCallback(async (id: string, newTitle: string) => {
    try {
      const response = await fetch(`${API_BASE}/api/conversations/${id}/title`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ title: newTitle }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      toast.success("标题已更新");
      // 刷新历史记录
      loadConversationHistory();
    } catch (error) {
      console.error("更新标题失败:", error);
      toast.error("更新标题失败");
    }
  }, [loadConversationHistory]);

  // 切换收藏状态
  const handleToggleFavorite = useCallback(async (id: string) => {
    try {
      const response = await fetch(`${API_BASE}/api/conversations/${id}/favorite`, {
        method: "POST",
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      const isFavorited = data.data?.is_favorited;

      toast.success(isFavorited ? "已收藏" : "已取消收藏");
      // 刷新历史记录
      loadConversationHistory();
    } catch (error) {
      console.error("切换收藏状态失败:", error);
      toast.error("操作失败");
    }
  }, [loadConversationHistory]);

  // 处理内嵌智能体会话ID变化（memoized 避免无限循环）
  const handleConversationIdChange = useCallback((conversationId: string) => {
    // 内嵌智能体创建/切换会话时，更新 URL
    // 注意：这里需要从 activeAgent 获取 agentId，但 activeAgent 可能变化
    // 所以我们使用 ref 来避免依赖 activeAgent
    const agentId = activeAgent?.componentProps?.agentId;
    const config = agentId ? AGENT_CONFIG[agentId] : null;
    if (config?.route) {
      window.history.replaceState(null, "", `/ai${config.route}/${conversationId}`);
    }
    setActiveHistoryId(conversationId);
  }, [activeAgent?.componentProps?.agentId]);

  // 处理标题变化（memoized 避免无限循环）
  const handleTitleChange = useCallback((title: string) => {
    // 内嵌智能体生成标题后，更新顶部标题
    setAgentTitle(title);
    // 注意：不再在这里调用 loadConversationHistory，避免循环
    // 历史记录刷新由 onHistoryRefresh 回调单独处理
  }, []);

  // 删除会话
  const handleDeleteConversation = useCallback(async (id: string) => {
    try {
      const response = await fetch(`${API_BASE}/api/conversations/${id}`, {
        method: "DELETE",
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      toast.success("会话已删除");
      // 如果删除的是当前会话，重置聊天
      if (id === chatThreadId) {
        resetChatHistory();
      }
      // 刷新历史记录
      loadConversationHistory();
    } catch (error) {
      console.error("删除会话失败:", error);
      toast.error("删除失败");
    }
  }, [chatThreadId, resetChatHistory, loadConversationHistory]);

  // 首页用渐变背景，智能体页面用普通背景
  const backgroundStyle = viewMode === "home"
    ? { background: 'linear-gradient(61deg, #F7EDEC 0%, #CCE6FF 100%)' }
    : { background: '#F7F9FA' };

  return (
    <div
      className="flex h-screen overflow-hidden dark:bg-gray-900"
      style={backgroundStyle}
    >
      {/* 历史对话侧边栏 - 始终显示 */}
      <HistorySidebar
        isCollapsed={isHistoryCollapsed}
        setIsCollapsed={setIsHistoryCollapsed}
        history={conversationHistory}
        activeHistoryId={activeHistoryId}
        onSelectHistory={handleSelectHistory}
        onNewConversation={handleNewConversation}
        onUpdateTitle={handleUpdateTitle}
        onToggleFavorite={handleToggleFavorite}
        onDeleteConversation={handleDeleteConversation}
        panelHeightClass="h-screen"
        authError={authError}
        isAgentView={viewMode !== "home"}
        isLoading={isHistoryLoading}
      />

      {/* 主内容区域 - 占据剩余空间 */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* 公共顶部导航栏 */}
        <TopNavBar
          title={viewMode === "home" ? currentConversationTitle : (agentTitle || "新会话")}
          onBack={handleBackToHome}
          onNewConversation={handleNewConversation}
          isCollapsed={isHistoryCollapsed}
          onToggleCollapse={() => setIsHistoryCollapsed(!isHistoryCollapsed)}
          showBackButton={true}
        />

        {/* 内容区域 */}
        <div className="flex-1 overflow-hidden">
          {viewMode === "home" ? (
            // 首页：对话和智能体选择
            <Conversation
              messages={chatMessages}
              input={chatInput}
              setInput={setChatInput}
              isStreaming={isChatStreaming}
              onSendMessage={sendChatMessage}
              onStopStreaming={stopChatStreaming}
              onAgentSelect={handleAgentSelect}
              conversationTitle={currentConversationTitle}
              panelHeightClass="h-full"
              uploadedFiles={uploadedFiles}
              onFileUpload={handleFileUpload}
              onRemoveFile={removeUploadedFile}
            />
          ) : (
            // 智能体视图
            activeAgent && (
              <AgentContainer
                activeAgent={activeAgent}
                onBack={handleBackToHome}
                onHistoryRefresh={loadConversationHistory}
                onConversationIdChange={handleConversationIdChange}
                onTitleChange={handleTitleChange}
              />
            )
          )}
        </div>
      </div>
    </div>
  );
}
