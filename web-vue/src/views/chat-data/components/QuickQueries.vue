<script setup lang="ts">
/**
 * 常用查询快捷入口组件
 * 根据后端收藏记录动态渲染，也支持调用方传入空态推荐问题
 * 支持 SQL / builder 收藏，也支持会话空态推荐问题
 */
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { deleteQueryFavorite, getQueryFavorites, getSkills, updateQueryFavorite } from '../api'
import queryWorkflowHintIcon from '../assets/icons/query-workflow-hint.svg'
import type { SkillInfo } from '../types'
import { getSkillCreatorLabel, getSkillCreatorShortLabel } from '../utils'

// Builder 筛选配置节点（支持 and/or 嵌套分组）
export interface BuilderFilterNode {
  id?: string
  table?: string
  tableAlias?: string
  column?: string
  semanticName?: string
  displayName?: string
  operator?: string
  value?: string | string[]
  valueType?: 'literal' | 'field_ref' | 'expression'
  rightTable?: string
  rightTableAlias?: string
  rightColumn?: string
  rightExpression?: string
  logic?: 'AND' | 'OR'
  and?: BuilderFilterNode[]
  or?: BuilderFilterNode[]
  expression?: string
  aggFunc?: string
  aggId?: string
}

// Builder 配置类型
export interface BuilderConfig {
  semantic_config_id?: string
  semantic_config_name?: string
  semanticConfigId?: string
  semanticConfigName?: string
  mainTable: string
  mainAlias?: string
  joins: Array<{
    id?: string
    leftTable?: string
    leftAlias?: string
    rightTable: string
    rightAlias?: string
    leftColumn: string
    rightColumn: string
    joinConditions?: Array<{
      leftTable?: string
      leftAlias?: string
      leftColumn: string
      rightColumn: string
    }>
    joinType: 'LEFT' | 'RIGHT' | 'INNER' | 'FULL'
  }>
  dimensions: Array<{
    id: string
    table: string
    tableAlias?: string
    column: string
    alias?: string
    semanticName?: string
    expression?: string
    transform?: 'raw' | 'year' | 'quarter' | 'month' | 'week' | 'day'
  }>
  aggregations: Array<{
    id: string
    func: 'COUNT' | 'SUM' | 'AVG' | 'MAX' | 'MIN' | 'COUNT_DISTINCT'
    table: string
    tableAlias?: string
    column: string
    alias?: string
    semanticName?: string
    expression?: string
  }>
  filters: BuilderFilterNode[]
  orderBy?: Array<{
    id: string
    table: string
    tableAlias?: string
    column: string
    semanticName?: string
    direction: 'ASC' | 'DESC'
  }>
  limit?: number | null
  offset?: number | null
}

interface QueryTemplate {
  id: string
  title: string
  query: string
  query_type?: 'sql' | 'builder'
  semantic_config_id?: string
  semantic_config_name?: string
  builder_config?: BuilderConfig
  icon: string
  color: string
  readonly?: boolean
}

export interface FallbackQuery {
  id?: string
  title: string
  query?: string
  icon?: string
  color?: string
}

interface Props {
  datasourceId?: string
  queryType?: 'sql' | 'builder'
  refreshTrigger?: number
  maxItems?: number
  mode?: 'default' | 'conversation'
  contentType?: 'both' | 'skills' | 'queries'
  fallbackQueries?: FallbackQuery[]
  compact?: boolean
  pageOffset?: number
  preferFallback?: boolean
  loadFavorites?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  datasourceId: '',
  queryType: undefined,
  refreshTrigger: 0,
  maxItems: 12,
  mode: 'default',
  contentType: 'both',
  fallbackQueries: () => [],
  compact: false,
  pageOffset: 0,
  preferFallback: false,
  loadFavorites: true
})

const emit = defineEmits<{
  selectQuery: [query: string]
  selectBuilderConfig: [config: BuilderConfig]
  selectSkill: [skill: SkillInfo]
}>()

// 状态
const templates = ref<QueryTemplate[]>([])
const skills = ref<SkillInfo[]>([])
const activeTab = ref<'skills' | 'queries'>('skills')
const searchQuery = ref('')
const isLoading = ref(false)
const isSkillLoading = ref(false)
const hoveredId = ref<string | null>(null)
const deletingId = ref<string | null>(null)
const showDeleteModal = ref(false)
const deletingTemplate = ref<QueryTemplate | null>(null)
const showEditModal = ref(false)
const editingId = ref<string | null>(null)
const editTitle = ref('')
const editContent = ref('')
const editQueryType = ref<'sql' | 'builder'>('sql')
const savingEdit = ref(false)

const normalizeFavoriteText = (value: unknown) => {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch (_err) {
    return String(value)
  }
}

const getFavoriteQueryText = (item: Record<string, any>) => {
  return normalizeFavoriteText(
    item.query_text ??
    item.queryText ??
    item.query ??
    item.sql ??
    item.content ??
    item.favorite_content ??
    item.favoriteContent
  )
}

const normalizeBuilderConfig = (value: unknown): BuilderConfig | undefined => {
  if (!value) return undefined
  if (typeof value === 'object') return value as BuilderConfig
  if (typeof value !== 'string') return undefined
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? parsed as BuilderConfig : undefined
  } catch (_err) {
    return undefined
  }
}

const normalizeText = (value: unknown) => String(value || '').trim()

const mergeBuilderSemanticConfig = (builderConfig: BuilderConfig | undefined, item: Record<string, any>) => {
  if (!builderConfig) return undefined
  const semanticConfigId = normalizeText(item.semantic_config_id || item.semanticConfigId || builderConfig.semantic_config_id || builderConfig.semanticConfigId)
  const semanticConfigName = normalizeText(item.semantic_config_name || item.semanticConfigName || builderConfig.semantic_config_name || builderConfig.semanticConfigName)
  return {
    ...builderConfig,
    ...(semanticConfigId ? { semantic_config_id: semanticConfigId } : {}),
    ...(semanticConfigName ? { semantic_config_name: semanticConfigName } : {})
  }
}

// 加载收藏查询
const loadQueries = async () => {
  if (!props.loadFavorites || props.mode === 'conversation') {
    templates.value = []
    return
  }
  if (!props.datasourceId) {
    templates.value = []
    return
  }

  isLoading.value = true
  try {
    const response = await getQueryFavorites({
      datasource_id: props.datasourceId,
      limit: props.maxItems,
      query_type: props.queryType
    })

    if (response.data?.queries) {
      templates.value = (response.data.queries as Array<Record<string, any>>).map((item) => {
        const builderConfig = mergeBuilderSemanticConfig(normalizeBuilderConfig(item.builder_config ?? item.builderConfig), item)
        const semanticConfigId = normalizeText(item.semantic_config_id || item.semanticConfigId || builderConfig?.semantic_config_id || builderConfig?.semanticConfigId)
        const semanticConfigName = normalizeText(item.semantic_config_name || item.semanticConfigName || builderConfig?.semantic_config_name || builderConfig?.semanticConfigName)
        return {
          id: String(item.id),
          title: String(item.favorite_title || item.title || '未命名收藏'),
          query: getFavoriteQueryText(item),
          query_type: (item.query_type || 'sql') as 'sql' | 'builder',
          semantic_config_id: semanticConfigId || undefined,
          semantic_config_name: semanticConfigName || undefined,
          builder_config: builderConfig,
          icon: String(item.icon || 'fa-star'),
          color: String(item.color || '#E8F3FF')
        }
      })
    } else {
      templates.value = []
    }
  } catch (err) {
    console.error('获取收藏查询失败:', err)
    templates.value = []
  } finally {
    isLoading.value = false
  }
}

const loadSkills = async () => {
  if (props.mode !== 'conversation') return
  if (props.contentType === 'queries') {
    skills.value = []
    return
  }
  isSkillLoading.value = true
  try {
    const response = await getSkills()
    skills.value = response.data?.skills || []
  } catch (err) {
    console.error('获取专家技能失败:', err)
    skills.value = []
  } finally {
    isSkillLoading.value = false
  }
}

const fallbackTemplates = computed<QueryTemplate[]>(() => {
  if (props.mode !== 'conversation' || activeContentType.value !== 'queries') return []
  const items: QueryTemplate[] = []
  props.fallbackQueries.forEach((item, index) => {
    const title = String(item.title || '').trim()
    const query = String(item.query || item.title || '').trim()
    if (!title || !query) return
    items.push({
      id: item.id ? `fallback-${item.id}` : `fallback-${index}-${title}`,
      title,
      query,
      icon: item.icon || 'fa-lightbulb',
      color: item.color || '#E8F3FF',
      readonly: true,
    })
  })
  return items
})

const querySourceTemplates = computed(() => {
  if (props.preferFallback && fallbackTemplates.value.length > 0) return fallbackTemplates.value
  if (templates.value.length > 0) return templates.value
  return fallbackTemplates.value
})

const hasSearchableContent = computed(() => {
  if (props.compact) return false
  if (props.mode !== 'conversation') return templates.value.length > 0
  if (activeContentType.value === 'skills') return skills.value.length > 0
  return querySourceTemplates.value.length > 0
})

// 过滤后的模板
const displayedTemplates = computed(() => {
  let result = querySourceTemplates.value

  if (searchQuery.value.trim()) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(t =>
      t.title.toLowerCase().includes(query) ||
      t.query.toLowerCase().includes(query)
    )
  }

  if (!props.compact || result.length <= props.maxItems) {
    return result.slice(0, props.maxItems)
  }
  const offset = Math.max(0, Number(props.pageOffset || 0)) % result.length
  const cycled = [...result.slice(offset), ...result.slice(0, offset)]
  return cycled.slice(0, props.maxItems)
})

const displayedSkills = computed(() => {
  let result = skills.value
  if (searchQuery.value.trim()) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(item =>
      String(item.title || item.name || '').toLowerCase().includes(query) ||
      String(item.description || '').toLowerCase().includes(query) ||
      String(item.category || '').toLowerCase().includes(query)
    )
  }
  return result
})

const activeContentType = computed<'skills' | 'queries'>(() => {
  if (props.mode !== 'conversation') return 'queries'
  if (props.contentType === 'skills' || props.contentType === 'queries') return props.contentType
  return activeTab.value
})

// 处理点击
const handleClick = (template: QueryTemplate) => {
  if (props.mode === 'conversation') {
    emit('selectQuery', template.query || template.title)
    return
  }
  const isBuilderType = template.query_type === 'builder' && template.builder_config
  if (isBuilderType && template.builder_config) {
    emit('selectBuilderConfig', template.builder_config)
  } else {
    emit('selectQuery', template.query)
  }
}

const handleSkillClick = (skill: SkillInfo) => {
  emit('selectSkill', skill)
}

const getConversationQuestionText = (template: QueryTemplate) => {
  return String(template.query || template.title || '').trim()
}

const getTemplateIcon = (template: QueryTemplate) => {
  return template.query_type === 'builder' ? 'fa-cubes' : template.icon || 'fa-star'
}

// 获取关联类型中文
const getJoinTypeLabel = (type: string): string => {
  const typeMap: Record<string, string> = {
    'LEFT': '左',
    'RIGHT': '右',
    'INNER': '内',
    'FULL': '全'
  }
  return typeMap[type] || type
}

const getTemplatePreview = (template: QueryTemplate) => {
  const queryPreview = String(template.query || '').replace(/\s+/g, ' ').trim()
  if (template.query_type !== 'builder') {
    return queryPreview || 'SQL 查询'
  }
  const config = template.builder_config
  if (!config) return queryPreview || '可视化查询配置'

  const formatFields = (items: Array<Record<string, any>> | undefined, formatter: (item: Record<string, any>) => string) => {
    const labels = (items || []).map(formatter).map(item => item.trim()).filter(Boolean)
    if (labels.length <= 2) return labels.join('、')
    return `${labels.slice(0, 2).join('、')} 等${labels.length}个`
  }
  const dimensions = formatFields(config.dimensions as Array<Record<string, any>> | undefined, item => String(item.alias || item.name || item.column || ''))
  const aggregations = formatFields(config.aggregations as Array<Record<string, any>> | undefined, item => {
    const alias = String(item.alias || item.name || '').trim()
    if (alias) return alias
    const func = String(item.func || '').trim()
    const column = String(item.column || '').trim()
    return func && column ? `${func}(${column})` : column
  })
  const joins = formatFields(config.joins as Array<Record<string, any>> | undefined, item => {
    const table = String(item.rightTable || '').trim()
    return table ? `${table}(${getJoinTypeLabel(String(item.joinType || ''))})` : ''
  })
  const parts = [
    config.semantic_config_name ? `指标集 ${config.semantic_config_name}` : '',
    config.mainTable ? `表 ${config.mainTable}` : '',
    dimensions ? `维度 ${dimensions}` : '',
    aggregations ? `指标 ${aggregations}` : '',
    joins ? `关联 ${joins}` : '',
    config.filters?.length ? `筛选 ${config.filters.length}` : '',
  ].filter(Boolean)
  return parts.join(' · ') || queryPreview || '可视化查询配置'
}

const handleDelete = (template: QueryTemplate, e: MouseEvent) => {
  e.stopPropagation()
  if (template.readonly) return
  if (deletingId.value) return
  deletingTemplate.value = template
  showDeleteModal.value = true
}

const confirmDelete = async () => {
  const template = deletingTemplate.value
  if (!template) return

  deletingId.value = template.id
  try {
    await deleteQueryFavorite(template.id)
    templates.value = templates.value.filter(item => item.id !== template.id)
    ElMessage.success('已删除')
    showDeleteModal.value = false
    deletingTemplate.value = null
  } catch (err: any) {
    const apiMsg = err?.response?.data?.msg
    ElMessage.error(apiMsg || '删除失败，请稍后重试')
  } finally {
    deletingId.value = null
  }
}

const closeDeleteModal = () => {
  if (deletingId.value) return
  showDeleteModal.value = false
  deletingTemplate.value = null
}

const openEdit = (template: QueryTemplate, e: MouseEvent) => {
  e.stopPropagation()
  if (template.readonly) return
  editingId.value = template.id
  editTitle.value = template.title
  editContent.value = template.query
  editQueryType.value = (template.query_type || 'sql') as 'sql' | 'builder'
  showEditModal.value = true
}

const submitEdit = async () => {
  const id = editingId.value
  if (!id) return
  const favoriteTitle = editTitle.value.trim()
  if (!favoriteTitle) {
    ElMessage.warning('请输入收藏标题')
    return
  }

  savingEdit.value = true
  try {
    const payload: Record<string, unknown> = { favorite_title: favoriteTitle }
    if (editQueryType.value !== 'builder') {
      const content = editContent.value.trim()
      if (!content) {
        ElMessage.warning('原始内容不能为空')
        return
      }
      payload.query_text = content
    }
    await updateQueryFavorite(id, payload)

    templates.value = templates.value.map(item => {
      if (item.id !== id) return item
      return {
        ...item,
        title: favoriteTitle,
        query: editQueryType.value === 'builder' ? item.query : editContent.value.trim()
      }
    })
    showEditModal.value = false
    ElMessage.success('已更新')
  } catch (err: any) {
    const apiMsg = err?.response?.data?.msg
    ElMessage.error(apiMsg || '更新失败，请稍后重试')
  } finally {
    savingEdit.value = false
  }
}

// 监听变化重新加载
watch(
  () => [props.datasourceId, props.queryType, props.refreshTrigger],
  () => {
    loadQueries()
    loadSkills()
  },
  { immediate: true }
)
</script>

<template>
  <div :class="compact ? 'w-full min-h-0 flex flex-col' : 'w-full min-h-full p-4 pt-8 flex flex-col'">
    <div v-if="mode === 'conversation' && contentType === 'both'" class="mb-4 flex items-center gap-2">
      <button
        type="button"
        :class="[
          'h-8 px-3 rounded-full text-[13px] transition-all duration-150',
          activeTab === 'skills' ? 'bg-[#1f82d9] text-white shadow-[0_4px_12px_rgba(31,130,217,0.2)]' : 'bg-white text-gray-500 border border-gray-200 hover:border-blue-200 hover:text-[#1f82d9]'
        ]"
        @click="activeTab = 'skills'; searchQuery = ''"
      >
        专家技能
      </button>
      <button
        type="button"
        :class="[
          'h-8 px-3 rounded-full text-[13px] transition-all duration-150',
          activeTab === 'queries' ? 'bg-[#1f82d9] text-white shadow-[0_4px_12px_rgba(31,130,217,0.2)]' : 'bg-white text-gray-500 border border-gray-200 hover:border-blue-200 hover:text-[#1f82d9]'
        ]"
        @click="activeTab = 'queries'; searchQuery = ''"
      >
        常用问题
      </button>
    </div>

    <!-- 搜索框 -->
    <div v-if="hasSearchableContent" class="mb-5">
      <div class="relative w-full">
        <i class="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-xs text-gray-400"></i>
        <input
          v-model="searchQuery"
          type="text"
          :placeholder="mode === 'conversation' ? (activeContentType === 'skills' ? '搜索专家技能...' : '搜索常用问题...') : '搜索查询模板...'"
          class="w-full h-10 pl-10 pr-9 border border-gray-200 rounded-[10px] bg-white text-sm outline-none transition-all duration-200 focus:border-blue-300 focus:shadow-[0_0_0_3px_rgba(59,130,246,0.1)] placeholder:text-gray-400"
        />
        <button v-if="searchQuery" class="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 flex items-center justify-center border-none bg-transparent text-gray-400 cursor-pointer transition-colors duration-150 hover:text-gray-700" @click="searchQuery = ''">
          <i class="fa-solid fa-times"></i>
        </button>
      </div>
    </div>

    <!-- 加载中 -->
    <div v-if="isLoading || (mode === 'conversation' && activeContentType === 'skills' && isSkillLoading)" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-[13px]">
      <i class="fa-solid fa-spinner fa-spin"></i>
      <span>加载中...</span>
    </div>

    <div v-else-if="mode === 'conversation' && activeContentType === 'skills' && displayedSkills.length > 0" class="max-h-[204px] overflow-y-auto overscroll-contain pr-1">
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <button
          v-for="skill in displayedSkills"
          :key="skill.name"
          class="query-card relative min-h-[92px] p-3 border border-black/[0.06] rounded-xl bg-white text-left cursor-pointer transition-all duration-300 overflow-hidden hover:border-[rgba(31,130,217,0.3)] hover:shadow-[0_8px_24px_rgba(31,130,217,0.12)] hover:-translate-y-[3px]"
          @click="handleSkillClick(skill)"
        >
          <div class="flex items-start justify-between gap-2 mb-2">
            <h4 class="min-w-0 flex-1 text-[13px] font-semibold text-gray-800 m-0 leading-5 break-words" :title="skill.title || skill.name">
              {{ skill.title || skill.name }}
            </h4>
            <span
              :class="[
                'shrink-0 rounded-md px-2 py-0.5 text-[11px] leading-5',
                skill.source === 'system' ? 'bg-slate-100 text-slate-500' : 'bg-blue-50 text-blue-600'
              ]"
              :title="getSkillCreatorLabel(skill)"
            >
              {{ getSkillCreatorShortLabel(skill) }}
            </span>
          </div>
          <div class="text-[13px] text-gray-500 leading-normal line-clamp-2">{{ skill.description || '暂无技能说明' }}</div>
        </button>
      </div>
    </div>

    <!-- 会话模式：普通问题列表 -->
    <div
      v-else-if="mode === 'conversation' && activeContentType === 'queries' && displayedTemplates.length > 0"
      :class="compact ? 'flex flex-col items-start gap-2' : 'overflow-hidden rounded-xl border border-slate-200 bg-white/95'"
    >
      <div
        v-for="template in displayedTemplates"
        :key="template.id"
        :class="[
          'group flex items-center gap-2',
          compact
            ? 'min-h-10 max-w-full'
            : 'min-h-11 border-b border-slate-100 last:border-b-0 hover:bg-blue-50/50'
        ]"
        @mouseenter="hoveredId = template.id"
        @mouseleave="hoveredId = null"
      >
        <button
          type="button"
          :class="[
            'flex min-w-0 flex-1 items-center gap-2 border-none bg-transparent text-left cursor-pointer',
            compact
              ? 'max-w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-[14px] shadow-[0_1px_2px_rgba(15,23,42,0.03)] transition-all hover:border-blue-200 hover:bg-blue-50/40 hover:text-blue-700 hover:shadow-[0_4px_12px_rgba(15,23,42,0.06)]'
              : 'px-3 py-2.5'
          ]"
          :title="getConversationQuestionText(template)"
          @click="handleClick(template)"
        >
          <span
            v-if="compact"
            class="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-blue-50 text-[10px] text-blue-500"
          >
            <i class="fa-regular fa-message"></i>
          </span>
          <i v-else class="fa-regular fa-message shrink-0 text-[13px] text-slate-400"></i>
          <span :class="compact ? 'min-w-0 flex-1 truncate leading-5 text-slate-700' : 'min-w-0 flex-1 truncate text-[14px] leading-5 text-slate-700'">
            {{ getConversationQuestionText(template) }}
          </span>
        </button>
        <div
          v-if="!template.readonly"
          class="mr-2 flex shrink-0 items-center gap-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100"
        >
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-md border border-transparent bg-transparent text-[12px] text-slate-400 hover:border-blue-100 hover:bg-blue-50 hover:text-blue-600"
            title="编辑收藏"
            @click="openEdit(template, $event)"
          >
            <i class="fa-regular fa-pen-to-square"></i>
          </button>
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-md border border-transparent bg-transparent text-[12px] text-slate-400 hover:border-red-100 hover:bg-red-50 hover:text-red-500"
            :title="deletingId === template.id ? '删除中...' : '删除收藏'"
            @click="handleDelete(template, $event)"
          >
            <i :class="deletingId === template.id ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-trash'"></i>
          </button>
        </div>
      </div>
    </div>

    <!-- SQL / 可视化收藏：卡片网格 -->
    <div v-else-if="mode !== 'conversation' && displayedTemplates.length > 0" class="grid grid-cols-[repeat(auto-fill,240px)] gap-3">
      <button
        v-for="template in displayedTemplates"
        :key="template.id"
        class="query-card relative h-[115px] w-[240px] p-2.5 border border-black/[0.06] rounded-xl bg-white text-left cursor-pointer transition-all duration-300 overflow-hidden hover:border-[rgba(31,130,217,0.3)] hover:shadow-[0_8px_24px_rgba(31,130,217,0.12)] hover:-translate-y-[3px]"
        @click="handleClick(template)"
        @mouseenter="hoveredId = template.id"
        @mouseleave="hoveredId = null"
      >
        <div class="mb-1 flex h-5 items-center gap-1">
          <div
            class="card-icon w-5 h-5 flex items-center justify-center rounded-md text-[10px] shrink-0 transition-all duration-300"
            :style="{ backgroundColor: '#E8F3FF', color: '#B3DBD2' }"
          >
            <i :class="['fa-solid', getTemplateIcon(template)]"></i>
          </div>
          <h4
            class="flex-1 min-w-0 text-[12px] font-medium leading-4 text-gray-700 m-0 overflow-hidden text-ellipsis whitespace-nowrap"
            :title="template.title"
          >
            {{ template.title }}
          </h4>
          <div
            v-if="!template.readonly"
            class="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-150"
            :class="{ 'opacity-100': hoveredId === template.id }"
          >
            <span
              class="w-4.5 h-4.5 flex items-center justify-center border border-transparent rounded bg-white/95 text-[9px] text-gray-400 transition-all duration-150 hover:text-[#1f82d9] hover:border-[#bfdbfe] hover:bg-[#eff6ff]"
              title="编辑收藏"
              role="button"
              @click="openEdit(template, $event)"
            >
              <i class="fa-regular fa-pen-to-square"></i>
            </span>
            <span
              class="w-4.5 h-4.5 flex items-center justify-center border border-transparent rounded bg-white/95 text-[9px] text-gray-400 transition-all duration-150 hover:text-red-500 hover:border-red-100 hover:bg-red-50"
              :title="deletingId === template.id ? '删除中...' : '删除收藏'"
              role="button"
              @click="handleDelete(template, $event)"
            >
              <i :class="deletingId === template.id ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-trash'"></i>
            </span>
          </div>
        </div>

        <div
          :class="[
            'h-[68px] overflow-hidden rounded-md border border-black/[0.04] px-2.5 py-1.5 text-[11px] leading-[18px] text-slate-500 line-clamp-3',
            template.query_type === 'builder' ? 'bg-blue-50/40' : 'bg-slate-50 font-mono'
          ]"
          :title="getTemplatePreview(template)"
        >
          {{ getTemplatePreview(template) }}
        </div>
      </button>
    </div>

    <!-- 无结果 -->
    <div v-else-if="searchQuery" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400">
      <i class="fa-solid fa-search text-2xl opacity-50"></i>
      <p class="text-[13px] m-0">没有找到匹配的{{ mode === 'conversation' && activeContentType === 'skills' ? '技能' : '查询' }}</p>
    </div>

    <!-- 空态引导（可视化查询） -->
    <div
      v-else-if="mode !== 'conversation' && queryType === 'builder'"
      class="flex-1 flex items-center justify-center"
    >
      <div class="inline-flex flex-col items-center gap-2 text-[14px] text-[#B2B7C2]">
        <img :src="queryWorkflowHintIcon" alt="" class="w-12 h-12 opacity-70" />
        <div class="text-center leading-6">
          <div>先在上方完成查询编辑，使用图表进行多种结果展示，最后用结果分析深入洞察。</div>
          <div class="text-[13px] text-[#C0C5CF]">你可以从拖拽维度/指标/筛选开始。</div>
        </div>
      </div>
    </div>
  </div>

  <el-dialog v-model="showEditModal" title="编辑收藏" width="640px" :close-on-click-modal="false">
    <div class="flex flex-col gap-3">
      <div>
        <div class="text-[13px] text-[#4b5563] mb-1.5">收藏标题</div>
        <el-input v-model="editTitle" maxlength="255" placeholder="请输入收藏标题" />
      </div>
      <div>
        <div class="text-[13px] text-[#4b5563] mb-1.5">原始内容</div>
        <el-input
          v-model="editContent"
          type="textarea"
          :rows="8"
          resize="vertical"
          :readonly="editQueryType === 'builder'"
          :placeholder="editQueryType === 'builder' ? '可视化查询原始配置不可编辑' : '请输入原始内容'"
        />
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="showEditModal = false">取消</el-button>
        <el-button type="primary" :loading="savingEdit" @click="submitEdit">保存</el-button>
      </div>
    </template>
  </el-dialog>

  <el-dialog v-model="showDeleteModal" title="删除收藏" width="420px" :close-on-click-modal="false">
    <div class="text-[14px] leading-6 text-[#4b5563]">
      确认删除收藏「{{ deletingTemplate?.title || '' }}」吗？
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button :disabled="!!deletingId" @click="closeDeleteModal">取消</el-button>
        <el-button type="danger" :loading="!!deletingId" @click="confirmDelete">删除</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
/* Card icon hover effect - requires parent selector */
.query-card:hover .card-icon {
  transform: scale(1.04);
  box-shadow: 0 3px 8px rgba(31, 130, 217, 0.12);
}

</style>
