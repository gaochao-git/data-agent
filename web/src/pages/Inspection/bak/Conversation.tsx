import { FormEvent, KeyboardEvent, useMemo, useEffect, useState, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { UseFormRegister, UseFormHandleSubmit, FieldErrors } from "react-hook-form";
import { API_BASE } from "../../../lib/config";
import { mergeClassNames, formatPromptPreview, formatLawItem, formatTimestamp } from "../utils";
import type { ChatMessage, AnalysisStage, AnalysisHistoryItem, AnalysisResult, ViolationFormValues, AnalysisTask, ToolType, ReportSubType, ViolationFileGroup } from "../types";
import { AnalysisProgressMessage } from "./AnalysisProgressMessage";
import { FileUploadZone } from "./FileUploadZone";
import { ToolCards } from "./ToolCards";
import { parseMessageContent } from "../utils/messageParser";

interface ConversationPanelProps {
  analysisStage: AnalysisStage;
  analysisHistory: AnalysisHistoryItem[];
  analysisTasks: AnalysisTask[];
  chatMessages: ChatMessage[];
  chatInput: string;
  setChatInput: (value: string) => void;
  isChatStreaming: boolean;
  chatStreamingStatus: string;
  activeAssistantMessageId: string | null;
  hasFiveRequiredInputs: boolean;
  missingRequiredLabels: string[];
  inspectionSummary: {
    inspectionContent: string;
    violationDescription: string;
  };
  analysisResult: AnalysisResult | null;
  register: UseFormRegister<ViolationFormValues>;
  handleSubmit: UseFormHandleSubmit<ViolationFormValues>;
  errors: FieldErrors<ViolationFormValues>;
  onSubmit: (data: ViolationFormValues) => Promise<void>;
  isAnalyzing: boolean;
  chatContainerRef: React.RefObject<HTMLDivElement>;
  sendChatMessage: (message: string) => Promise<void>;
  sendReportRequest: (reportSubType: string, uploadedFiles?: Array<{file_id: string; file_name: string; file_type: string}>, onAutoPreview?: (doc: { filename: string; title: string; excelData?: any; sourceData?: any }) => void) => Promise<void>;
  stopChatStreaming: () => Promise<void>;
  onStartNewInspection: () => void;
  panelHeightClass: string;
  conversationTitle?: string; // 对话标题
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void;
  onWordDocumentView?: (doc: { filename: string; title: string; sourceData?: any }) => void;
  onExcelDocumentView?: (doc: { filename: string; title: string; excelData: any }) => void;
  onReportClick?: (report: { name: string; content: string }) => void; // 点击相似案例报告
  threadId: string | null; // 线程 ID
  onAddChatMessage?: (message: ChatMessage) => void; // 添加聊天消息
  onEnsureThread?: () => Promise<string | null>; // 确保线程存在（用于文件上传前）
  resetTrigger?: number; // 重置触发器（数字变化时触发重置）
  updatedWordFilename?: string; // 编辑后更新的Word文件名（覆盖原始文件名）
}

const MAX_PROMPT_DISPLAY_LENGTH = 25;

export function Conversation({
  analysisStage,
  analysisHistory,
  analysisTasks,
  chatMessages,
  chatInput,
  setChatInput,
  isChatStreaming,
  chatStreamingStatus,
  activeAssistantMessageId,
  hasFiveRequiredInputs,
  missingRequiredLabels,
  inspectionSummary,
  analysisResult,
  register,
  handleSubmit,
  errors,
  onSubmit,
  isAnalyzing,
  chatContainerRef,
  sendChatMessage,
  sendReportRequest,
  stopChatStreaming,
  onStartNewInspection,
  conversationTitle,
  onLawClick,
  onWordDocumentView,
  onExcelDocumentView,
  onReportClick,
  panelHeightClass,
  threadId,
  onAddChatMessage,
  onEnsureThread,
  resetTrigger,
  updatedWordFilename,
}: ConversationPanelProps) {
  const [selectedTool, setSelectedTool] = useState<ToolType>(null);
  const [selectedReportSubType, setSelectedReportSubType] = useState<ReportSubType>(null);
  const [isUserScrolling, setIsUserScrolling] = useState(false);
  const isAutoScrollingRef = useRef(false); // 标记是否正在自动滚动
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);

  // 输入框自适应高度相关
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const formRef = useRef<HTMLDivElement | null>(null);
  const formMinHeight = 122;
  const formMaxHeight = 244;

  // 违法行为文件分组状态（用于检查报告生成）
  const [fileGroups, setFileGroups] = useState<ViolationFileGroup[]>([]);

  // 文件上传进度状态（key: group_id 或 'new' 表示新建分组）
  const [uploadingGroupIds, setUploadingGroupIds] = useState<Record<string, boolean>>({});

  // 检查报告文件状态（用于其他报告生成）
  const [inspectionReportFile, setInspectionReportFile] = useState<{ file_id: string; file_name: string; file_path: string } | null>(null);

  // 检查报告上传进度
  const [isUploadingInspectionReport, setIsUploadingInspectionReport] = useState(false);


  // 获取报告类型的显示名称
  const getReportTypeName = (type: ReportSubType): string => {
    const names: Record<string, string> = {
      "comprehensive-report": "检查报告",
      "rectification-notice": "责令整改通知书",
      "evidence-catalog": "证据目录",
      "info-summary": "信息简表"
    };
    return type ? names[type] || "" : "";
  };

  // 获取报告类型的配置（输入类型和提示文本）
  const getReportConfig = (type: ReportSubType): { inputType: 'fact_determination' | 'inspection_report'; uploadHint: string; fileTypeHint: string } => {
    if (type === "comprehensive-report") {
      return {
        inputType: 'fact_determination',
        uploadHint: '请上传所需文件',
        fileTypeHint: '事实认定书'
      };
    } else {
      // 其他报告类型需要上传检查报告
      return {
        inputType: 'inspection_report',
        uploadHint: '请上传检查报告Word文档',
        fileTypeHint: '检查报告'
      };
    }
  };

  // 重置输入区域到初始状态的统一方法
  const resetInputArea = () => {
    setSelectedTool(null);
    setSelectedReportSubType(null);
    setUploadingGroupIds({});
    setFileGroups([]);  // 清空已上传的文件
    setInspectionReportFile(null);  // 清空检查报告
    setIsUploadingInspectionReport(false);
  };

  // 监听 resetTrigger 变化，触发重置
  useEffect(() => {
    if (resetTrigger !== undefined && resetTrigger > 0) {
      resetInputArea();
    }
  }, [resetTrigger]);

  // 输入框高度自适应函数
  const adjustTextareaHeight = (element?: HTMLTextAreaElement | null) => {
    const textarea = element ?? textareaRef.current;
    const form = formRef.current;

    if (!textarea || !form) return;

    // 固定的 offset（操作区域高度约 44px + padding 约 24px）
    const fixedOffset = 68;

    // 重置 textarea 高度以获取真实的 scrollHeight
    textarea.style.height = "auto";

    // 计算 form 容器应该有的高度
    const nextFormHeight = Math.max(
      formMinHeight,
      Math.min(formMaxHeight, textarea.scrollHeight + fixedOffset),
    );

    // 计算 textarea 实际可用高度
    const nextTextareaHeight = nextFormHeight - fixedOffset;

    // 设置高度
    textarea.style.height = `${nextTextareaHeight}px`;
    textarea.style.overflowY = nextFormHeight >= formMaxHeight ? "auto" : "hidden";
    form.style.height = `${nextFormHeight}px`;
  };

  // 监听输入内容变化，自动调整高度
  useEffect(() => {
    adjustTextareaHeight();
  }, [chatInput]);

  // 组件挂载时初始化高度
  useEffect(() => {
    adjustTextareaHeight();
  }, []);

  // 工具选择变化时重新计算高度
  useEffect(() => {
    requestAnimationFrame(() => {
      adjustTextareaHeight();
    });
  }, [selectedTool, selectedReportSubType]);

  // 获取文件分组列表
  const fetchFileGroups = async (customThreadId?: string) => {
    const activeThreadId = customThreadId || threadId;
    if (!activeThreadId) return;

    try {
      const response = await fetch(`${API_BASE}/api/conversations/${activeThreadId}/state`, {
        credentials: 'include',
      });

      if (response.ok) {
        const data = await response.json();
        // business_response 格式：{ status, msg, code, data: { violation_file_groups } }
        const groups = data.data?.violation_file_groups || [];

        console.log('[文件分组查询] 后端返回数据:', {
          总数: groups.length,
          分组IDs: groups.map((g: any) => g.group_id),
          完整数据: groups
        });

        // 去重：按 group_id 去重，保留最后一个（最新的）
        const uniqueGroups = groups.reduce((acc: any[], group: any) => {
          const existingIndex = acc.findIndex(g => g.group_id === group.group_id);
          if (existingIndex >= 0) {
            console.warn('[文件分组查询] 发现重复 group_id:', group.group_id);
            // 替换为最新的
            acc[existingIndex] = group;
          } else {
            acc.push(group);
          }
          return acc;
        }, []);

        console.log('[文件分组查询] 去重后:', {
          总数: uniqueGroups.length,
          分组IDs: uniqueGroups.map((g: any) => g.group_id)
        });

        setFileGroups(uniqueGroups);
      }
    } catch (error) {
      console.error('[文件分组查询] 失败:', error);
    }
  };

  // 当线程ID变化或选择检查报告工具时，加载文件分组
  useEffect(() => {
    if (threadId && selectedTool === "report-generation" && selectedReportSubType === "comprehensive-report") {
      fetchFileGroups();
    }
  }, [threadId, selectedTool, selectedReportSubType]);


  const chatMarkdownPlugins = useMemo(() => [remarkGfm], []);

  const chatMarkdownComponents = useMemo<Components>(
    () => ({
      a: ({ className, ...props }) => (
        <a
          {...props}
          className={mergeClassNames(
            className,
            "text-indigo-600 dark:text-indigo-400 underline decoration-indigo-400/60 hover:decoration-indigo-500",
          )}
          target="_blank"
          rel="noreferrer"
        />
      ),
      ul: ({ className, ...props }) => (
        <ul
          {...props}
          className={mergeClassNames(className, "list-disc space-y-1 pl-4")}
        />
      ),
      ol: ({ className, ...props }) => (
        <ol
          {...props}
          className={mergeClassNames(className, "list-decimal space-y-1 pl-4")}
        />
      ),
      li: ({ className, ...props }) => (
        <li {...props} className={mergeClassNames(className, "leading-relaxed")} />
      ),
      strong: ({ className, ...props }) => (
        <strong
          {...props}
          className={mergeClassNames(
            className,
            "font-semibold text-gray-900 dark:text-gray-100",
          )}
        />
      ),
    }),
    [],
  );

  // 监听用户滚动行为
  useEffect(() => {
    const container = chatContainerRef.current;
    if (!container) return;

    const handleScroll = () => {
      // 如果是自动滚动触发的，忽略
      if (isAutoScrollingRef.current) {
        return;
      }

      const { scrollTop, scrollHeight, clientHeight } = container;
      const isAtBottom = scrollHeight - scrollTop - clientHeight < 50; // 允许50px的误差

      // 只有用户主动滚动离开底部时，才标记为正在滚动
      if (!isAtBottom) {
        setIsUserScrolling(true);
      }
      // 注意：不在这里重置 isUserScrolling，让用户保持控制
    };

    container.addEventListener('scroll', handleScroll);
    return () => container.removeEventListener('scroll', handleScroll);
  }, [chatContainerRef]);

  // 自动滚动到底部（仅当用户未主动滚动时）
  useEffect(() => {
    if (chatContainerRef.current && !isUserScrolling) {
      isAutoScrollingRef.current = true;
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
      // 延迟重置标志，确保 scroll 事件已处理完
      requestAnimationFrame(() => {
        isAutoScrollingRef.current = false;
      });
    }
  }, [chatMessages, chatContainerRef, isUserScrolling]);

  // 流式输出结束后，重置用户滚动状态（下次新消息会自动滚动）
  useEffect(() => {
    if (!isChatStreaming) {
      setIsUserScrolling(false);
    }
  }, [isChatStreaming]);

  // 上传检查报告（用于其他报告生成）
  const uploadInspectionReport = async (file: File, uploadThreadId?: string | null) => {
    // 校验文件类型
    const fileExt = file.name.toLowerCase().split('.').pop();
    if (fileExt !== 'docx' && fileExt !== 'wps' && fileExt !== 'doc') {
      toast.error('只能上传 Word 文档（.docx、.doc 或 .wps 格式）');
      return;
    }

    setIsUploadingInspectionReport(true);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('file_type_hint', '检查报告');

      const activeThreadId = uploadThreadId || threadId;
      if (!activeThreadId) {
        throw new Error('没有可用的会话 ID');
      }

      console.log('[检查报告上传] 开始上传，threadId:', activeThreadId);

      const response = await fetch(`${API_BASE}/api/conversations/${activeThreadId}/files`, {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();

      console.log('[检查报告上传] 后端响应:', result);

      if (result.status === 'error') {
        toast.error(result.msg || '文件上传失败');
        return;
      }

      // 保存检查报告文件信息
      setInspectionReportFile({
        file_id: result.data.file_id,
        file_name: result.data.file_name,
        file_path: result.data.file_path,
      });

      toast.success('检查报告上传成功');
    } catch (error) {
      console.error('[检查报告上传] 失败:', error);
      toast.error('上传失败，请稍后重试');
    } finally {
      setIsUploadingInspectionReport(false);
    }
  };

  // 上传事实认定书（创建新分组）
  const uploadFactDetermination = async (file: File, uploadThreadId?: string | null, skipLimitCheck: boolean = false): Promise<string | undefined> => {
    // 检查分组数量限制（批量上传时跳过，因为已经在Modal中检查过了）
    if (!skipLimitCheck && fileGroups.length >= 10) {
      toast.error('已达到最大上传数量限制（最多10个违法行为）');
      return undefined;
    }

    // 校验文件类型
    const fileExt = file.name.toLowerCase().split('.').pop();
    if (fileExt !== 'docx' && fileExt !== 'wps') {
      toast.error('只能上传 Word 文档（.docx 或 .wps 格式）');
      return undefined;
    }

    setUploadingGroupIds(prev => ({ ...prev, 'new': true }));

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('file_type_hint', '事实认定书');

      // 确保有 threadId（如果没有则创建新会话）
      let activeThreadId = uploadThreadId || threadId;

      if (!activeThreadId && onEnsureThread) {
        activeThreadId = await onEnsureThread();
      }

      if (!activeThreadId) {
        throw new Error('没有可用的会话 ID');
      }

      const response = await fetch(`${API_BASE}/api/conversations/${activeThreadId}/files`, {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();

      console.log('[事实认定书上传] 后端响应:', result);

      if (result.status === 'error') {
        toast.error(result.msg || '文件上传失败');
        return undefined;
      }

      toast.success('事实认定书上传成功');
      // 刷新文件分组列表（传入实际使用的 threadId）
      console.log('[事实认定书上传] 刷新文件列表，threadId:', activeThreadId);
      await fetchFileGroups(activeThreadId);

      // 返回使用的 threadId，供批量上传使用
      return activeThreadId;
    } catch (error) {
      console.error('[事实认定书上传] 失败:', error);
      toast.error('上传失败，请稍后重试');
      return undefined; // 返回 undefined 表示上传失败
    } finally {
      setUploadingGroupIds(prev => ({ ...prev, 'new': false }));
    }
  };

  // 删除整个分组
  const deleteFileGroup = async (groupId: string) => {
    if (!confirm('确定要删除此违法行为及其所有文件吗？')) {
      return;
    }

    try {
      const response = await fetch(
        `${API_BASE}/api/conversations/${threadId}/file-groups/${groupId}?delete_evidence_only=false`,
        {
          method: 'DELETE',
          credentials: 'include',
        }
      );

      const result = await response.json();

      if (result.status === 'error') {
        toast.error(result.msg || '删除失败');
        return;
      }

      toast.success('已删除违法行为');
      // 刷新文件分组列表
      await fetchFileGroups();
    } catch (error) {
      console.error('[删除分组] 失败:', error);
      toast.error('删除失败，请稍后重试');
    }
  };

  // 清空所有文件分组
  const clearAllFileGroups = async () => {
    if (!confirm(`确定要清空全部 ${fileGroups.length} 个违法行为吗？此操作不可恢复！`)) {
      return;
    }

    try {
      const response = await fetch(
        `${API_BASE}/api/conversations/${threadId}/file-groups`,
        {
          method: 'DELETE',
          credentials: 'include',
        }
      );

      const result = await response.json();

      if (result.status === 'error') {
        toast.error(result.msg || '清空失败');
        return;
      }

      toast.success(result.msg || '已清空所有违法行为');
      // 刷新文件分组列表
      await fetchFileGroups();
    } catch (error) {
      console.error('[清空所有分组] 失败:', error);
      toast.error('清空失败，请稍后重试');
    }
  };

  const suggestedPrompts = useMemo(() => {
    const prompts: string[] = [];

    if (analysisResult && analysisResult.违法行为分析) {
      const firstViolation = analysisResult.违法行为分析[0];

      if (firstViolation) {
        if (firstViolation.违反条款 && firstViolation.违反条款.length > 0) {
          const firstLaw = formatLawItem(firstViolation.违反条款[0]);
          if (firstLaw) {
            prompts.unshift(`结合 ${firstLaw}，还需要注意哪些法律要点？`);
          }
        }
        if (firstViolation.整改措施 && firstViolation.整改措施.length > 0) {
          prompts.unshift("针对这些问题还有更细化的整改措施吗？");
        }
        if (firstViolation.处罚建议 && firstViolation.处罚建议.length > 0) {
          prompts.push("请帮我概括处罚建议里的重点理由。");
        }
      }

      if (analysisResult.违法行为数量 > 1) {
        prompts.push(`这${analysisResult.违法行为数量}项事实认定如何合并处罚？`);
      }
    }

    return Array.from(new Set(prompts)).slice(0, 4);
  }, [analysisResult]);

  const handleChatSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const inputContent = chatInput.trim();

    // 报告生成模式
    if (selectedTool === "report-generation" && selectedReportSubType) {
      const reportConfig = getReportConfig(selectedReportSubType);

      if (reportConfig.inputType === 'fact_determination') {
        // 检查报告生成：需要先上传事实认定书
        if (fileGroups.length === 0) {
          toast.error("请先上传事实认定书");
          return;
        }

        // 提取文件信息
        const uploadedFilesInfo = fileGroups.map(group => ({
          file_id: group.fact_determination?.file_id || '',
          file_name: group.fact_determination?.filename || '',
          file_type: group.fact_determination?.file_type || 'docx'
        })).filter(f => f.file_id);

        // 调用报告生成 API，传入文件信息和自动预览回调
        await sendReportRequest(
          selectedReportSubType,
          uploadedFilesInfo,
          (doc) => {
            // 自动预览
            if (onWordDocumentView) {
              onWordDocumentView({
                filename: doc.filename,
                title: doc.title
              });
            }
          }
        );
      } else {
        // 其他报告生成：需要先上传检查报告
        if (!inspectionReportFile) {
          toast.error("请先上传检查报告Word文档");
          return;
        }

        // 调用报告生成 API，传入检查报告文件信息
        await sendReportRequest(
          selectedReportSubType,
          [inspectionReportFile],  // 传入检查报告文件信息
          (doc) => {
            // 自动预览（根据文件类型判断）
            if (doc.excelData && onExcelDocumentView) {
              onExcelDocumentView({
                filename: doc.filename,
                title: doc.title,
                excelData: doc.excelData
              });
            } else if (onWordDocumentView) {
              onWordDocumentView({
                filename: doc.filename,
                title: doc.title,
                sourceData: doc.sourceData
              });
            }
          }
        );
      }

      // 清空选择
      setSelectedReportSubType(null);
      setSelectedTool(null);
      return;
    }

    // 违法行为分析模式：需要输入内容
    if (selectedTool === "violation-analysis" && analysisStage === "initial") {
      if (!inputContent) {
        toast.error("请输入事实认定内容");
        return;
      }

      // 提取文件信息
      const uploadedFilesInfo = fileGroups.map(group => ({
        file_id: group.fact_determination?.file_id || '',
        file_name: group.fact_determination?.filename || '',
        file_type: group.fact_determination?.file_type || 'docx'
      })).filter(f => f.file_id);

      // 触发分析（onSubmit 内部会添加用户消息）
      const formData = {
        violationDescription: inputContent,
        uploadedFiles: uploadedFilesInfo.length > 0 ? uploadedFilesInfo : undefined
      };
      setChatInput(""); // 清空输入框
      setSelectedTool(null); // 清空工具选择
      await onSubmit(formData as ViolationFormValues);
      return;
    }

    // 其他模式需要输入内容
    if (!inputContent) return;

    // 发送普通聊天消息（sendChatMessage 内部会清空输入框）
    void sendChatMessage(inputContent);
  };

  const handlePromptClick = (prompt: string) => {
    void sendChatMessage(prompt);
  };

  const handleChatInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      // 使用表单提交，保持逻辑一致
      const form = event.currentTarget.form;
      if (form) {
        form.requestSubmit();
      }
    }
  };

  const handleFormReset = () => {
    // The form reset is handled by react-hook-form's reset function
    // which is passed via register
  };

  const renderChatMessagesSection = () => (
    <div className="space-y-4">
      {chatMessages.map((message) => {
        // 使用 parseMessageContent 解析消息内容
        const parsed = parseMessageContent(message);
        const { textContent, files, tasks, analysisData, excelData, reportSourceData } = parsed;

        // 如果是分析消息（通过 tasks 或 analysisData 判断）
        const hasAnalysisData = tasks.length > 0 || analysisData !== null || files.length > 0;
        if (hasAnalysisData) {
          const isStreamingAnalysis = tasks.length > 0 && !tasks.every(t => t.status === 'completed' || t.status === 'error' || t.status === 'cancelled');

          return (
            <div key={message.id} className="flex flex-col gap-2">
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4 }}
                className="w-full"
              >
                <div className="flex flex-col gap-1.5">
                  <AnalysisProgressMessage
                    tasks={tasks}
                    data={analysisData ? analysisData : (excelData ? { excel_data: excelData } : null)}
                    files={files}
                    reportSourceData={reportSourceData}
                    threadId={threadId || ''}
                    updatedWordFilename={updatedWordFilename}
                    onLawClick={onLawClick}
                    onWordDocumentView={onWordDocumentView}
                    onExcelDocumentView={onExcelDocumentView}
                    onReportClick={onReportClick}
                  />

                  {/* AI 分析消息尾部区域 - 只在分析完成后显示 */}
                  {!isStreamingAnalysis && analysisData && (
                    <div className="flex items-center justify-between gap-2 px-2 mt-2">
                      <div className="flex items-center gap-2">
                        {/* 复制按钮 */}
                        <button
                          type="button"
                          className={mergeClassNames(
                            "transition-colors",
                            copiedMessageId === message.id
                              ? "text-green-500"
                              : "text-gray-400 hover:text-indigo-500"
                          )}
                          title={copiedMessageId === message.id ? "已复制" : "复制内容"}
                          onClick={() => {
                            const content = JSON.stringify(analysisData, null, 2);
                            navigator.clipboard.writeText(content).then(() => {
                              setCopiedMessageId(message.id);
                              setTimeout(() => setCopiedMessageId(null), 2000);
                            }).catch(err => console.error('复制失败:', err));
                          }}
                        >
                          <i className={mergeClassNames(
                            "text-xs",
                            copiedMessageId === message.id ? "fa-solid fa-check" : "fa-regular fa-copy"
                          )}></i>
                        </button>
                        {/* 刷新按钮 - 重新分析 */}
                        <button
                          type="button"
                          className="text-gray-400 hover:text-indigo-500 transition-colors"
                          title="重新分析"
                          onClick={() => {
                            const messageIndex = chatMessages.findIndex(m => m.id === message.id);
                            if (messageIndex > 0) {
                              const previousMessage = chatMessages[messageIndex - 1];
                              if (previousMessage && previousMessage.role === "user") {
                                void onSubmit({ violationDescription: previousMessage.content } as ViolationFormValues);
                              }
                            }
                          }}
                        >
                          <i className="fa-solid fa-rotate-right text-xs"></i>
                        </button>
                      </div>
                      {/* 时间和 AI 生成标识 */}
                      <div className="flex items-center gap-2">
                        <div className="text-[10px] text-gray-400 dark:text-gray-500">
                          {formatTimestamp(message.timestamp)}
                        </div>
                        <div className="text-[10px] text-gray-400 dark:text-gray-500">
                          AI生成
                        </div>
                      </div>
                    </div>
                  )}

                  {/* 正在生成状态 */}
                  {isStreamingAnalysis && (
                    <div className="flex items-center gap-1.5 px-2 text-xs text-gray-500 dark:text-gray-400 mt-2">
                      <i className="fa-solid fa-spinner fa-spin text-[10px]"></i>
                      <span>正在生成...</span>
                    </div>
                  )}
                </div>
              </motion.div>
            </div>
          );
        }

        // 普通文本消息的原有渲染逻辑
        const isStreamingAssistantMessage =
          isChatStreaming &&
          message.role === "assistant" &&
          activeAssistantMessageId === message.id;
        const shouldShowMessageBubble =
          message.role === "user" || textContent.trim().length > 0;

        return (
          <div
            key={message.id}
            className={mergeClassNames(
              "flex flex-col gap-2",
              message.role === "user" ? "items-end" : "items-start",
            )}
          >
            {/* 显示用户上传的文件（在消息气泡上方） */}
            {message.role === "user" && message.additional_kwargs?.task?.uploaded_files?.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {message.additional_kwargs.task.uploaded_files.map((file: any, index: number) => (
                  <div
                    key={index}
                    className="inline-flex items-center gap-2 px-3 py-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm"
                  >
                    <div className="flex-shrink-0 w-8 h-8 bg-blue-100 dark:bg-blue-900/30 rounded flex items-center justify-center">
                      <i className="fa-solid fa-file-word text-blue-600 dark:text-blue-400 text-sm"></i>
                    </div>
                    <div className="flex flex-col min-w-0 max-w-[160px]">
                      <div className="text-xs font-medium text-gray-900 dark:text-gray-100 truncate">
                        {file.file_name}
                      </div>
                      <div className="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">
                        {file.file_type?.toUpperCase() || 'DOC'}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {shouldShowMessageBubble && (
              <div className="group flex flex-col gap-1.5 max-w-[85%]">
                <div
                  className={mergeClassNames(
                    "rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm",
                    message.role === "user"
                      ? "rounded-br-sm bg-[#edf3fe] text-gray-800 dark:bg-blue-900/30 dark:text-gray-200"
                      : "rounded-bl-sm border border-gray-200 bg-white text-gray-800 dark:border-gray-700 dark:bg-gray-900/70 dark:text-gray-200",
                  )}
                >
                  {message.role === "assistant" ? (
                    <div className="prose prose-sm max-w-none dark:prose-invert prose-headings:mt-3 prose-headings:mb-1 prose-p:my-1.5 prose-li:my-1 prose-ul:pl-5 prose-ol:pl-5">
                      {(() => {
                        // 检测下载按钮占位符（旧格式兼容）
                        const downloadMatch = textContent.match(/\[DOWNLOAD_BUTTON:(.+?)\]/);
                        const contentWithoutButton = textContent.replace(/\[DOWNLOAD_BUTTON:.+?\]/g, '').trim();

                        return (
                          <>
                            <ReactMarkdown
                              remarkPlugins={chatMarkdownPlugins}
                              components={chatMarkdownComponents}
                            >
                              {contentWithoutButton}
                            </ReactMarkdown>
                            {/* 渲染预览和下载按钮 */}
                            {downloadMatch && (() => {
                              const filename = downloadMatch[1];
                              // excelData 已通过 parseMessageContent 从 NonStandardContentBlock 中解析
                              const parsedExcelData = parsed.excelData;
                              const isExcel = filename.endsWith('.xlsx') || filename.endsWith('.xls');

                              const handleDownload = () => {
                                const downloadUrl = `${API_BASE}/api/conversations/${threadId}/files/${filename}`;
                                const link = window.document.createElement('a');
                                link.href = downloadUrl;
                                link.download = filename;
                                window.document.body.appendChild(link);
                                link.click();
                                window.document.body.removeChild(link);
                              };

                              const handlePreview = () => {
                                if (isExcel && parsedExcelData && onExcelDocumentView) {
                                  // 根据文件名判断Excel类型
                                  let excelTitle = '信息简表预览';
                                  if (filename.includes('证据目录')) {
                                    excelTitle = '证据目录预览';
                                  }
                                  // Excel预览
                                  onExcelDocumentView({
                                    filename,
                                    title: excelTitle,
                                    excelData: parsedExcelData
                                  });
                                } else if (onWordDocumentView) {
                                  // Word预览
                                  onWordDocumentView({
                                    filename,
                                    title: '整改通知书预览'
                                  });
                                }
                              };

                              return (
                                <div className="mt-2 flex items-center gap-2">
                                  <button
                                    onClick={handlePreview}
                                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 hover:bg-indigo-700 text-white rounded-md transition-colors duration-200"
                                  >
                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                                    </svg>
                                    <span>预览{isExcel ? '数据' : '文档'}</span>
                                  </button>
                                  <button
                                    onClick={handleDownload}
                                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-md transition-colors duration-200"
                                  >
                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                    </svg>
                                    <span>下载文档</span>
                                  </button>
                                </div>
                              );
                            })()}
                          </>
                        );
                      })()}
                    </div>
                  ) : (
                    <div className="whitespace-pre-wrap break-words">
                      {textContent}
                    </div>
                  )}
                </div>

                {/* 用户消息尾部 - 只显示时间 */}
                {message.role === "user" && (
                  <div className="flex items-center justify-end gap-2 px-2 mt-1.5">
                    <div className="text-[10px] text-gray-500 dark:text-gray-400">
                      {formatTimestamp(message.timestamp)}
                    </div>
                  </div>
                )}

                {/* AI 消息尾部区域 - 只在生成结束后显示 */}
                {message.role === "assistant" && !isStreamingAssistantMessage && (
                  <div className="flex items-center justify-between gap-2 px-2 mt-1.5">
                    <div className="flex items-center gap-2">
                      {/* 复制按钮 */}
                      <button
                        type="button"
                        className={mergeClassNames(
                          "transition-colors",
                          copiedMessageId === message.id
                            ? "text-green-500"
                            : "text-gray-400 hover:text-indigo-500"
                        )}
                        title={copiedMessageId === message.id ? "已复制" : "复制内容"}
                        onClick={() => {
                          navigator.clipboard.writeText(textContent).then(() => {
                            setCopiedMessageId(message.id);
                            setTimeout(() => setCopiedMessageId(null), 2000);
                          }).catch(err => console.error('复制失败:', err));
                        }}
                      >
                        <i className={mergeClassNames(
                          "text-xs",
                          copiedMessageId === message.id ? "fa-solid fa-check" : "fa-regular fa-copy"
                        )}></i>
                      </button>
                      {/* 刷新按钮 - 重新生成 */}
                      <button
                        type="button"
                        className="text-gray-400 hover:text-indigo-500 transition-colors"
                        title="重新生成"
                        onClick={() => {
                          const messageIndex = chatMessages.findIndex(m => m.id === message.id);
                          if (messageIndex > 0) {
                            const previousMessage = chatMessages[messageIndex - 1];
                            if (previousMessage && previousMessage.role === "user") {
                              const prevParsed = parseMessageContent(previousMessage);
                              void sendChatMessage(prevParsed.textContent);
                            }
                          }
                        }}
                      >
                        <i className="fa-solid fa-rotate-right text-xs"></i>
                      </button>
                    </div>
                    {/* 时间和 AI 生成标识 */}
                    <div className="flex items-center gap-2">
                      <div className="text-[10px] text-gray-400 dark:text-gray-500">
                        {formatTimestamp(message.timestamp)}
                      </div>
                      <div className="text-[10px] text-gray-400 dark:text-gray-500">
                        AI生成
                      </div>
                    </div>
                  </div>
                )}

                {/* 正在生成状态 - 显示在消息气泡下方 */}
                {message.role === "assistant" && isStreamingAssistantMessage && (
                  <div className="flex items-center gap-1.5 px-2 text-xs text-gray-500 dark:text-gray-400 mt-1.5">
                    <i className="fa-solid fa-spinner fa-spin text-[10px]"></i>
                    <span>正在生成...</span>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );

  return (
    <motion.section
      key="conversation"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className={mergeClassNames(
        "relative flex flex-col bg-white dark:bg-gray-900",
        panelHeightClass,
      )}
    >
      <div className="flex items-center justify-center px-3 py-3 h-[52px]">
        <h2 className={mergeClassNames(
          "text-xs font-medium truncate",
          conversationTitle
            ? "text-gray-900 dark:text-white"
            : "text-gray-500 dark:text-gray-400"
        )} title={conversationTitle}>
          {conversationTitle || "新会话"}
        </h2>
      </div>

      {/* 消息区域 - 有消息时显示 */}
      {chatMessages.length > 0 && (
        <div
          ref={chatContainerRef}
          className="flex-1 overflow-y-auto bg-gray-50/30 py-3 dark:bg-gray-800/20"
        >
          <div className="w-[774px] max-w-[90%] mx-auto space-y-4">
            {renderChatMessagesSection()}

          {/* 建议问题 - 只在没有消息正在生成时显示 */}
          {suggestedPrompts.length > 0 && !isChatStreaming && (
            <div className="pt-2">
              <div className="mt-3 flex flex-wrap gap-2">
                {suggestedPrompts.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => handlePromptClick(prompt)}
                    title={prompt}
                    className="rounded-full border border-indigo-200/80 bg-indigo-50/70 px-3 py-2 text-xs text-indigo-600 transition-colors hover:bg-indigo-100 dark:border-indigo-800/60 dark:bg-indigo-900/30 dark:text-indigo-300 dark:hover:bg-indigo-800/40"
                  >
                    {formatPromptPreview(prompt, MAX_PROMPT_DISPLAY_LENGTH)}
                  </button>
                ))}
              </div>
            </div>
          )}
          </div>
        </div>
      )}

      {/* 新会话时：欢迎信息（占位，实际内容通过 absolute 定位） */}
      {chatMessages.length === 0 && (
        <div className="flex-1" />
      )}

      <form
        onSubmit={handleChatSubmit}
        className={mergeClassNames(
          "pt-2 bg-white dark:bg-gray-900 w-[774px] max-w-[90%] mx-auto transition-all duration-300",
          chatMessages.length === 0 && "absolute left-1/2 -translate-x-1/2 top-[calc(40%+32px)] -translate-y-1/2"
        )}
      >
        {/* 新会话时显示欢迎语（选择工具后隐藏） */}
        {chatMessages.length === 0 && !selectedTool && (
          <motion.h3
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="text-[24px] font-semibold text-gray-900 dark:text-gray-100 text-center mb-4"
          >
            执法检查AI辅助
          </motion.h3>
        )}
        <div
          ref={formRef}
          className="rounded-2xl border border-gray-200 bg-white shadow-sm transition-all hover:border-gray-300 focus-within:border-indigo-400 focus-within:shadow-md dark:border-gray-700 dark:bg-gray-800/50"
          style={{ minHeight: `${formMinHeight}px`, maxHeight: `${formMaxHeight}px` }}
        >
          {/* 输入区域 - 统一布局结构 */}
          <div className="flex items-start gap-3 px-4 pt-4 pb-2">
            {/* 工具名称 - 显示选中的工具，固定在左侧 */}
            {(selectedTool === "violation-analysis" || (selectedTool === "report-generation" && selectedReportSubType)) && (
              <span className="text-base font-bold text-gray-900 dark:text-gray-100 flex-shrink-0 leading-[26px]">
                {selectedTool === "violation-analysis" ? "事实认定分析" : getReportTypeName(selectedReportSubType)}
              </span>
            )}
            {/* 内容区域 - 统一使用 flex-1 容器，固定最小高度 54px（与 textarea 初始高度一致） */}
            <div className="flex-1 min-h-[54px] flex items-start">
              {selectedTool === "report-generation" && selectedReportSubType ? (
                (() => {
                  const reportConfig = getReportConfig(selectedReportSubType);

                  if (reportConfig.inputType === 'fact_determination') {
                    /* 检查报告生成模式：展示已上传文件列表 */
                    return (
                      <div className="flex flex-col gap-2 w-full">
                        {/* 已上传的文件列表 */}
                        {fileGroups.length > 0 ? (
                          <div className="flex flex-wrap gap-2">
                            {fileGroups.map((group, index) => (
                              <div
                                key={group.group_id}
                                className="group flex items-center gap-1.5 px-2 py-1 bg-indigo-50 dark:bg-indigo-900/30 rounded text-sm"
                              >
                                <span className="w-4 h-4 rounded-full bg-indigo-200 dark:bg-indigo-800 text-indigo-700 dark:text-indigo-300 flex items-center justify-center text-xs font-medium flex-shrink-0">
                                  {index + 1}
                                </span>
                                <i className="fa-solid fa-file-word text-xs text-indigo-600 dark:text-indigo-400"></i>
                                <span className="text-gray-700 dark:text-gray-300 max-w-[150px] truncate" title={group.fact_determination.filename}>
                                  {group.fact_determination.filename}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => deleteFileGroup(group.group_id)}
                                  className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 transition-all"
                                  title="删除"
                                >
                                  <i className="fa-solid fa-times text-xs"></i>
                                </button>
                              </div>
                            ))}
                          </div>
                        ) : (
                          /* 无文件时的提示 */
                          <span className="text-base text-gray-400 dark:text-gray-500 leading-[26px]">
                            {uploadingGroupIds['new'] ? '上传中...' : '请点击右下角按钮上传事实认定书'}
                          </span>
                        )}
                      </div>
                    );
                  } else {
                    /* 其他报告生成模式：显示检查报告上传区 */
                    return (
                      <div className="flex items-center gap-3 h-[26px]">
                        <span className="text-base text-gray-400 dark:text-gray-500 whitespace-nowrap leading-[26px]">
                          {reportConfig.uploadHint}:
                        </span>
                        <label className={`flex items-center gap-1.5 px-2.5 py-0.5 text-sm rounded transition-colors cursor-pointer leading-[26px] ${
                          inspectionReportFile
                            ? 'bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-900/30 dark:hover:bg-indigo-900/50'
                            : 'bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600'
                        }`}>
                          <i className={`fa-solid ${inspectionReportFile ? 'fa-file-word' : 'fa-upload'} text-xs ${
                            inspectionReportFile
                              ? 'text-indigo-600 dark:text-indigo-400'
                              : 'text-gray-500 dark:text-gray-400'
                          }`}></i>
                          <span className={`${inspectionReportFile ? 'text-gray-900 dark:text-gray-100' : 'text-gray-600 dark:text-gray-400'} max-w-[300px] truncate`}>
                            {isUploadingInspectionReport ? '上传中...' : (inspectionReportFile ? inspectionReportFile.file_name : '选择检查报告')}
                          </span>
                          {inspectionReportFile && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                setInspectionReportFile(null);
                              }}
                              className="ml-1 text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400"
                              title="删除"
                            >
                              <i className="fa-solid fa-times text-xs"></i>
                            </button>
                          )}
                          <input
                            type="file"
                            accept=".docx,.doc,.wps"
                            className="hidden"
                            disabled={isUploadingInspectionReport}
                            onChange={async (e) => {
                              const file = e.target.files?.[0];
                              if (file) {
                                let currentThreadId = threadId;
                                if (!currentThreadId && onEnsureThread) {
                                  currentThreadId = await onEnsureThread();
                                  if (!currentThreadId) {
                                    toast.error('创建会话失败');
                                    return;
                                  }
                                }
                                await uploadInspectionReport(file, currentThreadId);
                              }
                              // 清空 input 以允许重复上传同一文件
                              e.target.value = '';
                            }}
                          />
                        </label>
                      </div>
                    );
                  }
                })()
              ) : (
                /* 其他模式：显示 textarea */
                <textarea
                  ref={textareaRef}
                  value={chatInput}
                  onChange={(event) => setChatInput(event.target.value)}
                  onKeyDown={handleChatInputKeyDown}
                  onInput={(event) => adjustTextareaHeight(event.currentTarget)}
                  placeholder={
                    selectedTool === "violation-analysis"
                      ? "请输入事实认定内容"
                      : selectedTool === "report-generation" && selectedReportSubType === "comprehensive-report"
                      ? "请上传事实认定书文件"
                      : selectedTool === "report-generation"
                      ? "该报告类型暂未开放"
                      : analysisStage === "initial"
                      ? "请先选择工具，或直接输入内容"
                      : "请输入追问内容，按 Enter 发送，Shift + Enter 换行"
                  }
                  className="w-full resize-none border-0 bg-transparent text-base text-gray-700 placeholder-gray-400 outline-none dark:text-gray-100 dark:placeholder-gray-500 leading-[26px]"
                  style={{ overflowY: "hidden" }}
                  disabled={selectedTool === "report-generation" && selectedReportSubType !== "comprehensive-report" && selectedReportSubType !== null}
                />
              )}
            </div>
          </div>

          {/* 操作区域 */}
          <div className="flex items-center justify-between gap-2 px-4 py-2">
            <div className="flex items-center gap-1.5 text-xs">
              {/* 选择工具后或有消息时显示工具选择器 */}
              {(chatMessages.length > 0 || selectedTool) && (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      if (selectedTool === "violation-analysis") {
                        setSelectedTool(null);
                      } else {
                        setSelectedTool("violation-analysis");
                        setSelectedReportSubType(null);
                      }
                    }}
                    className={`px-2 py-1 rounded-md transition-colors ${
                      selectedTool === "violation-analysis"
                        ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-300"
                        : "text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-300"
                    }`}
                  >
                    <i className="fa-solid fa-scale-balanced mr-1"></i>
                    事实认定
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (selectedTool === "report-generation" && selectedReportSubType === "comprehensive-report") {
                        setSelectedTool(null);
                        setSelectedReportSubType(null);
                      } else {
                        setSelectedTool("report-generation");
                        setSelectedReportSubType("comprehensive-report");
                      }
                    }}
                    className={`px-2 py-1 rounded-md transition-colors ${
                      selectedTool === "report-generation" && selectedReportSubType === "comprehensive-report"
                        ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-300"
                        : "text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-300"
                    }`}
                  >
                    <i className="fa-solid fa-file-word mr-1"></i>
                    检查报告
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (selectedTool === "report-generation" && selectedReportSubType === "rectification-notice") {
                        setSelectedTool(null);
                        setSelectedReportSubType(null);
                      } else {
                        setSelectedTool("report-generation");
                        setSelectedReportSubType("rectification-notice");
                      }
                    }}
                    className={`px-2 py-1 rounded-md transition-colors ${
                      selectedTool === "report-generation" && selectedReportSubType === "rectification-notice"
                        ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-300"
                        : "text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-300"
                    }`}
                  >
                    <i className="fa-solid fa-file-word mr-1"></i>
                    整改通知
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (selectedTool === "report-generation" && selectedReportSubType === "evidence-catalog") {
                        setSelectedTool(null);
                        setSelectedReportSubType(null);
                      } else {
                        setSelectedTool("report-generation");
                        setSelectedReportSubType("evidence-catalog");
                      }
                    }}
                    className={`px-2 py-1 rounded-md transition-colors ${
                      selectedTool === "report-generation" && selectedReportSubType === "evidence-catalog"
                        ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-300"
                        : "text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-300"
                    }`}
                  >
                    <i className="fa-solid fa-file-excel mr-1"></i>
                    证据目录
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (selectedTool === "report-generation" && selectedReportSubType === "info-summary") {
                        setSelectedTool(null);
                        setSelectedReportSubType(null);
                      } else {
                        setSelectedTool("report-generation");
                        setSelectedReportSubType("info-summary");
                      }
                    }}
                    className={`px-2 py-1 rounded-md transition-colors ${
                      selectedTool === "report-generation" && selectedReportSubType === "info-summary"
                        ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-300"
                        : "text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-300"
                    }`}
                  >
                    <i className="fa-solid fa-file-excel mr-1"></i>
                    信息简表
                  </button>
                </>
              )}
            </div>

            {/* 附件上传按钮 - 检查报告生成模式（事实认定书）时显示 */}
            {selectedTool === "report-generation" && selectedReportSubType && getReportConfig(selectedReportSubType).inputType === 'fact_determination' && (
              <label className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full transition-all hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer">
                <i className={`fa-solid ${uploadingGroupIds['new'] ? 'fa-spinner fa-spin' : 'fa-paperclip'} text-sm text-gray-500 dark:text-gray-400`}></i>
                <input
                  type="file"
                  accept=".docx,.wps"
                  multiple
                  className="hidden"
                  disabled={uploadingGroupIds['new'] || fileGroups.length >= 10}
                  onChange={async (e) => {
                    const files = Array.from(e.target.files || []);
                    if (files.length > 0) {
                      const remainingSlots = 10 - fileGroups.length;
                      if (files.length > remainingSlots) {
                        toast.error(`最多只能上传 ${remainingSlots} 个文件（当前已有 ${fileGroups.length} 个）`);
                        e.target.value = '';
                        return;
                      }
                      let sharedThreadId: string | null = threadId;
                      for (const file of files) {
                        try {
                          const usedThreadId = await uploadFactDetermination(file, sharedThreadId, true);
                          if (!sharedThreadId && usedThreadId) {
                            sharedThreadId = usedThreadId;
                          }
                        } catch (error) {
                          console.error('上传文件失败:', error);
                        }
                      }
                      e.target.value = '';
                    }
                  }}
                />
              </label>
            )}

            {isChatStreaming ? (
              <button
                type="button"
                onClick={() => void stopChatStreaming()}
                className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white shadow-sm transition-all hover:bg-indigo-700"
                title="停止生成"
              >
                <i className="fa-solid fa-stop text-xs"></i>
              </button>
            ) : (
              <button
                type="submit"
                title={(() => {
                  if (selectedTool === "violation-analysis" && analysisStage === "initial") {
                    return chatInput.trim().length === 0 ? "请输入事实认定内容" : "开始分析";
                  }
                  if (selectedTool === "report-generation" && selectedReportSubType) {
                    const reportConfig = getReportConfig(selectedReportSubType);
                    if (reportConfig.inputType === 'fact_determination') {
                      return fileGroups.length === 0 ? "请先上传事实认定书" : "生成检查报告";
                    } else {
                      return !inspectionReportFile ? "请先上传检查报告Word文档" : `生成${getReportTypeName(selectedReportSubType)}`;
                    }
                  }
                  if (analysisStage !== "initial" && !hasFiveRequiredInputs) {
                    return `请先完成事实认定：缺少${missingRequiredLabels.join("、")}`;
                  }
                  return "发送消息";
                })()}
                disabled={
                  isChatStreaming ||
                  (selectedTool === "violation-analysis" && chatInput.trim().length === 0) ||
                  (selectedTool === "report-generation" && selectedReportSubType && (() => {
                    const reportConfig = getReportConfig(selectedReportSubType);
                    if (reportConfig.inputType === 'fact_determination') {
                      return fileGroups.length === 0;
                    } else {
                      return !inspectionReportFile;
                    }
                  })()) ||
                  (selectedTool !== "violation-analysis" && selectedTool !== "report-generation" && chatInput.trim().length === 0) ||
                  (analysisStage !== "initial" && !hasFiveRequiredInputs && selectedTool !== "violation-analysis")
                }
                className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white shadow-sm transition-all hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-gray-300 disabled:shadow-none dark:disabled:bg-gray-700"
              >
                <i className="fa-solid fa-arrow-up text-xs"></i>
              </button>
            )}
          </div>
        </div>

        {/* 新会话时显示功能卡片（在输入框下方），选择工具后隐藏 */}
        {chatMessages.length === 0 && !selectedTool && (
          <ToolCards
            onSelectTool={(toolId, subType) => {
              setSelectedTool(toolId);
              setSelectedReportSubType(subType || null);
            }}
          />
        )}

        {/* AI 免责声明 - 始终显示 */}
        <div className="pt-1 pb-0.5 text-center text-xs text-gray-400 dark:text-gray-500">
          内容由AI生成，请仔细甄别
        </div>
      </form>

    </motion.section>
  );
}
