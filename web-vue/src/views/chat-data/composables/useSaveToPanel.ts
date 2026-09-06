import { ref } from 'vue'

/**
 * 「保存到看板」弹窗的共享状态与打开逻辑
 * 统一智能问数会话、辅助查询、SQL 编辑器、工具调用卡片各入口的弹窗参数组装口径；
 * 各入口的差异（图表类型/执行对象兜底取值）通过可选的取值函数注入。
 * 弹窗本体为 SaveToPanelModal，实际保存 API 调用收敛在该组件内部。
 */
export interface UseSaveToPanelOptions {
  /** chartType 为空时的兜底取值（如辅助查询回退当前图表类型、会话回退 'table'）；不传则保留空值 */
  getFallbackChartType?: () => string
  /** memberDatasourceId 为空时的兜底取值（如辅助查询回退当前结果页签的执行对象） */
  getFallbackMemberDatasourceId?: () => string | null | undefined
}

export const useSaveToPanel = (options: UseSaveToPanelOptions = {}) => {
  const showSaveModal = ref(false)
  const saveSql = ref('')
  const saveChartType = ref('table')
  const saveMemberDatasourceId = ref('')
  const saveMemberDatasourceIds = ref<string[]>([])

  /** 打开保存到看板弹窗；参数口径与 QueryResultPanel/ConversationMessageList 的 onSave 回调保持一致 */
  const handleSaveToDashboard = (
    sql: string,
    chartType: string,
    memberDatasourceId?: string,
    memberDatasourceIds?: string[],
  ) => {
    saveSql.value = sql
    saveChartType.value = chartType || options.getFallbackChartType?.() || ''
    saveMemberDatasourceId.value = String(memberDatasourceId || options.getFallbackMemberDatasourceId?.() || '').trim()
    const executedMemberIds = (memberDatasourceIds || [])
      .map(item => String(item || '').trim())
      .filter(Boolean)
    // 多个执行对象时整体带入，否则回退为单个执行对象（与既有各入口口径一致）
    saveMemberDatasourceIds.value = executedMemberIds.length > 1
      ? executedMemberIds
      : (saveMemberDatasourceId.value ? [saveMemberDatasourceId.value] : [])
    showSaveModal.value = true
  }

  return {
    showSaveModal,
    saveSql,
    saveChartType,
    saveMemberDatasourceId,
    saveMemberDatasourceIds,
    handleSaveToDashboard,
  }
}
