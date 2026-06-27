<script setup lang="ts">
/**
 * 常用查询快捷入口组件
 * 纯后端驱动，根据收藏记录动态渲染
 * 支持三种类型：conversation（问数问题）、sql（普通SQL）和 builder（可视化查询配置）
 */
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { deleteQueryFavorite, getQueryFavorites, getSkills, updateQueryFavorite } from '../api'
import queryWorkflowHintIcon from '../assets/icons/query-workflow-hint.svg'
import type { SkillInfo } from '../types'

// Builder 配置类型
export interface BuilderConfig {
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
    transform?: 'raw' | 'year' | 'quarter' | 'month' | 'week' | 'day'
  }>
  aggregations: Array<{
    id: string
    func: 'COUNT' | 'SUM' | 'AVG' | 'MAX' | 'MIN' | 'COUNT_DISTINCT'
    table: string
    tableAlias?: string
    column: string
    alias?: string
  }>
  filters: Array<{
    id?: string
    table?: string
    tableAlias?: string
    column?: string
    operator?: string
    value?: string
    valueType?: 'literal' | 'field_ref' | 'expression'
    rightTable?: string
    rightTableAlias?: string
    rightColumn?: string
    rightExpression?: string
    logic?: 'AND' | 'OR'
    and?: unknown[]
    or?: unknown[]
    expression?: string
    aggFunc?: string
    aggId?: string
  }>
  orderBy?: Array<{
    id: string
    table: string
    tableAlias?: string
    column: string
    direction: 'ASC' | 'DESC'
  }>
  limit?: number | null
  offset?: number | null
}

interface QueryTemplate {
  id: string
  title: string
  query: string
  query_type?: 'conversation' | 'sql' | 'builder'
  builder_config?: BuilderConfig
  icon: string
  color: string
}

interface Props {
  datasourceId?: string
  queryType?: 'sql' | 'builder'
  refreshTrigger?: number
  maxItems?: number
  mode?: 'default' | 'conversation'
  contentType?: 'both' | 'skills' | 'queries'
}

const props = withDefaults(defineProps<Props>(), {
  datasourceId: '',
  queryType: undefined,
  refreshTrigger: 0,
  maxItems: 12,
  mode: 'default',
  contentType: 'both'
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
const editQueryType = ref<'conversation' | 'sql' | 'builder'>('sql')
const savingEdit = ref(false)

// 加载收藏查询
const loadQueries = async () => {
  if (props.mode === 'conversation' && props.contentType === 'skills') {
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
      query_type: props.mode === 'conversation' ? 'conversation' : props.queryType
    })

    if (response.data?.queries) {
      templates.value = (response.data.queries as Array<Record<string, any>>).map((item) => ({
        id: String(item.id),
        title: String(item.favorite_title || item.title || '未命名收藏'),
        query: String(item.query_text || item.query || ''),
        query_type: (item.query_type || 'sql') as 'sql' | 'builder',
        builder_config: item.builder_config as BuilderConfig | undefined,
        icon: String(item.icon || 'fa-star'),
        color: String(item.color || '#E8F3FF')
      }))
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
    const response = await getSkills({ agent: 'analyze' })
    skills.value = response.data?.skills || []
  } catch (err) {
    console.error('获取专家技能失败:', err)
    skills.value = []
  } finally {
    isSkillLoading.value = false
  }
}

// 过滤后的模板
const displayedTemplates = computed(() => {
  let result = templates.value

  if (searchQuery.value.trim()) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(t =>
      t.title.toLowerCase().includes(query) ||
      t.query.toLowerCase().includes(query)
    )
  }

  return result.slice(0, props.maxItems)
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

const getSkillCreatorLabel = (skill: SkillInfo) => {
  if (skill.source === 'system') return '系统'
  return skill.creator_name || skill.creator_id || skill.owner_user_id || '用户技能'
}

const getSkillCreatorShortLabel = (skill: SkillInfo) => {
  const label = getSkillCreatorLabel(skill)
  return label.length > 3 ? `${label.slice(0, 3)}...` : label
}

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

const handleDelete = (template: QueryTemplate, e: MouseEvent) => {
  e.stopPropagation()
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
  editingId.value = template.id
  editTitle.value = template.title
  editContent.value = template.query
  editQueryType.value = (template.query_type || 'sql') as 'conversation' | 'sql' | 'builder'
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
  <div class="w-full min-h-full p-4 pt-8 flex flex-col">
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
    <div v-if="mode !== 'conversation' ? templates.length > 0 : (activeContentType === 'skills' ? skills.length > 0 : templates.length > 0)" class="mb-5">
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

    <!-- 查询卡片网格 -->
    <div v-else-if="(mode !== 'conversation' || activeContentType === 'queries') && displayedTemplates.length > 0" class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <button
        v-for="template in displayedTemplates"
        :key="template.id"
        class="query-card relative h-[115px] p-3 border border-black/[0.06] rounded-xl bg-white text-left cursor-pointer transition-all duration-300 overflow-hidden hover:border-[rgba(31,130,217,0.3)] hover:shadow-[0_8px_24px_rgba(31,130,217,0.12)] hover:-translate-y-[3px]"
        :class="{ 'h-auto min-h-[60px]': mode === 'conversation' }"
        @click="handleClick(template)"
        @mouseenter="hoveredId = template.id"
        @mouseleave="hoveredId = null"
      >
        <div class="absolute top-2 right-2 flex items-center gap-1 opacity-0 transition-opacity duration-150" :class="{ 'opacity-100': hoveredId === template.id }">
          <span
            class="w-6 h-6 flex items-center justify-center border border-transparent rounded bg-white/95 text-[11px] text-gray-400 transition-all duration-150 hover:text-[#1f82d9] hover:border-[#bfdbfe] hover:bg-[#eff6ff]"
            title="编辑收藏"
            role="button"
            @click="openEdit(template, $event)"
          >
            <i class="fa-regular fa-pen-to-square"></i>
          </span>
          <span
            class="w-6 h-6 flex items-center justify-center border border-transparent rounded bg-white/95 text-[11px] text-gray-400 transition-all duration-150 hover:text-red-500 hover:border-red-100 hover:bg-red-50"
            :title="deletingId === template.id ? '删除中...' : '删除收藏'"
            role="button"
            @click="handleDelete(template, $event)"
          >
            <i :class="deletingId === template.id ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-trash'"></i>
          </span>
        </div>

        <!-- 顶部：图标 + 标题 -->
        <div class="flex items-center gap-2 mb-2">
          <div
            class="card-icon w-8 h-8 flex items-center justify-center rounded-lg text-sm shrink-0 transition-all duration-300"
            :style="{ backgroundColor: '#E8F3FF', color: '#B3DBD2' }"
          >
            <i :class="['fa-solid', mode === 'conversation' ? 'fa-comment-dots' : (template.query_type === 'builder' ? 'fa-cubes' : template.icon)]"></i>
          </div>
          <h4
            class="flex-1 min-w-0 text-[13px] font-medium text-gray-700 m-0 overflow-hidden text-ellipsis whitespace-nowrap"
            :title="template.title"
          >
            {{ template.title.length > 12 ? template.title.substring(0, 12) + '...' : template.title }}
          </h4>
        </div>

        <!-- 会话模式：显示完整问题文本 -->
        <template v-if="mode === 'conversation'">
          <div class="text-[13px] text-gray-500 leading-normal line-clamp-2">{{ template.title }}</div>
        </template>

        <!-- 默认模式：Builder 配置信息 -->
        <template v-else>
          <div v-if="template.query_type === 'builder' && template.builder_config" class="flex flex-col gap-1">
            <!-- 主表 -->
            <div class="flex items-center gap-1">
              <span class="text-[11px] text-gray-400 shrink-0">表:</span>
              <span class="text-[11px] text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap">{{ template.builder_config.mainTable }}</span>
            </div>
            <!-- 关联表 -->
            <div v-if="template.builder_config.joins?.length" class="flex items-center gap-1">
              <span class="text-[11px] text-gray-400 shrink-0">关联:</span>
              <span class="text-[11px] text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap">
                {{ template.builder_config.joins.map(j => `${j.rightTable}(${getJoinTypeLabel(j.joinType)})`).join(', ') }}
              </span>
            </div>
            <!-- 维度 -->
            <div v-if="template.builder_config.dimensions?.length" class="flex items-center gap-1">
              <span class="text-[11px] text-gray-400 shrink-0">维度:</span>
              <span class="text-[11px] text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap">
                {{ template.builder_config.dimensions.map(d => d.column).join(', ') }}
              </span>
            </div>
            <!-- 聚合 -->
            <div v-if="template.builder_config.aggregations?.length" class="flex items-center gap-1">
              <span class="text-[11px] text-gray-400 shrink-0">聚合:</span>
              <span class="text-[11px] text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap">
                {{ template.builder_config.aggregations.map(a => `${a.func}(${a.column})`).join(', ') }}
              </span>
            </div>
            <!-- 筛选 -->
            <div v-if="template.builder_config.filters?.length" class="flex items-center gap-1">
              <span class="text-[11px] text-gray-400 shrink-0">条件:</span>
              <span class="text-[11px] text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap">
                {{ template.builder_config.filters.length === 1
                  ? `${template.builder_config.filters[0]?.column ?? ''}${template.builder_config.filters[0]?.operator ?? ''}${template.builder_config.filters[0]?.value ?? ''}`
                  : `${template.builder_config.filters[0]?.column ?? ''}${template.builder_config.filters[0]?.operator ?? ''}... 等${template.builder_config.filters.length}个`
                }}
              </span>
            </div>
          </div>

          <!-- SQL 预览 -->
          <div v-else class="py-2 px-2.5 rounded-md bg-slate-50 border border-black/[0.04] font-mono text-[11px] text-slate-500 leading-snug line-clamp-2">
            {{ template.query }}
          </div>
        </template>

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
  transform: scale(1.1);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

</style>
