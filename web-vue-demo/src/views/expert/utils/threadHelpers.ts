/**
 * 线程管理辅助函数
 * 迁移自: web/src/pages/GeneralAssistant/utils/threadHelpers.ts
 *
 * 使用锁机制防止并发创建多个线程
 */

/**
 * 线程创建锁，防止并发创建多个线程
 */
let threadCreationPromise: Promise<string | null> | null = null;

/**
 * 确保线程 ID 存在，如果不存在则创建新线程
 * 使用锁机制防止并发创建
 *
 * @param currentThreadId - 当前线程 ID
 * @param createNewThread - 创建新线程的异步函数
 * @param context - 上下文描述（用于日志）
 * @returns 线程 ID
 */
export async function ensureThreadId(
  currentThreadId: string | null,
  createNewThread: () => Promise<string | null>,
  context: string
): Promise<string | null> {
  // 如果已有线程 ID，直接返回
  if (currentThreadId) return currentThreadId;

  // 如果正在创建线程，等待完成
  if (threadCreationPromise) {
    console.log(`[${context}] 检测到线程正在创建，等待完成...`);
    return await threadCreationPromise;
  }

  console.log(`[${context}] 未找到线程ID，创建新线程...`);
  threadCreationPromise = createNewThread();

  try {
    const newThreadId = await threadCreationPromise;
    if (!newThreadId) {
      console.error(`[${context}] 创建线程失败`);
    }
    return newThreadId;
  } finally {
    // 创建完成后清除锁
    threadCreationPromise = null;
  }
}
