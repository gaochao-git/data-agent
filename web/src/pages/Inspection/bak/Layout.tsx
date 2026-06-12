import { useMemo, useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { FeaturePageLayout } from "../../components/FeaturePageLayout";
import { useViolationAnalysis } from "./hooks";
import { History } from "./components/History";
import { Conversation } from "./components/Conversation";
import { Console } from "./components/Console";
import { mergeClassNames } from "./utils";
import { panelHeightClass } from "./constants";
import { API_BASE } from "../../lib/config";
import type { ChatMessage } from "./types";

export function Layout() {
  const navigate = useNavigate();

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
    isHistoryCollapsed,
    setIsHistoryCollapsed,
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

    // Human-in-the-Loop 人工审核回调
    handleHumanReviewApprove,
    handleHumanReviewEdit,
    handleHumanReviewReject,

    // 报告生成恢复
    resumeReportGeneration,
  } = useViolationAnalysis();

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

  // 可调整面板宽度状态（百分比 - 相对于左侧栏右边的可用空间）
  const [conversationWidth, setConversationWidth] = useState(50); // 对话面板占右侧可用空间的 50%（右侧显示时）
  const [isDragging, setIsDragging] = useState(false);

  // 重置输入区域的触发器
  const [resetTrigger, setResetTrigger] = useState(0);

  // 当前会话标题（从后端获取）
  const [currentThreadTitle, setCurrentThreadTitle] = useState<string | undefined>(undefined);

  // 标记是否已经初始加载过标题
  const initialTitleLoadedRef = useRef(false);

  // 处理法律条款预览（包装原始 handleLawClick，增加清除其他预览状态）
  const handleLawClickWrapper = (law: { law_name: string; law_item_name: string; law_item_content: string }) => {
    handleLawClick(law);
    setWordDocument(null); // 关闭Word文档预览
    setExcelDocument(null); // 关闭Excel文档预览
    setCaseReport(null); // 关闭报告预览
  };

  // 处理 Word 文档预览
  const handleWordDocumentView = (doc: { filename: string; title: string; sourceData?: any }) => {
    setWordDocument(doc);
    setSelectedDocument(null); // 关闭法律文档预览
    setExcelDocument(null); // 关闭Excel文档预览
    setCaseReport(null); // 关闭报告预览
  };

  // 处理报告重新生成
  const handleReportRegenerate = (filename: string, sourceData: any) => {
    // 更新 wordDocument 状态，重新加载预览
    setWordDocument({ filename, title: wordDocument?.title || '检查报告', sourceData });
    // 更新文件名，使下载链接指向新文件
    setUpdatedWordFilename(filename);
  };

  // 处理 Excel 文档预览
  const handleExcelDocumentView = (doc: { filename: string; title: string; excelData: any }) => {
    setExcelDocument(doc);
    setSelectedDocument(null); // 关闭法律文档预览
    setWordDocument(null); // 关闭Word文档预览
    setCaseReport(null); // 关闭报告预览
  };

  // 处理相似案例报告预览
  const handleReportClick = (report: { name: string; content: string }) => {
    setCaseReport(report);
    setSelectedDocument(null); // 关闭法律文档预览
    setWordDocument(null); // 关闭Word文档预览
    setExcelDocument(null); // 关闭Excel文档预览
  };

  // 打开审核编辑器（在右侧控制台）- 支持所有审核类型
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
      // 有修改，调用 edit 回调
      void resumeReportGeneration({ action: "edit", data }, taskId, messageId, createAutoPreviewHandler());
    } else {
      // 无修改，调用 approve 回调
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

    const container = document.getElementById('resizable-container');
    if (!container) return;

    const containerRect = container.getBoundingClientRect();
    const historyWidth = isHistoryCollapsed ? 56 : 260; // 3.5rem or 260px
    const availableWidth = containerRect.width - historyWidth;
    const mouseX = e.clientX - containerRect.left - historyWidth;

    // 计算新的宽度百分比（限制在 30% - 70% 之间，确保两侧都可见）
    let newWidth = (mouseX / availableWidth) * 100;
    newWidth = Math.max(30, Math.min(70, newWidth));

    setConversationWidth(newWidth);
  }, [isDragging, isHistoryCollapsed]);

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

  // 当 History 组件加载完 threads 后，查找当前会话的标题
  const handleThreadsLoaded = useCallback((threads: Array<{ id: string; title: string }>) => {
    if (!chatThreadId) return;
    const currentConversation = threads.find((c) => c.id === chatThreadId);
    if (currentConversation?.title !== undefined) {
      setCurrentThreadTitle(currentConversation.title);
    }
    initialTitleLoadedRef.current = true;
  }, [chatThreadId]);

  // 获取当前会话标题：优先使用 activeHistory，否则使用从后端获取的标题
  const currentConversationTitle = useMemo(() => {
    if (activeHistory?.title) {
      return activeHistory.title;
    }
    return currentThreadTitle;
  }, [activeHistory, currentThreadTitle]);

  // 重置会话到初始状态（新建会话）
  const resetConversation = async () => {
    // 清理分析状态
    setAnalysisStage("initial");
    setAnalysisResult(null);
    setTaskStatus(null);
    setActiveHistoryId(null);

    // 清理聊天历史和线程
    resetChatHistory();

    // 清理当前会话标题
    setCurrentThreadTitle(undefined);
    initialTitleLoadedRef.current = false;

    // 清理文档预览
    setSelectedDocument(null);
    setWordDocument(null);
    setExcelDocument(null);
    setCaseReport(null);
    setUpdatedWordFilename(undefined);

    // 触发 Conversation 组件重置输入区域
    setResetTrigger(prev => prev + 1);

    // 导航回初始页面（不带 thread_id）
    navigate("/violation-entry", { replace: true });
  };

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

  // 包装的审核回调 - 根据 taskId 判断是分析审核还是报告审核
  const handleReviewApproveWrapper = useCallback((taskId: string) => {
    if (REPORT_REVIEW_TASK_IDS.includes(taskId)) {
      // 报告生成审核：找到包含该任务的消息并恢复
      const messageWithTask = findMessageWithTask(taskId);
      if (messageWithTask) {
        void resumeReportGeneration({ action: "approve" }, taskId, messageWithTask.id, createAutoPreviewHandler());
      }
    } else {
      // 分析审核
      handleHumanReviewApprove(taskId);
    }
  }, [findMessageWithTask, handleHumanReviewApprove, resumeReportGeneration, createAutoPreviewHandler]);

  const handleReviewEditWrapper = useCallback((taskId: string, data: any) => {
    if (REPORT_REVIEW_TASK_IDS.includes(taskId)) {
      // 报告生成审核
      const messageWithTask = findMessageWithTask(taskId);
      if (messageWithTask) {
        void resumeReportGeneration({ action: "edit", data }, taskId, messageWithTask.id, createAutoPreviewHandler());
      }
    } else {
      // 分析审核
      handleHumanReviewEdit(taskId, data);
    }
  }, [findMessageWithTask, handleHumanReviewEdit, resumeReportGeneration, createAutoPreviewHandler]);

  const handleReviewRejectWrapper = useCallback((taskId: string) => {
    if (REPORT_REVIEW_TASK_IDS.includes(taskId)) {
      // 报告生成审核
      const messageWithTask = findMessageWithTask(taskId);
      if (messageWithTask) {
        void resumeReportGeneration({ action: "reject" }, taskId, messageWithTask.id);
      }
    } else {
      // 分析审核
      handleHumanReviewReject(taskId);
    }
  }, [findMessageWithTask, handleHumanReviewReject, resumeReportGeneration]);

  // 处理添加或更新聊天消息
  const handleAddChatMessage = (message: ChatMessage) => {
    setChatMessages(prev => {
      // 检查是否已存在该 ID 的消息
      const existingIndex = prev.findIndex(msg => msg.id === message.id);
      if (existingIndex !== -1) {
        // 更新现有消息
        const newMessages = [...prev];
        newMessages[existingIndex] = message;
        return newMessages;
      } else {
        // 添加新消息
        return [...prev, message];
      }
    });
  };

  // 自动展开审核编辑器：监听消息变化，当有 awaiting_confirm 任务时自动打开
  useEffect(() => {
    // 如果已经有审核编辑器打开，不重复处理
    if (reviewEditorData) return;

    // 遍历消息查找 awaiting_confirm 状态的任务
    for (const msg of chatMessages) {
      if (msg.role !== 'assistant' || !Array.isArray(msg.content)) continue;

      const taskBlock = msg.content.find(
        (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
      );
      if (!taskBlock) continue;

      const tasks = (taskBlock as any).value?.data || [];
      const awaitingTask = tasks.find((t: any) => t.status === 'awaiting_confirm' && t.reviewData);

      if (awaitingTask) {
        // 自动打开审核编辑器
        console.log("[Layout] 找到待确认任务:", awaitingTask.id);
        console.log("[Layout] reviewData:", awaitingTask.reviewData);
        console.log("[Layout] reviewData.current_data:", awaitingTask.reviewData?.current_data);
        console.log("[Layout] reviewData.current_data.files:", awaitingTask.reviewData?.current_data?.files);
        handleOpenReviewEditor(awaitingTask.id, msg.id, awaitingTask.reviewData);
        break;
      }
    }
  }, [chatMessages, reviewEditorData]);

  return (
    <FeaturePageLayout
      title="执法检查管理"
      iconClassName="fa-solid fa-scale-balanced"
      mainClassName="w-full max-w-none mx-0 px-0 pt-0 pb-0"
    >
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="w-full"
      >
        <div
          id="resizable-container"
          className="flex items-start gap-0 w-full bg-white dark:bg-gray-900"
        >
          <History
            activeThreadId={chatThreadId}
            isHistoryCollapsed={isHistoryCollapsed}
            onToggleCollapse={() => setIsHistoryCollapsed((prev) => !prev)}
            onThreadsLoaded={handleThreadsLoaded}
            onSelectThread={async (threadId, title) => {
              try {
                // 清空分析历史的选中状态（切换到聊天历史）
                setActiveHistoryId(null);

                // 更新当前会话标题（直接使用从列表传递的标题）
                setCurrentThreadTitle(title);

                // 清除编辑后的文件名状态（切换会话时重置）
                setUpdatedWordFilename(undefined);

                // 切换到选中的会话
                const response = await fetch(`${API_BASE}/api/violation/conversations/${threadId}`, {
                  credentials: "include",
                });

                if (!response.ok) {
                  throw new Error(`HTTP ${response.status}`);
                }

                const data = await response.json();
                // business_response 格式：{ status, msg, code, data: { messages, title, interrupt } }
                const messages = data.data.messages;
                const interruptInfo = data.data.interrupt;

                // 清空当前聊天消息
                setChatMessages([]);

                // 加载历史消息到聊天界面
                // 过滤：只保留用户消息和最后一条带 task_progress 的 assistant 消息
                let formattedMessages: ChatMessage[] = [];
                let lastTaskProgressMsg: any = null;
                let lastTaskProgressIndex = -1;

                // 先找到最后一条带 task_progress 的 assistant 消息
                for (let i = messages.length - 1; i >= 0; i--) {
                  const msg = messages[i];
                  if (msg.role === 'assistant' && Array.isArray(msg.content)) {
                    const hasTaskProgress = msg.content.some(
                      (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
                    );
                    if (hasTaskProgress) {
                      lastTaskProgressMsg = msg;
                      lastTaskProgressIndex = i;
                      break;
                    }
                  }
                }

                // 构建消息列表：用户消息 + 最后一条进度消息
                messages.forEach((msg: any, index: number) => {
                  if (msg.role === 'user') {
                    // 保留所有用户消息
                    formattedMessages.push({
                      id: `history-${threadId}-${index}`,
                      role: msg.role,
                      content: msg.content,
                      timestamp: new Date(),
                      additional_kwargs: msg.additional_kwargs,
                    });
                  } else if (msg.role === 'assistant') {
                    // 只保留最后一条带 task_progress 的 assistant 消息
                    if (index === lastTaskProgressIndex) {
                      formattedMessages.push({
                        id: `history-${threadId}-${index}`,
                        role: msg.role,
                        content: msg.content,
                        timestamp: new Date(),
                        additional_kwargs: msg.additional_kwargs,
                      });
                    }
                    // 跳过其他中间的进度消息
                  }
                });

                console.log("[历史加载] 原始消息:", messages.length, "过滤后:", formattedMessages.length);

                // 如果存在中断状态，恢复审核任务状态
                if (interruptInfo && interruptInfo.interrupt_data) {
                  const reviewData = interruptInfo.interrupt_data;
                  const reviewType = reviewData.review_type;

                  // 根据 review_type 确定任务ID
                  const reviewTypeToTaskId: Record<string, string> = {
                    "split": "review_split",
                    "retrieve": "review_retrieve",
                    "extract": "review_extract",
                    "analyze": "review_analyze",
                    "evidence": "review_evidence",
                  };
                  const taskIdToUpdate = reviewTypeToTaskId[reviewType];

                  console.log("[历史加载] 检测到中断状态:", reviewType, "任务ID:", taskIdToUpdate);

                  // 找到最后一条 assistant 消息，更新其任务状态
                  const lastAssistantIndex = formattedMessages.findLastIndex(msg => msg.role === "assistant");
                  if (lastAssistantIndex >= 0 && taskIdToUpdate) {
                    const lastMsg = formattedMessages[lastAssistantIndex];
                    let newContent = Array.isArray(lastMsg.content) ? [...lastMsg.content] : [];

                    // 查找 task_progress 块并更新
                    const taskBlockIndex = newContent.findIndex(
                      (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
                    );

                    if (taskBlockIndex >= 0) {
                      const currentTasks = (newContent[taskBlockIndex] as any).value?.data || [];
                      const updatedTasks = currentTasks.map((t: any) => {
                        if (t.id === taskIdToUpdate) {
                          return {
                            ...t,
                            status: "awaiting_confirm",
                            reviewData: reviewData,
                          };
                        }
                        return t;
                      });

                      newContent[taskBlockIndex] = {
                        type: "non_standard",
                        value: {
                          type: "task_progress",
                          data: updatedTasks,
                        },
                      };

                      formattedMessages[lastAssistantIndex] = {
                        ...lastMsg,
                        content: newContent,
                      };

                      console.log("[历史加载] 已恢复中断状态，任务:", taskIdToUpdate, "更新后的任务列表:", updatedTasks);
                    } else {
                      console.log("[历史加载] 未找到 task_progress 块");
                    }
                  } else {
                    console.log("[历史加载] 未找到 assistant 消息或任务ID");
                  }
                }

                setChatMessages(formattedMessages);

                // 设置当前 threadId (这样后续对话会继续在这个线程中)
                switchToThread(threadId);
              } catch (error) {
                console.error("加载会话消息失败:", error);
              }
            }}
            onStartNewInspection={resetConversation}
            panelHeightClass={panelHeightClass}
            refreshTrigger={historyRefreshTrigger}
          />

          {/* 始终渲染 Conversation，用遮罩层显示加载状态 */}
          <div className="relative flex-1 flex min-w-0">
            <div
              className={isDocumentViewerVisible ? 'flex-shrink-0' : 'flex justify-center'}
              style={{
                width: isDocumentViewerVisible ? `${conversationWidth}%` : '100%',
                minWidth: '300px',
              }}
            >
              <div
                style={{
                  width: '100%',
                  maxWidth: undefined,
                }}
              >
              <Conversation
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
                onStartNewInspection={resetConversation}
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
              />
              </div>
            </div>

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
    </FeaturePageLayout>
  );
}
