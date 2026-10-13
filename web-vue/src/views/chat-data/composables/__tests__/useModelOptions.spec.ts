import { beforeEach, describe, expect, it, vi } from 'vitest'

// getChatDataConfig 提升到 mock 工厂外以便各用例注入返回值
const getChatDataConfig = vi.fn()
vi.mock('../../api', () => ({
  getChatDataConfig: (...args: unknown[]) => getChatDataConfig(...args),
}))

// useModelOptions 的状态提升在模块级：每个用例 resetModules + 动态 import 隔离缓存
const loadComposable = () => import('../useModelOptions')

describe('useModelOptions', () => {
  beforeEach(() => {
    vi.resetModules()
    getChatDataConfig.mockReset()
  })

  it('首次加载拉取 /config，默认模型取 is_default 项', async () => {
    getChatDataConfig.mockResolvedValue({
      status: 'success',
      data: { models: [{ id: 'm-sql' }, { id: 'm-dsl', is_default: true }] },
    })
    const { useModelOptions } = await loadComposable()
    const ctx = useModelOptions()

    await ctx.load()

    expect(getChatDataConfig).toHaveBeenCalledTimes(1)
    expect(ctx.modelOptions.value.map(item => item.id)).toEqual(['m-sql', 'm-dsl'])
    expect(ctx.defaultModelId.value).toBe('m-dsl')
  })

  it('已有缓存时非 force 调用直接复用，不再请求 /config', async () => {
    getChatDataConfig.mockResolvedValue({
      status: 'success',
      data: { models: [{ id: 'm1', is_default: true }] },
    })
    const { useModelOptions } = await loadComposable()
    const ctx = useModelOptions()

    await ctx.load()
    await ctx.load()

    expect(getChatDataConfig).toHaveBeenCalledTimes(1)
  })

  it('force=true 绕过缓存重拉注册表，新建会话可感知后端配置变更', async () => {
    getChatDataConfig
      .mockResolvedValueOnce({
        status: 'success',
        data: { models: [{ id: 'old-model', is_default: true }] },
      })
      .mockResolvedValueOnce({
        status: 'success',
        data: { models: [{ id: 'new-model', is_default: true }] },
      })
    const { useModelOptions } = await loadComposable()
    const ctx = useModelOptions()

    await ctx.load()
    expect(ctx.defaultModelId.value).toBe('old-model')

    await ctx.load(true)

    expect(getChatDataConfig).toHaveBeenCalledTimes(2)
    expect(ctx.modelOptions.value.map(item => item.id)).toEqual(['new-model'])
    expect(ctx.defaultModelId.value).toBe('new-model')
  })

  it('并发调用共享 inflight，只发一次请求', async () => {
    getChatDataConfig.mockImplementation(() => new Promise(resolve => {
      setTimeout(() => resolve({ status: 'success', data: { models: [{ id: 'm1' }] } }), 10)
    }))
    const { useModelOptions } = await loadComposable()
    const ctx = useModelOptions()

    await Promise.all([ctx.load(), ctx.load(), ctx.load()])

    expect(getChatDataConfig).toHaveBeenCalledTimes(1)
  })

  it('接口失败时保持空列表且清空 inflight，下次调用可重试', async () => {
    getChatDataConfig
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({ status: 'success', data: { models: [{ id: 'm1', is_default: true }] } })
    const { useModelOptions } = await loadComposable()
    const ctx = useModelOptions()

    await ctx.load()
    expect(ctx.modelOptions.value).toEqual([])
    expect(ctx.defaultModelId.value).toBe('')

    await ctx.load()
    expect(ctx.defaultModelId.value).toBe('m1')
  })
})
