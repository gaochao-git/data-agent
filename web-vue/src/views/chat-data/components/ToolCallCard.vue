<script setup lang="ts">
/**
 * 工具调用卡片组件
 * 展示工具名、参数、结果
 */
import { ref, computed } from 'vue'
import { format as formatSQL } from 'sql-formatter'
import { ElMessage } from 'element-plus'
import type { ExecutionTargetOption, ToolCallInfo } from '../types'
import QueryResultPanel from './QueryResultPanel.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'
import { extractGeneratedSqlFromToolCall, extractQueryResultPayload, extractSemanticDslFromArgs, getQueryResultError, parseQueryResultEnvelope } from '../utils/semanticTool'
import { getToolCompactDescription, getToolDisplayName } from '../utils/toolDisplay'
import { useSaveToPanel } from '../composables/useSaveToPanel'

interface Props {
  toolCall: ToolCallInfo
  isExpanded?: boolean
  compact?: boolean
  active?: boolean
  showRunningIndicator?: boolean
  renderQueryResultPreview?: boolean
  datasourceId?: string
  executionTargets?: ExecutionTargetOption[]
  onViewAnalysis?: () => void
  isAnalysisActive?: boolean
  conversationId?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  isExpanded: false,
  compact: false,
  active: false,
  showRunningIndicator: true,
  renderQueryResultPreview: true
})

const emit = defineEmits<{
  toggleExpand: []
  select: []
}>()

// 保存到看板：共享弹窗状态与打开逻辑
const {
  showSaveModal,
  saveSql,
  saveChartType,
  saveMemberDatasourceId,
  saveMemberDatasourceIds,
  handleSaveToDashboard,
} = useSaveToPanel()


// 折叠状态
const paramsExpanded = ref(true)
const resultsExpanded = ref(true)

// 参数标签
const PARAM_LABELS: Record<string, string> = {
  metrics: '指标',
  dimensions: '维度',
  filters: '数据筛选',
  having: '分组过滤',
  order_by: '排序',
  limit: '限制条数',
  offset: '跳过条数',
  sql: 'SQL',
  result_id: '结果ID',
  chart_type: '图表类型',
  x_field: 'X轴字段',
  y_field: 'Y轴字段',
  series_field: '分组字段',
  skill_name: '技能名称',
  reference_names: '参考文档',
  file_path: '文件路径',
  command_args: '命令参数',
  source_path: '源路径',
  target_path: '目标路径',
  destination_path: '目标路径',
  semantic_config_id: '语义模型ID',
  content: '内容',
  mode: '写入模式',
  max_depth: '最大深度',
  recursive: '递归删除',
  detail: '详细信息',
  pattern: '关键词',
  path: '路径',
  glob: '文件匹配',
  output_mode: '输出模式',
  dsl: '语义DSL',
  query_params: '查询参数',
  dashboard_spec: '看板规格',
  query: '用户问题',
  user_query: '用户问题',
  operation: '操作',
  field_type: '字段类型',
  datasource_id: '数据源',
  member_datasource_id: '执行机构',
  member_datasource_ids: '执行机构',
  subagent_type: '子智能体类型'
}

// 个别工具的参数名与通用语义冲突（如 task 的 description 是「任务书」，
// 不代表所有工具的 description 都该这么显示），按工具维度覆盖全局表。
const TOOL_PARAM_LABEL_OVERRIDES: Record<string, Record<string, string>> = {
  task: { description: '任务书' }
}

const paramLabel = (key: string): string =>
  TOOL_PARAM_LABEL_OVERRIDES[props.toolCall.tool_name]?.[key] ?? PARAM_LABELS[key] ?? key

type SemanticSelectionResult = {
  success?: boolean
  operation?: string
  directory?: boolean
  semantic_config_ids?: string[]
  semantic_configs?: Record<string, Record<string, unknown>>
  semantic_config_directory?: Array<Record<string, unknown>>
  message?: string
}

// DSL 参数标签颜色
const DSL_TAG_COLORS: Record<string, { bg: string; text: string }> = {
  metrics: { bg: 'rgba(59, 130, 246, 0.1)', text: '#2563eb' },
  dimensions: { bg: 'rgba(249, 115, 22, 0.1)', text: '#ea580c' },
  filters: { bg: 'rgba(16, 185, 129, 0.1)', text: '#059669' },
  having: { bg: 'rgba(139, 92, 246, 0.1)', text: '#7c3aed' },
  order_by: { bg: 'rgba(236, 72, 153, 0.1)', text: '#db2777' }
}

const displayName = computed(() => getToolDisplayName(props.toolCall.tool_name))

// 状态
const isRunning = computed(() => props.toolCall.status === 'generating' || props.toolCall.status === 'running')
const isCompleted = computed(() => props.toolCall.status === 'completed')
const isError = computed(() => props.toolCall.status === 'error')

const getResultField = (field: string): string => {
  const result = String(props.toolCall.result || '')
  const match = result.match(new RegExp(`^${field}=([^\\n]+)`, 'm'))
  return match?.[1]?.trim() || ''
}

const compactDescription = computed(() => {
  return getToolCompactDescription(props.toolCall.tool_name, props.toolCall.args || {})
})

const generatedSql = computed(() => extractGeneratedSqlFromToolCall(props.toolCall))
const visibleError = computed(() => getQueryResultError(props.toolCall.result) || String(props.toolCall.result || '执行失败'))

// 是否可以保存到看板（semantic_query_tool 完成且查询结果包含内部生成的 SQL）
const canSaveToPanel = computed(() => {
  return props.toolCall.tool_name === 'semantic_query_tool' &&
    props.toolCall.status === 'completed' &&
    Boolean(generatedSql.value)
})

const copyText = (text: string, message: string) => {
  navigator.clipboard.writeText(text).then(() => {
    ElMessage.success(message)
  }).catch(() => {
    ElMessage.error('复制失败')
  })
}

const handleCopyParam = (key: string, value: unknown) => {
  copyText(formatParamValue(key, value), key === 'sql' ? '已复制 SQL' : '已复制 JSON')
}

const handleCopyResultSql = (sql: string) => {
  copyText(formatSql(sql), '已复制 SQL')
}

// 格式化 SQL
function formatSql(sql: string): string {
  try {
    return formatSQL(sql, {
      language: 'mysql',
      tabWidth: 2,
      keywordCase: 'upper',
      linesBetweenQueries: 2
    })
  } catch {
    return sql
  }
}

function isEmptyParamValue(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (Array.isArray(value) && value.length === 0) return true
  if (typeof value === 'object' && !Array.isArray(value) && Object.keys(value as object).length === 0) return true
  return false
}

function buildSemanticDslParam(args: Record<string, unknown>): Record<string, unknown> | null {
  if (Object.prototype.hasOwnProperty.call(args, 'dsl')) return null
  if (props.toolCall.tool_name !== 'dsl2sql_tool' && props.toolCall.tool_name !== 'semantic_query_tool') return null
  return extractSemanticDslFromArgs(args)
}

// 过滤要展示的参数
type DisplayParam = [key: string, value: unknown]

interface TodoItem { content: string; status: 'pending' | 'in_progress' | 'completed' }
const isWriteTodos = computed(() => props.toolCall.tool_name === 'write_todos')
const todoItems = computed<TodoItem[]>(() => {
  if (!isWriteTodos.value) return []
  const todos = (props.toolCall.args || {})?.todos
  return Array.isArray(todos)
    ? todos.filter(item => item && typeof item.content === 'string').map(item => ({
        content: String(item.content),
        status: item.status === 'completed' || item.status === 'in_progress' ? item.status : 'pending',
      }))
    : []
})

const displayParams = computed<DisplayParam[]>(() => {
  const args = props.toolCall.args || {}
  const entries: DisplayParam[] = Object.entries(args).filter(([key, value]) => {
    if (key === 'visualization' || (props.toolCall.tool_name === 'write_todos' && key === 'todos')) return false
    if (value === null || value === undefined) return false
    if (Array.isArray(value) && value.length === 0) return false
    if (typeof value === 'object' && !Array.isArray(value) && Object.keys(value as object).length === 0) return false
    return true
  })
  const semanticDsl = buildSemanticDslParam(args)
  if (semanticDsl && !isEmptyParamValue(semanticDsl)) entries.push(['dsl', semanticDsl])
  if (props.toolCall.tool_name === 'dsl2sql_tool' || props.toolCall.tool_name === 'semantic_query_tool') {
    const queryArgs = Object.fromEntries(entries)
    return Object.keys(queryArgs).length > 0 ? [['query_params', queryArgs]] : []
  }
  return entries
})

// 判断是否为 DSL 标签类型参数
function isDslTagParam(key: string): boolean {
  return ['metrics', 'dimensions', 'filters', 'having', 'order_by'].includes(key)
}

// 格式化参数值
function formatParamValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') {
    if (key === 'sql') return formatSql(value)
    return value
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]'
    if (typeof value[0] === 'object') return JSON.stringify(value, null, 2)
    return value.join(', ')
  }
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

// 判断是否需要代码块
function needsCodeBlock(key: string, value: unknown): boolean {
  if (key === 'sql') return true
  if (Array.isArray(value) && value.length > 0 && typeof value[0] === 'object') return true
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) return true
  return false
}

// 格式化 filter 对象
function formatFilterItem(filter: unknown): string {
  if (typeof filter === 'string') return filter
  if (filter && typeof filter === 'object') {
    const f = filter as Record<string, unknown>
    const fieldName = f.field || f.dimension
    if (fieldName && f.operator) {
      const valueStr = Array.isArray(f.value) ? f.value.join(', ') : String(f.value ?? '')
      return `${fieldName} ${f.operator} ${valueStr}`
    }
    return JSON.stringify(filter)
  }
  return String(filter)
}

// 获取 DSL 标签项目
function getDslTagItems(key: string, value: unknown[]): string[] {
  return value.map((item) => {
    if (typeof item === 'string') return item
    if (key === 'filters' || key === 'having') return formatFilterItem(item)
    if (key === 'order_by' && typeof item === 'object' && item !== null) {
      const o = item as Record<string, unknown>
      return `${o.field || o.column || ''} ${o.direction || o.order || 'ASC'}`
    }
    return typeof item === 'object' ? JSON.stringify(item) : String(item)
  })
}

// 清理结果内容
function cleanResult(result: string): string {
  const text = String(result || '').trim()
  if (parseQueryResultEnvelope(text)) return ''
  return text
    .replace(/<chart_config>[\s\S]*?<\/chart_config>/g, '')
    .trim()
}

function parseSemanticSelectionResult(result?: string): SemanticSelectionResult | null {
  const toolName = props.toolCall.tool_name
  if (toolName !== 'semantic_search') return null
  const cleaned = cleanResult(String(result || '')).replace(/^\[semantic_search\]\s*/, '').trim()
  if (!cleaned) return null
  try {
    const parsed = JSON.parse(cleaned) as SemanticSelectionResult
    if (parsed.operation !== 'search_models') return null
    return parsed
  } catch {
    return null
  }
}

const semanticSelectionResult = computed(() => parseSemanticSelectionResult(props.toolCall.result))
const semanticSelectionRawJson = computed(() => {
  if (!semanticSelectionResult.value) return ''
  return cleanResult(String(props.toolCall.result || '')).trim()
})
const semanticSelectionConfigs = computed(() => {
  const result = semanticSelectionResult.value
  if (!result) return []
  const configs = result.semantic_configs || {}
  const ids = Array.isArray(result.semantic_config_ids) ? result.semantic_config_ids : []
  if (result.directory) {
    return (result.semantic_config_directory || []).map((config) => ({
      id: String(config.id || '').trim(),
      name: String(config.name || config.id || '').trim(),
    })).filter(config => config.id)
  }
  return ids.map((id) => ({
    id: String(id || '').trim(),
    name: String(configs[id]?.name || id).trim(),
  })).filter(config => config.id)
})
const semanticSelectionHeader = computed(() => {
  const result = semanticSelectionResult.value
  if (!result) return ''
  if (result.directory) return '当前可见语义模型'
  return semanticSelectionConfigs.value.length > 0
    ? `检索到 ${semanticSelectionConfigs.value.length} 个相关语义模型`
    : '未检索到相关语义模型'
})

type ResultPart = { type: 'text' | 'sql' | 'json'; content: string }

function formatQueryResultJson(result: string): string {
  const payload = extractQueryResultPayload(result)
  return payload ? JSON.stringify(payload, null, 2) : ''
}

// 解析结果内容，将 SQL 代码块、JSON 与普通文本分离
function parseResultContent(result: string): ResultPart[] {
  if (props.toolCall.tool_name === 'semantic_search' && semanticSelectionResult.value) return []
  if (props.toolCall.tool_name === 'semantic_query_tool' && !props.renderQueryResultPreview) {
    const jsonText = formatQueryResultJson(result)
    if (jsonText) return [{ type: 'json', content: jsonText }]
  }
  const cleaned = cleanResult(result)
  if (!cleaned) return []
  const parts: ResultPart[] = []
  const regex = /```(?:sql)?\s*\n?([\s\S]*?)```/g
  let lastIndex = 0
  let match

  while ((match = regex.exec(cleaned)) !== null) {
    if (match.index > lastIndex) {
      const text = cleaned.slice(lastIndex, match.index).trim()
      if (text) parts.push({ type: 'text', content: text })
    }
    parts.push({ type: 'sql', content: (match[1] || '').trim() })
    lastIndex = match.index + match[0].length
  }

  if (lastIndex < cleaned.length) {
    const text = cleaned.slice(lastIndex).trim()
    if (text) parts.push({ type: 'text', content: text })
  }

  return parts
}

// SQL 关键字列表
const SQL_KEYWORDS = [
  'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'NOT', 'IN', 'BETWEEN', 'LIKE',
  'ORDER BY', 'GROUP BY', 'HAVING', 'LIMIT', 'OFFSET', 'JOIN', 'LEFT JOIN',
  'RIGHT JOIN', 'INNER JOIN', 'OUTER JOIN', 'ON', 'AS', 'DISTINCT', 'COUNT',
  'SUM', 'AVG', 'MIN', 'MAX', 'CAST', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
  'NULL', 'IS', 'ASC', 'DESC', 'UNION', 'ALL', 'EXISTS', 'CREATE', 'INSERT',
  'UPDATE', 'DELETE', 'SET', 'VALUES', 'INTO', 'TABLE', 'INDEX', 'VIEW',
  'Nullable', 'DATE', 'INT', 'VARCHAR', 'TEXT', 'BOOLEAN', 'FLOAT', 'DOUBLE'
]

// SQL 语法高亮解析
interface SqlToken {
  type: 'keyword' | 'string' | 'number' | 'comment' | 'text'
  value: string
}

function parseSqlTokens(sql: string): SqlToken[][] {
  const lines = sql.split('\n')
  return lines.map(line => {
    const tokens: SqlToken[] = []
    let remaining = line

    while (remaining.length > 0) {
      // 字符串（单引号）
      const stringMatch = remaining.match(/^'[^']*'/)
      if (stringMatch) {
        tokens.push({ type: 'string', value: stringMatch[0] })
        remaining = remaining.slice(stringMatch[0].length)
        continue
      }

      // 注释
      if (remaining.startsWith('--')) {
        tokens.push({ type: 'comment', value: remaining })
        remaining = ''
        continue
      }

      // 关键字
      let foundKeyword = false
      for (const keyword of SQL_KEYWORDS) {
        const regex = new RegExp(`^(${keyword})(?![a-zA-Z0-9_])`, 'i')
        const match = remaining.match(regex)
        if (match) {
          const keywordValue = match[1] || ''
          tokens.push({ type: 'keyword', value: keywordValue })
          remaining = remaining.slice(keywordValue.length)
          foundKeyword = true
          break
        }
      }
      if (foundKeyword) continue

      // 数字
      const numberMatch = remaining.match(/^\d+(\.\d+)?/)
      if (numberMatch) {
        tokens.push({ type: 'number', value: numberMatch[0] })
        remaining = remaining.slice(numberMatch[0].length)
        continue
      }

      // 普通字符
      tokens.push({ type: 'text', value: remaining[0] || '' })
      remaining = remaining.slice(1)
    }

    return tokens
  })
}

// 获取 SQL token 的 Tailwind 类
function getSqlTokenClass(type: string): string {
  const classMap: Record<string, string> = {
    keyword: 'text-blue-600 font-medium',
    string: 'text-emerald-600',
    number: 'text-amber-600',
    comment: 'text-gray-400 italic',
    text: 'text-gray-700'
  }
  return classMap[type] || 'text-gray-700'
}
</script>

<template>
  <div
    v-if="compact"
    class="group inline-flex max-w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs transition-all duration-200 cursor-pointer"
    :class="[
      'border-transparent bg-transparent text-slate-400 hover:text-slate-600',
      isError ? 'border-red-200 bg-red-50 text-red-600' : ''
    ]"
    @click="emit('select')"
  >
    <i class="fa-solid fa-wrench transition-colors duration-200 group-hover:text-slate-600"></i>
    <span class="flex min-w-0 items-center" :title="compactDescription || toolCall.tool_name">
      <span class="shrink-0 font-semibold">{{ displayName }}</span>
      <span v-if="compactDescription" class="ml-2 min-w-0 truncate font-mono text-[11px] font-normal text-slate-400 transition-colors duration-200 group-hover:text-slate-600">{{ compactDescription }}</span>
    </span>
    <i v-if="isRunning && showRunningIndicator" class="fa-solid fa-spinner fa-spin text-[11px] text-slate-400"></i>
    <i class="fa-solid fa-chevron-right text-[10px] text-slate-400 opacity-0 transition-all duration-200 group-hover:text-slate-600 group-hover:opacity-100"></i>
  </div>
  <div
    v-else
    class="min-w-0 max-w-full rounded-xl overflow-hidden bg-white/60 border border-black/[0.04] transition-all duration-200 hover:bg-white/80"
    :class="{
      'bg-[rgba(31,130,217,0.05)] border-[rgba(31,130,217,0.15)]': isRunning
    }"
  >
    <!-- 标题栏 -->
    <div
      class="flex min-w-0 items-center justify-between px-3.5 py-2.5 cursor-pointer transition-colors duration-200 hover:bg-black/[0.03]"
      :class="{ 'bg-black/[0.02]': isExpanded }"
      @click="emit('toggleExpand')"
    >
      <div class="flex min-w-0 items-center gap-3">
        <div class="w-6 h-6 flex items-center justify-center rounded-lg bg-black/5 text-[11px] text-gray-500">
          <i class="fa-solid fa-wrench"></i>
        </div>
        <span class="min-w-0 truncate text-sm font-medium text-gray-700" :title="toolCall.tool_name">{{ displayName }}</span>
        <i v-if="isRunning && showRunningIndicator" class="fa-solid fa-spinner fa-spin text-xs text-[#1f82d9]"></i>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <i
          class="fa-solid fa-chevron-right text-xs text-gray-400 transition-transform duration-200"
          :class="{ 'rotate-90': isExpanded }"
        ></i>
      </div>
    </div>

    <!-- 内容区 -->
    <div v-if="isExpanded" class="min-w-0 px-3.5 py-2.5 border-t border-black/[0.04] bg-black/[0.02]">
      <!-- 任务计划步骤列表（write_todos 专用） -->
      <div v-if="isWriteTodos && todoItems.length > 0" class="mb-3 last:mb-0">
        <div class="flex flex-col gap-1.5 p-3 rounded-lg bg-white border border-black/[0.06]">
          <div v-for="(item, idx) in todoItems" :key="idx" class="flex items-center gap-2 text-[13px]">
            <i v-if="item.status === 'completed'" class="fa-solid fa-circle-check text-emerald-500 text-xs shrink-0"></i>
            <i v-else-if="item.status === 'in_progress'" class="fa-solid fa-spinner fa-spin text-[#1677FF] text-xs shrink-0"></i>
            <span v-else class="h-3 w-3 shrink-0 rounded-full border border-dashed border-slate-300"></span>
            <span :class="[
              item.status === 'completed' ? 'text-slate-400 line-through' : '',
              item.status === 'in_progress' ? 'text-slate-800 font-medium' : '',
              item.status === 'pending' ? 'text-slate-500' : '',
            ]" class="truncate">{{ item.content }}</span>
          </div>
        </div>
      </div>
      <!-- 参数区（可折叠） -->
      <div v-if="displayParams.length > 0" class="mb-3 last:mb-0">
        <button
          class="flex items-center gap-1.5 text-xs font-medium text-gray-500 bg-transparent border-none p-0 cursor-pointer transition-colors duration-200 hover:text-gray-700"
          @click="paramsExpanded = !paramsExpanded"
        >
          <i
            class="fa-solid fa-chevron-right text-[10px] transition-transform duration-200"
            :class="{ 'rotate-90': paramsExpanded }"
          ></i>
          <span>参数</span>
        </button>
        <div v-if="paramsExpanded" class="mt-2">
          <div class="min-w-0 p-3 rounded-lg bg-white border border-black/[0.06]">
            <template v-for="[key, value] in displayParams" :key="key">
              <!-- DSL 标签类型 -->
              <div v-if="isDslTagParam(key) && Array.isArray(value)" class="mb-2 flex flex-col gap-1 sm:flex-row sm:items-start sm:gap-2 last:mb-0">
                <span class="shrink-0 pt-0.5 text-xs text-gray-400 sm:max-w-28 sm:truncate" :title="paramLabel(key)">{{ paramLabel(key) }}</span>
                <div class="min-w-0 flex flex-wrap gap-1">
                  <span
                    v-for="(item, idx) in getDslTagItems(key, value as unknown[])"
                    :key="idx"
                    class="inline-flex items-center px-2 py-0.5 rounded text-xs"
                    :style="{
                      backgroundColor: DSL_TAG_COLORS[key]?.bg,
                      color: DSL_TAG_COLORS[key]?.text
                    }"
                  >
                    {{ item }}
                  </span>
                </div>
              </div>
              <!-- 代码块类型 -->
              <div v-else-if="needsCodeBlock(key, value)" class="mb-3 last:mb-0">
                <div class="mb-1.5 flex items-center gap-2">
                  <div class="max-w-[160px] shrink-0 truncate text-xs text-gray-400" :title="paramLabel(key)">{{ paramLabel(key) }}</div>
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 border-none bg-transparent p-0 text-[11px] text-gray-400 cursor-pointer hover:text-gray-600"
                    :title="key === 'sql' ? '复制 SQL' : '复制 JSON'"
                    @click.stop="handleCopyParam(key, value)"
                  >
                    <i class="fa-regular fa-copy"></i>
                    <span>{{ key === 'sql' ? '复制SQL' : '复制JSON' }}</span>
                  </button>
                </div>
                <pre class="max-h-40 max-w-full overflow-auto px-3.5 py-3 rounded-lg bg-slate-50 border border-slate-200 text-xs font-mono leading-relaxed m-0"><template v-if="key === 'sql'"><div v-for="(lineTokens, lineIdx) in parseSqlTokens(formatParamValue(key, value))" :key="lineIdx" class="min-h-[1.4em]"><template v-for="(token, tokenIdx) in lineTokens" :key="tokenIdx"><span :class="getSqlTokenClass(token.type)">{{ token.value }}</span></template><template v-if="lineTokens.length === 0">&nbsp;</template></div></template><code v-else class="text-emerald-600">{{ formatParamValue(key, value) }}</code></pre>
              </div>
              <!-- 普通类型 -->
              <div v-else class="mb-3 flex flex-col gap-1 sm:flex-row sm:items-start sm:gap-2 last:mb-0">
                <span class="shrink-0 pt-0.5 text-xs text-gray-400 sm:max-w-28 sm:truncate" :title="paramLabel(key)">{{ paramLabel(key) }}</span>
                <span class="min-w-0 max-w-full whitespace-pre-wrap break-words font-mono text-sm text-gray-700">{{ formatParamValue(key, value) }}</span>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- 结果区 -->
      <div v-if="toolCall.status === 'running'" class="flex items-center gap-2 text-sm text-[#1f82d9] p-3 bg-[linear-gradient(135deg,rgba(31,130,217,0.08)_0%,rgba(31,130,217,0.02)_100%)] rounded-[10px]">
        <i v-if="showRunningIndicator" class="fa-solid fa-spinner fa-spin text-base"></i>
        <span>执行中...</span>
      </div>
      <div v-else-if="toolCall.status === 'completed' && (toolCall.result || toolCall.renderId)" class="mb-3 last:mb-0">
        <button
          class="flex items-center gap-1.5 text-xs font-medium text-gray-500 bg-transparent border-none p-0 cursor-pointer transition-colors duration-200 hover:text-gray-700"
          @click="resultsExpanded = !resultsExpanded"
        >
          <i
            class="fa-solid fa-chevron-right text-[10px] transition-transform duration-200"
            :class="{ 'rotate-90': resultsExpanded }"
          ></i>
          <span>结果</span>
        </button>
        <div v-if="resultsExpanded" class="mt-2">
          <!-- 指标集选择结果 -->
          <div v-if="semanticSelectionResult" class="min-w-0 p-3 rounded-lg bg-blue-50/70 border border-blue-100 mb-2 last:mb-0">
            <div class="flex items-center gap-2 font-medium text-slate-800">
              <i class="fa-solid fa-layer-group text-[11px] text-blue-500"></i>
              <span>{{ semanticSelectionHeader }}</span>
            </div>
            <div v-if="semanticSelectionConfigs.length" class="mt-2 flex flex-wrap gap-1.5">
              <span
                v-for="config in semanticSelectionConfigs"
                :key="config.id"
                class="rounded border border-blue-100 bg-white px-2 py-0.5 text-xs text-blue-600"
                :title="config.id"
              >
                {{ config.name || config.id }}
              </span>
            </div>
            <div v-if="semanticSelectionRawJson" class="mt-3">
              <div class="mb-1.5 text-xs text-slate-500">模型实际接收内容</div>
              <pre class="max-h-[32rem] max-w-full overflow-auto whitespace-pre-wrap break-words px-3.5 py-3 rounded-lg bg-white border border-blue-100 text-xs font-mono leading-relaxed text-slate-700 m-0">{{ semanticSelectionRawJson }}</pre>
            </div>
          </div>
          <!-- 文本结果 -->
          <div v-if="toolCall.result && parseResultContent(toolCall.result).length > 0" class="min-w-0 p-3 rounded-lg bg-white border border-black/[0.06] mb-2 last:mb-0">
            <template v-for="(part, idx) in parseResultContent(toolCall.result)" :key="idx">
              <div v-if="part.type === 'sql'" class="mb-2 last:mb-0">
                <div class="mb-1.5 flex items-center justify-start">
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 border-none bg-transparent p-0 text-[11px] text-gray-400 cursor-pointer hover:text-gray-600"
                    title="复制 SQL"
                    @click.stop="handleCopyResultSql(part.content)"
                  >
                    <i class="fa-regular fa-copy"></i>
                    <span>复制SQL</span>
                  </button>
                </div>
                <pre class="max-h-64 max-w-full overflow-auto px-3.5 py-3 rounded-lg bg-slate-50 border border-slate-200 text-xs font-mono leading-relaxed m-0"><div v-for="(lineTokens, lineIdx) in parseSqlTokens(formatSql(part.content))" :key="lineIdx" class="min-h-[1.4em]"><template v-for="(token, tokenIdx) in lineTokens" :key="tokenIdx"><span :class="getSqlTokenClass(token.type)">{{ token.value }}</span></template><template v-if="lineTokens.length === 0">&nbsp;</template></div></pre>
              </div>
              <pre v-else-if="part.type === 'json'" class="max-h-[46vh] max-w-full overflow-auto whitespace-pre-wrap break-words px-3.5 py-3 rounded-lg bg-slate-50 border border-slate-200 text-xs font-mono leading-relaxed text-slate-700 m-0">{{ part.content }}</pre>
              <div v-else class="text-sm text-gray-600 whitespace-pre-wrap mb-2 last:mb-0">{{ part.content }}</div>
            </template>
          </div>
          <!-- 渲染结果（表格/图表） -->
          <div v-if="renderQueryResultPreview && toolCall.renderId && toolCall.renderType === 'queryResult'" class="min-w-0 max-w-full overflow-hidden">
            <QueryResultPanel
              class="max-h-[46vh]"
              :result-data="toolCall.renderId"
              :conversation-id="conversationId"
              :sql="generatedSql"
              :semantic-params="toolCall.semanticParams"
              :on-view-analysis="onViewAnalysis"
              :is-analysis-active="isAnalysisActive"
              :on-save="canSaveToPanel ? handleSaveToDashboard : undefined"
            />
          </div>
          <div v-else-if="toolCall.renderId && toolCall.renderType === 'chart'" class="flex items-center gap-2.5 p-5 text-center text-gray-500 text-[13px] bg-[linear-gradient(135deg,#fafbfc_0%,#f5f7fa_100%)] rounded-xl border border-dashed border-black/10">
            <i class="fa-solid fa-chart-column text-lg bg-[linear-gradient(135deg,#1f82d9_0%,#135589_100%)] bg-clip-text text-transparent"></i>
            <span>图表配置已生成</span>
          </div>
          <!-- 无内容时 -->
          <div v-if="!toolCall.result && !toolCall.renderId" class="p-3 rounded-lg bg-white border border-black/[0.06] flex items-center gap-2 text-sm text-emerald-500">
            <i class="fa-solid fa-check"></i>
            <span>已完成</span>
          </div>
        </div>
      </div>
      <div v-else-if="toolCall.status === 'completed' && !toolCall.result && !toolCall.renderId" class="flex items-center gap-2 text-sm text-emerald-500 p-3 bg-[linear-gradient(135deg,rgba(16,185,129,0.08)_0%,rgba(16,185,129,0.02)_100%)] rounded-[10px]">
        <i class="fa-solid fa-check"></i>
        <span>已完成</span>
      </div>
      <div v-else-if="toolCall.status === 'error' && toolCall.result" class="p-3 rounded-lg bg-red-500/5 border border-red-500/20">
        <div class="flex items-center gap-1.5 text-xs font-medium text-red-500 mb-2">
          <i class="fa-solid fa-triangle-exclamation"></i>
          <span>错误</span>
        </div>
        <div class="text-sm text-red-600 whitespace-pre-wrap break-words">{{ visibleError }}</div>
      </div>
    </div>

    <!-- 保存到看板弹窗 -->
    <SaveToPanelModal
      v-if="canSaveToPanel"
      v-model:visible="showSaveModal"
      :sql="saveSql || generatedSql"
      :chart-type="saveChartType"
      :datasource-id="datasourceId"
      :member-datasource-id="saveMemberDatasourceId"
      :member-datasource-ids="saveMemberDatasourceIds"
      :execution-targets="executionTargets"
    />
  </div>
</template>

<style scoped>
/* Tailwind's animate-pulse uses a different animation, keep custom pulse for running state */
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

.animate-pulse {
  animation: pulse 2s ease-in-out infinite;
}
</style>
