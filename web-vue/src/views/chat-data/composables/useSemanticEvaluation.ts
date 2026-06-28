import { computed, onUnmounted, ref, type ComputedRef, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  draftAccuracyEvaluationCase,
  generateAccuracyEvaluationCases,
  getAccuracyEvaluationCases,
  getAccuracyEvaluationRunDetail,
  getAccuracyEvaluationRuns,
  runAccuracyEvaluationStream,
  saveAccuracyEvaluationCases,
  type AccuracyEvaluationCaseItem,
  type AccuracyEvaluationReport,
  type AccuracyEvaluationRunCaseHistoryItem,
  type AccuracyEvaluationRunHistoryItem,
} from '../api'
import type { SemanticConfigItem } from '../types'

export type EvaluationCaseRunStatus = 'idle' | 'queued' | 'running' | 'passed' | 'failed' | 'pending' | 'stopped'

export interface EvaluationResultDetail extends Record<string, unknown> {
  id?: string
  question?: string
  gold_sql?: string
  gold_dsl?: unknown
  expected_dsl?: unknown
  predicted_dsl?: unknown
  generated_sql?: string
  agent_tool_sql?: string
  error?: string
}

export interface EvaluationCaseRunRow extends AccuracyEvaluationCaseItem {
  selected: boolean
  runStatus: EvaluationCaseRunStatus
  runError: string
  runResult: EvaluationResultDetail | null
}

interface UseSemanticEvaluationOptions {
  datasourceId: Ref<string>
  activeConfig: ComputedRef<SemanticConfigItem | null>
  hasUnsavedSemanticConfig: ComputedRef<boolean>
}

export function useSemanticEvaluation(options: UseSemanticEvaluationOptions) {
  const evaluateDrawerTab = ref<'cases' | 'run' | 'history'>('cases')
  const evaluationRunning = ref(false)
  const evaluationCasesLoading = ref(false)
  const evaluationCasesSaving = ref(false)
  const evaluationCases = ref<AccuracyEvaluationCaseItem[]>([])
  const evaluationBusinessDomain = ref('')
  const evaluationSceneReady = ref(false)
  const evaluationCasesDirty = ref(false)
  const showCaseEditorDialog = ref(false)
  const draftingCase = ref(false)
  const editingCaseIndex = ref(-1)
  const editingCase = ref<AccuracyEvaluationCaseItem>({
    id: '',
    nl: '',
    gold_dsl: {},
    gold_sql: '',
    query_type: 'aggregation',
    difficulty: 'easy',
    source: 'human',
    business_domain: '',
  })
  const editingCaseDslText = ref('{}')
  const editingCaseDslError = ref('')
  const evaluationReport = ref<AccuracyEvaluationReport | null>(null)
  const evaluationForm = ref({
    limit: 10,
    skip_execution: true,
    local_compile: true,
    include_traces: false,
    request_timeout: 180,
  })
  const evaluationResultFilter = ref<'all' | 'passed' | 'failed'>('all')
  const selectedEvaluationResultId = ref('')
  const evaluationRunRows = ref<EvaluationCaseRunRow[]>([])
  const evaluationRunProgress = ref({ current: 0, total: 0 })
  const evaluationLiveSummary = ref<AccuracyEvaluationReport['summary'] | null>(null)
  const evaluationHistoryLoading = ref(false)
  const evaluationHistoryDetailLoading = ref(false)
  const evaluationHistoryRuns = ref<AccuracyEvaluationRunHistoryItem[]>([])
  const evaluationHistoryTotal = ref(0)
  const evaluationHistoryPage = ref(1)
  const evaluationHistoryPageSize = ref(10)
  const selectedEvaluationHistoryRunId = ref('')
  const evaluationHistoryCases = ref<AccuracyEvaluationRunCaseHistoryItem[]>([])
  const selectedEvaluationHistoryCaseId = ref('')
  let evaluationRunAbortController: AbortController | null = null

  const currentConfig = () => options.activeConfig.value
  const currentDatasourceId = () => options.datasourceId.value

  const evaluationMetricTips = {
    semantic: '按指标、维度、筛选、排序、分页等语义槽位逐项比较，反映模型是否理解了问题。',
    structure: '生成的语义结构与标准结构是否完全一致；标准集未要求排序时，不因模型多生成排序扣分。',
    sql: '生成 SQL 与标准 SQL 标准化后是否完全一致，主要用于排查最终 SQL 是否偏离。',
  }
  const evaluationMetricTipByLabel: Record<string, string> = {
    语义得分: evaluationMetricTips.semantic,
    结构匹配: evaluationMetricTips.structure,
    SQL匹配: evaluationMetricTips.sql,
  }

  const evaluationCaseMap = computed(() => {
    const map = new Map<string, AccuracyEvaluationCaseItem>()
    for (const item of evaluationCases.value) map.set(item.id, item)
    return map
  })

  const evaluationResultRows = computed<EvaluationResultDetail[]>(() => {
    const rows = evaluationReport.value?.results || []
    return rows.map(row => {
      const id = String(row.id || '')
      const gold = evaluationCaseMap.value.get(id)
      return {
        ...row,
        gold_sql: gold?.gold_sql || '',
        gold_dsl: gold?.gold_dsl || row.expected_dsl,
      }
    })
  })

  const filteredEvaluationResultRows = computed(() => {
    return evaluationResultRows.value.filter(row => {
      if (evaluationResultFilter.value === 'passed') return isEvaluationResultPassed(row)
      if (evaluationResultFilter.value === 'failed') return !isEvaluationResultPassed(row)
      return true
    })
  })

  const selectedEvaluationResult = computed<EvaluationResultDetail | null>(() => {
    const runRow = evaluationRunRows.value.find(row => row.id === selectedEvaluationResultId.value)
    if (runRow?.runResult) {
      return {
        ...runRow.runResult,
        id: runRow.id,
        question: runRow.nl,
        gold_sql: runRow.gold_sql,
        gold_dsl: runRow.gold_dsl,
        expected_dsl: runRow.runResult.expected_dsl || runRow.gold_dsl,
      }
    }
    if (!selectedEvaluationResultId.value) return filteredEvaluationResultRows.value[0] || null
    return evaluationResultRows.value.find(row => String(row.id) === selectedEvaluationResultId.value) || null
  })

  const evaluationResultStats = computed(() => {
    const rows = evaluationResultRows.value
    const passed = rows.filter(row => isEvaluationResultPassed(row)).length
    return { total: rows.length, passed, failed: rows.length - passed }
  })

  const evaluationActiveSummary = computed(() => evaluationLiveSummary.value || evaluationReport.value?.summary || null)
  const evaluationSummaryCards = computed(() => {
    const summary = evaluationActiveSummary.value
    if (!summary) return []
    const debug = summary.debug_metrics || {}
    const passed = evaluationRunRows.value.filter(row => row.selected && row.runStatus === 'passed').length
    const failed = evaluationRunRows.value.filter(row => row.selected && ['failed', 'pending', 'stopped'].includes(row.runStatus)).length
    return [
      { label: '总用例', value: summary.total, tone: 'default' },
      { label: '通过', value: passed || evaluationResultStats.value.passed, tone: 'success' },
      { label: '未通过', value: failed || evaluationResultStats.value.failed, tone: 'danger' },
      { label: '语义得分', value: formatEvaluationPercent(summary.semantic_accuracy), tone: 'primary' },
      { label: '结构匹配', value: formatEvaluationPercent(debug.dsl_exact_match_rate), tone: 'default' },
      { label: 'SQL匹配', value: formatEvaluationPercent(debug.sql_exact_match_rate), tone: 'default' },
    ]
  })

  const evaluationSelectedRows = computed(() => evaluationRunRows.value.filter(row => row.selected))
  const evaluationSelectedCount = computed(() => evaluationSelectedRows.value.length)
  const evaluationAllSelected = computed({
    get: () => evaluationRunRows.value.length > 0 && evaluationRunRows.value.every(row => row.selected),
    set: (value: boolean) => {
      evaluationRunRows.value = evaluationRunRows.value.map(row => ({ ...row, selected: value }))
    },
  })
  const evaluationRunRowsFiltered = computed(() => {
    if (evaluationResultFilter.value === 'all') return evaluationRunRows.value
    return evaluationRunRows.value.filter(row => {
      if (row.runStatus === 'idle' || row.runStatus === 'queued' || row.runStatus === 'running') return evaluationResultFilter.value === 'failed'
      const passed = row.runStatus === 'passed'
      return evaluationResultFilter.value === 'passed' ? passed : !passed
    })
  })
  const evaluationCaseStats = computed(() => {
    const total = evaluationCases.value.length
    const human = evaluationCases.value.filter(item => item.source === 'human').length
    const llm = evaluationCases.value.filter(item => item.source === 'llm').length
    return { total, human, llm }
  })
  const selectedEvaluationHistoryRun = computed(() => evaluationHistoryRuns.value.find(item => item.run_id === selectedEvaluationHistoryRunId.value) || null)
  const selectedEvaluationHistoryCase = computed<EvaluationResultDetail | null>(() => {
    if (!selectedEvaluationHistoryCaseId.value) return (evaluationHistoryCases.value[0]?.result as EvaluationResultDetail) || null
    return (evaluationHistoryCases.value.find(item => item.case_id === selectedEvaluationHistoryCaseId.value)?.result as EvaluationResultDetail) || null
  })

  const evaluationHistoryTesterName = (row: AccuracyEvaluationRunHistoryItem) => {
    return String(row.tester_name || row.create_by_name || row.update_by_name || '—')
  }

  const openEvaluationDrawer = async () => {
    if (!currentConfig()) return
    evaluationReport.value = null
    evaluationCasesDirty.value = false
    await loadEvaluationCases()
    evaluateDrawerTab.value = evaluationSceneReady.value ? 'run' : 'cases'
    if (options.hasUnsavedSemanticConfig.value) {
      ElMessage.warning('当前指标集有未保存修改，请保存后再运行评测')
    }
  }

  const formatEvaluationPercent = (value: unknown) => {
    if (typeof value !== 'number') return '-'
    return `${(value * 100).toFixed(1)}%`
  }

  const formatEvaluationBooleanRate = (value: unknown) => {
    if (typeof value !== 'boolean') return '-'
    return value ? '100.0%' : '0.0%'
  }

  const formatEvaluationTime = (value: unknown) => {
    if (!value) return '-'
    return String(value)
  }

  const buildDefaultEvaluationDsl = (): Record<string, unknown> => ({
    metrics: [],
    dimensions: [],
    filters: [],
    having: [],
    order_by: [],
    limit: null,
    offset: null,
  })

  const withDefaultPaginationFields = (value: Record<string, unknown> | null | undefined): Record<string, unknown> => {
    const payload = value || {}
    return {
      ...payload,
      limit: Object.prototype.hasOwnProperty.call(payload, 'limit') ? payload.limit : null,
      offset: Object.prototype.hasOwnProperty.call(payload, 'offset') ? payload.offset : null,
    }
  }

  const formatDslText = (value: Record<string, unknown>) => JSON.stringify(withDefaultPaginationFields(value), null, 2)

  const validateEvaluationDsl = (dsl: unknown): string => {
    if (!dsl || typeof dsl !== 'object' || Array.isArray(dsl)) return '标准语义结构必须是 JSON 对象'
    const payload = dsl as Record<string, unknown>
    if (Array.isArray(payload.limit)) return 'limit 必须是返回条数整数；LIMIT 5,3 请写成 {"limit": 3, "offset": 5}'
    if (Array.isArray(payload.offset)) return 'offset 必须是跳过条数整数'
    if (payload.limit !== undefined && payload.limit !== null && (!Number.isInteger(payload.limit) || Number(payload.limit) <= 0)) {
      return 'limit 必须是正整数或 null'
    }
    if (payload.offset !== undefined && payload.offset !== null && (!Number.isInteger(payload.offset) || Number(payload.offset) < 0)) {
      return 'offset 必须是非负整数或 null'
    }
    if (payload.offset !== undefined && payload.offset !== null && (payload.limit === undefined || payload.limit === null)) {
      return 'offset 必须配合 limit 使用'
    }
    return ''
  }

  const normalizeEditorDsl = (dsl: unknown): Record<string, unknown> | null => {
    if (!dsl || typeof dsl !== 'object' || Array.isArray(dsl)) return null
    return withDefaultPaginationFields(dsl as Record<string, unknown>)
  }

  const normalizeEvaluationCase = (item: any, businessDomain: string): AccuracyEvaluationCaseItem => ({
    ...item,
    gold_dsl: withDefaultPaginationFields(item?.gold_dsl || {}),
    source: item.source === 'model_generated' ? 'llm' : item.source,
    business_domain: businessDomain,
  })

  const buildDefaultCase = (): AccuracyEvaluationCaseItem => ({
    id: `${currentConfig()?.id || 'scene'}_${Date.now()}`,
    nl: '',
    gold_dsl: buildDefaultEvaluationDsl(),
    gold_sql: '',
    query_type: 'aggregation',
    difficulty: 'easy',
    source: 'human',
    business_domain: currentConfig()?.name || 'scene',
  })

  const loadEvaluationCases = async () => {
    const config = currentConfig()
    const datasourceId = currentDatasourceId()
    if (!config || !datasourceId) return
    evaluationCasesLoading.value = true
    try {
      const res = await getAccuracyEvaluationCases(datasourceId, config.id)
      if (res.status !== 'success') {
        ElMessage.error(res.msg || '加载测试集失败')
        return
      }
      const businessDomain = res.data.business_domain || config.name
      evaluationCases.value = (res.data.cases || []).map((c: any) => normalizeEvaluationCase(c, businessDomain))
      evaluationBusinessDomain.value = businessDomain
      evaluationSceneReady.value = evaluationCases.value.length > 0
      evaluationCasesDirty.value = false
      syncEvaluationRunList()
    } catch (err) {
      console.error('加载测试集失败:', err)
      ElMessage.error('加载测试集失败')
    } finally {
      evaluationCasesLoading.value = false
    }
  }

  const generateEvaluationCasesDraft = async () => {
    const config = currentConfig()
    const datasourceId = currentDatasourceId()
    if (!config || !datasourceId) return

    if (evaluationCases.value && evaluationCases.value.length > 0) {
      try {
        await ElMessageBox.confirm(
          '当前指标集已有测试集，继续生成将覆盖已有测试集，是否继续？',
          '确认生成',
          {
            confirmButtonText: '继续',
            cancelButtonText: '取消',
            type: 'warning',
            customClass: 'chatdata-message-box',
            modalClass: 'chatdata-message-box-overlay',
          }
        )
      } catch {
        return
      }
    }

    evaluationCasesLoading.value = true
    try {
      const res = await generateAccuracyEvaluationCases(datasourceId, config.id)
      if (res.status !== 'success') {
        ElMessage.error(res.msg || '生成测试集失败')
        return
      }
      const businessDomain = res.data.business_domain || config.name
      evaluationCases.value = (res.data.cases || []).map((c: any) => normalizeEvaluationCase(c, businessDomain))
      evaluationBusinessDomain.value = businessDomain
      evaluationSceneReady.value = evaluationCases.value.length > 0
      evaluationCasesDirty.value = evaluationCases.value.length > 0
      syncEvaluationRunList()
      evaluateDrawerTab.value = 'cases'
      ElMessage.success('已根据当前指标集生成测试集草稿，请校对后保存')
    } catch (err) {
      console.error('生成测试集失败:', err)
      ElMessage.error('生成测试集失败')
    } finally {
      evaluationCasesLoading.value = false
    }
  }

  const saveEvaluationCases = async () => {
    const config = currentConfig()
    const datasourceId = currentDatasourceId()
    if (!config || !datasourceId) return
    if (!evaluationCasesDirty.value) {
      ElMessage.info('测试集没有未保存修改')
      return
    }
    evaluationCasesSaving.value = true
    try {
      const res = await saveAccuracyEvaluationCases(datasourceId, config.id, {
        business_domain: evaluationBusinessDomain.value || config.name,
        cases: evaluationCases.value,
      })
      if (res.status !== 'success') {
        ElMessage.error(res.msg || '保存测试集失败')
        return
      }
      const businessDomain = res.data.business_domain || config.name
      evaluationCases.value = (res.data.cases || []).map((c: any) => normalizeEvaluationCase(c, businessDomain))
      evaluationBusinessDomain.value = businessDomain
      evaluationSceneReady.value = evaluationCases.value.length > 0
      evaluationCasesDirty.value = false
      syncEvaluationRunList()
      ElMessage.success('测试集已保存，可以开始评测')
      evaluateDrawerTab.value = 'run'
    } catch (err) {
      console.error('保存测试集失败:', err)
      const message = err instanceof Error && err.message && err.message !== 'error' ? err.message : '保存测试集失败'
      ElMessage.error(message)
    } finally {
      evaluationCasesSaving.value = false
    }
  }

  const openNewCaseEditor = () => {
    editingCaseIndex.value = -1
    editingCase.value = buildDefaultCase()
    editingCaseDslText.value = formatDslText(editingCase.value.gold_dsl)
    editingCaseDslError.value = ''
    showCaseEditorDialog.value = true
  }

  const draftEditingCaseByLlm = async () => {
    const config = currentConfig()
    const datasourceId = currentDatasourceId()
    if (!config || !datasourceId) return
    if (!editingCase.value.nl.trim()) {
      ElMessage.warning('请先输入自然语言问题或 SQL')
      return
    }
    draftingCase.value = true
    try {
      const res = await draftAccuracyEvaluationCase(datasourceId, config.id, {
        hint: editingCase.value.nl,
        existing_questions: evaluationCases.value.map(item => item.nl).filter(Boolean),
      })
      if (res.status !== 'success' || !res.data?.case) {
        ElMessage.error(res.msg || '生成用例初稿失败')
        return
      }
      const nextCase = normalizeEvaluationCase(res.data.case, config.name)
      editingCase.value = nextCase
      editingCaseDslText.value = formatDslText(nextCase.gold_dsl)
      editingCaseDslError.value = ''
      if (!evaluationBusinessDomain.value) evaluationBusinessDomain.value = nextCase.business_domain
      ElMessage.success('已生成标准语义结构初稿')
    } catch (err) {
      console.error('生成标准语义结构初稿失败:', err)
      const message = err instanceof Error && err.message && err.message !== 'error' ? err.message : '生成标准语义结构初稿失败'
      ElMessage.error(message)
    } finally {
      draftingCase.value = false
    }
  }

  const openEditCaseEditor = (index: number) => {
    const currentCase = evaluationCases.value[index]
    if (!currentCase) return
    editingCaseIndex.value = index
    editingCase.value = {
      ...currentCase,
      gold_dsl: { ...(currentCase.gold_dsl || {}) },
      business_domain: currentConfig()?.name || 'scene',
    }
    editingCaseDslText.value = formatDslText(editingCase.value.gold_dsl)
    editingCaseDslError.value = ''
    showCaseEditorDialog.value = true
  }

  const removeEvaluationCase = (index: number) => {
    if (!confirm('确定删除这个评测用例吗？')) return
    evaluationCases.value.splice(index, 1)
    evaluationCasesDirty.value = true
  }

  const saveCaseEditor = () => {
    try {
      editingCaseDslError.value = ''
      const parsedDsl = JSON.parse(editingCaseDslText.value || '{}')
      if (!editingCase.value.id.trim() || !editingCase.value.nl.trim()) {
        ElMessage.warning('用例 ID 和问题不能为空')
        return
      }
      const dslError = validateEvaluationDsl(parsedDsl)
      if (dslError) {
        editingCaseDslError.value = dslError
        return
      }
      const normalizedDsl = normalizeEditorDsl(parsedDsl)
      if (!normalizedDsl) {
        editingCaseDslError.value = '标准语义结构必须是 JSON 对象'
        return
      }
      const nextCase: AccuracyEvaluationCaseItem = {
        ...editingCase.value,
        gold_dsl: normalizedDsl,
        business_domain: editingCase.value.business_domain || evaluationBusinessDomain.value || currentConfig()?.name || 'scene',
      }
      if (editingCaseIndex.value >= 0) evaluationCases.value.splice(editingCaseIndex.value, 1, nextCase)
      else evaluationCases.value.unshift(nextCase)
      if (!evaluationBusinessDomain.value) evaluationBusinessDomain.value = nextCase.business_domain
      evaluationCasesDirty.value = true
      showCaseEditorDialog.value = false
    } catch (_err) {
      const message = '标准语义结构不是合法 JSON；LIMIT 5,3 请写成 {"limit": 3, "offset": 5}'
      editingCaseDslError.value = message
      ElMessage.error(message)
    }
  }

  const isEvaluationResultPassed = (row: Record<string, unknown>) => {
    if (row.status === 'failed') return false
    const semantic = typeof row.semantic_accuracy === 'number' ? row.semantic_accuracy : 0
    return semantic >= 1 && row.dsl_exact_match !== false && row.compile_success !== false
  }

  const syncEvaluationRunList = () => {
    const prev = new Map(evaluationRunRows.value.map(row => [row.id, row]))
    evaluationRunRows.value = evaluationCases.value.map(item => {
      const old = prev.get(item.id)
      return {
        ...item,
        selected: old?.selected ?? true,
        runStatus: old?.runStatus ?? 'idle',
        runError: old?.runError ?? '',
        runResult: old?.runResult ?? null,
      }
    })
  }

  const findEvaluationRunRow = (caseId: string) => evaluationRunRows.value.find(row => row.id === caseId)

  const applyEvaluationCaseResult = (caseId: string, result: EvaluationResultDetail) => {
    const row = findEvaluationRunRow(caseId)
    if (!row) return
    row.runResult = result
    row.runError = String(result.error || result.reason || '')
    if (result.status === 'pending') {
      row.runStatus = 'pending'
      return
    }
    if (result.status === 'failed' || row.runError) {
      row.runStatus = 'failed'
      return
    }
    row.runStatus = isEvaluationResultPassed(result) ? 'passed' : 'failed'
  }

  const failPendingEvaluationRows = (caseIds: string[], message: string) => {
    const pendingStatuses: EvaluationCaseRunStatus[] = ['queued', 'running']
    for (const caseId of caseIds) {
      const row = findEvaluationRunRow(caseId)
      if (!row || !pendingStatuses.includes(row.runStatus)) continue
      row.runStatus = 'failed'
      row.runError = message
      row.runResult = {
        status: 'failed',
        error: message,
      }
    }
  }

  const stopPendingEvaluationRows = () => {
    for (const row of evaluationRunRows.value) {
      if (row.runStatus !== 'queued' && row.runStatus !== 'running') continue
      row.runStatus = 'stopped'
      row.runError = '评测已停止'
      row.runResult = null
    }
  }

  const stopEvaluationRun = () => {
    stopPendingEvaluationRows()
    evaluationRunAbortController?.abort()
    evaluationRunAbortController = null
    evaluationRunning.value = false
  }

  const runActiveEvaluation = async () => {
    const config = currentConfig()
    const datasourceId = currentDatasourceId()
    if (!config || !datasourceId) return
    if (!evaluationSceneReady.value || evaluationCases.value.length === 0) {
      ElMessage.warning('请先在「测试集」页根据当前指标集生成草稿并保存')
      evaluateDrawerTab.value = 'cases'
      return
    }
    if (evaluationCasesDirty.value) {
      ElMessage.warning('测试集有未保存修改，请先保存后再评测')
      evaluateDrawerTab.value = 'cases'
      return
    }
    if (options.hasUnsavedSemanticConfig.value) {
      ElMessage.warning('当前指标集有未保存修改，请先保存后再评测')
      return
    }
    const selectedIds = evaluationSelectedRows.value.map(row => row.id)
    if (selectedIds.length === 0) {
      ElMessage.warning('请至少勾选一个用例')
      return
    }
    evaluateDrawerTab.value = 'run'
    evaluationRunning.value = true
    evaluationLiveSummary.value = null
    evaluationReport.value = null
    evaluationRunProgress.value = { current: 0, total: selectedIds.length }
    evaluationRunAbortController?.abort()
    const runController = new AbortController()
    evaluationRunAbortController = runController
    for (const row of evaluationRunRows.value) {
      if (!row.selected) {
        row.runStatus = 'idle'
        row.runResult = null
        row.runError = ''
        continue
      }
      row.runStatus = 'queued'
      row.runResult = null
      row.runError = ''
    }
    try {
      await runAccuracyEvaluationStream(
        {
          ...evaluationForm.value,
          datasource_id: datasourceId,
          semantic_config_id: config.id,
          agent_id: 'chat_data',
          query_mode: 'query',
          case_ids: selectedIds,
          limit: selectedIds.length,
        },
        {
          onStart: (payload) => {
            evaluationRunProgress.value = { current: 0, total: payload.total }
          },
          onCaseStart: (payload) => {
            const row = findEvaluationRunRow(payload.id)
            if (row) row.runStatus = 'running'
          },
          onCaseResult: (payload) => {
            applyEvaluationCaseResult(payload.id, payload.result as EvaluationResultDetail)
            evaluationRunProgress.value = { current: payload.index, total: payload.total }
            if (!selectedEvaluationResultId.value) selectedEvaluationResultId.value = payload.id
          },
          onProgress: (payload) => {
            evaluationLiveSummary.value = payload.summary
            evaluationRunProgress.value = { current: payload.index, total: payload.total }
          },
          onDone: (payload) => {
            evaluationReport.value = payload.report
            evaluationLiveSummary.value = payload.report.summary
            const firstFailed = evaluationRunRows.value.find(row => row.selected && row.runStatus === 'failed')
            selectedEvaluationResultId.value = firstFailed?.id || selectedIds[0] || ''
            ElMessage.success('评测完成')
          },
          onError: (message) => {
            const errorMessage = message || '评测失败'
            failPendingEvaluationRows(selectedIds, errorMessage)
            ElMessage.error(errorMessage)
          },
        },
        runController.signal,
      )
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        ElMessage.info('已停止评测')
        return
      }
      console.error('运行评测失败:', err)
      const message = err instanceof Error && err.message && err.message !== 'error' ? err.message : '评测失败，请查看后端日志'
      failPendingEvaluationRows(selectedIds, message)
      ElMessage.error(message)
    } finally {
      if (evaluationRunAbortController === runController) {
        evaluationRunning.value = false
        evaluationRunAbortController = null
      }
    }
  }

  const evaluationRunStatusLabel = (status: EvaluationCaseRunStatus) => {
    if (status === 'running') return '运行中'
    if (status === 'queued') return '排队中'
    if (status === 'passed') return '通过'
    if (status === 'failed') return '未通过'
    if (status === 'pending') return '待评分'
    if (status === 'stopped') return '已停止'
    return '待运行'
  }

  const evaluationRunStatusClass = (status: EvaluationCaseRunStatus) => {
    if (status === 'running' || status === 'queued') return 'text-blue-600 bg-blue-50'
    if (status === 'passed') return 'text-emerald-700 bg-emerald-50'
    if (status === 'failed') return 'text-red-600 bg-red-50'
    if (status === 'pending') return 'text-amber-700 bg-amber-50'
    if (status === 'stopped') return 'text-gray-600 bg-gray-100'
    return 'text-gray-500 bg-gray-100'
  }

  const openEvaluationRunDetail = (row: EvaluationCaseRunRow) => {
    selectedEvaluationResultId.value = row.id
  }

  const loadEvaluationHistoryRuns = async () => {
    const config = currentConfig()
    const datasourceId = currentDatasourceId()
    if (!config || !datasourceId) return
    evaluationHistoryLoading.value = true
    try {
      const res = await getAccuracyEvaluationRuns({
        datasource_id: datasourceId,
        semantic_config_id: config.id,
        page: evaluationHistoryPage.value,
        page_size: evaluationHistoryPageSize.value,
      })
      if (res.status !== 'success') {
        ElMessage.error(res.msg || '获取评测历史失败')
        return
      }
      evaluationHistoryRuns.value = res.data?.runs || []
      evaluationHistoryTotal.value = Number(res.data?.total || 0)
      const firstRun = evaluationHistoryRuns.value[0]
      if (!selectedEvaluationHistoryRunId.value && firstRun) {
        await loadEvaluationHistoryDetail(firstRun.run_id)
      }
    } catch (err) {
      console.error('获取评测历史失败:', err)
      ElMessage.error('获取评测历史失败')
    } finally {
      evaluationHistoryLoading.value = false
    }
  }

  const loadEvaluationHistoryDetail = async (runId: string) => {
    if (!runId) return
    selectedEvaluationHistoryRunId.value = runId
    evaluationHistoryDetailLoading.value = true
    try {
      const res = await getAccuracyEvaluationRunDetail(runId)
      if (res.status !== 'success') {
        ElMessage.error(res.msg || '获取评测历史详情失败')
        return
      }
      evaluationHistoryCases.value = res.data?.cases || []
      selectedEvaluationHistoryCaseId.value = evaluationHistoryCases.value[0]?.case_id || ''
    } catch (err) {
      console.error('获取评测历史详情失败:', err)
      ElMessage.error('获取评测历史详情失败')
    } finally {
      evaluationHistoryDetailLoading.value = false
    }
  }

  const handleEvaluationHistoryPageChange = async (page: number) => {
    evaluationHistoryPage.value = page
    selectedEvaluationHistoryRunId.value = ''
    selectedEvaluationHistoryCaseId.value = ''
    evaluationHistoryCases.value = []
    await loadEvaluationHistoryRuns()
  }

  const handleEvaluationTabChange = (tab: 'cases' | 'run' | 'history') => {
    if (tab === 'history') {
      selectedEvaluationHistoryRunId.value = ''
      selectedEvaluationHistoryCaseId.value = ''
      evaluationHistoryCases.value = []
      void loadEvaluationHistoryRuns()
    }
  }

  const formatJsonPreview = (value: unknown) => {
    if (value === null || value === undefined) return '—'
    try {
      return JSON.stringify(value, null, 2)
    } catch (_err) {
      return String(value)
    }
  }

  const evaluationSlotScoreItems = (row: EvaluationResultDetail | null) => {
    const slots = row?.slot_scores
    if (!slots || typeof slots !== 'object') return []
    const labels: Record<string, string> = {
      metrics: '指标',
      dimensions: '维度',
      filters: '筛选条件',
      having: '指标条件',
      order_by: '排序',
      limit: '返回条数',
      offset: '跳过条数',
      visualization: '展示方式',
    }
    return Object.entries(slots as Record<string, number>).map(([key, score]) => ({ key: labels[key] || key, score }))
  }

  const evaluationToolTraceItems = (row: EvaluationResultDetail | null) => {
    const traces = row?.tool_traces
    return Array.isArray(traces) ? traces as Array<Record<string, unknown>> : []
  }

  const evaluationHistoryCaseResult = (row: AccuracyEvaluationRunCaseHistoryItem): EvaluationResultDetail => (row.result as EvaluationResultDetail) || {}

  onUnmounted(() => {
    evaluationRunAbortController?.abort()
    evaluationRunAbortController = null
  })

  return {
    draftingCase,
    editingCase,
    editingCaseDslError,
    editingCaseDslText,
    evaluateDrawerTab,
    evaluationActiveSummary,
    evaluationAllSelected,
    evaluationBusinessDomain,
    evaluationCases,
    evaluationCasesDirty,
    evaluationCasesLoading,
    evaluationCasesSaving,
    evaluationCaseStats,
    evaluationForm,
    evaluationHistoryCaseResult,
    evaluationHistoryCases,
    evaluationHistoryDetailLoading,
    evaluationHistoryLoading,
    evaluationHistoryPage,
    evaluationHistoryPageSize,
    evaluationHistoryRuns,
    evaluationHistoryTesterName,
    evaluationHistoryTotal,
    evaluationMetricTipByLabel,
    evaluationMetricTips,
    evaluationResultFilter,
    evaluationRunProgress,
    evaluationRunning,
    evaluationRunRows,
    evaluationRunRowsFiltered,
    evaluationRunStatusClass,
    evaluationRunStatusLabel,
    evaluationSelectedCount,
    evaluationSlotScoreItems,
    evaluationSummaryCards,
    evaluationToolTraceItems,
    formatEvaluationBooleanRate,
    formatEvaluationPercent,
    formatEvaluationTime,
    formatJsonPreview,
    generateEvaluationCasesDraft,
    handleEvaluationHistoryPageChange,
    handleEvaluationTabChange,
    loadEvaluationCases,
    loadEvaluationHistoryDetail,
    loadEvaluationHistoryRuns,
    openEditCaseEditor,
    openEvaluationDrawer,
    openEvaluationRunDetail,
    openNewCaseEditor,
    removeEvaluationCase,
    runActiveEvaluation,
    saveCaseEditor,
    saveEvaluationCases,
    selectedEvaluationHistoryCase,
    selectedEvaluationHistoryCaseId,
    selectedEvaluationHistoryRun,
    selectedEvaluationHistoryRunId,
    selectedEvaluationResult,
    selectedEvaluationResultId,
    showCaseEditorDialog,
    stopEvaluationRun,
    draftEditingCaseByLlm,
  }
}
