import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import AsyncQueryResult from '../AsyncQueryResult.vue'

// 透传默认 slot 的 el-dialog stub（详情弹窗内容在默认 slot 里）
const ElDialogSlotStub = { template: '<div><slot /></div>' }

const mountResult = (resultData: Record<string, unknown>, props: Record<string, unknown> = {}) => mount(AsyncQueryResult, {
  props: {
    resultData: JSON.stringify(resultData),
    allowFullscreen: false,
    ...props,
  },
  global: {
    stubs: {
      'el-dialog': ElDialogSlotStub,
    },
  },
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('AsyncQueryResult', () => {
  it('renders SQL errors inline and expands the complete ANSI-free detail on demand', async () => {
    const fullError = '\u001b[4mInvalid expression\u001b[0m near LIMIT1\nPlease use LIMIT 100'
    const wrapper = mount(AsyncQueryResult, {
      props: {
        resultData: JSON.stringify({
          status: 'failed',
          sql: 'select * from orders limit1 100',
          error: fullError,
          columns: [],
          data: [],
          total_count: 0,
        }),
      },
      global: {
        stubs: {
          'el-dialog': true,
        },
      },
    })

    expect(wrapper.text()).toContain('SQL 执行错误，请查看详情')
    expect(wrapper.text()).not.toContain('Invalid expression near LIMIT1')
    expect(wrapper.text()).not.toContain('Please use LIMIT 100')
    expect(wrapper.text()).not.toContain('\u001b[4m')
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.text()).not.toContain('成功')
    expect(wrapper.text()).not.toContain('共 0 条')
    expect(wrapper.get('[data-testid="toggle-query-error"]').attributes('aria-expanded')).toBe('false')

    await wrapper.get('[data-testid="toggle-query-error"]').trigger('click')

    expect(wrapper.text()).toContain('Please use LIMIT 100')
    expect(wrapper.text()).not.toContain('\u001b[4m')
    expect(wrapper.get('[data-testid="toggle-query-error"]').attributes('aria-expanded')).toBe('true')
  })

  it('失败结果展示错误来源标签', async () => {
    const wrapper = mountResult({
      status: 'failed',
      error: 'TExecuteStatementResp(...)',
      error_source: 'remote_http',
      columns: [],
      data: [],
      total_count: 0,
    }, {
      global: { stubs: { 'el-dialog': true } },
    })

    expect(wrapper.text()).toContain('SQL 执行错误，请查看详情')
    expect(wrapper.text()).toContain('外部接口')
  })

  it('DSL 模式徽标可点击，弹窗展示 DSL 与编译 SQL', async () => {
    const wrapper = mountResult({
      status: 'succeeded',
      query_mode: 'dsl',
      sql: "select 商品名称 from 商品 where 商品名称 like '%剃须%'",
      columns: ['商品名称'],
      data: [{ 商品名称: '飞利浦剃须刀' }],
      total_count: 1,
    }, {
      semanticParams: {
        metrics: [],
        dimensions: ['商品名称'],
        filters: [{ dimension: '商品名称', operator: 'LIKE', value: '%剃须%' }],
      },
    })

    const badge = wrapper.get('[data-testid="query-mode-badge"]')
    expect(badge.text()).toBe('DSL')
    await badge.trigger('click')

    expect(wrapper.text()).toContain('DSL（语义层查询条件）')
    expect(wrapper.text()).toContain('SQL（由 DSL 编译生成）')
    expect(wrapper.text()).toContain('SELECT')
    expect(wrapper.text()).toContain('%剃须%')
    expect(wrapper.find('[data-testid="copy-query-dsl"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="copy-query-sql"]').exists()).toBe(true)
  })

  it('SQL 模式弹窗只展示 SQL，不展示 DSL 区块', async () => {
    const wrapper = mountResult({
      status: 'succeeded',
      query_mode: 'sql',
      sql: 'select count(*) from orders',
      columns: ['count(*)'],
      data: [{ 'count(*)': 5 }],
      total_count: 1,
    })

    await wrapper.get('[data-testid="query-mode-badge"]').trigger('click')

    expect(wrapper.text()).toContain('SQL')
    expect(wrapper.text()).not.toContain('DSL（语义层查询条件）')
    expect(wrapper.text()).not.toContain('SQL（由 DSL 编译生成）')
    expect(wrapper.find('[data-testid="copy-query-dsl"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="copy-query-sql"]').exists()).toBe(true)
  })

  it('无 SQL 且无 DSL 内容时徽标保持纯展示不可点击', () => {
    const wrapper = mountResult({
      status: 'failed',
      query_mode: 'dsl',
      error: '编译失败',
      columns: [],
      data: [],
      total_count: 0,
    })

    expect(wrapper.text()).toContain('DSL')
    expect(wrapper.find('[data-testid="query-mode-badge"]').exists()).toBe(false)
  })

  it('复制按钮写入剪贴板并提示成功', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    const wrapper = mountResult({
      status: 'succeeded',
      query_mode: 'sql',
      sql: 'select 1',
      columns: ['1'],
      data: [{ '1': 1 }],
      total_count: 1,
    })

    await wrapper.get('[data-testid="query-mode-badge"]').trigger('click')
    await wrapper.get('[data-testid="copy-query-sql"]').trigger('click')
    await Promise.resolve()

    expect(writeText).toHaveBeenCalledTimes(1)
    expect(String(writeText.mock.calls[0]?.[0])).toContain('SELECT')
  })
})
