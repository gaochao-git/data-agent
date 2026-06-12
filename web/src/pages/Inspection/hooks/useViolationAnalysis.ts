import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { API_BASE } from "../../../lib/config";
import { violationFormSchema, createInitialChatMessages, requiredLabelsMap } from "../constants";
import { createMessageId, ensureThreadId, appendMessageContent, setMessageError } from "../utils";
import { processSSEStream } from "./useSSEStream";
import type {
  AnalysisResult,
  SSETaskStatus,
  ChatMessage,
  AnalysisStage,
  AnalysisHistoryItem,
  ViolationFormValues,
  AnalysisTask,
  HumanReviewData,
  ResumeInput,
} from "../types";

interface UseViolationAnalysisOptions {
  /** 后端 agent_id */
  agentId?: string;
}

// 默认 agent_id
const DEFAULT_AGENT_ID = "report_jcbg";

export function useViolationAnalysis(options: UseViolationAnalysisOptions = {}) {
  const { agentId = DEFAULT_AGENT_ID } = options;

  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [taskStatus, setTaskStatus] = useState<SSETaskStatus | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(() =>
    createInitialChatMessages(),
  );
  const [chatInput, setChatInput] = useState("");
  const [isChatStreaming, setIsChatStreaming] = useState(false);
  const [chatStreamingStatus, setChatStreamingStatus] = useState("");
  const [activeAssistantMessageId, setActiveAssistantMessageId] = useState<string | null>(null);
  const [currentChatTaskId, setCurrentChatTaskId] = useState("");
  const [chatThreadId, setChatThreadId] = useState("");
  const [analysisStage, setAnalysisStage] = useState<AnalysisStage>("initial");
  const [analysisHistory, setAnalysisHistory] = useState<AnalysisHistoryItem[]>([]);
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);
  const [isHistoryCollapsed, setIsHistoryCollapsed] = useState(false);
  const [analysisTasks, setAnalysisTasks] = useState<AnalysisTask[]>([]);
  const [selectedDocument, setSelectedDocument] = useState<{
    law_name: string;
    law_item_name: string;
    law_item_content: string;
  } | null>(null);
  const [historyRefreshTrigger, setHistoryRefreshTrigger] = useState(0); // 历史记录刷新触发器

  // Human-in-the-Loop: 当前待审核的任务ID（用于追踪哪个任务处于待确认状态）
  const pendingReviewTaskIdRef = useRef<string | null>(null);

  const chatContainerRef = useRef<HTMLDivElement | null>(null);
  const chatAbortControllerRef = useRef<AbortController | null>(null);
  const analysisAbortControllerRef = useRef<AbortController | null>(null); // 分析流的 AbortController
  const chatTaskIdRef = useRef<string>("");
  const manualStopRef = useRef(false);
  const lastProgressMessageRef = useRef<string>("");
  const activeHistoryIdRef = useRef<string | null>(null);
  const tasksMessageIdRef = useRef<string | null>(null);
  const loadedThreadIdRef = useRef<string | null>(null); // 记录已加载历史消息的 thread_id

  useEffect(() => {
    activeHistoryIdRef.current = activeHistoryId;
  }, [activeHistoryId]);

  // 加载指定会话的历史消息
  const loadThreadMessages = useCallback(async (threadId: string) => {
    if (!threadId || loadedThreadIdRef.current === threadId) {
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/api/violation/conversations/${threadId}`, {
        credentials: "include",
      });

      if (!response.ok) {
        console.error('[加载会话] 加载会话失败:', response.status);
        return;
      }

      const data = await response.json();
      const messages = data.data.messages;
      const interruptInfo = data.data.interrupt;
      const persistedTaskProgress = data.data.task_progress;
      let lastTaskProgressIndex = -1;
      for (let i = messages.length - 1; i >= 0; i--) {
        const msg = messages[i];
        if (msg.role === 'assistant' && Array.isArray(msg.content)) {
          const hasTaskProgress = msg.content.some(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
          );
          if (hasTaskProgress) {
            lastTaskProgressIndex = i;
            break;
          }
        }
      }

      const formattedMessages: ChatMessage[] = [];

      messages.forEach((msg: any, index: number) => {
        if (msg.role === 'assistant' && Array.isArray(msg.content)) {
          const hasTaskProgress = msg.content.some(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
          );
          if (hasTaskProgress && index !== lastTaskProgressIndex) {
            return;
          }
        }

        let content = msg.content;

        if (Array.isArray(content)) {
          const hasFile = content.some((b: any) => b.type === 'file');
          if (hasFile) {
            content = content.map((block: any) => {
              if (block.type === 'non_standard' && block.value?.type === 'task_progress') {
                return {
                  ...block,
                  value: {
                    ...block.value,
                    data: (block.value.data || []).map((task: AnalysisTask) => ({
                      ...task,
                      status: 'completed' as const
                    }))
                  }
                };
              }
              return block;
            });
          }
        }

        formattedMessages.push({
          id: `history-${threadId}-${index}`,
          role: msg.role,
          content: content,
          timestamp: new Date(),
          additional_kwargs: msg.additional_kwargs,
        });
      });

      console.log('[加载会话] 原始消息:', messages.length, '过滤后:', formattedMessages.length);

      if (interruptInfo && interruptInfo.interrupt_data) {
        const reviewData = interruptInfo.interrupt_data;
        const reviewType = reviewData.review_type;

        const reviewTypeToTaskId: Record<string, string> = {
          "split": "review_split",
          "retrieve": "review_retrieve",
          "extract": "review_extract",
          "analyze": "review_analyze",
          "evidence": "review_evidence",
        };
        const taskIdToUpdate = reviewTypeToTaskId[reviewType];

        console.log('[加载会话] 检测到中断状态:', reviewType, '任务ID:', taskIdToUpdate);

        const lastAssistantIndex = formattedMessages.findLastIndex(msg => msg.role === "assistant");
        if (lastAssistantIndex >= 0 && taskIdToUpdate) {
          const lastMsg = formattedMessages[lastAssistantIndex];
          let newContent = Array.isArray(lastMsg.content) ? [...lastMsg.content] : [];

          const taskBlockIndex = newContent.findIndex(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
          );

          let baseTasks: any[] = [];
          if (persistedTaskProgress && persistedTaskProgress.length > 0) {
            baseTasks = persistedTaskProgress;
          } else if (taskBlockIndex >= 0) {
            baseTasks = (newContent[taskBlockIndex] as any).value?.data || [];
          }

          const updatedTasks = baseTasks.map((t: any) => {
            if (t.id === taskIdToUpdate) {
              return {
                ...t,
                status: "awaiting_confirm",
                reviewData: reviewData,
              };
            }
            return t;
          });

          const taskBlock = {
            type: "non_standard",
            value: {
              type: "task_progress",
              data: updatedTasks,
            },
          };

          if (taskBlockIndex >= 0) {
            newContent[taskBlockIndex] = taskBlock;
          } else {
            newContent.push(taskBlock);
          }

          formattedMessages[lastAssistantIndex] = {
            ...lastMsg,
            content: newContent,
          };
        }
      }

      setChatMessages(formattedMessages);
      setChatThreadId(threadId);
      loadedThreadIdRef.current = threadId;
    } catch (error) {
      console.error('[加载会话] 加载会话消息失败:', error);
    }
  }, []);


  const missingRequiredLabels = useMemo(() => {
    const r = analysisResult;
    if (!r || !r.违法行为分析 || r.违法行为分析.length === 0) {
      return Object.values(requiredLabelsMap);
    }

    const firstViolation = r.违法行为分析[0];
    if (!firstViolation) {
      return Object.values(requiredLabelsMap);
    }

    const fieldChecks = [
      { field: firstViolation.违反条款, label: requiredLabelsMap.legalBasis },
      { field: firstViolation.处罚条款, label: requiredLabelsMap.penaltyClauseList },
      { field: firstViolation.整改措施, label: requiredLabelsMap.rectificationMeasures },
      { field: firstViolation.裁量基准, label: requiredLabelsMap.adminPenaltyStandards },
      { field: firstViolation.处罚建议, label: requiredLabelsMap.penaltySuggestions },
    ];

    return fieldChecks
      .filter(({ field }) => !Array.isArray(field) || field.length === 0)
      .map(({ label }) => label);
  }, [analysisResult]);

  const hasFiveRequiredInputs = useMemo(
    () => missingRequiredLabels.length === 0,
    [missingRequiredLabels],
  );

  const {
    register,
    handleSubmit,
    formState: { errors },
    reset,
    getValues,
    watch,
  } = useForm<ViolationFormValues>({
    resolver: zodResolver(violationFormSchema),
  });

  const violationDescriptionValue = watch("violationDescription");

  const activeHistory = useMemo(
    () => analysisHistory.find((item) => item.id === activeHistoryId) ?? null,
    [analysisHistory, activeHistoryId],
  );

  const updateHistoryItem = useCallback(
    (historyId: string, updater: (item: AnalysisHistoryItem) => AnalysisHistoryItem) => {
      setAnalysisHistory((prev) =>
        prev.map((item) => (item.id === historyId ? updater(item) : item)),
      );
    },
    [],
  );

  // 跟踪已生成标题的线程 ID，确保每个线程只生成一次标题
  const generatedTitlesRef = useRef<Set<string>>(new Set());

  const generateAndUpdateTitle = useCallback(async (threadId: string) => {
    // 检查是否已经为这个线程生成过标题
    if (generatedTitlesRef.current.has(threadId)) {
      return;
    }

    // 标记为正在生成
    generatedTitlesRef.current.add(threadId);

    try {
      const response = await fetch(`${API_BASE}/api/conversations/${threadId}/title`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`生成标题失败: ${response.status}`);
      }

      const data = await response.json();
      // business_response 格式：{ status, msg, code, data: { title } }
      const title = data.data.title;

      // 更新当前 activeHistoryId 对应的历史记录
      if (activeHistoryIdRef.current) {
        updateHistoryItem(activeHistoryIdRef.current, (item) => ({
          ...item,
          title: title,
        }));
      }
      // 触发历史记录刷新
      setHistoryRefreshTrigger(prev => prev + 1);
    } catch (error) {
      console.warn("[标题生成] 失败:", error);
      // 失败时从 Set 中移除，允许重试
      generatedTitlesRef.current.delete(threadId);
    }
  }, [updateHistoryItem]);

  const appendAnalysisMessage = useCallback((tasks: AnalysisTask[]) => {
    const messageId = createMessageId();
    tasksMessageIdRef.current = messageId;
    setChatMessages((prev) => [
      ...prev,
      {
        id: messageId,
        role: "assistant",
        content: [
          // 使用 NonStandardContentBlock 存放任务进度
          {
            type: "non_standard",
            value: { type: "task_progress", data: tasks }
          }
        ],
        timestamp: new Date(),
      },
    ]);
  }, []);

  const updateAnalysisMessage = useCallback((
    updates: {
      tasks?: AnalysisTask[];
      analysisResult?: AnalysisResult | null;
      isCompleted?: boolean;
      content?: string;
    }
  ) => {
    const messageId = tasksMessageIdRef.current;
    if (!messageId) {
      console.warn('[updateAnalysisMessage] 跳过更新，messageId为空');
      return;
    }

    setChatMessages((prev) =>
      prev.map((msg) => {
        if (msg.id === messageId) {
          let newContent = Array.isArray(msg.content) ? [...msg.content] : [];

          // 更新任务进度（NonStandardContentBlock）
          if (updates.tasks !== undefined) {
            const taskBlockIndex = newContent.findIndex(
              (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
            );
            const taskBlock = {
              type: 'non_standard',
              value: { type: 'task_progress', data: updates.tasks }
            };
            if (taskBlockIndex >= 0) {
              newContent[taskBlockIndex] = taskBlock;
            } else {
              newContent.push(taskBlock);
            }
          }

          // 更新分析数据（NonStandardContentBlock）
          if (updates.analysisResult !== undefined) {
            const dataBlockIndex = newContent.findIndex(
              (b: any) => b.type === 'non_standard' && b.value?.type === 'analysis_data'
            );
            const dataBlock = {
              type: 'non_standard',
              value: { type: 'analysis_data', data: updates.analysisResult }
            };
            if (dataBlockIndex >= 0) {
              newContent[dataBlockIndex] = dataBlock;
            } else if (updates.analysisResult) {
              newContent.push(dataBlock);
            }
          }

          // 更新文本内容（TextContentBlock）
          if (updates.content !== undefined) {
            const textBlockIndex = newContent.findIndex(
              (b: any) => b.type === 'text'
            );
            const textBlock = { type: 'text', text: updates.content };
            if (textBlockIndex >= 0) {
              newContent[textBlockIndex] = textBlock;
            } else {
              newContent.unshift(textBlock);  // 文本放在最前面
            }
          }

          return {
            ...msg,
            content: newContent
          };
        }
        return msg;
      })
    );
  }, []);

  // 当任务状态更新时，同步更新分析消息中的任务
  useEffect(() => {
    if (analysisTasks.length > 0 && tasksMessageIdRef.current) {
      updateAnalysisMessage({ tasks: analysisTasks });
    }
  }, [analysisTasks, updateAnalysisMessage]);

  const pushProgressMessageFromEvent = (eventData: any) => {
    // 处理 NonStandardContentBlock 格式的任务进度
    if (eventData?.value?.type === "task_progress") {
      // 直接返回，不添加文本消息，任务列表由 TasksMessage 组件单独渲染
      return;
    }

    const candidates = [
      typeof eventData?.message === "string" ? eventData.message.trim() : "",
      typeof eventData?.current_node === "string"
        ? eventData.current_node.trim()
        : "",
      typeof eventData?.status_text === "string"
        ? eventData.status_text.trim()
        : "",
      typeof eventData?.title === "string" ? eventData.title.trim() : "",
      typeof eventData?.data?.message === "string"
        ? eventData.data.message.trim()
        : "",
      typeof eventData?.data?.current_node === "string"
        ? eventData.data.current_node.trim()
        : "",
      typeof eventData?.data?.title === "string"
        ? eventData.data.title.trim()
        : "",
    ];

    let statusText = candidates.find((text) => text.length > 0) || "";

    if (!statusText && eventData?.status === "completed") {
      statusText = "智能分析完成";
    }

    if (
      !statusText &&
      eventData?.status === "error" &&
      typeof eventData?.error === "string"
    ) {
      statusText = `分析失败：${eventData.error}`;
    }

    if (!statusText) {
      return;
    }

    const formattedMessage = statusText;

    // 不要重复相同的消息
    if (lastProgressMessageRef.current === formattedMessage) {
      return;
    }

    lastProgressMessageRef.current = formattedMessage;

    // 根据消息内容更新任务状态
    if (formattedMessage.includes("开始分析") || formattedMessage.includes("分析事实认定")) {
      setAnalysisTasks((prev) =>
        prev.map((task) =>
          task.id === "task-1" ? { ...task, status: "in_progress" } : task
        )
      );
    } else if (formattedMessage.includes("拆分完成") || formattedMessage.includes("识别出")) {
      // 拆分完成 = task-1 完成
      setAnalysisTasks((prev) =>
        prev.map((task) =>
          task.id === "task-1" ? { ...task, status: "completed" } : task
        )
      );
    } else if (formattedMessage.includes("检索法律法规")) {
      setAnalysisTasks((prev) =>
        prev.map((task) =>
          task.id === "task-1"
            ? { ...task, status: "completed" }
            : task.id === "task-2"
              ? { ...task, status: "in_progress" }
              : task
        )
      );
    } else if (formattedMessage.includes("格式化输出") || formattedMessage.includes("整理检索")) {
      setAnalysisTasks((prev) =>
        prev.map((task) =>
          task.id === "task-2"
            ? { ...task, status: "completed" }
            : task.id === "task-3"
              ? { ...task, status: "in_progress" }
              : task
        )
      );
    } else if (formattedMessage.includes("生成分析报告") || formattedMessage.includes("分析完成")) {
      setAnalysisTasks((prev) =>
        prev.map((task) =>
          task.id === "task-2"
            ? { ...task, status: "completed" }
            : task.id === "task-3"
              ? { ...task, status: "completed" }
              : task
        )
      );
    }
  };

  const resetChatHistory = () => {
    chatAbortControllerRef.current?.abort();
    chatAbortControllerRef.current = null;
    setChatMessages(createInitialChatMessages());
    setChatThreadId("");
    setIsChatStreaming(false);
    setChatStreamingStatus("");
    setChatInput("");
    setActiveAssistantMessageId(null);
    setCurrentChatTaskId("");
    chatTaskIdRef.current = "";
    setAnalysisTasks([]);
    loadedThreadIdRef.current = null; // 清空已加载的 thread_id 标记
  };

  // 使用传入的 agentId（已在参数解构时设置默认值）

  /**
   * 创建新线程（符合 LangGraph 官方最佳实践）
   * 在新建会话时立即调用，预先创建 thread_id
   */
  const createNewThread = async (): Promise<string | null> => {
    try {
      const response = await fetch(`${API_BASE}/api/conversations`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          agent_id: agentId,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || '对话创建失败');
      }

      const data = await response.json();
      // business_response 格式：{ status, msg, code, data: { id, created_at } }
      const newThreadId = data.data.id;
      // 立即保存到状态
      setChatThreadId(newThreadId);

      // 标记该 thread_id 已加载,避免 URL 变化时重复加载历史
      loadedThreadIdRef.current = newThreadId;

      return newThreadId;
    } catch (error) {
      console.error('[线程创建] 失败:', error);
      toast.error("线程创建失败", {
        description: error instanceof Error ? error.message : "无法创建会话线程，请重试",
      });
      return null;
    }
  };

  const callViolationAnalysisSSE = async (
    data: ViolationFormValues,
    historyId: string,
    threadId: string,
    retryCount = 0,
  ): Promise<void> => {
    const maxRetries = 3;

    // 不再提前初始化任务，等后端返回 task_progress 时再渲染
    // 任务卡片由 onTask 事件驱动创建

    // 重置消息 ID ref，确保新分析会创建新消息
    tasksMessageIdRef.current = null;
    // 立即更新 activeHistoryIdRef，因为 useEffect 是异步的
    activeHistoryIdRef.current = historyId;
    manualStopRef.current = false;
    setIsAnalyzing(true);
    setIsChatStreaming(true);
    if (activeHistoryIdRef.current === historyId) {
      setAnalysisStage("analyzing");
    }
    setTaskStatus({
      taskId: historyId,
      status: "pending",
      progress: 0,
      currentStep:
        retryCount > 0
          ? `重试中 (${retryCount}/${maxRetries})...`
          : "准备分析...",
    });

    lastProgressMessageRef.current = "";
    updateHistoryItem(historyId, (item) => ({
      ...item,
      status: "analyzing",
      progress: 0,
      error: undefined,
      updatedAt: new Date(),
    }));

    const controller = new AbortController();
    analysisAbortControllerRef.current = controller;

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${threadId}/completion`,
        method: "POST",
        body: {
          query: data.violationDescription,
          agent_id: agentId,
          options: { task_type: "violation_analysis" },
        },
        signal: controller.signal,
        handlers: {
          onMessage: (content) => {
            // 收到文本消息，说明后端走的是 QA 流程（意图识别判断不是分析请求）
            // 创建或追加普通文本消息
            if (!tasksMessageIdRef.current) {
              // 还没有消息，创建一个新的助手消息
              const newMessageId = createMessageId();
              tasksMessageIdRef.current = newMessageId;
              setChatMessages((prev) => [
                ...prev,
                {
                  id: newMessageId,
                  role: "assistant" as const,
                  content: content,
                  timestamp: new Date(),
                }
              ]);
            } else {
              // 已有消息，追加内容
              setChatMessages((prev) =>
                prev.map((msg) =>
                  msg.id === tasksMessageIdRef.current
                    ? { ...msg, content: (typeof msg.content === 'string' ? msg.content : '') + content }
                    : msg
                )
              );
            }
            // 清除任务/分析状态，切换到 QA 模式
            setAnalysisTasks([]);
            setIsAnalyzing(false);
            setAnalysisStage("initial");
            setTaskStatus(null);
          },
          onTask: (tasks) => {
            if (tasks) {
              if (!tasksMessageIdRef.current) {
                appendAnalysisMessage(tasks);
              }
              setAnalysisTasks(tasks);
            }
          },
          onCompleted: (eventData) => {
            // 从 completed 事件中提取 message
            // 后端返回格式：{ messages: [{...}] } 或 { message: {...} }
            const message = eventData?.messages?.[0] || eventData?.message;

            // 从 content 数组中的 NonStandardContentBlock 提取 analysis_data
            let analysisData: AnalysisResult | null = null;
            if (message && Array.isArray(message.content)) {
              const dataBlock = message.content.find(
                (b: any) => b.type === 'non_standard' && b.value?.type === 'analysis_data'
              );
              if (dataBlock) {
                analysisData = dataBlock.value?.data || null;
              }
            }

            // 总是生成标题（无论是分析还是QA流程）
            void generateAndUpdateTitle(threadId);

            if (analysisData) {
              updateHistoryItem(historyId, (item) => ({
                ...item,
                status: "completed",
                result: analysisData,
                updatedAt: new Date(),
              }));

              if (activeHistoryIdRef.current === historyId) {
                setAnalysisResult(analysisData);
                setAnalysisStage("completed");
                setTaskStatus((prev) =>
                  prev ? { ...prev, status: "completed", currentStep: "分析完成" } : null
                );
                setAnalysisTasks((prev) =>
                  prev.map((task) => ({ ...task, status: "completed" as const }))
                );
                updateAnalysisMessage({
                  analysisResult: analysisData,
                  isCompleted: true,
                  content: `分析完成，共识别出 ${analysisData.违法行为数量} 项事实认定`
                });
                pushProgressMessageFromEvent(eventData);
              }
              setIsAnalyzing(false);
              toast.success("违规分析完成！");
            }
          },
          onError: (errorMsg) => {
            console.error("[SSE] 收到错误事件:", errorMsg);
            throw new Error(errorMsg);
          },
          onInterrupt: (interruptData) => {
            // Human-in-the-Loop: 图被中断，等待用户审核
            console.log("[SSE] 收到中断事件:", interruptData);
            const reviewData = interruptData as HumanReviewData;

            // 根据 review_type 确定是哪个任务需要确认
            // 后端任务ID: split, review_split, retrieve, review_retrieve, format
            const taskIdToUpdate = reviewData.review_type === "split"
              ? "review_split"  // 拆分结果审核
              : "review_retrieve"; // 检索结果审核

            pendingReviewTaskIdRef.current = taskIdToUpdate;

            // 更新任务状态为待确认，并附加审核数据
            setAnalysisTasks((prev) =>
              prev.map((task) =>
                task.id === taskIdToUpdate
                  ? { ...task, status: "awaiting_confirm" as const, reviewData }
                  : task.status === "in_progress"
                    ? { ...task, status: "pending" as const }
                    : task
              )
            );
          },
          onEnd: () => {
            // 流结束统一清理 UI 状态
            setIsChatStreaming(false);
            setChatStreamingStatus("");
            setActiveAssistantMessageId(null);
            analysisAbortControllerRef.current = null;
          },
        },
      });
    } catch (error) {
      console.error(`SSE调用失败:`, error);

      const message =
        error instanceof Error ? error.message : "未知错误";

      // 如果是手动停止，设置为完成状态而不是错误状态
      if (manualStopRef.current) {
        console.log('[错误处理] 检测到手动停止，设置完成状态');
        setTaskStatus((prev) =>
          prev
            ? {
                ...prev,
                status: "completed",
              }
            : null,
        );
        updateHistoryItem(historyId, (item) => ({
          ...item,
          status: "completed",
          updatedAt: new Date(),
        }));
        if (activeHistoryIdRef.current === historyId) {
          setAnalysisStage("completed");
        }
        console.log('[错误处理] 完成状态已设置');
      } else {
        console.log('[错误处理] 真实错误，设置错误状态');
        // 真实错误：显示错误提示并设置错误状态
        toast.error(`违规分析失败: ${message}`);
        setTaskStatus((prev) =>
          prev
            ? {
                ...prev,
                status: "error",
                error: message,
              }
            : null,
        );
        updateHistoryItem(historyId, (item) => ({
          ...item,
          status: "error",
          error: message,
          updatedAt: new Date(),
        }));
        if (activeHistoryIdRef.current === historyId) {
          setAnalysisStage("error");
          // 更新所有任务为错误状态
          setAnalysisTasks((prev) =>
            prev.map((task) =>
              task.status === "in_progress" || task.status === "pending"
                ? { ...task, status: "error" as const }
                : task
            )
          );
          // 更新分析消息为错误状态
          updateAnalysisMessage({
            analysisResult: null,
            isCompleted: true,
            content: `分析失败: ${message}`
          });
        }
      }
    } finally {
      setIsAnalyzing(false);
      setIsChatStreaming(false); // 重置流式状态
      analysisAbortControllerRef.current = null; // 清理 AbortController
      manualStopRef.current = false; // 重置手动停止标志
    }
  };

  const onSubmit = async (data: ViolationFormValues) => {
    const historyId = createMessageId();

    // 检查是否已有线程 ID，如果没有则创建新线程
    const currentThreadId = await ensureThreadId(chatThreadId, createNewThread, "违规分析");
    if (!currentThreadId) {
      return; // 终止提交流程
    }

    const newHistory: AnalysisHistoryItem = {
      id: historyId,
      inspectionContent: "",
      violationDescription: data.violationDescription,
      startedAt: new Date(),
      updatedAt: new Date(),
      status: "analyzing",
      progress: 0,
      result: null,
    };

    setAnalysisHistory((prev) => [newHistory, ...prev]);
    setActiveHistoryId(historyId);
    setAnalysisStage("analyzing");
    setAnalysisResult(null);
    setTaskStatus(null);
    setIsAnalyzing(false);
    setCurrentChatTaskId("");
    chatTaskIdRef.current = "";

    // 添加用户的事实认定输入作为消息
    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: "user",
      content: data.violationDescription,
      timestamp: new Date(),
      additional_kwargs: {
        task: {
          task_type: "violation_analysis",
          uploaded_files: data.uploadedFiles || []
        }
      },
    };
    setChatMessages((prev) => [...prev, userMessage]);

    await callViolationAnalysisSSE(data, historyId, currentThreadId);
  };

  const sendChatMessage = async (rawText: string) => {
    const trimmed = rawText.trim();
    if (!trimmed || isChatStreaming) {
      return;
    }

    // 确保有线程 ID（如果没有则创建新线程）
    const currentThreadId = await ensureThreadId(chatThreadId, createNewThread, "问答");
    if (!currentThreadId) {
      return;
    }

    chatTaskIdRef.current = "";
    setCurrentChatTaskId("");
    manualStopRef.current = false;

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: "user",
      content: trimmed,
      timestamp: new Date(),
    };

    const assistantMessageId = createMessageId();
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      timestamp: new Date(),
    };

    // 先设置流式状态,确保"正在生成..."立即显示
    setIsChatStreaming(true);
    setChatStreamingStatus("正在生成...");
    setActiveAssistantMessageId(assistantMessageId);
    setChatInput("");
    // 然后再添加消息
    setChatMessages((prev) => [...prev, userMessage, assistantMessage]);

    // 如果是第一条 QA 消息且没有 activeHistoryId，创建历史记录
    let currentHistoryId = activeHistoryId;
    if (!activeHistoryId && chatMessages.length === 0) {
      const newHistoryId = `qa_${Date.now()}`;
      const newHistory: AnalysisHistoryItem = {
        id: newHistoryId,
        inspectionContent: "",
        violationDescription: trimmed,
        startedAt: new Date(),
        updatedAt: new Date(),
        status: "draft",
        progress: 0,
        result: null,
      };
      setAnalysisHistory((prev) => [newHistory, ...prev]);
      setActiveHistoryId(newHistoryId);
      activeHistoryIdRef.current = newHistoryId; // 同步更新 ref
      currentHistoryId = newHistoryId;
      console.log('[QA会话] 创建新的历史记录:', newHistoryId);
    }

    const controller = new AbortController();
    chatAbortControllerRef.current = controller;

    // 重置任务消息 ref，用于追踪分析消息
    tasksMessageIdRef.current = null;

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${currentThreadId}/completion`,
        method: "POST",
        body: {
          query: trimmed,
          agent_id: agentId,
        },
        signal: controller.signal,
        handlers: {
          onMessage: (content) => {
            setChatMessages((prev) =>
              appendMessageContent(prev, assistantMessageId, content)
            );
          },
          onTask: (tasks) => {
            // 后端判断是分析请求，返回 task_progress
            // 需要把消息转换为结构化消息
            if (tasks) {
              // 如果还没有创建任务消息，将当前助手消息转换为任务消息
              if (!tasksMessageIdRef.current) {
                tasksMessageIdRef.current = assistantMessageId;
                setChatMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === assistantMessageId
                      ? {
                          ...msg,
                          content: [
                            {
                              type: "non_standard",
                              value: { type: "task_progress", data: tasks }
                            }
                          ]
                        }
                      : msg
                  )
                );
              } else {
                // 更新任务进度
                setChatMessages((prev) =>
                  prev.map((msg) => {
                    if (msg.id !== tasksMessageIdRef.current) return msg;
                    let newContent = Array.isArray(msg.content) ? [...msg.content] : [];
                    const taskBlockIndex = newContent.findIndex(
                      (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
                    );
                    const taskBlock = {
                      type: 'non_standard',
                      value: { type: 'task_progress', data: tasks }
                    };
                    if (taskBlockIndex >= 0) {
                      newContent[taskBlockIndex] = taskBlock;
                    } else {
                      newContent.push(taskBlock);
                    }
                    return { ...msg, content: newContent };
                  })
                );
              }
              setAnalysisTasks(tasks);
              setIsAnalyzing(true);
              setAnalysisStage("analyzing");
            }
          },
          onCompleted: (eventData) => {
            // 异步生成标题（第一次完成时）
            void generateAndUpdateTitle(currentThreadId);
            setCurrentChatTaskId("");
            chatTaskIdRef.current = "";

            // 如果是分析流程，处理完成事件中的 analysis_data
            if (tasksMessageIdRef.current && eventData?.messages?.[0]) {
              const message = eventData.messages[0];
              if (Array.isArray(message.content)) {
                // 提取 analysis_data
                const dataBlock = message.content.find(
                  (b: any) => b.type === 'non_standard' && b.value?.type === 'analysis_data'
                );
                if (dataBlock) {
                  const analysisData = dataBlock.value?.data;
                  if (analysisData) {
                    setAnalysisResult(analysisData);
                    setAnalysisStage("completed");
                    setAnalysisTasks((prev) =>
                      prev.map((task) => ({ ...task, status: "completed" as const }))
                    );
                  }
                }
                // 更新消息内容为完整的后端返回
                setChatMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === tasksMessageIdRef.current
                      ? { ...msg, content: message.content }
                      : msg
                  )
                );
              }
              setIsAnalyzing(false);
            }
          },
          onError: (errorMsg) => {
            console.error("[QA SSE] 收到错误事件:", errorMsg);
            setChatMessages((prev) =>
              setMessageError(prev, assistantMessageId, `❌ ${errorMsg}`)
            );
            toast.error(errorMsg);
          },
          onEnd: () => {
            // 流结束统一清理 UI 状态
            setIsChatStreaming(false);
            setChatStreamingStatus("");
            setActiveAssistantMessageId(null);
            chatAbortControllerRef.current = null;
          },
        },
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "发送失败，请稍后再试";
      console.error("执法追问失败:", error);
      toast.error(message);
      setChatMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMessageId
            ? {
                ...msg,
                content:
                  msg.content ||
                  `（对话失败：${
                    error instanceof Error ? error.message : "未知错误"
                  }）`,
                timestamp: new Date(),
              }
            : msg,
        ),
      );
    }
  };

  const stopChatStreaming = useCallback(() => {
    if (!isChatStreaming) {
      return;
    }

    const assistantIdSnapshot = activeAssistantMessageId;

    console.log('[停止] 用户手动停止，设置 manualStopRef = true');
    manualStopRef.current = true;

    // 断开聊天连接
    if (chatAbortControllerRef.current) {
      console.log('[停止] 中止聊天连接');
      chatAbortControllerRef.current.abort();
      chatAbortControllerRef.current = null;
    }

    // 断开分析连接
    if (analysisAbortControllerRef.current) {
      console.log('[停止] 中止分析连接');
      analysisAbortControllerRef.current.abort();
      analysisAbortControllerRef.current = null;
    }

    // 重置聊天状态
    setIsChatStreaming(false);
    setChatStreamingStatus("");
    setActiveAssistantMessageId(null);
    setCurrentChatTaskId("");
    chatTaskIdRef.current = "";

    // 重置分析状态
    setIsAnalyzing(false);
    setAnalysisStage("completed");

    console.log('[停止] 状态已重置');

    // 更新消息状态
    setChatMessages((prev) =>
      prev.map((msg) => {
        // 更新聊天消息（assistant）
        if (msg.id === assistantIdSnapshot) {
          if (!msg.content) {
            return { ...msg, content: "（已取消）", timestamp: new Date() };
          }
          if (msg.content.includes("（已取消）")) {
            return msg;
          }
          return {
            ...msg,
            content: `${msg.content}\n（已取消）`,
            timestamp: new Date(),
          };
        }
        // 更新分析消息 - 将所有进行中的任务标记为已取消
        // 任务进度存储在 content 数组的 NonStandardContentBlock 中
        if (Array.isArray(msg.content)) {
          const hasTaskProgress = msg.content.some(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
          );
          if (hasTaskProgress) {
            console.log('[停止] 任务状态设为已取消');
            return {
              ...msg,
              content: msg.content.map((block: any) => {
                if (block.type === 'non_standard' && block.value?.type === 'task_progress') {
                  return {
                    ...block,
                    value: {
                      ...block.value,
                      data: (block.value.data || []).map((task: AnalysisTask) =>
                        task.status === "in_progress"
                          ? { ...task, status: "cancelled" as const }
                          : task
                      )
                    }
                  };
                }
                return block;
              }),
              timestamp: new Date(),
            };
          }
        }
        return msg;
      }),
    );
  }, [activeAssistantMessageId, isChatStreaming]);

  // 处理法律条款点击
  const handleLawClick = useCallback(
    (law: { law_name: string; law_item_name: string; law_item_content: string }) => {
      setSelectedDocument(law);
    },
    []
  );

  // 前端报告类型到后端拼音缩写和中文名称的映射
  const REPORT_TYPE_MAP: Record<string, { pinyin: string; name: string }> = {
    "rectification-notice": { pinyin: "zgtzs", name: "整改通知书" },
    "comprehensive-report": { pinyin: "jcbg", name: "执法检查报告" },
    "evidence-catalog": { pinyin: "zjml", name: "证据目录" },
    "info-summary": { pinyin: "xxjb", name: "信息简表" },
  };

  const sendReportRequest = async (
    reportSubType: string,
    uploadedFiles?: Array<{file_id: string; file_name: string; file_type: string; uploaded_at?: string}>,
    onAutoPreview?: (doc: { filename: string; title: string; excelData?: any; sourceData?: any }) => void
  ) => {
    if (!reportSubType || isChatStreaming) {
      return;
    }

    // 确保有线程 ID
    const currentThreadId = await ensureThreadId(chatThreadId, createNewThread, "报告生成");
    if (!currentThreadId) {
      return;
    }

    const reportTypeInfo = REPORT_TYPE_MAP[reportSubType];
    if (!reportTypeInfo) {
      toast.error(`未知的报告类型: ${reportSubType}`);
      return;
    }

    const reportMessage = `请生成${reportTypeInfo.name}`;

    chatTaskIdRef.current = "";
    setCurrentChatTaskId("");
    manualStopRef.current = false;

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: "user",
      content: reportMessage,
      timestamp: new Date(),
      additional_kwargs: {
        task: {
          task_type: "report",
          report_type: reportTypeInfo.pinyin,
          uploaded_files: uploadedFiles || []  // 统一使用 uploaded_files，与后端一致
        }
      },
    };

    const assistantMessageId = createMessageId();
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: "assistant",
      content: [
        { type: "text", text: "正在生成报告..." }
      ],
      timestamp: new Date(),
    };

    tasksMessageIdRef.current = assistantMessageId;
    setChatMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsChatStreaming(true);
    setChatStreamingStatus("正在生成报告...");
    setActiveAssistantMessageId(assistantMessageId);

    const controller = new AbortController();
    chatAbortControllerRef.current = controller;

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${currentThreadId}/completion`,
        method: "POST",
        body: {
          query: reportMessage,
          agent_id: agentId,
          options: {
            task_type: "report",
            report_type: reportTypeInfo.pinyin,
            uploaded_files: uploadedFiles || [],
          }
        },
        signal: controller.signal,
        handlers: {
          onMessage: (content) => {
            setChatMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantMessageId
                  ? { ...msg, content: msg.content + content }
                  : msg
              )
            );
          },
          onTask: (tasks) => {
            // 将 task_progress 放到 content 数组的 NonStandardContentBlock 中
            // 符合 LangChain 官方规范
            setChatMessages((prev) =>
              prev.map((msg) => {
                if (msg.id !== assistantMessageId) return msg;

                let newContent = Array.isArray(msg.content) ? [...msg.content] : [];

                // 查找现有的 task_progress 块
                const taskBlockIndex = newContent.findIndex(
                  (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
                );
                const taskBlock = {
                  type: 'non_standard',
                  value: { type: 'task_progress', data: tasks }
                };

                if (taskBlockIndex >= 0) {
                  newContent[taskBlockIndex] = taskBlock;
                } else {
                  newContent.push(taskBlock);
                }

                return {
                  ...msg,
                  content: newContent
                };
              })
            );
          },
          onInterrupt: (interruptData) => {
            // Human-in-the-Loop: 报告生成被中断，等待用户审核
            console.log("[报告生成] 收到中断事件:", interruptData);
            console.log("[报告生成] current_data:", interruptData.current_data);
            console.log("[报告生成] current_data 类型:", typeof interruptData.current_data);
            console.log("[报告生成] current_data.files:", interruptData.current_data?.files);
            const reviewData = interruptData as HumanReviewData;

            // 根据 review_type 确定任务ID
            const reviewTypeToTaskId: Record<string, string> = {
              "extract": "review_extract",
              "analyze": "review_analyze",
              "retrieve": "review_retrieve",
              "evidence": "review_evidence",
            };
            const taskIdToUpdate = reviewTypeToTaskId[reviewData.review_type] || "review_extract";
            console.log("[报告生成] 审核类型:", reviewData.review_type, "任务ID:", taskIdToUpdate);
            pendingReviewTaskIdRef.current = taskIdToUpdate;

            // 更新消息中的任务状态为待确认，并附加审核数据
            setChatMessages((prev) =>
              prev.map((msg) => {
                if (msg.id !== assistantMessageId) return msg;

                let newContent = Array.isArray(msg.content) ? [...msg.content] : [];

                // 查找现有的 task_progress 块并更新
                const taskBlockIndex = newContent.findIndex(
                  (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
                );

                if (taskBlockIndex >= 0) {
                  const currentTasks = (newContent[taskBlockIndex] as any).value?.data || [];
                  const updatedTasks = currentTasks.map((task: AnalysisTask) =>
                    task.id === taskIdToUpdate
                      ? { ...task, status: "awaiting_confirm" as const, reviewData }
                      : task.status === "in_progress"
                        ? { ...task, status: "pending" as const }
                        : task
                  );
                  newContent[taskBlockIndex] = {
                    type: 'non_standard',
                    value: { type: 'task_progress', data: updatedTasks }
                  };
                } else {
                  // 没有找到 task_progress 块，创建一个包含审核任务的新块
                  console.log("[报告生成] 未找到 task_progress 块，创建新块");
                  const newTaskBlock = {
                    type: 'non_standard',
                    value: {
                      type: 'task_progress',
                      data: [{
                        id: taskIdToUpdate,
                        title: reviewData.message || "待审核",
                        status: "awaiting_confirm" as const,
                        reviewData
                      }]
                    }
                  };
                  // 移除 "正在生成报告..." 文本，添加任务块
                  newContent = newContent.filter((b: any) => !(b.type === 'text' && b.text === '正在生成报告...'));
                  newContent.push(newTaskBlock);
                }

                return {
                  ...msg,
                  content: newContent
                };
              })
            );
          },
          // onFile 已删除 - 文件信息在 completed 事件的 content 数组中
          onCompleted: (eventData) => {
            console.log("[报告生成] 收到完成事件:", eventData);

            // 从 completed 事件中提取 message
            // 后端返回格式：{ messages: [{...}] } 或 { message: {...} }
            const message = eventData?.messages?.[0] || eventData?.message;
            if (message) {
              setChatMessages((prev) =>
                prev.map((msg) => {
                  if (msg.id !== assistantMessageId) return msg;

                  // 直接使用后端返回的 content（包含 NonStandardContentBlock）
                  return {
                    ...msg,
                    content: message.content || msg.content
                  };
                })
              );

              // 自动预览：从 content 中提取文件信息
              if (onAutoPreview && message.content) {
                const content = message.content;
                if (Array.isArray(content)) {
                  // 查找文件块（FileContentBlock，使用 file_id 作为文件名）
                  const fileBlock = content.find((b: any) => b.type === 'file');
                  // 查找 excel_data 块
                  const excelBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'excel_data'
                  );
                  // 查找 file_title 块
                  const titleBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'file_title'
                  );
                  // 查找 report_source_data 块（用于编辑后重新生成）
                  const sourceDataBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'report_source_data'
                  );

                  if (fileBlock) {
                    // file_id 就是文件名（如 "整改通知书_xxx.docx"）
                    const filename = fileBlock.file_id;
                    const title = titleBlock?.value?.data || reportTypeInfo.name;
                    const excelData = excelBlock?.value?.data;
                    const sourceData = sourceDataBlock?.value?.data;

                    if (filename) {
                      onAutoPreview({ filename, title, excelData, sourceData });
                    }
                  }
                }
              }
            }

            // 生成会话标题（第一次完成时）
            void generateAndUpdateTitle(currentThreadId);
            toast.success("报告生成成功");
          },
          onError: (errorMsg) => {
            console.error("[报告生成] 收到错误事件:", errorMsg);
            setChatMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantMessageId
                  ? { ...msg, content: `❌ ${errorMsg}` }
                  : msg
              )
            );
            toast.error(errorMsg);
          },
          onEnd: () => {
            // 流结束统一清理 UI 状态
            setIsChatStreaming(false);
            setChatStreamingStatus("");
            setActiveAssistantMessageId(null);
            chatAbortControllerRef.current = null;
          },
        },
      });
    } catch (error: unknown) {
      if (error instanceof Error) {
        if (error.name === "AbortError") {
          console.log("[报告生成] 用户取消");
        } else {
          console.error("[报告生成] 错误:", error);
          toast.error(error.message || "报告生成失败");
          setChatMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: `错误: ${error.message}` }
                : msg
            )
          );
        }
      }
    }
  };

  // 切换到指定线程
  const switchToThread = useCallback((threadId: string) => {
    setChatThreadId(threadId);
  }, []);

  /**
   * Human-in-the-Loop: 恢复被中断的报告生成
   */
  const resumeReportGeneration = useCallback(async (
    resumeInput: ResumeInput,
    taskId: string,
    messageId: string,
    onAutoPreview?: (doc: { filename: string; title: string; excelData?: any; sourceData?: any }) => void
  ) => {
    if (!chatThreadId) {
      toast.error("没有活动的会话");
      return;
    }

    // 如果用户取消，停止报告生成
    if (resumeInput.action === "reject") {
      setIsChatStreaming(false);
      setChatMessages((prev) =>
        prev.map((msg) => {
          if (msg.id !== messageId) return msg;

          let newContent = Array.isArray(msg.content) ? [...msg.content] : [];
          const taskBlockIndex = newContent.findIndex(
            (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
          );

          if (taskBlockIndex >= 0) {
            const currentTasks = (newContent[taskBlockIndex] as any).value?.data || [];
            const updatedTasks = currentTasks.map((task: AnalysisTask) =>
              task.status === "pending" || task.status === "in_progress" || task.status === "awaiting_confirm"
                ? { ...task, status: "cancelled" as const, reviewData: undefined }
                : task
            );
            newContent[taskBlockIndex] = {
              type: 'non_standard',
              value: { type: 'task_progress', data: updatedTasks }
            };
          }

          return { ...msg, content: newContent };
        })
      );
      pendingReviewTaskIdRef.current = null;
      toast.info("已取消报告生成");
      return;
    }

    // 将待确认任务标记为已完成
    setChatMessages((prev) =>
      prev.map((msg) => {
        if (msg.id !== messageId) return msg;

        let newContent = Array.isArray(msg.content) ? [...msg.content] : [];
        const taskBlockIndex = newContent.findIndex(
          (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
        );

        if (taskBlockIndex >= 0) {
          const currentTasks = (newContent[taskBlockIndex] as any).value?.data || [];
          const updatedTasks = currentTasks.map((task: AnalysisTask) =>
            task.id === taskId
              ? { ...task, status: "completed" as const, reviewData: undefined }
              : task
          );
          newContent[taskBlockIndex] = {
            type: 'non_standard',
            value: { type: 'task_progress', data: updatedTasks }
          };
        }

        return { ...msg, content: newContent };
      })
    );
    pendingReviewTaskIdRef.current = null;

    // 恢复流式状态
    setIsChatStreaming(true);

    const controller = new AbortController();
    chatAbortControllerRef.current = controller;

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${chatThreadId}/resume`,
        method: "POST",
        body: {
          action: resumeInput.action,
          data: resumeInput.data,
        },
        signal: controller.signal,
        handlers: {
          onTask: (tasks) => {
            // 更新消息中的任务进度
            setChatMessages((prev) =>
              prev.map((msg) => {
                if (msg.id !== messageId) return msg;

                let newContent = Array.isArray(msg.content) ? [...msg.content] : [];
                const taskBlockIndex = newContent.findIndex(
                  (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
                );
                const taskBlock = {
                  type: 'non_standard',
                  value: { type: 'task_progress', data: tasks }
                };

                if (taskBlockIndex >= 0) {
                  newContent[taskBlockIndex] = taskBlock;
                } else {
                  newContent.push(taskBlock);
                }

                return { ...msg, content: newContent };
              })
            );
          },
          onCompleted: (eventData) => {
            console.log("[报告恢复] 收到完成事件:", eventData);

            // 从 completed 事件中提取 message
            // 后端返回格式：{ messages: [{...}] } 或 { message: {...} }
            const message = eventData?.messages?.[0] || eventData?.message;
            if (message) {
              setChatMessages((prev) =>
                prev.map((msg) => {
                  if (msg.id !== messageId) return msg;
                  return {
                    ...msg,
                    content: message.content || msg.content
                  };
                })
              );

              // 自动预览
              if (onAutoPreview && message.content) {
                const content = message.content;
                if (Array.isArray(content)) {
                  const fileBlock = content.find((b: any) => b.type === 'file');
                  const excelBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'excel_data'
                  );
                  const titleBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'file_title'
                  );
                  // 查找 report_source_data 块（用于编辑后重新生成）
                  const sourceDataBlock = content.find((b: any) =>
                    b.type === 'non_standard' && b.value?.type === 'report_source_data'
                  );

                  if (fileBlock) {
                    const filename = fileBlock.file_id;
                    const title = titleBlock?.value?.data || "报告";
                    const excelData = excelBlock?.value?.data;
                    const sourceData = sourceDataBlock?.value?.data;

                    if (filename) {
                      onAutoPreview({ filename, title, excelData, sourceData });
                    }
                  }
                }
              }
            }

            void generateAndUpdateTitle(chatThreadId);
            toast.success("报告生成成功");
          },
          onError: (errorMsg) => {
            console.error("[报告恢复] 收到错误事件:", errorMsg);
            toast.error(errorMsg);
          },
          onInterrupt: (interruptData) => {
            // 再次被中断
            console.log("[报告恢复] 再次收到中断事件:", interruptData);
            const reviewData = interruptData as HumanReviewData;
            // 根据 review_type 确定任务ID
            const reviewTypeToTaskId: Record<string, string> = {
              "extract": "review_extract",
              "analyze": "review_analyze",
              "retrieve": "review_retrieve",
              "evidence": "review_evidence",
            };
            const taskIdToUpdate = reviewTypeToTaskId[reviewData.review_type] || "review_extract";
            console.log("[报告恢复] 审核类型:", reviewData.review_type, "任务ID:", taskIdToUpdate);
            pendingReviewTaskIdRef.current = taskIdToUpdate;

            setChatMessages((prev) =>
              prev.map((msg) => {
                if (msg.id !== messageId) return msg;

                let newContent = Array.isArray(msg.content) ? [...msg.content] : [];
                const taskBlockIndex = newContent.findIndex(
                  (b: any) => b.type === 'non_standard' && b.value?.type === 'task_progress'
                );

                if (taskBlockIndex >= 0) {
                  const currentTasks = (newContent[taskBlockIndex] as any).value?.data || [];
                  const updatedTasks = currentTasks.map((task: AnalysisTask) =>
                    task.id === taskIdToUpdate
                      ? { ...task, status: "awaiting_confirm" as const, reviewData }
                      : task.status === "in_progress"
                        ? { ...task, status: "pending" as const }
                        : task
                  );
                  newContent[taskBlockIndex] = {
                    type: 'non_standard',
                    value: { type: 'task_progress', data: updatedTasks }
                  };
                }

                return { ...msg, content: newContent };
              })
            );
          },
          onEnd: () => {
            setIsChatStreaming(false);
            setChatStreamingStatus("");
            setActiveAssistantMessageId(null);
            chatAbortControllerRef.current = null;
          },
        },
      });
    } catch (error) {
      console.error("Resume report SSE调用失败:", error);
      const message = error instanceof Error ? error.message : "未知错误";
      toast.error(`恢复报告生成失败: ${message}`);
      setIsChatStreaming(false);
    }
  }, [chatThreadId, generateAndUpdateTitle]);

  /**
   * Human-in-the-Loop: 恢复被中断的对话
   */
  const resumeConversation = useCallback(async (resumeInput: ResumeInput, taskId: string) => {
    if (!chatThreadId) {
      toast.error("没有活动的会话");
      return;
    }

    // 如果用户取消，停止分析
    if (resumeInput.action === "reject") {
      setIsAnalyzing(false);
      setIsChatStreaming(false);
      setAnalysisTasks((prev) =>
        prev.map((task) =>
          task.status === "pending" || task.status === "in_progress" || task.status === "awaiting_confirm"
            ? { ...task, status: "cancelled" as const, reviewData: undefined }
            : task
        )
      );
      pendingReviewTaskIdRef.current = null;
      toast.info("已取消分析");
      return;
    }

    // 将待确认任务标记为已完成，清除审核数据
    setAnalysisTasks((prev) =>
      prev.map((task) =>
        task.id === taskId
          ? { ...task, status: "completed" as const, reviewData: undefined }
          : task
      )
    );
    pendingReviewTaskIdRef.current = null;

    // 恢复分析状态
    setIsAnalyzing(true);
    setIsChatStreaming(true);

    const controller = new AbortController();
    analysisAbortControllerRef.current = controller;

    try {
      await processSSEStream({
        url: `${API_BASE}/api/conversations/${chatThreadId}/resume`,
        method: "POST",
        body: {
          action: resumeInput.action,
          data: resumeInput.data,
        },
        signal: controller.signal,
        handlers: {
          onTask: (tasks) => {
            if (tasks) {
              setAnalysisTasks(tasks);
            }
          },
          onCompleted: (eventData) => {
            // 从 completed 事件中提取分析结果
            // 后端返回格式：{ messages: [{...}] } 或 { message: {...} }
            const message = eventData?.messages?.[0] || eventData?.message;
            if (!message) {
              console.warn('[resume onCompleted] 没有找到 message, eventData:', eventData);
              return;
            }

            let analysisData: AnalysisResult | null = null;
            if (Array.isArray(message.content)) {
              const dataBlock = message.content.find(
                (b: any) => b.type === 'non_standard' && b.value?.type === 'analysis_data'
              );
              if (dataBlock) {
                analysisData = dataBlock.value?.data || null;
              }
            }

            if (analysisData) {
              // 更新历史记录
              if (activeHistoryIdRef.current) {
                updateHistoryItem(activeHistoryIdRef.current, (item) => ({
                  ...item,
                  status: "completed",
                  result: analysisData,
                  updatedAt: new Date(),
                }));
              }

              // 生成会话标题
              void generateAndUpdateTitle(chatThreadId);

              // 更新 UI 状态
              setAnalysisResult(analysisData);
              setAnalysisStage("completed");
              setTaskStatus((prev) =>
                prev ? { ...prev, status: "completed", currentStep: "分析完成" } : null
              );
              setAnalysisTasks((prev) =>
                prev.map((task) => ({ ...task, status: "completed" as const }))
              );
              updateAnalysisMessage({
                analysisResult: analysisData,
                isCompleted: true,
                content: `分析完成，共识别出 ${analysisData.违法行为数量} 项事实认定`
              });

              setIsAnalyzing(false);
              toast.success("违规分析完成！");
            }
          },
          onError: (errorMsg) => {
            console.error("[resume SSE] 收到错误事件:", errorMsg);
            toast.error(errorMsg);
            setIsAnalyzing(false);
            setIsChatStreaming(false);
          },
          onInterrupt: (interruptData) => {
            // 再次被中断，更新任务为待确认状态
            console.log("[resume SSE] 再次收到中断事件:", interruptData);
            const reviewData = interruptData as HumanReviewData;

            // 根据 review_type 确定是哪个任务需要确认
            // 后端任务ID: split, review_split, retrieve, review_retrieve, format
            const taskIdToUpdate = reviewData.review_type === "split"
              ? "review_split"
              : "review_retrieve";

            pendingReviewTaskIdRef.current = taskIdToUpdate;

            setAnalysisTasks((prev) =>
              prev.map((task) =>
                task.id === taskIdToUpdate
                  ? { ...task, status: "awaiting_confirm" as const, reviewData }
                  : task.status === "in_progress"
                    ? { ...task, status: "pending" as const }
                    : task
              )
            );
          },
          onEnd: () => {
            setIsChatStreaming(false);
            analysisAbortControllerRef.current = null;
          },
        },
      });
    } catch (error) {
      console.error("Resume SSE调用失败:", error);
      const message = error instanceof Error ? error.message : "未知错误";
      toast.error(`恢复分析失败: ${message}`);
      setIsAnalyzing(false);
      setIsChatStreaming(false);
    }
  }, [chatThreadId, generateAndUpdateTitle, updateAnalysisMessage, updateHistoryItem]);

  // Human-in-the-Loop 回调函数
  const handleHumanReviewApprove = useCallback((taskId: string) => {
    void resumeConversation({ action: "approve" }, taskId);
  }, [resumeConversation]);

  const handleHumanReviewEdit = useCallback((taskId: string, data: { violations: any[] }) => {
    void resumeConversation({ action: "edit", data }, taskId);
  }, [resumeConversation]);

  const handleHumanReviewReject = useCallback((taskId: string) => {
    void resumeConversation({ action: "reject" }, taskId);
  }, [resumeConversation]);

  return {
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
    currentChatTaskId,
    chatThreadId,
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
    historyRefreshTrigger, // 历史记录刷新触发器

    // Form
    register,
    handleSubmit,
    errors,
    reset,
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
    loadThreadMessages,  // 加载指定会话的历史消息

    // Human-in-the-Loop 人工审核回调
    handleHumanReviewApprove,
    handleHumanReviewEdit,
    handleHumanReviewReject,

    // 报告生成恢复（用于报告审核后继续生成）
    resumeReportGeneration,
  };
}
