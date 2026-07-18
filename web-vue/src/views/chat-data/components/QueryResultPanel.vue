<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AsyncQueryResult from './AsyncQueryResult.vue'
import ResultTabs from './ResultTabs.vue'
import { getConversationArtifact } from '../api'
import type { ChartType } from '../chartTypes'
import { dedupeRepeatedErrorText, isQuerySucceeded, normalizeErrorKey } from '../utils'

interface SemanticParams {
  metrics?: string[]
  dimensions?: Array<string | { name: string; time_grain?: string; [key: string]: unknown }>
  filters?: Array<{
    dimension?: string
    operator?: string
    value?: unknown
    [key: string]: unknown
  }>
}

interface VisualizationConfig {
  type?: string
  x_field?: string
  y_field?: string
}

interface QueryResult {
  status?: string | null
  success?: boolean
  sql?: string
  query_mode?: string
  confidence?: number | string
  semantic_config_id?: string
  semantic_config_name?: string
  columns?: string[]
  data?: Record<string, unknown>[]
  total_count?: number
  execution_time_ms?: number
  error?: string
  cache_hit?: boolean
  cache_age_seconds?: number | null
  cache_expires_in_seconds?: number | null
  member_datasource_id?: string
  member_name?: string
  results?: QueryResult[]
  artifact_id?: string
  meta?: {
    artifact_id?: string
    [key: string]: unknown
  }
}

type ResultTabItem = {
  key: string | number
  label: string
  title?: string
  status?: 'success' | 'error' | 'none'
}

interface QueryResultPanelItem {
  key?: string | number
  title?: string
  resultData: string
  sql?: string
  memberDatasourceId?: string
  memberName?: string
  status?: 'success' | 'error' | 'none'
}

type ViewMode = 'vertical' | 'card' | ChartType

interface Props {
  resultData?: string | null
  items?: QueryResultPanelItem[]
  semanticParams?: SemanticParams
  visualization?: VisualizationConfig
  sql?: string
  onSave?: (sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => void
  onViewAnalysis?: () => void
  isAnalysisActive?: boolean
  paginationEnabled?: boolean
  pageSize?: number
  maxRows?: number
  showIndex?: boolean
  autoWrap?: boolean
  metricsFirst?: boolean
  allowFullscreen?: boolean
  initialViewMode?: ViewMode
  activeIndex?: number
}

defineOptions({
  name: 'QueryResultPanel',
  inheritAttrs: false
})

const props = withDefaults(defineProps<Props>(), {
  items: () => [],
  isAnalysisActive: false,
  paginationEnabled: false,
  pageSize: 20,
  maxRows: 1000,
  showIndex: false,
  autoWrap: false,
  metricsFirst: false,
  allowFullscreen: true,
  activeIndex: 0,
})

const emit = defineEmits<{
  'update:activeIndex': [value: number]
}>()

const activeIndex = ref(0)
const artifactResult = ref<QueryResult | null>(null)
const artifactLoading = ref(false)
const artifactError = ref('')

const parseInlineResult = (raw: string | null | undefined): { result: QueryResult | null; error: string | null } => {
  if (!raw) return { result: null, error: null }
  try {
    return { result: JSON.parse(raw) as QueryResult, error: null }
  } catch {
    return { result: null, error: '无法解析查询结果' }
  }
}

const inlineParse = computed(() => parseInlineResult(props.resultData))
const artifactId = computed(() => String(
  inlineParse.value.result?.artifact_id ||
  inlineParse.value.result?.meta?.artifact_id ||
  ''
).trim())
const panelResult = computed(() => artifactResult.value || inlineParse.value.result?.data || inlineParse.value.result)
const parseError = computed(() => inlineParse.value.error)

const resultToData = (result: QueryResult) => JSON.stringify(result)
const getMemberFullTitle = (item: Pick<QueryResultPanelItem, 'memberName' | 'memberDatasourceId'> | null | undefined) => {
  const memberName = String(item?.memberName || '').trim()
  const memberDatasourceId = String(item?.memberDatasourceId || '').trim()
  if (memberName && memberDatasourceId) return `${memberName}（${memberDatasourceId}）`
  return memberName || memberDatasourceId
}

const getItemLabel = (item: Pick<QueryResultPanelItem, 'title' | 'memberName' | 'memberDatasourceId'> | null | undefined, index: number) => {
  const title = String(item?.title || '').trim()
  const memberName = String(item?.memberName || '').trim()
  const memberDatasourceId = String(item?.memberDatasourceId || '').trim()
  const memberFullTitle = getMemberFullTitle(item)
  if (title && title !== memberFullTitle) return title
  return memberName || title || memberDatasourceId || `结果${index + 1}`
}

const parseItemResult = (item: QueryResultPanelItem): QueryResult | null => {
  try {
    return JSON.parse(item.resultData) as QueryResult
  } catch {
    return null
  }
}

const buildMergedErrorItem = (
  items: QueryResultPanelItem[],
  parsedResults: QueryResult[],
  groupIndex: number
): QueryResultPanelItem => {
  const firstResult = parsedResults[0]
  const errorText = dedupeRepeatedErrorText(firstResult?.error, '查询失败')
  const memberNames = items.map((item, index) => getMemberFullTitle(item) || item.title || `执行对象${index + 1}`)
  const detailText = `影响执行对象：${memberNames.join('、')}`
  const resultData = resultToData({
    ...firstResult,
    status: 'failed',
    columns: [],
    data: [],
    total_count: 0,
    error: `${errorText}\n\n${detailText}`,
  })
  return {
    key: `merged-error-${groupIndex}-${normalizeErrorKey(errorText)}`,
    title: `执行失败（${items.length}个对象）`,
    resultData,
    sql: items[0]?.sql || props.sql,
    status: 'error',
  }
}

const mergeRepeatedErrorItems = (items: QueryResultPanelItem[]) => {
  const result: QueryResultPanelItem[] = []
  const errorGroups = new Map<string, { items: QueryResultPanelItem[]; parsedResults: QueryResult[] }>()
  items.forEach((item) => {
    const parsed = parseItemResult(item)
    if (!parsed || isQuerySucceeded(parsed)) {
      result.push(item)
      return
    }
    const key = normalizeErrorKey(parsed.error || '')
    if (!key) {
      result.push(item)
      return
    }
    const group = errorGroups.get(key) || { items: [], parsedResults: [] }
    group.items.push(item)
    group.parsedResults.push(parsed)
    errorGroups.set(key, group)
  })

  Array.from(errorGroups.values()).forEach((group, index) => {
    const firstItem = group.items[0]
    if (!firstItem) return
    if (group.items.length === 1) {
      result.push(firstItem)
      return
    }
    result.push(buildMergedErrorItem(group.items, group.parsedResults, index))
  })
  return result
}

const itemsFromResult = computed<QueryResultPanelItem[]>(() => {
  const result = panelResult.value
  if (!result) return []
  const memberResults = result.results || []
  if (memberResults.length > 0) {
    return memberResults.map((item, index) => ({
      key: item.member_datasource_id || item.member_name || index,
      title: getItemLabel({
        title: item.member_name,
        memberName: item.member_name,
        memberDatasourceId: item.member_datasource_id,
      }, index),
      resultData: resultToData({
        ...item,
        sql: item.sql || result.sql,
        query_mode: item.query_mode || result.query_mode,
        confidence: item.confidence ?? result.confidence,
        semantic_config_id: item.semantic_config_id || result.semantic_config_id,
        semantic_config_name: item.semantic_config_name || result.semantic_config_name,
      }),
      sql: item.sql || result.sql || props.sql,
      memberDatasourceId: item.member_datasource_id,
      memberName: item.member_name,
      status: isQuerySucceeded(item) ? 'success' : 'error',
    }))
  }
  return [{
    key: result.member_datasource_id || result.member_name || 'single',
    title: result.member_name || '',
    resultData: resultToData(result),
    sql: result.sql || props.sql,
    memberDatasourceId: result.member_datasource_id,
    memberName: result.member_name,
    status: isQuerySucceeded(result) ? 'success' : 'error',
  }]
})

const normalizedItems = computed<QueryResultPanelItem[]>(() => {
  const items = props.items.length > 0 ? props.items : itemsFromResult.value
  return mergeRepeatedErrorItems(items)
})

const tabs = computed<ResultTabItem[]>(() =>
  normalizedItems.value.map((item, index) => ({
    key: item.key || item.memberDatasourceId || item.title || index,
    label: getItemLabel(item, index),
    title: getMemberFullTitle(item) || item.title || item.sql || getItemLabel(item, index),
    status: item.status,
  }))
)

const activeItem = computed(() => normalizedItems.value[activeIndex.value] || normalizedItems.value[0] || null)
const activeResultData = computed(() => activeItem.value?.resultData || '')
const activeSql = computed(() => activeItem.value?.sql || props.sql)
const activeMemberDatasourceIds = computed(() =>
  normalizedItems.value
    .map(item => String(item.memberDatasourceId || '').trim())
    .filter((id, index, list) => id && list.indexOf(id) === index)
)
const showTabs = computed(() => {
  if (tabs.value.length > 1) return true
  const item = activeItem.value
  const title = String(item?.title || '').trim()
  return Boolean(item?.memberName || item?.memberDatasourceId || (title && title !== '结果1'))
})

watch(
  () => props.activeIndex,
  (value) => {
    const next = Number(value)
    if (Number.isFinite(next) && next >= 0 && next !== activeIndex.value) {
      activeIndex.value = next
    }
  },
  { immediate: true }
)

watch(
  activeIndex,
  (value) => emit('update:activeIndex', value)
)

watch(
  () => normalizedItems.value.map(item => `${item.key || ''}:${item.memberDatasourceId || ''}:${item.title || ''}`).join('|'),
  () => {
    if (activeIndex.value >= normalizedItems.value.length) activeIndex.value = 0
    if (activeIndex.value < 0) activeIndex.value = 0
  },
  { immediate: true }
)

watch(
  artifactId,
  async (id) => {
    artifactResult.value = null
    artifactError.value = ''
    if (!id || props.items.length > 0) {
      artifactLoading.value = false
      return
    }
    artifactLoading.value = true
    try {
      const response = await getConversationArtifact<QueryResult>(id)
      if (id !== artifactId.value) return
      artifactResult.value = response.data || null
      if (!artifactResult.value) artifactError.value = '查询结果详情为空'
    } catch (error) {
      if (id !== artifactId.value) return
      artifactError.value = error instanceof Error ? error.message : '加载查询结果详情失败'
    } finally {
      if (id === artifactId.value) artifactLoading.value = false
    }
  },
  { immediate: true }
)
</script>

<template>
  <div v-bind="$attrs" class="flex h-full min-h-0 flex-col overflow-hidden">
    <div v-if="parseError" class="flex flex-col items-center justify-center gap-3 p-8 text-sm text-red-600 bg-gradient-to-br from-red-100/50 to-red-200/30">
      <i class="fa-solid fa-circle-exclamation text-2xl opacity-80"></i>
      <span>{{ parseError }}</span>
    </div>

    <div v-else-if="artifactLoading" class="flex flex-col items-center justify-center gap-3 p-8 text-sm text-gray-500 bg-gradient-to-br from-slate-50 to-slate-100">
      <i class="fa-solid fa-spinner fa-spin text-2xl text-[#1f82d9]"></i>
      <span>正在加载查询结果详情...</span>
    </div>

    <div v-else-if="artifactError" class="flex flex-col items-center justify-center gap-3 p-8 text-sm text-red-600 bg-gradient-to-br from-red-100/50 to-red-200/30">
      <i class="fa-solid fa-circle-exclamation text-2xl opacity-80"></i>
      <span>{{ artifactError }}</span>
    </div>

    <template v-else>
      <ResultTabs
        v-if="showTabs"
        v-model:active-index="activeIndex"
        :tabs="tabs"
        show-status
      />
      <AsyncQueryResult
        v-if="activeResultData"
        :key="`${activeIndex}-${activeItem?.memberDatasourceId || activeItem?.key || 'single'}`"
        class="min-h-0 flex-1"
        :result-data="activeResultData"
        :semantic-params="semanticParams"
        :visualization="visualization"
        :sql="activeSql"
        :member-datasource-ids="activeMemberDatasourceIds"
        :on-save="onSave"
        :on-view-analysis="onViewAnalysis"
        :is-analysis-active="isAnalysisActive"
        :pagination-enabled="paginationEnabled"
        :page-size="pageSize"
        :max-rows="maxRows"
        :show-index="showIndex"
        :auto-wrap="autoWrap"
        :metrics-first="metricsFirst"
        :allow-fullscreen="allowFullscreen"
        :initial-view-mode="initialViewMode"
      />
    </template>
  </div>
</template>
