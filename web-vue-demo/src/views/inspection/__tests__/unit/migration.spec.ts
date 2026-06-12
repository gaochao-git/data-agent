/**
 * Inspection 模块迁移单元测试
 * 验证步骤一至步骤四的基本功能
 */

import { describe, it, expect, vi } from 'vitest'

// ========== 步骤一测试：类型定义 ==========
describe('步骤一：类型定义', () => {
  it('应该能够导入所有核心类型', async () => {
    const types = await import('../../types')

    // 验证核心类型存在
    expect(types).toBeDefined()
    expect(typeof types).toBe('object')
  })

  it('应该能够导入工具类型', async () => {
    const types = await import('../../types')

    // 验证工具类型
    expect(types).toBeDefined()
  })
})

// ========== 步骤二测试：API 和 SSE ==========
describe('步骤二：API 和 SSE', () => {
  it('应该能够导入 API 函数', async () => {
    const api = await import('../../api/inspection')

    // 验证核心 API 函数存在
    expect(api.createConversation).toBeDefined()
    expect(api.getConversationMessages).toBeDefined()
    expect(api.uploadFile).toBeDefined()
    expect(api.completionStream).toBeDefined()
  })

  it('应该能够导入 SSE 工具', async () => {
    const sse = await import('../../composables/useSSEStream')

    // 验证 SSE 工具存在
    expect(sse.useSSEStream).toBeDefined()
    expect(sse.processSSEStream).toBeDefined()
  })
})

// ========== 步骤三测试：useViolationAnalysis ==========
describe('步骤三：useViolationAnalysis', () => {
  it('应该能够导入 useViolationAnalysis', async () => {
    const { useViolationAnalysis } = await import('../../composables/useViolationAnalysis')

    // 验证 composable 存在
    expect(useViolationAnalysis).toBeDefined()
    expect(typeof useViolationAnalysis).toBe('function')
  })
})

// ========== 步骤四测试：EmbeddedLayout ==========
describe('步骤四：EmbeddedLayout', () => {
  it('应该能够导入 EmbeddedLayout 组件', async () => {
    // 由于 Vue 组件是默认导出，我们需要检查模块是否存在
    const layoutModule = await import('../../EmbeddedLayout.vue')

    // 验证组件模块存在
    expect(layoutModule).toBeDefined()
    expect(layoutModule.default).toBeDefined()
  })
})

// ========== 综合测试 ==========
describe('迁移综合测试', () => {
  it('所有步骤的导出都应该可用', async () => {
    // 并行导入所有模块
    const [types, api, sse, composables] = await Promise.all([
      import('../../types'),
      import('../../api/inspection'),
      import('../../composables/useSSEStream'),
      import('../../composables/useViolationAnalysis'),
    ])

    // 验证所有模块都已加载
    expect(types).toBeDefined()
    expect(api).toBeDefined()
    expect(sse).toBeDefined()
    expect(composables).toBeDefined()

    // 验证核心函数存在
    expect(api.createConversation).toBeDefined()
    expect(sse.processSSEStream).toBeDefined()
    expect(composables.useViolationAnalysis).toBeDefined()
  })
})
