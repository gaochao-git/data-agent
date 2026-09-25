/**
 * QueryBuilder 的 SQL 编译 / 预览 / 执行 / 取消链路。
 * 自 index.vue 抽出，代码保持原实现；组件上下文以 deps 显式注入。
 */
import { onUnmounted, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { compileSemanticBuilderSql, cancelQueryTask, getChatDataConfig } from '../../../api'
import { isQuerySucceeded } from '../../../utils'
import { isAbortError } from '../../../utils/abort'
import {
  getApiErrorDetail,
  normalizeBuilderErrorMessage,
} from '../utils/builderExpressions'

export interface QueryBuilderResultTab {
  title: string
  rawResultData: string
  executedSql: string
  memberDatasourceId?: string
  memberName?: string
  status?: 'success' | 'error' | 'none'
}

export interface UseQueryBuilderExecutionDeps {
  datasourceId: () => string
  semanticConfigId: () => string
  submitSqlTask: ReturnType<typeof import('../../../composables/useQueryTask').useQueryTask>['submitSqlTask']
  isBuilderConfigured: ComputedRef<boolean>
  builderConfigSnapshot: ComputedRef<string>
  executionTargets: ComputedRef<Array<{ member_datasource_id?: string }>>
  selectedMemberDatasourceIds: ComputedRef<string[]>
  validateBuilderFilters: () => boolean
  validateQueryPagination: () => boolean
  validateAggregationModeBeforeRun: () => boolean
  ensureSemanticConfigSelected: () => boolean
  snapshotSemanticBuilderConfig: () => Record<string, unknown>
  buildBuilderConfigSnapshot: () => string
  activateResultTab: (index: number) => void
  cancelResultAnalysis: () => void
}

export function useQueryBuilderExecution(deps: UseQueryBuilderExecutionDeps) {
  const executing = ref(false)
  const executingTaskText = ref('')
  const executingTaskId = ref('')
  const cancellingTask = ref(false)
  const rawResultData = ref<string | null>(null)
  const executedSql = ref<string>('')
  const compiledSql = ref('')
  const compiledSqlSnapshot = ref('')
  const skipQueryCache = ref(false)
  const queryCacheEnabled = ref(false)
  const quickQueryRefresh = ref(0)
  const resultTabs = ref<QueryBuilderResultTab[]>([])
  const activeResultTabIndex = ref(0)
  const showSqlPreview = ref(false)
  const previewSql = ref('')
  const previewSqlLoading = ref(false)

  // 当前查询任务轮询的取消通道:组件卸载时中止,避免销毁后仍继续轮询/写状态
  let queryTaskAbortController: AbortController | null = null

  const compileCurrentBuilderSql = async (
    semanticBuilderConfig: Record<string, unknown> = deps.snapshotSemanticBuilderConfig(),
    snapshot: string = deps.buildBuilderConfigSnapshot(),
    datasourceId: string = deps.datasourceId(),
    semanticConfigId: string = deps.semanticConfigId(),
    force = false,
  ): Promise<string> => {
    if (!deps.validateBuilderFilters()) {
      return ''
    }
    if (!deps.validateQueryPagination()) {
      return ''
    }
    const compileSnapshot = JSON.stringify({ datasourceId, semanticConfigId, snapshot })
    if (!force && compiledSql.value && compiledSqlSnapshot.value === compileSnapshot) {
      return compiledSql.value
    }
    if (!deps.ensureSemanticConfigSelected()) {
      return ''
    }
    const response = await compileSemanticBuilderSql({
      datasource_id: datasourceId,
      semantic_config_id: semanticConfigId,
      semantic_builder_config: semanticBuilderConfig,
    })
    const sqlText = String(response.data?.sql || '').trim()
    compiledSql.value = sqlText
    compiledSqlSnapshot.value = compileSnapshot
    return sqlText
  }

  const handlePreviewSql = async () => {
    if (!deps.validateAggregationModeBeforeRun()) {
      return
    }
    if (!deps.ensureSemanticConfigSelected()) {
      return
    }
    if (!deps.isBuilderConfigured.value) {
      ElMessage.warning('请先配置查询条件')
      return
    }
    if (!deps.validateQueryPagination()) {
      return
    }
    if (showSqlPreview.value) {
      showSqlPreview.value = false
      return
    }
    previewSqlLoading.value = true
    try {
      previewSql.value = await compileCurrentBuilderSql()
      if (!previewSql.value) {
        return
      }
      showSqlPreview.value = true
    } catch (err) {
      const rawMessage = getApiErrorDetail(err) || (err instanceof Error ? err.message : '')
      const message = normalizeBuilderErrorMessage(rawMessage, 'SQL 编译失败')
      ElMessage.error(message)
    } finally {
      previewSqlLoading.value = false
    }
  }

  const loadRuntimeConfig = async () => {
    try {
      const response = await getChatDataConfig()
      queryCacheEnabled.value = !!response.data?.query_cache_enabled
      if (!queryCacheEnabled.value) {
        skipQueryCache.value = false
      }
    } catch (err) {
      console.warn('获取智能问数运行时配置失败:', err)
      queryCacheEnabled.value = false
      skipQueryCache.value = false
    }
  }

  const executeQuery = async () => {
    if (!deps.validateAggregationModeBeforeRun()) {
      return
    }
    if (!deps.ensureSemanticConfigSelected()) {
      return
    }
    if (!deps.isBuilderConfigured.value) {
      ElMessage.warning('请先配置查询条件')
      return
    }
    if (deps.executionTargets.value.length > 0 && deps.selectedMemberDatasourceIds.value.length === 0) {
      ElMessage.warning('请选择执行数据源')
      return
    }
    if (!deps.validateQueryPagination()) {
      return
    }
    if (!deps.validateBuilderFilters()) {
      return
    }
    executing.value = true
    executingTaskText.value = '任务创建中...'
    executingTaskId.value = ''
    rawResultData.value = null
    deps.cancelResultAnalysis()
    executedSql.value = ''
    resultTabs.value = []
    activeResultTabIndex.value = 0
    // 每次执行创建独立取消通道，供组件卸载时中止本次轮询
    const taskController = new AbortController()
    queryTaskAbortController = taskController
    try {
      const datasourceId = deps.datasourceId()
      const semanticConfigId = deps.semanticConfigId()
      const memberDatasourceIds = [...deps.selectedMemberDatasourceIds.value]
      const shouldSkipQueryCache = queryCacheEnabled.value ? skipQueryCache.value : false
      const semanticBuilderConfig = deps.snapshotSemanticBuilderConfig()

      const task = await deps.submitSqlTask({
        datasource_id: datasourceId,
        member_datasource_ids: memberDatasourceIds,
        query_type: 'builder',
        semantic_builder_config: semanticBuilderConfig,
        semantic_config_id: semanticConfigId,
        query_text: '可视化查询',
        skip_query_cache: shouldSkipQueryCache,
      }, {
        returnTerminalTask: true,
        signal: taskController.signal,
        onProgress: (taskStatus) => {
          executingTaskText.value = taskStatus.progress_text || '执行中...'
          executingTaskId.value = taskStatus.task_id
        }
      })
      const memberResults = task.results || []
      if (memberResults.length > 0) {
        resultTabs.value = memberResults.map((result, index) => {
          const memberLabel = result.member_name || result.member_datasource_id || `成员${index + 1}`
          const payload = isQuerySucceeded(result)
            ? {
                status: 'succeeded',
                sql: result.sql || '',
                columns: result.columns || [],
                data: result.data || [],
                total_count: result.total_count || 0,
                execution_time_ms: result.execution_time_ms,
                cache_hit: result.cache_hit,
                cache_age_seconds: result.cache_age_seconds,
                cache_expires_in_seconds: result.cache_expires_in_seconds,
                member_datasource_id: result.member_datasource_id,
                member_name: result.member_name,
              }
            : {
                status: result.status || 'failed',
                sql: result.sql || '',
                columns: result.columns || [],
                data: result.data || [],
                total_count: result.total_count || 0,
                execution_time_ms: result.execution_time_ms,
                error: result.error || '查询失败',
                member_datasource_id: result.member_datasource_id,
                member_name: result.member_name,
              }
          return {
            title: memberLabel,
            rawResultData: JSON.stringify(payload),
            executedSql: result.sql || '',
            memberDatasourceId: result.member_datasource_id,
            memberName: result.member_name,
            status: isQuerySucceeded(result) ? 'success' : 'error',
          }
        })
        deps.activateResultTab(0)
        // 刷新快捷查询
        quickQueryRefresh.value++
      } else {
        rawResultData.value = JSON.stringify({
          status: 'failed',
          error: task.error_message || '查询失败'
        })
        resultTabs.value = [{
          title: '结果1',
          rawResultData: rawResultData.value,
          executedSql: '',
          status: 'error',
        }]
      }
    } catch (err) {
      // 组件卸载触发的轮询取消不属于查询失败，不向结果区写错误，避免已销毁组件仍弹错误提示
      if (!isAbortError(err)) {
        const rawMessage = getApiErrorDetail(err) || (err instanceof Error ? err.message : '')
        const message = normalizeBuilderErrorMessage(rawMessage, '查询失败')
        rawResultData.value = JSON.stringify({
          status: 'failed',
          error: message
        })
        resultTabs.value = [{
          title: '结果1',
          rawResultData: rawResultData.value,
          executedSql: '',
          status: 'error',
        }]
      }
    } finally {
      executing.value = false
      executingTaskText.value = ''
      executingTaskId.value = ''
      cancellingTask.value = false
    }
  }

  const handleCancelExecutingTask = async () => {
    if (!executingTaskId.value || cancellingTask.value) return
    cancellingTask.value = true
    try {
      const response = await cancelQueryTask(executingTaskId.value)
      if (response.status !== 'success') {
        ElMessage.error(response.msg || '暂停失败')
        cancellingTask.value = false
        return
      }
      executingTaskText.value = '暂停请求已提交...'
      if (response.data?.status === 'partial_success' || response.data?.status === 'failed') {
        const firstMessage = response.data?.executions?.find(execution => execution.message)?.message
        ElMessage.warning(firstMessage || '部分 SQL 暂停失败')
      } else {
        ElMessage.success('已提交暂停请求')
      }
    } catch (err) {
      console.error('暂停可视化查询任务失败:', err)
      ElMessage.error('暂停失败，请稍后重试')
      cancellingTask.value = false
    }
  }

  // 配置发生变化时，若 SQL 预览已展开则自动折叠；再次点击时重新编译最新 SQL。
  watch(deps.builderConfigSnapshot, (nextSnapshot, prevSnapshot) => {
    if (nextSnapshot === prevSnapshot) return
    compiledSql.value = ''
    compiledSqlSnapshot.value = ''
    previewSql.value = ''
    if (!showSqlPreview.value) return
    showSqlPreview.value = false
  })

  // 卸载时中止进行中的查询任务轮询（AbortError 由调用方静默处理）
  onUnmounted(() => {
    queryTaskAbortController?.abort()
    queryTaskAbortController = null
  })

  return {
    executing,
    executingTaskText,
    executingTaskId,
    cancellingTask,
    rawResultData,
    executedSql,
    compiledSql,
    compiledSqlSnapshot,
    skipQueryCache,
    queryCacheEnabled,
    quickQueryRefresh,
    resultTabs,
    activeResultTabIndex,
    showSqlPreview,
    previewSql,
    previewSqlLoading,
    compileCurrentBuilderSql,
    handlePreviewSql,
    loadRuntimeConfig,
    executeQuery,
    handleCancelExecutingTask,
  }
}
