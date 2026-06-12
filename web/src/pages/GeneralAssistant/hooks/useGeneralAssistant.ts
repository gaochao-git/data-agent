/**
 * 通用助手 Hook
 * 负责管理对话状态、历史记录和 API 调用
 */

import { useState, useRef, useCallback, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { API_BASE } from "../../../lib/config";
import { useSSEStream } from "../../Inspection/hooks/useSSEStream";
import { ensureThreadId } from "../utils/threadHelpers";
import type { ChatMessage, ConversationHistoryItem, UploadedFile } from "../types";

export function useGeneralAssistant() {
  const navigate = useNavigate();
  const { conversationId: conversationIdFromUrl } = useParams<{ conversationId?: string }>();
  const { startStream, stopStream } = useSSEStream();

  // 对话状态
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [isChatStreaming, setIsChatStreaming] = useState(false);
  const [activeAssistantMessageId, setActiveAssistantMessageId] = useState<string | null>(null);

  // 历史对话状态
  const [conversationHistory, setConversationHistory] = useState<ConversationHistoryItem[]>([]);
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);
  const [isHistoryCollapsed, setIsHistoryCollapsed] = useState(true);
  const [isHistoryLoading, setIsHistoryLoading] = useState(true);  // 初始为 true，首次加载时显示
  const [authError, setAuthError] = useState<string | null>(null);

  // 标记是否已完成首次加载（首次加载显示 loading，后续刷新静默更新）
  const isFirstLoadRef = useRef(true);

  // 文件上传状态
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);

  // 当前线程 ID - 初始化时从 URL 读取
  const [chatThreadId, setChatThreadId] = useState<string | null>(() => {
    return conversationIdFromUrl || null;
  });

  // 历史刷新触发器
  const [historyRefreshTrigger, setHistoryRefreshTrigger] = useState(0);

  // Refs
  const chatContainerRef = useRef<HTMLDivElement>(null);

  // 跟踪已生成标题的线程 ID，确保每个线程只生成一次标题
  const generatedTitlesRef = useRef<Set<string>>(new Set());

  // 监听 URL 参数变化，更新 threadId 并加载消息
  useEffect(() => {
    if (conversationIdFromUrl && conversationIdFromUrl !== chatThreadId) {
      setChatThreadId(conversationIdFromUrl);
      setActiveHistoryId(conversationIdFromUrl);
    } else if (!conversationIdFromUrl && chatThreadId) {
      // URL 中没有 threadId 时，清除当前 threadId
      setChatThreadId(null);
      setActiveHistoryId(null);
    }
  }, [conversationIdFromUrl, chatThreadId]);

  // 根据 file_ids 批量获取文件信息
  const fetchFilesByIds = async (fileIds: string[]): Promise<UploadedFile[]> => {
    const files: UploadedFile[] = [];
    for (const fid of fileIds) {
      try {
        const res = await fetch(`${API_BASE}/api/uploaded-files/${fid}`, {
          credentials: "include",
        });
        if (res.ok) {
          const data = await res.json();
          files.push({
            file_id: data.file_id,
            original_filename: data.original_filename,
            file_type: data.file_type,
            file_size: data.file_size,
            status: data.status,
          });
        }
      } catch (e) {
        console.warn(`获取文件信息失败: ${fid}`, e);
      }
    }
    return files;
  };

  // 页面加载时，如果 URL 中有 threadId 且消息为空，则加载历史消息
  useEffect(() => {
    const loadMessagesFromUrl = async () => {
      if (conversationIdFromUrl && chatMessages.length === 0) {
        try {
          const response = await fetch(`${API_BASE}/api/conversations/${conversationIdFromUrl}`, {
            credentials: "include",
          });

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }

          const data = await response.json();
          const messages = data.data?.messages || [];

          // 格式化消息，根据 file_ids 获取文件详情
          const formattedMessages: ChatMessage[] = await Promise.all(
            messages.map(async (msg: any, index: number) => {
              const message: ChatMessage = {
                id: `history-${conversationIdFromUrl}-${index}`,
                role: msg.role,
                content: msg.content,
                timestamp: new Date(),
              };

              // 如果有 file_ids，获取文件详情
              if (msg.file_ids && msg.file_ids.length > 0) {
                message.attachments = await fetchFilesByIds(msg.file_ids);
              }

              return message;
            })
          );

          setChatMessages(formattedMessages);
        } catch (error) {
          console.error("加载历史消息失败:", error);
        }
      }
    };

    loadMessagesFromUrl();
  }, [conversationIdFromUrl]); // 只在 conversationIdFromUrl 变化时执行

  /**
   * 生成并更新会话标题
   */
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
      const title = data.data?.title;

      if (title) {
        console.log("[通用助手] 标题已生成:", title);
        // 触发历史记录刷新
        setHistoryRefreshTrigger((prev) => prev + 1);
      }
    } catch (error) {
      console.warn("[通用助手] 标题生成失败:", error);
      // 失败时从 Set 中移除，允许重试
      generatedTitlesRef.current.delete(threadId);
    }
  }, []);

  // Agent ID 常量
  const AGENT_ID = "general";

  /**
   * 上传文件
   */
  const uploadFile = useCallback(
    async (file: File, conversationId: string): Promise<UploadedFile | null> => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("thread_id", conversationId);

      try {
        const response = await fetch(`${API_BASE}/api/uploaded-files/upload`, {
          method: "POST",
          body: formData,
          credentials: "include",
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        return {
          file_id: data.file_id,
          original_filename: data.original_filename,
          file_type: data.file_type,
          file_size: data.file_size,
          status: data.status,
        };
      } catch (error) {
        console.error("文件上传失败:", error);
        toast.error("文件上传失败");
        return null;
      }
    },
    []
  );

  /**
   * 轮询文件解析状态
   */
  const pollFileStatus = useCallback(
    async (fileId: string): Promise<string> => {
      const maxAttempts = 30; // 最多轮询30次
      const interval = 1000; // 每秒轮询一次

      for (let i = 0; i < maxAttempts; i++) {
        try {
          const response = await fetch(`${API_BASE}/api/uploaded-files/${fileId}`, {
            credentials: "include",
          });

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }

          const data = await response.json();
          const status = data.status;

          if (status === "completed" || status === "failed") {
            return status;
          }

          // 更新本地状态
          setUploadedFiles((prev) =>
            prev.map((f) => (f.file_id === fileId ? { ...f, status } : f))
          );

          // 等待后继续轮询
          await new Promise((resolve) => setTimeout(resolve, interval));
        } catch (error) {
          console.error("轮询文件状态失败:", error);
          return "failed";
        }
      }

      return "timeout";
    },
    []
  );

  /**
   * 创建新的对话线程
   */
  const createNewThread = useCallback(async (): Promise<string | null> => {
    try {
      const response = await fetch(`${API_BASE}/api/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_id: AGENT_ID,
        }),
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();

      // 检查认证错误
      if (data.code === 40101) {
        toast.error("请先登录");
        return null;
      }

      const threadId = data.data?.id;

      if (threadId) {
        setChatThreadId(threadId);
        navigate(`/general/${threadId}`, { replace: true });
        return threadId;
      }

      return null;
    } catch (error) {
      console.error("创建对话线程失败:", error);
      toast.error("创建会话失败");
      return null;
    }
  }, [navigate]);

  /**
   * 处理文件上传
   */
  const handleFileUpload = useCallback(
    async (files: FileList) => {
      // 先把 FileList 转成数组，避免后续 navigate 导致 FileList 失效
      const fileArray = Array.from(files);
      console.log("[文件上传] 开始, 文件数量:", fileArray.length, "chatThreadId:", chatThreadId);

      // 确保有线程 ID
      let currentThreadId = chatThreadId;
      if (!currentThreadId) {
        console.log("[文件上传] 没有 threadId，创建新会话...");
        currentThreadId = await createNewThread();
        console.log("[文件上传] 创建结果:", currentThreadId);
        if (!currentThreadId) {
          toast.error("无法创建会话");
          return;
        }
      }

      console.log("[文件上传] 使用 threadId:", currentThreadId);

      for (const file of fileArray) {
        console.log("[文件上传] 处理文件:", file.name);
        // 先添加到本地状态，显示"上传中"
        const tempId = `temp-${Date.now()}-${Math.random()}`;
        const tempFile: UploadedFile = {
          file_id: tempId,
          original_filename: file.name,
          file_type: file.name.split(".").pop()?.toLowerCase() || "",
          file_size: file.size,
          status: "uploading",
        };
        setUploadedFiles((prev) => [...prev, tempFile]);

        // 上传文件
        console.log("[文件上传] 调用 uploadFile:", file.name, currentThreadId);
        const result = await uploadFile(file, currentThreadId);
        console.log("[文件上传] uploadFile 结果:", result);
        if (result) {
          // 更新为真实的文件信息，确保状态是 "parsing"
          const fileWithParsingStatus = { ...result, status: "parsing" };
          setUploadedFiles((prev) =>
            prev.map((f) => (f.file_id === tempId ? fileWithParsingStatus : f))
          );

          // 延迟 500ms 后开始轮询，让用户能看到 "解析中" 状态
          setTimeout(() => {
            pollFileStatus(result.file_id).then((finalStatus) => {
              setUploadedFiles((prev) =>
                prev.map((f) =>
                  f.file_id === result.file_id ? { ...f, status: finalStatus } : f
                )
              );
            });
          }, 500);
        } else {
          // 上传失败，移除临时文件
          setUploadedFiles((prev) => prev.filter((f) => f.file_id !== tempId));
        }
      }
    },
    [chatThreadId, createNewThread, uploadFile, pollFileStatus]
  );

  /**
   * 移除已上传的文件
   */
  const removeUploadedFile = useCallback(
    async (fileId: string) => {
      try {
        await fetch(`${API_BASE}/api/uploaded-files/${fileId}`, {
          method: "DELETE",
          credentials: "include",
        });
      } catch (error) {
        console.error("删除文件失败:", error);
      }

      setUploadedFiles((prev) => prev.filter((f) => f.file_id !== fileId));
    },
    []
  );

  /**
   * 清空已上传的文件
   */
  const clearUploadedFiles = useCallback(() => {
    setUploadedFiles([]);
  }, []);

  /**
   * 发送聊天消息
   */
  const sendChatMessage = useCallback(
    async (message: string) => {
      if (!message.trim()) return;

      // 确保有线程 ID（使用锁机制防止并发创建）
      const currentThreadId = await ensureThreadId(chatThreadId, createNewThread, "通用助手对话");
      if (!currentThreadId) {
        toast.error("无法创建会话");
        return;
      }

      // 更新本地状态
      if (chatThreadId !== currentThreadId) {
        setChatThreadId(currentThreadId);
      }

      // 添加用户消息
      const userMessageId = `user-${Date.now()}`;
      const userMessage: ChatMessage = {
        id: userMessageId,
        role: "user",
        content: message,
        timestamp: new Date(),
        attachments: uploadedFiles.length > 0 ? [...uploadedFiles] : undefined,
      };
      setChatMessages((prev) => [...prev, userMessage]);

      // 发送后清空文件列表
      setUploadedFiles([]);

      // 创建 AI 消息占位符
      const assistantMessageId = `assistant-${Date.now()}`;
      const assistantMessage: ChatMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        timestamp: new Date(),
      };
      setChatMessages((prev) => [...prev, assistantMessage]);
      setActiveAssistantMessageId(assistantMessageId);

      // 开始流式输出
      setIsChatStreaming(true);

      // 提取当前消息关联的文件 ID
      const currentFileIds = uploadedFiles.length > 0
        ? uploadedFiles.map(f => f.file_id)
        : undefined;

      try {
        await startStream({
          url: `${API_BASE}/api/conversations/${currentThreadId}/completion`,
          method: "POST",
          body: {
            query: message,
            agent_id: AGENT_ID,
            file_ids: currentFileIds,
          },
          handlers: {
            onStart: () => {
              console.log("[通用助手] 流开始");
            },
            onMessage: (content: string) => {
              // 累积文本内容
              setChatMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantMessageId
                    ? { ...msg, content: (msg.content as string) + content }
                    : msg
                )
              );
            },
            onCompleted: (data) => {
              console.log("[通用助手] 流完成", data);
              // 先刷新历史（显示新会话）
              setHistoryRefreshTrigger((prev) => prev + 1);
              // 异步生成标题（完成后会再次触发刷新）
              void generateAndUpdateTitle(currentThreadId);
            },
            onError: (error) => {
              console.error("[通用助手] 流错误:", error);
              toast.error(`发送失败: ${error}`);
            },
            onEnd: () => {
              setIsChatStreaming(false);
              setActiveAssistantMessageId(null);
            },
          },
        });
      } catch (error) {
        console.error("[通用助手] 发送消息失败:", error);
        toast.error("发送失败，请稍后重试");
        setIsChatStreaming(false);
        setActiveAssistantMessageId(null);
      }
    },
    [chatThreadId, createNewThread, startStream, generateAndUpdateTitle, uploadedFiles]
  );

  /**
   * 停止流式输出
   */
  const stopChatStreaming = useCallback(async () => {
    stopStream();
    setIsChatStreaming(false);
    setActiveAssistantMessageId(null);
  }, [stopStream]);

  /**
   * 加载历史对话列表（所有智能体的会话）
   * 首次加载显示 loading，后续刷新静默更新（避免页面抖动）
   */
  const loadConversationHistory = useCallback(async () => {
    // 只有首次加载时显示 loading
    if (isFirstLoadRef.current) {
      setIsHistoryLoading(true);
    }
    try {
      // 不传 agent_id，获取所有会话
      const response = await fetch(`${API_BASE}/api/conversations`, {
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();

      // 检查认证错误
      if (data.code === 40101) {
        setAuthError("请先登录后再使用");
        setConversationHistory([]);
        return;
      }

      // 清除之前的错误
      setAuthError(null);

      const conversations = data.data || [];

      const formattedHistory: ConversationHistoryItem[] = conversations.map((conv: any) => {
        // 确保 timestamp 是有效的 Date 对象
        const updateTime = conv.update_time ? new Date(conv.update_time) : null;
        const createTime = conv.create_time ? new Date(conv.create_time) : null;
        const timestamp = updateTime || createTime || new Date();

        return {
          id: conv.id,
          title: conv.title || "新会话",
          timestamp,
          preview: conv.preview || "",
          is_favorited: conv.is_favorited || false,
          agent_id: conv.agent_id, // 保存 agent_id 用于跳转
          create_time: createTime || undefined,
          update_time: updateTime || undefined,
          message_count: conv.message_count || 0,
        };
      });

      // 按创建时间降序排序（最新创建的在前面）
      formattedHistory.sort((a, b) => {
        const timeA = a.create_time || a.timestamp;
        const timeB = b.create_time || b.timestamp;
        return timeB.getTime() - timeA.getTime();
      });

      setConversationHistory(formattedHistory);
    } catch (error) {
      console.error("加载历史对话失败:", error);
    } finally {
      // 首次加载完成后，标记为已完成并关闭 loading
      if (isFirstLoadRef.current) {
        isFirstLoadRef.current = false;
        setIsHistoryLoading(false);
      }
    }
  }, []);

  /**
   * 切换到历史对话
   */
  const switchToThread = useCallback(
    async (threadId: string) => {
      try {
        setChatThreadId(threadId);
        setActiveHistoryId(threadId);

        const response = await fetch(`${API_BASE}/api/conversations/${threadId}`, {
          credentials: "include",
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        const messages = data.data?.messages || [];

        // 格式化消息，根据 file_ids 获取文件详情
        const formattedMessages: ChatMessage[] = await Promise.all(
          messages.map(async (msg: any, index: number) => {
            const message: ChatMessage = {
              id: `history-${threadId}-${index}`,
              role: msg.role,
              content: msg.content,
              timestamp: new Date(),
            };

            // 如果有 file_ids，获取文件详情
            if (msg.file_ids && msg.file_ids.length > 0) {
              message.attachments = await fetchFilesByIds(msg.file_ids);
            }

            return message;
          })
        );

        setChatMessages(formattedMessages);

        // 更新 URL
        navigate(`/general/${threadId}`, { replace: true });
      } catch (error) {
        console.error("加载历史对话失败:", error);
        toast.error("加载对话失败");
      }
    },
    [navigate]
  );

  /**
   * 清空聊天状态（不导航）
   */
  const clearChatState = useCallback(() => {
    setChatMessages([]);
    setChatInput("");
    setChatThreadId(null);
    setActiveHistoryId(null);
    setIsChatStreaming(false);
    setActiveAssistantMessageId(null);
    setUploadedFiles([]);
  }, []);

  /**
   * 重置对话（新建会话）- 清空状态并导航到 /general
   */
  const resetChatHistory = useCallback(() => {
    clearChatState();
    navigate("/general", { replace: true });
  }, [clearChatState, navigate]);

  return {
    // 状态
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
    uploadedFiles,
    authError,

    // Refs
    chatContainerRef,

    // 函数
    sendChatMessage,
    stopChatStreaming,
    loadConversationHistory,
    switchToThread,
    resetChatHistory,
    clearChatState,
    createNewThread,
    handleFileUpload,
    removeUploadedFile,
    clearUploadedFiles,
  };
}
