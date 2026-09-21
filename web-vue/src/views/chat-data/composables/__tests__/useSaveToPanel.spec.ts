import { describe, expect, it } from 'vitest'
import { ref } from 'vue'

import { useSaveToPanel } from '../useSaveToPanel'

describe('useSaveToPanel', () => {
  it('默认打开弹窗并带入 SQL、图表类型与单个执行对象', () => {
    const panel = useSaveToPanel()
    panel.handleSaveToDashboard('select 1', 'bar', 'm-1', ['m-1'])
    expect(panel.showSaveModal.value).toBe(true)
    expect(panel.saveSql.value).toBe('select 1')
    expect(panel.saveChartType.value).toBe('bar')
    expect(panel.saveMemberDatasourceId.value).toBe('m-1')
    expect(panel.saveMemberDatasourceIds.value).toEqual(['m-1'])
  })

  it('执行对象多于一个时整体带入', () => {
    const panel = useSaveToPanel()
    panel.handleSaveToDashboard('select 1', 'table', 'm-1', ['m-1', 'm-2', 'm-3'])
    expect(panel.saveMemberDatasourceIds.value).toEqual(['m-1', 'm-2', 'm-3'])
  })

  it('执行对象只有一个或为空时回退为单个 memberDatasourceId', () => {
    const panel = useSaveToPanel()
    panel.handleSaveToDashboard('select 1', 'table', 'm-1', [])
    expect(panel.saveMemberDatasourceIds.value).toEqual(['m-1'])
    panel.showSaveModal.value = false
    panel.handleSaveToDashboard('select 1', 'table', 'm-2', [' '])
    expect(panel.saveMemberDatasourceIds.value).toEqual(['m-2'])
    panel.showSaveModal.value = false
    panel.handleSaveToDashboard('select 1', 'table', '  ', [])
    expect(panel.saveMemberDatasourceIds.value).toEqual([])
    expect(panel.saveMemberDatasourceId.value).toBe('')
  })

  it('chartType 为空时使用 getFallbackChartType 兜底', () => {
    const panel = useSaveToPanel({ getFallbackChartType: () => 'line' })
    panel.handleSaveToDashboard('select 1', '', 'm-1')
    expect(panel.saveChartType.value).toBe('line')
  })

  it('会话入口口径：chartType 为空时回退 table', () => {
    const panel = useSaveToPanel({ getFallbackChartType: () => 'table' })
    panel.handleSaveToDashboard('select 1', '', 'm-1')
    expect(panel.saveChartType.value).toBe('table')
  })

  it('未配置兜底时保留空值（SQL 编辑器/工具卡片口径）', () => {
    const panel = useSaveToPanel()
    panel.handleSaveToDashboard('select 1', '', 'm-1')
    expect(panel.saveChartType.value).toBe('')
  })

  it('memberDatasourceId 为空时使用 getFallbackMemberDatasourceId 兜底（辅助查询当前页签口径）', () => {
    const fallbackId = ref('tab-member-1')
    const panel = useSaveToPanel({
      getFallbackMemberDatasourceId: () => fallbackId.value,
    })
    panel.handleSaveToDashboard('select 1', 'table')
    expect(panel.saveMemberDatasourceId.value).toBe('tab-member-1')
    expect(panel.saveMemberDatasourceIds.value).toEqual(['tab-member-1'])
  })

  it('memberDatasourceId 存在时不触发兜底取值', () => {
    let fallbackCalled = false
    const panel = useSaveToPanel({
      getFallbackMemberDatasourceId: () => {
        fallbackCalled = true
        return 'ignored'
      },
    })
    panel.handleSaveToDashboard('select 1', 'table', 'm-1')
    expect(panel.saveMemberDatasourceId.value).toBe('m-1')
    expect(fallbackCalled).toBe(false)
  })
})
