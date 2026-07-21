import { computed, ref, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { isQuerySucceeded } from '../../../utils'
import type {
  AggregationField,
  AnalysisType,
  BuilderQueryResultPayload,
  DimensionField,
} from '../types'
import {
  applyPeriodCompareAnalysis,
  applyResultCompareAnalysis,
  applyResultPercentAnalysis,
  applyResultSortAnalysis,
  applyResultTopNAnalysis,
  applyResultTotalAnalysis,
  getDefaultAnalysisSortField,
  toNumericValue,
} from '../utils/resultAnalysis'

type ResultEnvelope = BuilderQueryResultPayload & { status?: string | null }

interface UseQueryBuilderResultAnalysisOptions {
  resultData: Ref<string | null>
  analyzedResultData: Ref<string | null>
  dimensions: Ref<DimensionField[]>
  aggregations: Ref<AggregationField[]>
  getDimensionDisplayName: (field: DimensionField) => string
  getMetricDisplayName: (field: AggregationField) => string
  getCompareAnalysisWarning: () => string | null
}

const parseResultEnvelope = (result: string | null): ResultEnvelope | null => {
  if (!result) return null
  try {
    return JSON.parse(result) as ResultEnvelope
  } catch {
    return null
  }
}

export const useQueryBuilderResultAnalysis = (options: UseQueryBuilderResultAnalysisOptions) => {
  const selectedAnalysisType = ref<AnalysisType>('sort')
  const appliedAnalysisType = ref<AnalysisType | null>(null)
  const compareType = ref<'mom' | 'yoy'>('mom')
  const resultSortField = ref('')
  const resultSortDirection = ref<'ASC' | 'DESC'>('DESC')
  const resultTopN = ref(10)
  const topNField = ref('')
  const topNDirection = ref<'ASC' | 'DESC'>('DESC')
  const percentMetricField = ref('')
  const percentScope = ref<'total' | 'group'>('total')
  const percentGroupField = ref('')
  const compareMetricField = ref('')
  const compareBaselineMode = ref<'first' | 'max' | 'min' | 'avg'>('first')
  const analyzedResultData = options.analyzedResultData

  const currentResultEnvelope = computed(() => parseResultEnvelope(options.resultData.value))
  const resultAnalysisFieldOptions = computed(() => currentResultEnvelope.value?.columns || [])
  const resultAnalysisMetricOptions = computed(() => {
    const rows = currentResultEnvelope.value?.data || []
    return resultAnalysisFieldOptions.value.filter((column) =>
      rows.some((row) => toNumericValue(row[column]) !== null),
    )
  })
  const resultAnalysisDimensionOptions = computed(() =>
    resultAnalysisFieldOptions.value.filter((column) => column !== percentMetricField.value),
  )
  const hasAnalyzedResult = computed(() => !!analyzedResultData.value)
  const isCompareAnalysisApplied = computed(
    () => hasAnalyzedResult.value && appliedAnalysisType.value === 'yoy',
  )
  const isCompareAnalysisDisabled = computed(
    () => options.dimensions.value[0]?.transform === 'week',
  )
  const analyzedResultSemanticParams = computed(() => {
    const columns = currentResultEnvelope.value?.columns || []
    const rows = currentResultEnvelope.value?.data || []
    const metrics = columns.filter((column) =>
      rows.some((row) => toNumericValue(row[column]) !== null),
    )
    return { metrics, dimensions: columns.filter((column) => !metrics.includes(column)) }
  })
  const resultSemanticParams = computed(() => {
    if (isCompareAnalysisApplied.value) {
      return {
        metrics: ['当前值', '上期值', '差值', '比率'],
        dimensions: options.dimensions.value.map(options.getDimensionDisplayName),
      }
    }
    if (hasAnalyzedResult.value) return analyzedResultSemanticParams.value
    return {
      metrics: options.aggregations.value.map(options.getMetricDisplayName),
      dimensions: options.dimensions.value.map(options.getDimensionDisplayName),
    }
  })

  const buildAnalyzedResultEnvelope = (
    parsed: ResultEnvelope,
    payload: BuilderQueryResultPayload,
  ) => {
    analyzedResultData.value = JSON.stringify({
      status: 'succeeded',
      sql: parsed.sql,
      columns: payload.columns,
      data: payload.data,
      total_count: payload.total_count,
      execution_time_ms: payload.execution_time_ms,
    })
  }
  const ensureResultReadyForAnalysis = () => {
    if (!options.resultData.value) {
      ElMessage.warning('请先查询原始结果')
      return null
    }
    const parsed = parseResultEnvelope(options.resultData.value)
    if (!parsed || !isQuerySucceeded(parsed) || parsed.error) {
      ElMessage.warning('当前原始结果不可分析')
      return null
    }
    return parsed
  }
  const defaultSortField = (columns: string[]) =>
    getDefaultAnalysisSortField(columns, currentResultEnvelope.value?.data || [])
  const handleWarning = (warning: string | null) => {
    if (!warning) return false
    ElMessage.warning(warning)
    return true
  }

  const runCompareAnalysis = () => {
    if (isCompareAnalysisDisabled.value) {
      ElMessage.warning('同环比结果分析暂不支持按周')
      return
    }
    const parsed = ensureResultReadyForAnalysis()
    if (!parsed) return
    try {
      const { payload, warning } = applyPeriodCompareAnalysis(parsed, {
        transform: options.dimensions.value[0]?.transform || 'raw',
        compareType: compareType.value,
        precheckWarning: options.getCompareAnalysisWarning(),
      })
      if (handleWarning(warning)) {
        analyzedResultData.value = null
        appliedAnalysisType.value = null
        return
      }
      buildAnalyzedResultEnvelope(parsed, payload)
      appliedAnalysisType.value = 'yoy'
    } catch {
      ElMessage.warning('当前原始结果无法解析，不能进行分析')
    }
  }
  const runSortAnalysis = () => {
    const parsed = ensureResultReadyForAnalysis()
    if (!parsed) return
    const field = resultSortField.value || defaultSortField(parsed.columns || [])
    if (!field) return ElMessage.warning('当前结果没有可排序字段')
    resultSortField.value = field
    const { payload, warning } = applyResultSortAnalysis(parsed, field, resultSortDirection.value)
    if (handleWarning(warning)) return
    buildAnalyzedResultEnvelope(parsed, payload)
    appliedAnalysisType.value = 'sort'
  }
  const runTopNAnalysis = () => {
    const parsed = ensureResultReadyForAnalysis()
    if (!parsed) return
    const field = topNField.value || defaultSortField(parsed.columns || [])
    if (!field) return ElMessage.warning('当前结果没有可用于 Top N 的排序字段')
    topNField.value = field
    const { payload, warning } = applyResultTopNAnalysis(
      parsed,
      field,
      topNDirection.value,
      Number(resultTopN.value),
    )
    if (handleWarning(warning)) return
    buildAnalyzedResultEnvelope(parsed, payload)
    appliedAnalysisType.value = 'topn'
  }
  const runTotalAnalysis = () => {
    const parsed = ensureResultReadyForAnalysis()
    if (!parsed) return
    const { payload, warning } = applyResultTotalAnalysis(parsed)
    if (handleWarning(warning)) return
    buildAnalyzedResultEnvelope(parsed, payload)
    appliedAnalysisType.value = 'total'
  }
  const runPercentAnalysis = () => {
    const parsed = ensureResultReadyForAnalysis()
    if (!parsed) return
    const metric = percentMetricField.value || resultAnalysisMetricOptions.value[0] || ''
    if (!metric) return ElMessage.warning('当前结果没有可计算百分比的数值列')
    if (percentScope.value === 'group' && !percentGroupField.value)
      return ElMessage.warning('请选择分组维度后再计算组内占比')
    percentMetricField.value = metric
    const { payload, warning } = applyResultPercentAnalysis(
      parsed,
      metric,
      percentScope.value,
      percentGroupField.value,
    )
    if (handleWarning(warning)) return
    buildAnalyzedResultEnvelope(parsed, payload)
    appliedAnalysisType.value = 'percent'
  }
  const runCompareBaselineAnalysis = () => {
    const parsed = ensureResultReadyForAnalysis()
    if (!parsed) return
    const metric = compareMetricField.value || resultAnalysisMetricOptions.value[0] || ''
    if (!metric) return ElMessage.warning('当前结果没有可对比的数值列')
    compareMetricField.value = metric
    const { payload, warning } = applyResultCompareAnalysis(
      parsed,
      metric,
      compareBaselineMode.value,
    )
    if (handleWarning(warning)) return
    buildAnalyzedResultEnvelope(parsed, payload)
    appliedAnalysisType.value = 'compare'
  }
  const cancelResultAnalysis = () => {
    analyzedResultData.value = null
    appliedAnalysisType.value = null
  }
  const resetResultAnalysis = () => {
    cancelResultAnalysis()
    resultSortField.value = ''
    resultSortDirection.value = 'DESC'
    resultTopN.value = 10
    topNField.value = ''
    topNDirection.value = 'DESC'
    percentMetricField.value = ''
    percentScope.value = 'total'
    percentGroupField.value = ''
    compareMetricField.value = ''
    compareBaselineMode.value = 'first'
  }

  watch(
    resultAnalysisFieldOptions,
    (columns) => {
      if (!columns.length) {
        resultSortField.value = ''
        topNField.value = ''
        percentMetricField.value = ''
        percentGroupField.value = ''
        compareMetricField.value = ''
        return
      }
      if (!columns.includes(resultSortField.value))
        resultSortField.value = defaultSortField(columns)
      if (!columns.includes(topNField.value)) topNField.value = defaultSortField(columns)
      if (!resultAnalysisMetricOptions.value.includes(percentMetricField.value))
        percentMetricField.value = resultAnalysisMetricOptions.value[0] || ''
      if (!resultAnalysisDimensionOptions.value.includes(percentGroupField.value))
        percentGroupField.value = resultAnalysisDimensionOptions.value[0] || ''
      if (!resultAnalysisMetricOptions.value.includes(compareMetricField.value))
        compareMetricField.value = resultAnalysisMetricOptions.value[0] || ''
    },
    { immediate: true },
  )

  return {
    selectedAnalysisType,
    appliedAnalysisType,
    compareType,
    resultSortField,
    resultSortDirection,
    resultTopN,
    topNField,
    topNDirection,
    percentMetricField,
    percentScope,
    percentGroupField,
    compareMetricField,
    compareBaselineMode,
    analyzedResultData,
    hasAnalyzedResult,
    currentResultEnvelope,
    resultAnalysisFieldOptions,
    resultAnalysisMetricOptions,
    resultAnalysisDimensionOptions,
    isCompareAnalysisApplied,
    isCompareAnalysisDisabled,
    resultSemanticParams,
    runCompareAnalysis,
    runSortAnalysis,
    runTopNAnalysis,
    runTotalAnalysis,
    runPercentAnalysis,
    runCompareBaselineAnalysis,
    cancelResultAnalysis,
    resetResultAnalysis,
  }
}
