<script setup lang="ts">
/**
 * 工具调用卡片组件
 * 展示工具名、参数、结果
 */
import { ref, computed } from 'vue'
import { format as formatSQL } from 'sql-formatter'
import { ElMessage } from 'element-plus'
import type { ToolCallInfo } from '../types'
import { useChartRecommend } from '../composables/useChartRecommend'
import AsyncQueryResult from './AsyncQueryResult.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'

// 图表推荐开关（共享状态，与其他组件同步）
const { chartRecommendEnabled } = useChartRecommend()

interface Props {
  toolCall: ToolCallInfo
  isExpanded?: boolean
  datasourceId?: string
  onViewAnalysis?: () => void
  isAnalysisActive?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isExpanded: false
})

const emit = defineEmits<{
  toggleExpand: []
}>()

// 保存到看板弹窗
const showSaveModal = ref(false)


// 折叠状态
const paramsExpanded = ref(true)
const resultsExpanded = ref(true)

// 工具元数据
const TOOL_META: Record<string, { label: string; icon: string; iconColor: string }> = {
  // 普通对话
  load_skill_tool: { label: '普通对话', icon: 'fa-comment-dots', iconColor: 'text-[#5B8FF9]' },
  // SQL 阶段
  dsl2sql_tool: { label: '语义转SQL', icon: 'fa-chart-line', iconColor: 'text-[#36CFC9]' },
  validate_sql_tool: { label: 'SQL校验', icon: 'fa-chart-line', iconColor: 'text-[#36CFC9]' },
  generate_chart_tool: { label: '聚合查询', icon: 'fa-chart-line', iconColor: 'text-[#36CFC9]' },
  execute_sql_tool: { label: 'SQL执行', icon: 'fa-table', iconColor: 'text-[#597EF7]' }
}

// 参数标签
const PARAM_LABELS: Record<string, string> = {
  metrics: '指标',
  dimensions: '维度',
  filters: '数据筛选',
  having: '分组过滤',
  order_by: '排序',
  limit: '限制条数',
  sql: 'SQL',
  result_id: '结果ID',
  chart_type: '图表类型',
  x_field: 'X轴字段',
  y_field: 'Y轴字段',
  series_field: '分组字段',
  skill_name: '技能名称'
}

// DSL 参数标签颜色
const DSL_TAG_COLORS: Record<string, { bg: string; text: string }> = {
  metrics: { bg: 'rgba(59, 130, 246, 0.1)', text: '#2563eb' },
  dimensions: { bg: 'rgba(249, 115, 22, 0.1)', text: '#ea580c' },
  filters: { bg: 'rgba(16, 185, 129, 0.1)', text: '#059669' },
  having: { bg: 'rgba(139, 92, 246, 0.1)', text: '#7c3aed' },
  order_by: { bg: 'rgba(236, 72, 153, 0.1)', text: '#db2777' }
}

// 获取工具元数据
const meta = computed(() => {
  return TOOL_META[props.toolCall.tool_name] || {
    label: '普通对话',
    icon: 'fa-comment-dots',
    iconColor: 'text-[#5B8FF9]'
  }
})

// 状态
const isRunning = computed(() => props.toolCall.status === 'generating' || props.toolCall.status === 'running')
const isCompleted = computed(() => props.toolCall.status === 'completed')
const isError = computed(() => props.toolCall.status === 'error')

// 是否可以保存到看板（execute_sql_tool 完成且有 SQL）
const canSaveToPanel = computed(() => {
  return props.toolCall.tool_name === 'execute_sql_tool' &&
    props.toolCall.status === 'completed' &&
    props.toolCall.args?.sql
})

// 复制工具调用信息
const handleCopy = () => {
  const text = JSON.stringify({
    tool_name: props.toolCall.tool_name,
    args: props.toolCall.args,
    result: props.toolCall.result
  }, null, 2)
  navigator.clipboard.writeText(text).then(() => {
    ElMessage.success('已复制工具调用信息')
  }).catch(() => {
    ElMessage.error('复制失败')
  })
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

// 过滤要展示的参数
const displayParams = computed(() => {
  return Object.entries(props.toolCall.args || {}).filter(([_, value]) => {
    if (value === null || value === undefined) return false
    if (Array.isArray(value) && value.length === 0) return false
    if (typeof value === 'object' && !Array.isArray(value) && Object.keys(value as object).length === 0) return false
    return true
  })
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
  return result
    .replace(/<query_result>[\s\S]*?<\/query_result>/g, '')
    .replace(/<chart_config>[\s\S]*?<\/chart_config>/g, '')
    .trim()
}

// 解析结果内容，将 SQL 代码块与普通文本分离
function parseResultContent(result: string): Array<{ type: 'text' | 'sql'; content: string }> {
  const cleaned = cleanResult(result)
  if (!cleaned) return []
  const parts: Array<{ type: 'text' | 'sql'; content: string }> = []
  const regex = /```(?:sql)?\s*\n?([\s\S]*?)```/g
  let lastIndex = 0
  let match

  while ((match = regex.exec(cleaned)) !== null) {
    if (match.index > lastIndex) {
      const text = cleaned.slice(lastIndex, match.index).trim()
      if (text) parts.push({ type: 'text', content: text })
    }
    parts.push({ type: 'sql', content: match[1].trim() })
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
          tokens.push({ type: 'keyword', value: match[1] })
          remaining = remaining.slice(match[1].length)
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
      tokens.push({ type: 'text', value: remaining[0] })
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
    class="rounded-xl overflow-hidden bg-white/60 border border-black/[0.04] transition-all duration-200 hover:bg-white/80"
    :class="{
      'bg-[rgba(31,130,217,0.05)] border-[rgba(31,130,217,0.15)]': isRunning
    }"
  >
    <!-- 标题栏 -->
    <div
      class="flex items-center justify-between px-3.5 py-2.5 cursor-pointer transition-colors duration-200 hover:bg-black/[0.03]"
      :class="{ 'bg-black/[0.02]': isExpanded }"
      @click="emit('toggleExpand')"
    >
      <div class="flex items-center gap-3">
        <!-- 状态图标 -->
        <div
          v-if="isRunning"
          class="w-6 h-6 flex items-center justify-center rounded-lg bg-[linear-gradient(135deg,rgba(31,130,217,0.15)_0%,rgba(31,130,217,0.05)_100%)] text-[#1f82d9] text-[11px] animate-pulse"
        >
          <i class="fa-solid fa-spinner fa-spin"></i>
        </div>
        <div
          v-else-if="isCompleted"
          class="w-6 h-6 flex items-center justify-center rounded-lg bg-[linear-gradient(135deg,#10b981_0%,#059669_100%)] text-white text-[11px] shadow-[0_2px_8px_rgba(16,185,129,0.3)]"
        >
          <i class="fa-solid fa-check"></i>
        </div>
        <div
          v-else-if="isError"
          class="w-6 h-6 flex items-center justify-center rounded-lg bg-[linear-gradient(135deg,#ef4444_0%,#dc2626_100%)] text-white text-[11px] shadow-[0_2px_8px_rgba(239,68,68,0.3)]"
        >
          <i class="fa-solid fa-xmark"></i>
        </div>
        <div v-else class="w-6 h-6 flex items-center justify-center rounded-lg bg-black/5 text-[11px] text-gray-500">
          <i :class="['fa-solid', meta.icon]"></i>
        </div>
        <span class="text-sm font-medium text-gray-700">{{ meta.label }}</span>
      </div>
      <div class="flex items-center gap-2">
        <!-- 复制按钮 -->
        <button
          v-if="isCompleted"
          class="w-7 h-7 flex items-center justify-center border-none rounded-md bg-transparent text-gray-400 cursor-pointer text-xs transition-all duration-200 hover:bg-black/5 hover:text-gray-500"
          title="复制工具信息"
          @click.stop="handleCopy"
        >
          <i class="fa-regular fa-copy"></i>
        </button>
        <!-- 保存到看板按钮 -->
        <button
          v-if="canSaveToPanel"
          class="w-7 h-7 flex items-center justify-center border-none rounded-md bg-transparent text-gray-400 cursor-pointer text-xs transition-all duration-200 hover:bg-black/5 hover:text-gray-500"
          title="保存到看板"
          @click.stop="showSaveModal = true"
        >
          <i class="fa-regular fa-bookmark"></i>
        </button>
        <i
          class="fa-solid fa-chevron-right text-xs text-gray-400 transition-transform duration-200"
          :class="{ 'rotate-90': isExpanded }"
        ></i>
      </div>
    </div>

    <!-- 内容区 -->
    <div v-if="isExpanded" class="px-3.5 py-2.5 border-t border-black/[0.04] bg-black/[0.02]">
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
          <div class="p-3 rounded-lg bg-white border border-black/[0.06]">
            <template v-for="[key, value] in displayParams" :key="key">
              <!-- DSL 标签类型 -->
              <div v-if="isDslTagParam(key) && Array.isArray(value)" class="flex items-start gap-2 mb-2 last:mb-0">
                <span class="text-xs text-gray-400 min-w-[60px] shrink-0">{{ PARAM_LABELS[key] || key }}</span>
                <div class="flex flex-wrap gap-1">
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
                <div class="text-xs text-gray-400 min-w-[60px] shrink-0 mb-1.5">{{ PARAM_LABELS[key] || key }}</div>
                <pre class="max-h-40 overflow-auto px-3.5 py-3 rounded-lg bg-slate-50 border border-slate-200 text-xs font-mono leading-relaxed m-0"><template v-if="key === 'sql'"><div v-for="(lineTokens, lineIdx) in parseSqlTokens(formatParamValue(key, value))" :key="lineIdx" class="min-h-[1.4em]"><template v-for="(token, tokenIdx) in lineTokens" :key="tokenIdx"><span :class="getSqlTokenClass(token.type)">{{ token.value }}</span></template><template v-if="lineTokens.length === 0">&nbsp;</template></div></template><code v-else class="text-emerald-600">{{ formatParamValue(key, value) }}</code></pre>
              </div>
              <!-- 普通类型 -->
              <div v-else class="mb-3 last:mb-0">
                <span class="text-xs text-gray-400 min-w-[60px] shrink-0">{{ PARAM_LABELS[key] || key }}</span>
                <span class="text-sm text-gray-700">{{ formatParamValue(key, value) }}</span>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- 结果区 -->
      <div v-if="toolCall.status === 'running'" class="flex items-center gap-2 text-sm text-[#1f82d9] p-3 bg-[linear-gradient(135deg,rgba(31,130,217,0.08)_0%,rgba(31,130,217,0.02)_100%)] rounded-[10px]">
        <i class="fa-solid fa-spinner fa-spin text-base"></i>
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
          <!-- 文本结果 -->
          <div v-if="toolCall.result && parseResultContent(toolCall.result).length > 0" class="p-3 rounded-lg bg-white border border-black/[0.06] mb-2 last:mb-0">
            <template v-for="(part, idx) in parseResultContent(toolCall.result)" :key="idx">
              <pre v-if="part.type === 'sql'" class="max-h-64 overflow-auto px-3.5 py-3 rounded-lg bg-slate-50 border border-slate-200 text-xs font-mono leading-relaxed m-0 mb-2 last:mb-0"><div v-for="(lineTokens, lineIdx) in parseSqlTokens(formatSql(part.content))" :key="lineIdx" class="min-h-[1.4em]"><template v-for="(token, tokenIdx) in lineTokens" :key="tokenIdx"><span :class="getSqlTokenClass(token.type)">{{ token.value }}</span></template><template v-if="lineTokens.length === 0">&nbsp;</template></div></pre>
              <div v-else class="text-sm text-gray-600 whitespace-pre-wrap mb-2 last:mb-0">{{ part.content }}</div>
            </template>
          </div>
          <!-- 渲染结果（表格/图表） -->
          <div v-if="toolCall.renderId && toolCall.renderType === 'queryResult'">
            <AsyncQueryResult
              class="max-h-[46vh]"
              :result-data="toolCall.renderId"
              :sql="toolCall.args?.sql as string"
              :semantic-params="toolCall.semanticParams"
              :visualization="chartRecommendEnabled ? toolCall.visualization : undefined"
              :on-view-analysis="onViewAnalysis"
              :is-analysis-active="isAnalysisActive"
              :on-save="canSaveToPanel ? () => (showSaveModal = true) : undefined"
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
        <div class="text-sm text-red-600">{{ toolCall.result }}</div>
      </div>
    </div>

    <!-- 保存到看板弹窗 -->
    <SaveToPanelModal
      v-if="canSaveToPanel"
      v-model:visible="showSaveModal"
      :sql="toolCall.args?.sql as string"
      :datasource-id="datasourceId"
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
