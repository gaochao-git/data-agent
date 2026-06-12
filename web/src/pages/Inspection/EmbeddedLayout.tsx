import { useMemo, useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useViolationAnalysis } from "./hooks";
import { EmbeddedConversation } from "./components/EmbeddedConversation";
import { Console } from "./components/Console";
import { mergeClassNames } from "./utils";
import { API_BASE } from "../../lib/config";
import type { ChatMessage } from "./types";
import { AGENTS } from "../GeneralAssistant/agentRegistry";

/**
 * 简化版执法检查组件 - 用于嵌入到 AgentContainer 中
 * 移除了：
 * - History 侧边栏（使用主应用的侧边栏）
 * - FeaturePageLayout 包装器（使用 AgentContainer 的统一 header）
 * - ToolCards 工具选择（智能体已在主页面选择）
 * 保留核心功能：
 * - 对话界面
 * - 文件上传
 * - 分析处理
 * - 结果展示
 */

interface EmbeddedLayoutProps {
  // 后端 agent_id
  agentId?: string;
  // 初始会话ID（从历史记录点击进入时传递）
  initialThreadId?: string;
  // 历史记录刷新回调（通知父组件刷新侧边栏）
  onHistoryRefresh?: () => void;
  // 会话ID变化回调（通知父组件更新URL）
  onConversationIdChange?: (conversationId: string) => void;
  // 标题变化回调（通知父组件更新顶部标题）
  onTitleChange?: (title: string) => void;
}

export function EmbeddedLayout({ agentId, initialThreadId, onHistoryRefresh, onConversationIdChange, onTitleChange }: EmbeddedLayoutProps) {
  const {
    // State
    analysisResult,
    isAnalyzing,
    taskStatus,
    analysisTasks,
    chatMessages,
    setChatMessages,
    chatInput,
    setChatInput,
    isChatStreaming,
    chatStreamingStatus,
    activeAssistantMessageId,
    analysisStage,
    analysisHistory,
    activeHistoryId,
    setActiveHistoryId,
    missingRequiredLabels,
    hasFiveRequiredInputs,
    violationDescriptionValue,
    activeHistory,
    selectedDocument,
    setSelectedDocument,
    chatThreadId,
    historyRefreshTrigger,

    // Form
    register,
    handleSubmit,
    errors,
    onSubmit,

    // Refs
    chatContainerRef,

    // Functions
    sendChatMessage,
    sendReportRequest,
    stopChatStreaming,
    resetChatHistory,
    createNewThread,
    setAnalysisResult,
    setAnalysisStage,
    setTaskStatus,
    handleLawClick,
    switchToThread,
    loadThreadMessages,

    // Human-in-the-Loop 人工审核回调
    handleHumanReviewApprove,
    handleHumanReviewEdit,
    handleHumanReviewReject,

    // 报告生成恢复
    resumeReportGeneration,
  } = useViolationAnalysis({ agentId });

  // 当传入 initialThreadId 时，加载对应会话的历史消息
  useEffect(() => {
    if (initialThreadId && initialThreadId !== chatThreadId) {
      loadThreadMessages(initialThreadId);
    }
  }, [initialThreadId, loadThreadMessages]);

  // 当 chatThreadId 变化时，通知父组件更新 URL
  useEffect(() => {
    if (chatThreadId) {
      onConversationIdChange?.(chatThreadId);
    }
  }, [chatThreadId, onConversationIdChange]);

  // 当 historyRefreshTrigger 变化时，通知父组件刷新侧边栏
  useEffect(() => {
    if (historyRefreshTrigger > 0 && onHistoryRefresh) {
      onHistoryRefresh();
    }
  }, [historyRefreshTrigger, onHistoryRefresh]);

  // Word 文档预览状态（包含 sourceData 用于编辑后重新生成）
  const [wordDocument, setWordDocument] = useState<{
    filename: string;
    title: string;
    sourceData?: {
      report_type: string;
      inspection_content?: string;
      violation_facts?: string[];
      legal_basis?: string[];
      penalty_clauses?: string[];
    };
  } | null>(null);

  // Excel 文档预览状态
  const [excelDocument, setExcelDocument] = useState<{ filename: string; title: string; excelData: any } | null>(null);

  // 相似案例报告预览状态
  const [caseReport, setCaseReport] = useState<{ name: string; content: string } | null>(null);

  // 更新后的 Word 文件名（编辑保存后更新）
  const [updatedWordFilename, setUpdatedWordFilename] = useState<string | undefined>(undefined);

  // 审核编辑状态（用于右侧控制台编辑所有类型的审核数据）
  const [reviewEditorData, setReviewEditorData] = useState<{
    taskId: string;
    messageId: string;
    reviewData: {
      review_type: "split" | "retrieve" | "extract" | "analyze" | "evidence";
      message: string;
      current_data: any;
    };
  } | null>(null);

  // 可调整面板宽度状态（百分比 - 相对于可用空间）
  const [conversationWidth, setConversationWidth] = useState(50);
  const [isDragging, setIsDragging] = useState(false);

  // 重置输入区域的触发器
  const [resetTrigger, setResetTrigger] = useState(0);

  // 根据 agentId 获取对应智能体的引导配置
  const agentGuide = useMemo(() => {
    // 根据后端 agentId 匹配前端 agent
    const agentIdToFrontendId: Record<string, string> = {
      "fact_analysis": "inspection-fact-analysis",
      "report_jcbg": "inspection-report-jcbg",
      "report_zgtzs": "inspection-report-zgtzs",
      "report_zjml": "inspection-report-zjml",
      "report_xxjb": "inspection-report-xxjb",
    };
    const frontendAgentId = agentIdToFrontendId[agentId || "report_jcbg"];
    const agent = frontendAgentId ? AGENTS.find(a => a.id === frontendAgentId) : undefined;
    return agent?.guide;
  }, [agentId]);

  // 当前会话标题（从后端获取）
  const [currentThreadTitle, setCurrentThreadTitle] = useState<string | undefined>(undefined);

  // 标记是否已经初始加载过标题
  const initialTitleLoadedRef = useRef(false);

  const panelHeightClass = "h-full"; // 嵌入模式使用全高度

  // 处理法律条款预览（包装原始 handleLawClick，增加清除其他预览状态）
  const handleLawClickWrapper = (law: { law_name: string; law_item_name: string; law_item_content: string }) => {
    handleLawClick(law);
    setWordDocument(null);
    setExcelDocument(null);
    setCaseReport(null);
  };

  // 处理 Word 文档预览
  const handleWordDocumentView = (doc: { filename: string; title: string; sourceData?: any }) => {
    setWordDocument(doc);
    setSelectedDocument(null);
    setExcelDocument(null);
    setCaseReport(null);
  };

  // 处理报告重新生成
  const handleReportRegenerate = (filename: string, sourceData: any) => {
    setWordDocument({ filename, title: wordDocument?.title || '检查报告', sourceData });
    setUpdatedWordFilename(filename);
  };

  // 处理 Excel 文档预览
  const handleExcelDocumentView = (doc: { filename: string; title: string; excelData: any }) => {
    setExcelDocument(doc);
    setSelectedDocument(null);
    setWordDocument(null);
    setCaseReport(null);
  };

  // 处理相似案例报告预览
  const handleReportClick = (report: { name: string; content: string }) => {
    setCaseReport(report);
    setSelectedDocument(null);
    setWordDocument(null);
    setExcelDocument(null);
  };

  // 打开审核编辑器（在右侧控制台）
  const handleOpenReviewEditor = (taskId: string, messageId: string, reviewData: { review_type: string; message: string; current_data: any }) => {
    setReviewEditorData({ taskId, messageId, reviewData: reviewData as any });
    setSelectedDocument(null);
    setWordDocument(null);
    setExcelDocument(null);
    setCaseReport(null);
  };

  // 审核编辑确认回调
  const handleReviewEditorConfirm = (data: any, isEdited: boolean) => {
    if (!reviewEditorData) return;

    const { taskId, messageId } = reviewEditorData;

    if (isEdited) {
      void resumeReportGeneration({ action: "edit", data }, taskId, messageId, createAutoPreviewHandler());
    } else {
      void resumeReportGeneration({ action: "approve" }, taskId, messageId, createAutoPreviewHandler());
    }

    setReviewEditorData(null);
  };

  // 审核编辑取消回调
  const handleReviewEditorCancel = () => {
    if (!reviewEditorData) return;

    const { taskId, messageId } = reviewEditorData;
    void resumeReportGeneration({ action: "reject" }, taskId, messageId);
    setReviewEditorData(null);
  };

  // 当选择了文档或审核编辑时显示右边面板
  const isDocumentViewerVisible = selectedDocument !== null || wordDocument !== null || excelDocument !== null || caseReport !== null || reviewEditorData !== null;

  // 处理拖动开始
  const handleDragStart = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  // 处理拖动
  const handleDrag = useCallback((e: MouseEvent) => {
    if (!isDragging) return;

    const container = document.getElementById('embedded-resizable-container');
    if (!container) return;

    const containerRect = container.getBoundingClientRect();
    const availableWidth = containerRect.width;
    const mouseX = e.clientX - containerRect.left;

    // 计算新的宽度百分比（限制在 30% - 70% 之间）
    let newWidth = (mouseX / availableWidth) * 100;
    newWidth = Math.max(30, Math.min(70, newWidth));

    setConversationWidth(newWidth);
  }, [isDragging]);

  // 处理拖动结束
  const handleDragEnd = useCallback(() => {
    setIsDragging(false);
  }, []);

  // 监听拖动事件
  useEffect(() => {
    if (isDragging) {
      document.addEventListener('mousemove', handleDrag);
      document.addEventListener('mouseup', handleDragEnd);
      return () => {
        document.removeEventListener('mousemove', handleDrag);
        document.removeEventListener('mouseup', handleDragEnd);
      };
    }
  }, [isDragging, handleDrag, handleDragEnd]);

  const inspectionSummary = activeHistory
    ? {
        inspectionContent: activeHistory.inspectionContent,
        violationDescription: activeHistory.violationDescription,
      }
    : {
        inspectionContent: "",
        violationDescription: violationDescriptionValue,
      };

  // 初始加载或标题刷新时，从历史列表中查找标题
  useEffect(() => {
    if (!chatThreadId) {
      return;
    }

    if (historyRefreshTrigger === 0 && initialTitleLoadedRef.current) {
      return;
    }

    const fetchThreadTitle = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/conversations`, {
          credentials: "include",
        });

        if (response.ok) {
          const data = await response.json();
          const conversations = data.data || [];
          const currentConversation = conversations.find((c: any) => c.id === chatThreadId);
          if (currentConversation?.title !== undefined) {
            setCurrentThreadTitle(currentConversation.title);
          }
          initialTitleLoadedRef.current = true;
        }
      } catch (error) {
        console.error("获取会话标题失败:", error);
      }
    };

    fetchThreadTitle();
  }, [historyRefreshTrigger, chatThreadId]);

  // 获取当前会话标题
  const currentConversationTitle = useMemo(() => {
    if (activeHistory?.title) {
      return activeHistory.title;
    }
    return currentThreadTitle;
  }, [activeHistory, currentThreadTitle]);

  // 当标题变化时，通知父组件更新顶部标题
  useEffect(() => {
    if (currentConversationTitle) {
      onTitleChange?.(currentConversationTitle);
    }
  }, [currentConversationTitle, onTitleChange]);

  // 报告审核任务ID列表
  const REPORT_REVIEW_TASK_IDS = ["review_extract", "review_analyze", "review_retrieve", "review_evidence"];

  // 查找包含指定任务的消息
  const findMessageWithTask = useCallback((taskId: string) => {
    return chatMessages.find(msg => {
      if (!Array.isArray(msg.content)) return false;
      const taskBlock = msg.content.find(
        (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
      );
      if (!taskBlock) return false;
      const tasks = (taskBlock as any).value?.data || [];
      return tasks.some((t: any) => t.id === taskId && t.status === 'awaiting_confirm');
    });
  }, [chatMessages]);

  // 自动预览处理函数
  const createAutoPreviewHandler = useCallback(() => {
    return (doc: { filename: string; title: string; excelData?: any; sourceData?: any }) => {
      const isExcel = doc.filename.endsWith('.xlsx') || doc.filename.endsWith('.xls');
      if (isExcel && doc.excelData) {
        handleExcelDocumentView({ filename: doc.filename, title: doc.title, excelData: doc.excelData });
      } else {
        handleWordDocumentView({ filename: doc.filename, title: doc.title, sourceData: doc.sourceData });
      }
    };
  }, []);

  // 包装的审核回调
  const handleReviewApproveWrapper = useCallback((taskId: string) => {
    if (REPORT_REVIEW_TASK_IDS.includes(taskId)) {
      const messageWithTask = findMessageWithTask(taskId);
      if (messageWithTask) {
        void resumeReportGeneration({ action: "approve" }, taskId, messageWithTask.id, createAutoPreviewHandler());
      }
    } else {
      handleHumanReviewApprove(taskId);
    }
  }, [findMessageWithTask, handleHumanReviewApprove, resumeReportGeneration, createAutoPreviewHandler]);

  const handleReviewEditWrapper = useCallback((taskId: string, data: any) => {
    if (REPORT_REVIEW_TASK_IDS.includes(taskId)) {
      const messageWithTask = findMessageWithTask(taskId);
      if (messageWithTask) {
        void resumeReportGeneration({ action: "edit", data }, taskId, messageWithTask.id, createAutoPreviewHandler());
      }
    } else {
      handleHumanReviewEdit(taskId, data);
    }
  }, [findMessageWithTask, handleHumanReviewEdit, resumeReportGeneration, createAutoPreviewHandler]);

  const handleReviewRejectWrapper = useCallback((taskId: string) => {
    if (REPORT_REVIEW_TASK_IDS.includes(taskId)) {
      const messageWithTask = findMessageWithTask(taskId);
      if (messageWithTask) {
        void resumeReportGeneration({ action: "reject" }, taskId, messageWithTask.id);
      }
    } else {
      handleHumanReviewReject(taskId);
    }
  }, [findMessageWithTask, handleHumanReviewReject, resumeReportGeneration]);

  // 处理添加或更新聊天消息
  const handleAddChatMessage = (message: ChatMessage) => {
    setChatMessages(prev => {
      const existingIndex = prev.findIndex(msg => msg.id === message.id);
      if (existingIndex !== -1) {
        const newMessages = [...prev];
        newMessages[existingIndex] = message;
        return newMessages;
      } else {
        return [...prev, message];
      }
    });
  };

  // 自动展开审核编辑器
  useEffect(() => {
    if (reviewEditorData) return;

    for (const msg of chatMessages) {
      if (msg.role !== 'assistant' || !Array.isArray(msg.content)) continue;

      const taskBlock = msg.content.find(
        (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
      );
      if (!taskBlock) continue;

      const tasks = (taskBlock as any).value?.data || [];
      const awaitingTask = tasks.find((t: any) => t.status === 'awaiting_confirm' && t.reviewData);

      if (awaitingTask) {
        handleOpenReviewEditor(awaitingTask.id, msg.id, awaitingTask.reviewData);
        break;
      }
    }
  }, [chatMessages, reviewEditorData]);

  // 新建检查（在嵌入模式下简化为清空状态）
  const handleStartNewInspection = () => {
    setAnalysisStage("initial");
    setAnalysisResult(null);
    setTaskStatus(null);
    setActiveHistoryId(null);
    resetChatHistory();
    setCurrentThreadTitle(undefined);
    initialTitleLoadedRef.current = false;
    setSelectedDocument(null);
    setWordDocument(null);
    setExcelDocument(null);
    setCaseReport(null);
    setUpdatedWordFilename(undefined);
    setResetTrigger(prev => prev + 1);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="w-full h-full"
    >
      <div
        id="embedded-resizable-container"
        className="flex items-start gap-0 w-full h-full bg-white dark:bg-gray-900"
      >
        {/* 对话区域 */}
        <div className="relative flex-1 flex min-w-0 h-full">
          <div
            className={isDocumentViewerVisible ? 'flex-shrink-0 h-full' : 'flex justify-center h-full'}
            style={{
              width: isDocumentViewerVisible ? `${conversationWidth}%` : '100%',
              minWidth: '300px',
            }}
          >
            <div className="h-full" style={{ width: '100%', maxWidth: undefined }}>
              <EmbeddedConversation
                analysisStage={analysisStage}
                analysisHistory={analysisHistory}
                analysisTasks={analysisTasks}
                chatMessages={chatMessages}
                chatInput={chatInput}
                setChatInput={setChatInput}
                isChatStreaming={isChatStreaming}
                chatStreamingStatus={chatStreamingStatus}
                activeAssistantMessageId={activeAssistantMessageId}
                hasFiveRequiredInputs={hasFiveRequiredInputs}
                missingRequiredLabels={missingRequiredLabels}
                inspectionSummary={inspectionSummary}
                analysisResult={analysisResult}
                register={register}
                handleSubmit={handleSubmit}
                errors={errors}
                onSubmit={onSubmit}
                isAnalyzing={isAnalyzing}
                chatContainerRef={chatContainerRef}
                sendChatMessage={sendChatMessage}
                sendReportRequest={sendReportRequest}
                stopChatStreaming={stopChatStreaming}
                onStartNewInspection={handleStartNewInspection}
                conversationTitle={currentConversationTitle}
                onLawClick={handleLawClickWrapper}
                onWordDocumentView={handleWordDocumentView}
                onExcelDocumentView={handleExcelDocumentView}
                onReportClick={handleReportClick}
                panelHeightClass={panelHeightClass}
                threadId={chatThreadId}
                onAddChatMessage={handleAddChatMessage}
                onEnsureThread={createNewThread}
                resetTrigger={resetTrigger}
                updatedWordFilename={updatedWordFilename}
                agentId={agentId}
                guide={agentGuide}
              />
            </div>
          </div>

          {/* 文档查看器区域 */}
          <AnimatePresence>
            {isDocumentViewerVisible && (
              <>
                {/* 拖动分隔条 */}
                <div
                  className={mergeClassNames(
                    "flex items-center justify-center cursor-col-resize bg-gray-100 hover:bg-indigo-200 dark:bg-gray-800 dark:hover:bg-indigo-900 transition-colors",
                    panelHeightClass,
                    isDragging ? "bg-indigo-300 dark:bg-indigo-800" : ""
                  )}
                  style={{ width: '4px', userSelect: 'none' }}
                  onMouseDown={handleDragStart}
                >
                  <div className="w-0.5 h-12 bg-gray-400 dark:bg-gray-600 rounded-full"></div>
                </div>

                {/* 文档查看器 */}
                <div
                  className="overflow-x-auto flex-shrink-0"
                  style={{
                    width: `${100 - conversationWidth}%`,
                    minWidth: '300px',
                  }}
                >
                  <Console
                    key={reviewEditorData?.taskId || caseReport?.name || wordDocument?.filename || excelDocument?.filename || selectedDocument?.law_item_name || 'default'}
                    document={selectedDocument}
                    wordDocument={wordDocument}
                    excelDocument={excelDocument}
                    caseReport={caseReport}
                    reviewEditor={reviewEditorData ? {
                      reviewData: reviewEditorData.reviewData,
                      onConfirm: handleReviewEditorConfirm,
                      onCancel: handleReviewEditorCancel,
                    } : null}
                    onClose={() => {
                      setSelectedDocument(null);
                      setWordDocument(null);
                      setExcelDocument(null);
                      setCaseReport(null);
                      setReviewEditorData(null);
                    }}
                    onRegenerate={handleReportRegenerate}
                    panelHeightClass={panelHeightClass}
                    threadId={chatThreadId || ''}
                  />
                </div>
              </>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  );
}
