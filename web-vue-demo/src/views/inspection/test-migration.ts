/**
 * Inspection 模块迁移测试文件
 * 用于验证步骤一至步骤四的完成状态
 */

// ========== 步骤一测试：类型定义 ==========
import type {
  // 基础类型
  LawItem,
  HistoricalCase,
  EvidenceItem,
  ViolationAnalysis,
  AnalysisResult,

  // 消息类型
  ChatMessage,
  ChatRole,
  ContentBlock,
  TextContentBlock,
  FileContentBlock,
  NonStandardContentBlock,

  // 任务状态
  AnalysisTask,
  TaskStatus,
  SSETaskStatus,

  // Human-in-the-Loop
  HumanReviewData,
  ResumeInput,
  ReviewAction,

  // 历史记录
  AnalysisHistoryItem,
  AnalysisStage,

  // 工具类型
  ToolType,
  ReportSubType,
  ViolationFileGroup,
} from './types'

// 类型测试：确保所有类型都可以正确导出
export type TypeTest = {
  lawItem: LawItem
  case_: HistoricalCase
  evidence: EvidenceItem
  violation: ViolationAnalysis
  result: AnalysisResult
  message: ChatMessage
  task: AnalysisTask
  reviewData: HumanReviewData
  historyItem: AnalysisHistoryItem
  fileGroup: ViolationFileGroup
}

// ========== 步骤二测试：API 和 SSE ==========
import {
  // API 函数
  createConversation,
  getConversationMessages,
  getConversationState,
  getConversationFiles,
  uploadFile,
  deleteFileGroup,
  deleteFile,
  completionStream,
  resumeStream,
  generateTitle,
  getDownloadUrl,
} from './api/inspection'

import {
  // SSE 相关
  useSSEStream,
  processSSEStream,
} from './composables/useSSEStream'

import type {
  SSEEventHandlers,
  SSEStreamOptions,
} from './composables/useSSEStream'

// API 导出测试
export const apiTest = {
  createConversation,
  getConversationMessages,
  processSSEStream,
}

// ========== 步骤三测试：useViolationAnalysis ==========
import {
  useViolationAnalysis,
} from './composables/useViolationAnalysis'

import type {
  UseViolationAnalysisOptions,
  UseViolationAnalysisReturn,
} from './composables/useViolationAnalysis'

// Composable 导出测试
export const composableTest = {
  useViolationAnalysis,
}

// ========== 步骤四测试：EmbeddedLayout ==========
import EmbeddedLayout from './EmbeddedLayout.vue'

// 组件导出测试
export const componentTest = {
  EmbeddedLayout,
}

// ========== 测试总结 ==========
export const migrationStatus = {
  step1: '✅ 类型定义已迁移 (types/)',
  step2: '✅ API 和 SSE 已迁移 (api/ + useSSEStream)',
  step3: '✅ useViolationAnalysis 已迁移 (composables/)',
  step4: '✅ EmbeddedLayout 已迁移 (EmbeddedLayout.vue)',
}

export default migrationStatus
