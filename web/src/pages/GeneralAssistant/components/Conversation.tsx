import { useState, useRef, useEffect, useMemo, FormEvent, KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AgentGrid } from "./AgentGrid";
import { DeepThinkIcon } from "./unified/conversation/ThinkingBlock";
import type { ChatMessage, Agent, UploadedFile } from "../types";
import { AGENT_CATEGORIES } from "../agentRegistry";
import uploadIcon from "../../../assets/upload.png";

interface ConversationProps {
  messages: ChatMessage[];
  input: string;
  setInput: (value: string) => void;
  isStreaming: boolean;
  onSendMessage: (message: string) => void;
  onStopStreaming: () => void;
  onAgentSelect: (agent: Agent) => void;
  conversationTitle?: string;
  panelHeightClass: string;
  // 文件上传相关
  uploadedFiles: UploadedFile[];
  onFileUpload: (files: FileList) => void;
  onRemoveFile: (fileId: string) => void;
}

export function Conversation({
  messages,
  input,
  setInput,
  isStreaming,
  onSendMessage,
  onStopStreaming,
  onAgentSelect,
  conversationTitle,
  panelHeightClass,
  uploadedFiles,
  onFileUpload,
  onRemoveFile,
}: ConversationProps) {
  const navigate = useNavigate();

  const handleNewConversation = () => {
    window.location.reload(); // 简单刷新页面以开始新会话
  };
  const [deepseekR1Enabled, setDeepseekR1Enabled] = useState(true);
  const [deepThinkEnabled, setDeepThinkEnabled] = useState(false);
  const [currentDescIndex, setCurrentDescIndex] = useState(0);
  const [displayedText, setDisplayedText] = useState("");
  const [isTyping, setIsTyping] = useState(true);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const scrollAnchorRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 从分类配置中获取功能描述
  const featureDescriptions = useMemo(() =>
    AGENT_CATEGORIES
      .filter(cat => cat.description)
      .map(cat => cat.description!),
    []
  );

  // 打字机效果 - 一个字一个字显示
  useEffect(() => {
    if (featureDescriptions.length === 0) return;
    const currentText = featureDescriptions[currentDescIndex];
    let charIndex = 0;
    setDisplayedText("");
    setIsTyping(true);

    const typingInterval = setInterval(() => {
      if (charIndex < currentText.length) {
        setDisplayedText(currentText.slice(0, charIndex + 1));
        charIndex++;
      } else {
        setIsTyping(false);
        clearInterval(typingInterval);
      }
    }, 100); // 每100ms显示一个字

    return () => clearInterval(typingInterval);
  }, [currentDescIndex, featureDescriptions]);

  // 切换到下一句话
  useEffect(() => {
    if (!isTyping && featureDescriptions.length > 0) {
      const timer = setTimeout(() => {
        setCurrentDescIndex((prev) => (prev + 1) % featureDescriptions.length);
      }, 2000); // 显示完成后等待2秒再切换

      return () => clearTimeout(timer);
    }
  }, [isTyping, featureDescriptions]);

  // 使用 Intersection Observer 检测锚点是否可见（业界最佳实践）
  useEffect(() => {
    const anchor = scrollAnchorRef.current;
    const container = chatContainerRef.current;
    if (!anchor || !container) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // 锚点可见 = 用户在底部
        setIsAtBottom(entries[0].isIntersecting);
      },
      {
        root: container,  // 指定滚动容器
        threshold: 0,
        rootMargin: "0px 0px 150px 0px"
      }
    );

    observer.observe(anchor);
    return () => observer.disconnect();
  }, [messages.length > 0]); // 当有消息时重新设置 observer

  // 自动滚动：只有当用户在底部时才滚动
  useEffect(() => {
    if (!isAtBottom) return;

    const anchor = scrollAnchorRef.current;
    if (!anchor) return;

    // 使用 requestAnimationFrame 避免频繁滚动
    const rafId = requestAnimationFrame(() => {
      anchor.scrollIntoView({ behavior: "instant", block: "end" });
    });

    return () => cancelAnimationFrame(rafId);
  }, [messages, isAtBottom]);

  // 发送新消息时滚动到底部
  useEffect(() => {
    if (messages.length > 0) {
      const lastMessage = messages[messages.length - 1];
      if (lastMessage.role === "user") {
        setIsAtBottom(true);
        setTimeout(() => {
          scrollAnchorRef.current?.scrollIntoView({ behavior: "instant", block: "end" });
        }, 0);
      }
    }
  }, [messages.length]);

  // 自适应输入框高度
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      const newHeight = Math.min(textareaRef.current.scrollHeight, 200);
      textareaRef.current.style.height = `${newHeight}px`;
      // 内容超出最大高度时显示滚动条
      textareaRef.current.style.overflowY = textareaRef.current.scrollHeight > 200 ? "auto" : "hidden";
    }
  }, [input]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (input.trim() && !isStreaming) {
      onSendMessage(input.trim());
      setInput("");
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as any);
    }
  };

  const handleAgentSelect = (agent: Agent) => {
    // 根据智能体的展示模式决定行为
    if (agent.displayMode === "navigate" && agent.navigatePath) {
      // 跳转到新页面
      navigate(agent.navigatePath);
    } else if (agent.displayMode === "inline") {
      // 在当前页面内展示（通过回调通知父组件）
      onAgentSelect(agent);
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      onFileUpload(files);
    }
    e.target.value = '';
  };

  // 处理粘贴事件（支持粘贴图片）
  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const imageFiles: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) {
          // 生成文件名：粘贴的图片_时间戳.扩展名
          const ext = item.type.split('/')[1] || 'png';
          const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
          const newFile = new File([file], `粘贴的图片_${timestamp}.${ext}`, { type: item.type });
          imageFiles.push(newFile);
        }
      }
    }

    if (imageFiles.length > 0) {
      e.preventDefault(); // 阻止默认粘贴行为
      // 创建 FileList-like 对象
      const dataTransfer = new DataTransfer();
      imageFiles.forEach(file => dataTransfer.items.add(file));
      onFileUpload(dataTransfer.files);
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`;
  };

  const getFileIcon = (fileName: string): string => {
    const ext = fileName.split('.').pop()?.toLowerCase();
    switch (ext) {
      case 'pdf':
        return 'fa-file-pdf';
      case 'doc':
      case 'docx':
        return 'fa-file-word';
      case 'xls':
      case 'xlsx':
        return 'fa-file-excel';
      case 'jpg':
      case 'jpeg':
      case 'png':
      case 'gif':
        return 'fa-file-image';
      case 'txt':
        return 'fa-file-lines';
      default:
        return 'fa-file';
    }
  };

  const getFileTypeLabel = (fileName: string): string => {
    const ext = fileName.split('.').pop()?.toUpperCase();
    return ext || 'FILE';
  };

  const truncateFileName = (fileName: string, maxLength: number = 30): string => {
    if (fileName.length <= maxLength) return fileName;
    const ext = fileName.split('.').pop() || '';
    const nameWithoutExt = fileName.slice(0, fileName.length - ext.length - 1);
    const truncatedName = nameWithoutExt.slice(0, maxLength - ext.length - 4);
    return `${truncatedName}...${ext}`;
  };

  return (
    <motion.section
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className={`relative flex flex-col ${panelHeightClass}`}
    >
      {/* 消息区域 - 有消息时显示 */}
      {messages.length > 0 && (
        <div
          ref={chatContainerRef}
          className="flex-1 overflow-y-auto py-3"
        >
          <div className="w-[900px] max-w-[90%] mx-auto space-y-4">
            {messages.map((message) => {
              const isStreamingAssistantMessage =
                isStreaming &&
                message.role === "assistant" &&
                message.id === messages[messages.length - 1]?.id;

              const messageContent = typeof message.content === "string" ? message.content : "";
              const hasContent = messageContent.trim().length > 0;

              // 如果是助手消息但还没有内容，只显示"正在生成..."状态
              if (message.role === "assistant" && !hasContent && isStreamingAssistantMessage) {
                return (
                  <div key={message.id} className="flex items-start">
                    <div className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 px-2">
                      <i className="fa-solid fa-spinner fa-spin text-[10px]"></i>
                      <span>正在生成...</span>
                    </div>
                  </div>
                );
              }

              // 如果是助手消息但没有内容且不在流式输出中，跳过渲染
              if (message.role === "assistant" && !hasContent) {
                return null;
              }

              return (
                <div
                  key={message.id}
                  className={`flex flex-col gap-2 ${
                    message.role === "user" ? "items-end" : "items-start"
                  }`}
                >
                  {/* 用户消息的附件显示（在消息气泡上方） */}
                  {message.role === "user" && message.attachments && message.attachments.length > 0 && (
                    <div className="flex flex-wrap gap-2 max-w-[85%]">
                      {message.attachments.map((file) => (
                        <div
                          key={file.file_id}
                          className="inline-flex items-center gap-2 px-3 py-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm"
                        >
                          <div className="flex-shrink-0 w-8 h-8 bg-indigo-100 dark:bg-indigo-900/30 rounded flex items-center justify-center">
                            <i className={`fa-solid ${getFileIcon(file.original_filename)} text-indigo-600 dark:text-indigo-400 text-sm`}></i>
                          </div>
                          <div className="flex flex-col min-w-0 max-w-[160px]">
                            <div className="text-xs font-medium text-gray-900 dark:text-gray-100 truncate">
                              {file.original_filename}
                            </div>
                            <div className="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">
                              {getFileTypeLabel(file.original_filename)} {formatFileSize(file.file_size)}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="group flex flex-col gap-1.5 max-w-[85%]">
                    <div
                      className={`rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
                        message.role === "user"
                          ? "rounded-br-sm bg-[#edf3fe] text-gray-800 dark:bg-blue-900/30 dark:text-gray-200"
                          : "overflow-x-auto rounded-bl-sm border border-gray-200 bg-white text-gray-800 dark:border-gray-700 dark:bg-gray-900/70 dark:text-gray-200"
                      }`}
                    >
                      {message.role === "assistant" ? (
                        <div className="prose prose-sm max-w-none dark:prose-invert prose-headings:mt-3 prose-headings:mb-1 prose-p:my-1.5 prose-li:my-1 prose-ul:pl-5 prose-ol:pl-5">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              table: ({ children }) => (
                                <div className="overflow-x-auto my-4">
                                  <table className="w-full border-collapse border border-gray-300 dark:border-gray-600">
                                    {children}
                                  </table>
                                </div>
                              ),
                              thead: ({ children }) => (
                                <thead className="bg-gray-100 dark:bg-gray-800">
                                  {children}
                                </thead>
                              ),
                              th: ({ children }) => (
                                <th className="border border-gray-300 dark:border-gray-600 px-3 py-2 text-left font-semibold">
                                  {children}
                                </th>
                              ),
                              td: ({ children }) => (
                                <td className="border border-gray-300 dark:border-gray-600 px-3 py-2">
                                  {children}
                                </td>
                              ),
                            }}
                          >
                            {messageContent}
                          </ReactMarkdown>
                        </div>
                      ) : (
                        <div className="whitespace-pre-wrap break-words">
                          {messageContent}
                        </div>
                      )}
                    </div>

                    {/* 助手消息底部信息 */}
                    {message.role === "assistant" && (
                      <div className="flex items-center justify-between gap-2 px-2">
                        {isStreamingAssistantMessage && hasContent ? (
                          <div className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
                            <i className="fa-solid fa-spinner fa-spin text-[10px]"></i>
                            <span>正在生成...</span>
                          </div>
                        ) : (
                          <>
                            <div className="flex items-center gap-2">
                              {/* 复制按钮 */}
                              <button
                                type="button"
                                className="text-gray-400 hover:text-indigo-500 transition-colors"
                                title="复制内容"
                                onClick={() => {
                                  navigator.clipboard.writeText(messageContent).catch(err => console.error('复制失败:', err));
                                }}
                              >
                                <i className="fa-regular fa-copy text-xs"></i>
                              </button>
                              {/* 重新生成按钮 */}
                              <button
                                type="button"
                                className="text-gray-400 hover:text-indigo-500 transition-colors"
                                title="重新生成"
                                onClick={() => {
                                  const messageIndex = messages.findIndex(m => m.id === message.id);
                                  if (messageIndex > 0) {
                                    const previousMessage = messages[messageIndex - 1];
                                    if (previousMessage && previousMessage.role === "user") {
                                      setInput(previousMessage.content);
                                      setTimeout(() => {
                                        const form = document.querySelector('form');
                                        form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                                      }, 100);
                                    }
                                  }
                                }}
                              >
                                <i className="fa-solid fa-rotate-right text-xs"></i>
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            {/* 滚动锚点 - 用于检测是否在底部 */}
            <div ref={scrollAnchorRef} className="h-px" />
          </div>
        </div>
      )}

      {/* 新会话时：欢迎信息占位 */}
      {messages.length === 0 && (
        <div className="flex-1" />
      )}

      {/* 输入框和工具区域 */}
      <div
        className={`w-[848px] max-w-[90%] mx-auto transition-all duration-300 ${
          messages.length === 0 ? "absolute left-1/2 -translate-x-1/2 top-[12%] max-h-[85vh] flex flex-col" : ""
        }`}
      >
        <form onSubmit={handleSubmit} className={`pt-2 flex-shrink-0 ${messages.length > 0 ? "pb-4" : ""}`}>
        {/* 新会话时显示欢迎语 */}
        {messages.length === 0 && (
          <div className="text-center mb-4">
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6 }}
              className="flex items-center justify-center gap-3 mb-2"
            >
              <h3
                className="text-[24px] font-semibold"
                style={{
                  background: 'linear-gradient(112deg, #007EA7 1%, #1F3A56 100%)',
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                  backgroundClip: 'text',
                }}
              >
                智能体中心
              </h3>
            </motion.div>
            <p className="text-sm text-gray-500 dark:text-gray-400 h-5">
              {displayedText}
              {isTyping && (
                <span className="inline-block w-0.5 h-4 ml-0.5 bg-gray-400 animate-pulse align-middle"></span>
              )}
            </p>
          </div>
        )}

        <div className="rounded-2xl border border-gray-200 bg-white shadow-sm transition-all hover:border-gray-300 focus-within:border-indigo-400 focus-within:shadow-md dark:border-gray-700 dark:bg-gray-800/50">
          {/* 上传文件展示区域 */}
          {uploadedFiles.length > 0 && (
            <div className="px-4 pt-4 pb-2">
              <div className="inline-flex flex-wrap gap-2">
                {uploadedFiles.map((file) => (
                  <div
                    key={file.file_id}
                    className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 w-64 ${
                      file.status === "completed"
                        ? "border-green-200 bg-green-50 dark:border-green-700 dark:bg-green-900/30"
                        : file.status === "failed"
                        ? "border-red-200 bg-red-50 dark:border-red-700 dark:bg-red-900/30"
                        : file.status === "parsing"
                        ? "border-amber-200 bg-amber-50 dark:border-amber-700 dark:bg-amber-900/30"
                        : file.status === "uploading"
                        ? "border-blue-200 bg-blue-50 dark:border-blue-700 dark:bg-blue-900/30"
                        : "border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-800/50"
                    }`}
                    title={file.original_filename}
                  >
                    <div className={`flex h-8 w-8 flex-shrink-0 items-center justify-center rounded ${
                      file.status === "uploading"
                        ? "bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400"
                        : file.status === "parsing"
                        ? "bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400"
                        : file.status === "completed"
                        ? "bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400"
                        : file.status === "failed"
                        ? "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400"
                        : "bg-indigo-100 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400"
                    }`}>
                      {file.status === "uploading" ? (
                        <i className="fa-solid fa-arrow-up-from-bracket fa-spin text-sm"></i>
                      ) : file.status === "parsing" ? (
                        <i className="fa-solid fa-gear fa-spin text-sm"></i>
                      ) : file.status === "completed" ? (
                        <i className="fa-solid fa-check text-sm"></i>
                      ) : file.status === "failed" ? (
                        <i className="fa-solid fa-exclamation text-sm"></i>
                      ) : (
                        <i className={`fa-solid ${getFileIcon(file.original_filename)} text-sm`}></i>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 dark:text-white truncate">
                        {file.original_filename}
                      </p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {file.status === "uploading"
                          ? "上传中..."
                          : file.status === "parsing"
                          ? "解析中..."
                          : file.status === "failed"
                          ? "解析失败"
                          : `${getFileTypeLabel(file.original_filename)} ${formatFileSize(file.file_size)}`}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => onRemoveFile(file.file_id)}
                      className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded text-gray-400 hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
                      title="删除文件"
                    >
                      <i className="fa-solid fa-times text-xs"></i>
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 输入区域 */}
          <div className="flex items-start gap-3 px-4 pt-4 pb-2">
            <div className="flex-1 min-h-[54px] flex items-start">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                placeholder="请先选择智能体，或直接输入内容（支持粘贴图片）"
                className="w-full resize-none border-0 bg-transparent text-base text-gray-700 placeholder-gray-400 outline-none dark:text-gray-100 dark:placeholder-gray-500 leading-[26px]"
                style={{ overflowY: "hidden", minHeight: "26px", maxHeight: "200px" }}
                disabled={isStreaming}
              />
            </div>
          </div>

          {/* 操作区域 */}
          <div className="flex items-center justify-between gap-2 px-4 py-2">
            <div className="flex items-center gap-2 text-xs">
              {/* DeepSeek-R1 按钮 */}
              <button
                type="button"
                onClick={() => setDeepseekR1Enabled(!deepseekR1Enabled)}
                className={`px-2 py-1 rounded-md transition-colors ${
                  deepseekR1Enabled
                    ? "bg-cyan-100 dark:bg-cyan-900/50"
                    : "hover:bg-gray-100 dark:hover:bg-gray-700"
                }`}
                title="使用 DeepSeek-R1 模型"
              >
                <span
                  style={deepseekR1Enabled ? {
                    background: 'linear-gradient(112deg, #007EA7 1%, #1F3A56 100%)',
                    WebkitBackgroundClip: 'text',
                    WebkitTextFillColor: 'transparent',
                    backgroundClip: 'text',
                  } : { color: '#6B7280' }}
                >
                  DeepSeek-R1
                </span>
              </button>

              {/* 深度思考按钮 */}
              <button
                type="button"
                onClick={() => setDeepThinkEnabled(!deepThinkEnabled)}
                className={`flex items-center gap-1 px-2 py-1 rounded-md transition-colors ${
                  deepThinkEnabled
                    ? "bg-cyan-100 dark:bg-cyan-900/50"
                    : "hover:bg-gray-100 dark:hover:bg-gray-700"
                }`}
                title="开启深度思考模式"
              >
                <span
                  style={deepThinkEnabled ? {
                    background: 'linear-gradient(112deg, #007EA7 1%, #1F3A56 100%)',
                    WebkitBackgroundClip: 'text',
                    WebkitTextFillColor: 'transparent',
                    backgroundClip: 'text',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                  } : { color: '#6B7280', display: 'flex', alignItems: 'center', gap: '4px' }}
                >
                  <DeepThinkIcon />
                  深度思考
                </span>
              </button>
            </div>

            {/* 右侧按钮组 */}
            <div className="flex items-center gap-2">
              {/* 文件上传按钮 */}
              <label
                className="flex h-8 w-8 items-center justify-center rounded-full transition-all hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer"
                title="上传文件"
              >
                <img src={uploadIcon} alt="上传" className="w-[18px] h-[18px]" />
                <input
                  type="file"
                  className="hidden"
                  multiple
                  accept=".pdf,.doc,.docx,.txt,.jpg,.jpeg,.png,.xls,.xlsx,.md"
                  onChange={handleFileInputChange}
                />
              </label>

              {/* 发送/停止按钮 */}
              {isStreaming ? (
                <button
                  type="button"
                  onClick={onStopStreaming}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-600 text-white transition-all hover:bg-indigo-700"
                  title="停止生成"
                >
                  <i className="fa-solid fa-stop text-xs"></i>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-600 text-white transition-all hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-gray-300 disabled:opacity-50 dark:disabled:bg-gray-700"
                  title="发送消息 (Enter)"
                >
                  <i className="fa-solid fa-arrow-up text-xs"></i>
                </button>
              )}
            </div>
          </div>
        </div>

        {/* AI 免责声明 - 仅在输入框位于底部时显示 */}
        {messages.length > 0 && (
          <div className="pt-1 pb-0.5 text-center text-xs text-gray-400 dark:text-gray-500">
            内容由AI生成,请仔细甄别
          </div>
        )}
        </form>

        {/* 新会话时显示智能体卡片 - 相对于整个页面居中 */}
        {messages.length === 0 && (
          <div className="mt-6 max-h-[45vh]">
            <AgentGrid onAgentSelect={handleAgentSelect} />
          </div>
        )}
      </div>
    </motion.section>
  );
}
