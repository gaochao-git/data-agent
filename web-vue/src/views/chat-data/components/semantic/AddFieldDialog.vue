<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { PropType } from 'vue'
import { ElMessage } from 'element-plus'
import type { AddFieldForm, AddFieldTableOption, DerivedMetricFilter, SemanticMetric } from '../../types'

const props = defineProps({
  // 弹窗作用域：field=字段配置（原子指标/维度），metric=指标扩展（派生/复合指标）
  scope: { type: String as PropType<'field' | 'metric'>, default: 'field' },
  tables: { type: Array as PropType<AddFieldTableOption[]>, default: () => [] },
  // 原子指标候选：派生指标的基础指标 / 复合指标的公式成员（expression 用于展开预览）
  aggregateMetrics: { type: Array as PropType<Array<{ name: string; expression?: string }>>, default: () => [] },
  timeDimensions: { type: Array as PropType<Array<{ name: string }>>, default: () => [] },
  filterDimensions: { type: Array as PropType<Array<{ name: string }>>, default: () => [] },
  // 编辑中的派生/复合指标；null 表示新增
  editingDerived: { type: Object as PropType<SemanticMetric | null>, default: null },
})

const emit = defineEmits<{
  tableChange: []
  columnChange: []
  confirm: []
  confirmDerived: [metric: SemanticMetric]
}>()

const visible = defineModel<boolean>('visible', { default: false })
const form = defineModel<AddFieldForm>('form', { required: true })

// 类型切换说明：一行小字跟随所选类型，替代大卡片占位
const MODE_HINTS: Record<AddFieldForm['mode'], string> = {
  metric: '表字段的聚合计算，如 COUNT(category_id)',
  dimension: '表字段的文本 / 时间属性',
  derived: '基础指标 + 统计周期 + 修饰词过滤，查询时自动展开',
  composite: '多个原子指标的四则运算，除法自动加除零保护',
}

const MODE_LABELS: Record<AddFieldForm['mode'], string> = {
  metric: '原子指标',
  dimension: '维度',
  derived: '派生指标',
  composite: '复合指标',
}

const addFieldLabelMode = ref<'name' | 'description'>('name')
const addFieldTableKeyword = ref('')
const addFieldColumnKeyword = ref('')

// 作用域决定可选类型：字段配置只建物理字段，指标扩展只建口径指标
const availableModes = computed<Array<AddFieldForm['mode']>>(() =>
  props.scope === 'metric' ? ['derived', 'composite'] : ['metric', 'dimension']
)

const isDerivedMode = computed(() => form.value.mode === 'derived')
const isCompositeMode = computed(() => form.value.mode === 'composite')
const isSpecialMode = computed(() => isDerivedMode.value || isCompositeMode.value)
const modeHint = computed(() => MODE_HINTS[form.value.mode] || '')
// 标题与宽度固定，类型差异由左上角类型切换表达，避免切换类型时产生"换弹窗"的观感
const dialogTitle = computed(() => {
  const prefix = props.editingDerived ? '编辑' : '新增'
  return props.scope === 'metric' ? `${prefix}扩展指标` : `${prefix}字段`
})

// ============ 派生/复合指标公共状态 ============
const PERIOD_KIND_OPTIONS = [
  { value: 'today', label: '今日' },
  { value: 'yesterday', label: '昨日' },
  { value: 'last_n_days', label: '近N天' },
  { value: 'this_week', label: '本周' },
  { value: 'this_month', label: '本月' },
  { value: 'between', label: '自定义区间' },
]

// 操作符全集与后端 FILTER_OPERATORS 保持一致
const FILTER_OPERATOR_OPTIONS = [
  '=', '!=', '<>', '>', '<', '>=', '<=',
  'IN', 'NOT IN', 'LIKE', 'NOT LIKE', 'STARTS WITH', 'ENDS WITH',
  'BETWEEN', 'IS NULL', 'IS NOT NULL', 'EMPTY STRING',
]
const NO_VALUE_OPERATORS = new Set(['IS NULL', 'IS NOT NULL', 'EMPTY STRING'])
const LIST_OPERATORS = new Set(['IN', 'NOT IN'])
const VALUE_REQUIRED_OPERATORS = new Set(FILTER_OPERATOR_OPTIONS.filter(op => !NO_VALUE_OPERATORS.has(op)))

const derivedBaseMetric = ref('')
const derivedDescription = ref('')
const derivedUsePeriod = ref(false)
const derivedPeriodKind = ref('')
const derivedPeriodN = ref(7)
const derivedPeriodStart = ref('')
const derivedPeriodEnd = ref('')
const derivedTimeDimension = ref('')
const derivedFilters = ref<Array<{ dimension: string; operator: string; value: string }>>([])

// ============ 复合指标表单状态 ============
const compositeFormula = ref('')
const compositeDescription = ref('')
const FORMULA_REF_RE = /\{([^{}]+)\}/g
const FORMULA_ALLOWED_RE = /^[\d\s+\-*/().]*$/

const resetDerivedForm = () => {
  derivedBaseMetric.value = ''
  derivedDescription.value = ''
  derivedUsePeriod.value = false
  derivedPeriodKind.value = ''
  derivedPeriodN.value = 7
  derivedPeriodStart.value = ''
  derivedPeriodEnd.value = ''
  derivedTimeDimension.value = ''
  derivedFilters.value = []
}

const resetCompositeForm = () => {
  compositeFormula.value = ''
  compositeDescription.value = ''
}

const fillDerivedForm = (metric: SemanticMetric) => {
  derivedBaseMetric.value = metric.base_metric || ''
  derivedDescription.value = metric.description || ''
  const period = metric.period
  if (period && period.kind) {
    derivedUsePeriod.value = true
    derivedPeriodKind.value = period.kind
    derivedPeriodN.value = typeof period.n === 'number' && period.n > 0 ? period.n : 7
    derivedPeriodStart.value = period.start || ''
    derivedPeriodEnd.value = period.end || ''
    derivedTimeDimension.value = period.time_dimension || ''
  }
  derivedFilters.value = (metric.filters || []).map(item => ({
    dimension: item.dimension || '',
    operator: item.operator || '=',
    value: Array.isArray(item.value) ? item.value.join(',') : String(item.value ?? ''),
  }))
}

const fillCompositeForm = (metric: SemanticMetric) => {
  compositeFormula.value = metric.formula || ''
  compositeDescription.value = metric.description || ''
}

const initSpecialForm = () => {
  resetDerivedForm()
  resetCompositeForm()
  if (props.editingDerived) {
    if (props.editingDerived.metric_kind === 'composite') fillCompositeForm(props.editingDerived)
    else fillDerivedForm(props.editingDerived)
  }
}

watch(visible, value => {
  if (!value) return
  addFieldTableKeyword.value = ''
  addFieldColumnKeyword.value = ''
  // 当前类型不属于本作用域时（如从字段配置切到指标扩展入口），回落到第一个可选类型
  if (!availableModes.value.includes(form.value.mode)) {
    form.value.mode = availableModes.value[0]
  }
  if (isSpecialMode.value) initSpecialForm()
})

watch(isSpecialMode, value => {
  if (value && visible.value) initSpecialForm()
})

// 弹窗打开时切换类型：清空各模式专属脏状态，显示名称在物理字段与口径指标间不串用
watch(
  () => form.value.mode,
  (mode, prev) => {
    if (!visible.value || mode === prev) return
    resetDerivedForm()
    resetCompositeForm()
    form.value.displayName = ''
  }
)

const isPeriodKindWithN = computed(() => derivedPeriodKind.value === 'last_n_days')
const isPeriodKindBetween = computed(() => derivedPeriodKind.value === 'between')

const periodLabel = computed(() => {
  if (!derivedUsePeriod.value || !derivedPeriodKind.value) return ''
  if (isPeriodKindWithN.value) return `近${derivedPeriodN.value || 0}天`
  if (isPeriodKindBetween.value) return `${derivedPeriodStart.value || '?'}~${derivedPeriodEnd.value || '?'}`
  const option = PERIOD_KIND_OPTIONS.find(item => item.value === derivedPeriodKind.value)
  return option?.label || derivedPeriodKind.value
})

const definitionPreview = computed(() => {
  const parts: string[] = []
  if (derivedBaseMetric.value) parts.push(`指标[${derivedBaseMetric.value}]`)
  const periodText = periodLabel.value
  if (periodText) parts.push(periodText)
  derivedFilters.value.forEach(item => {
    const dimension = (item.dimension || '').trim()
    if (!dimension) return
    if (NO_VALUE_OPERATORS.has(item.operator)) {
      parts.push(`${dimension} ${item.operator}`)
      return
    }
    const valueText = item.value.includes('，') || item.value.includes(',') || LIST_OPERATORS.has(item.operator) || item.operator === 'BETWEEN'
      ? item.value.split(/[，,]/).map(entry => entry.trim()).filter(Boolean).join('、')
      : item.value
    parts.push(`${dimension}${item.operator}${valueText}`)
  })
  return parts.join(' · ')
})

const addFilterRow = () => {
  derivedFilters.value.push({ dimension: '', operator: '=', value: '' })
}

const removeFilterRow = (index: number) => {
  derivedFilters.value.splice(index, 1)
}

const parseFilterValue = (raw: string, operator: string): string | string[] => {
  const text = raw.trim()
  if (operator === 'BETWEEN' || LIST_OPERATORS.has(operator)) {
    return text.split(/[，,]/).map(entry => entry.trim()).filter(Boolean)
  }
  return text
}

const filterValuePlaceholder = (operator: string): string => {
  if (operator === 'BETWEEN') return '起止值用逗号分隔'
  if (LIST_OPERATORS.has(operator)) return '多个值用逗号分隔'
  return '筛选值'
}

const handleConfirmDerived = () => {
  if (props.aggregateMetrics.length === 0) {
    ElMessage.warning('请先配置至少一个聚合指标，再创建派生指标')
    return
  }
  const name = form.value.displayName.trim()
  if (!name) {
    ElMessage.warning('请填写显示名称')
    return
  }
  if (!derivedBaseMetric.value) {
    ElMessage.warning('请选择基础指标')
    return
  }
  let period: SemanticMetric['period']
  if (derivedUsePeriod.value && derivedPeriodKind.value) {
    if (!derivedTimeDimension.value) {
      ElMessage.warning('统计周期需要选择时间维度')
      return
    }
    if (derivedPeriodKind.value === 'last_n_days' && (!derivedPeriodN.value || derivedPeriodN.value < 1)) {
      ElMessage.warning('近N天的 N 必须是正整数')
      return
    }
    period = { kind: derivedPeriodKind.value } as NonNullable<SemanticMetric['period']>
    if (isPeriodKindWithN.value) period.n = derivedPeriodN.value
    if (isPeriodKindBetween.value) {
      period.start = derivedPeriodStart.value.trim()
      period.end = derivedPeriodEnd.value.trim()
      if (!period.start || !period.end) {
        ElMessage.warning('自定义区间需要完整的起止日期')
        return
      }
    }
    period.time_dimension = derivedTimeDimension.value
  } else {
    period = undefined
  }
  const filters: DerivedMetricFilter[] = []
  for (const item of derivedFilters.value) {
    const dimension = item.dimension.trim()
    const noValueNeeded = NO_VALUE_OPERATORS.has(item.operator)
    const valueRequired = VALUE_REQUIRED_OPERATORS.has(item.operator)
    if (!dimension && !noValueNeeded && !item.value.trim()) continue
    if (!dimension) {
      ElMessage.warning('修饰词过滤需要选择维度')
      return
    }
    if (!noValueNeeded && !item.value.trim()) {
      ElMessage.warning(`修饰词过滤 ${dimension} 缺少筛选值`)
      return
    }
    if (item.operator === 'BETWEEN') {
      const parts = parseFilterValue(item.value, item.operator) as string[]
      if (parts.length !== 2) {
        ElMessage.warning(`修饰词过滤 ${dimension} 的 BETWEEN 需要完整的起止值（逗号分隔）`)
        return
      }
      filters.push({ dimension, operator: item.operator, value: parts })
      continue
    }
    filters.push({ dimension, operator: item.operator, value: parseFilterValue(item.value, item.operator) })
  }
  const metric: SemanticMetric = {
    name,
    expression: '',
    table: '',
    description: derivedDescription.value.trim(),
    metric_kind: 'derived',
    base_metric: derivedBaseMetric.value,
    period,
    filters,
  }
  emit('confirmDerived', metric)
  visible.value = false
}

// ============ 复合指标：公式编辑 ============
const parseFormulaRefs = (formula: string): string[] => {
  const refs: string[] = []
  const seen = new Set<string>()
  for (const match of formula.matchAll(FORMULA_REF_RE)) {
    const name = (match[1] || '').trim()
    if (name && !seen.has(name)) {
      seen.add(name)
      refs.push(name)
    }
  }
  return refs
}

const formulaRefs = computed(() => parseFormulaRefs(compositeFormula.value))
const formulaRefMissing = (name: string) => !props.aggregateMetrics.some(metric => metric.name === name)
const formulaCharsValid = computed(() => FORMULA_ALLOWED_RE.test(compositeFormula.value.replace(/\{[^{}]+\}/g, '')))

// ============ 复合指标：@ 引用补全 ============
const formulaInputRef = ref<{ textarea?: HTMLTextAreaElement } | null>(null)
const mentionOpen = ref(false)
const mentionQuery = ref('')
const mentionIndex = ref(0)

const mentionCandidates = computed(() => {
  const query = mentionQuery.value.trim().toLowerCase()
  return props.aggregateMetrics
    .filter(metric => !query || metric.name.toLowerCase().includes(query))
    .slice(0, 8)
})

const closeMention = () => {
  mentionOpen.value = false
  mentionQuery.value = ''
  mentionIndex.value = 0
}

// 光标前文本须形如 "行首/空白/运算符 + @ + 无空白过滤词"，避免误触
const matchMentionTrigger = (before: string): RegExpMatchArray | null => before.match(/(^|[\s+\-*/(])@([^\s@]*)$/)

const handleFormulaInput = () => {
  const input = formulaInputRef.value?.textarea
  if (!input) return
  const caret = input.selectionStart ?? 0
  const match = matchMentionTrigger(compositeFormula.value.slice(0, caret))
  if (!match) {
    closeMention()
    return
  }
  mentionQuery.value = match[2] || ''
  mentionIndex.value = 0
  mentionOpen.value = props.aggregateMetrics.length > 0
}

const applyMention = (name: string) => {
  const input = formulaInputRef.value?.textarea
  if (!input) {
    closeMention()
    return
  }
  const caret = input.selectionStart ?? 0
  const before = compositeFormula.value.slice(0, caret)
  const trigger = before.match(/@([^\s@]*)$/)
  if (!trigger) {
    closeMention()
    return
  }
  const start = caret - trigger[0].length
  const inserted = `{${name}} `
  compositeFormula.value = compositeFormula.value.slice(0, start) + inserted + compositeFormula.value.slice(caret)
  closeMention()
  nextTick(() => {
    input.focus()
    input.setSelectionRange(start + inserted.length, start + inserted.length)
  })
}

const handleFormulaKeydown = (event: KeyboardEvent) => {
  if (!mentionOpen.value) return
  const total = mentionCandidates.value.length
  if (event.key === 'ArrowDown' && total > 0) {
    event.preventDefault()
    mentionIndex.value = (mentionIndex.value + 1) % total
  } else if (event.key === 'ArrowUp' && total > 0) {
    event.preventDefault()
    mentionIndex.value = (mentionIndex.value - 1 + total) % total
  } else if (event.key === 'Enter') {
    const candidate = mentionCandidates.value[mentionIndex.value]
    if (candidate) {
      event.preventDefault()
      applyMention(candidate.name)
    }
  } else if (event.key === 'Escape') {
    closeMention()
  }
}

const compositePreview = computed(() => {
  const formula = compositeFormula.value.trim()
  if (!formula) return ''
  // 代入成员聚合表达式；除号后的引用模拟后端的 NULLIF 除零保护，缺失引用保留 {名称} 以便标红
  return formula.replace(FORMULA_REF_RE, (match, ref: string, offset: number) => {
    const name = (ref || '').trim()
    const member = props.aggregateMetrics.find(metric => metric.name === name)
    if (!member?.expression) return match
    if (formula.slice(0, offset).trimEnd().endsWith('/')) {
      return `NULLIF(${member.expression}, 0)`
    }
    return member.expression
  })
})

const handleConfirmComposite = () => {
  if (props.aggregateMetrics.length === 0) {
    ElMessage.warning('请先配置至少一个聚合指标，再创建复合指标')
    return
  }
  const name = form.value.displayName.trim()
  if (!name) {
    ElMessage.warning('请填写显示名称')
    return
  }
  const formula = compositeFormula.value.trim()
  if (!formula) {
    ElMessage.warning('请填写公式，用 {指标名} 引用原子指标')
    return
  }
  const refs = parseFormulaRefs(formula)
  if (refs.length === 0) {
    ElMessage.warning('公式中没有任何 {指标} 引用')
    return
  }
  const missingRef = refs.find(ref => formulaRefMissing(ref))
  if (missingRef) {
    ElMessage.warning(`公式引用的指标不存在：${missingRef}`)
    return
  }
  if (!formulaCharsValid.value) {
    ElMessage.warning('公式仅支持四则运算、括号、数字和 {指标} 引用')
    return
  }
  const metric: SemanticMetric = {
    name,
    expression: '',
    table: '',
    description: compositeDescription.value.trim(),
    metric_kind: 'composite',
    formula,
  }
  emit('confirmDerived', metric)
  visible.value = false
}

const handleConfirm = () => {
  if (isDerivedMode.value) {
    handleConfirmDerived()
    return
  }
  if (isCompositeMode.value) {
    handleConfirmComposite()
    return
  }
  emit('confirm')
}

// ============ 物理字段模式（既有逻辑） ============
const addFieldColumns = computed(() => {
  const entry = props.tables.find(t => t.alias === form.value.tableAlias)
  return entry?.columns || []
})

const filteredAddFieldTables = computed(() => {
  const keyword = addFieldTableKeyword.value.trim().toLowerCase()
  if (!keyword) return props.tables
  return props.tables.filter(entry =>
    entry.alias.toLowerCase().includes(keyword) ||
    entry.table.toLowerCase().includes(keyword) ||
    (entry.description || '').toLowerCase().includes(keyword)
  )
})

const filteredAddFieldColumns = computed(() => {
  const keyword = addFieldColumnKeyword.value.trim().toLowerCase()
  if (!keyword) return addFieldColumns.value
  return addFieldColumns.value.filter(c =>
    c.name.toLowerCase().includes(keyword) ||
    (c.description || '').toLowerCase().includes(keyword)
  )
})

const getAddFieldTableLabel = (entry: AddFieldTableOption) => {
  const base = addFieldLabelMode.value === 'description' ? (entry.description || entry.table) : entry.alias
  return entry.alias === entry.table ? base : `${base}（${entry.table}）`
}

const getAddFieldColumnLabel = (col: { name: string; description?: string }) => {
  if (addFieldLabelMode.value === 'description') return col.description || col.name
  return col.name
}

const handleAddFieldTableFilter = (keyword: string) => {
  addFieldTableKeyword.value = keyword
}

const handleAddFieldColumnFilter = (keyword: string) => {
  addFieldColumnKeyword.value = keyword
}

const handleTableChange = () => {
  addFieldColumnKeyword.value = ''
  emit('tableChange')
}
</script>

<template>
  <el-dialog v-model="visible" width="640px" :title="dialogTitle">
    <div class="space-y-3">
      <!-- ① 首行：左侧类型切换（按作用域过滤）+ 右侧中英文切换（仅物理字段模式需要） -->
      <div class="flex items-center justify-between">
        <el-radio-group v-model="form.mode" size="small">
          <el-radio-button v-for="mode in availableModes" :key="mode" :value="mode">{{ MODE_LABELS[mode] }}</el-radio-button>
        </el-radio-group>
        <el-radio-group v-if="!isSpecialMode" v-model="addFieldLabelMode" size="small">
          <el-radio-button value="description">中文</el-radio-button>
          <el-radio-button value="name">英文</el-radio-button>
        </el-radio-group>
      </div>
      <div class="text-[11px] text-gray-400 leading-4">{{ modeHint }}</div>
      <!-- ② 配置区：按所选类型渲染 -->
      <template v-if="!isSpecialMode">
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="text-xs text-gray-600 mb-1">表</div>
            <el-select
              v-model="form.tableAlias"
              filterable
              placeholder="请选择表"
              :filter-method="handleAddFieldTableFilter"
              @change="handleTableChange"
            >
              <el-option
                v-for="entry in filteredAddFieldTables"
                :key="entry.alias"
                :label="getAddFieldTableLabel(entry)"
                :value="entry.alias"
              />
            </el-select>
          </div>
          <div>
            <div class="text-xs text-gray-600 mb-1">字段</div>
            <el-select
              v-model="form.column"
              filterable
              placeholder="请选择字段"
              :filter-method="handleAddFieldColumnFilter"
              @change="emit('columnChange')"
            >
              <el-option
                v-for="c in filteredAddFieldColumns"
                :key="c.name"
                :label="getAddFieldColumnLabel(c)"
                :value="c.name"
              />
            </el-select>
          </div>
        </div>
        <div>
          <div class="text-xs text-gray-600 mb-1">字段名</div>
          <el-input v-model="form.fieldName" placeholder="请输入逻辑字段名（同表内唯一）" />
        </div>
      </template>
      <template v-else-if="isDerivedMode">
        <div>
          <div class="text-xs text-gray-600 mb-1">基础指标</div>
          <el-select v-model="derivedBaseMetric" filterable placeholder="请选择聚合指标">
            <el-option
              v-for="metric in aggregateMetrics"
              :key="metric.name"
              :label="metric.name"
              :value="metric.name"
            />
          </el-select>
        </div>
        <div class="rounded-md border border-gray-200 p-3 space-y-3">
          <div class="flex items-center justify-between">
            <div class="text-xs font-medium text-gray-600">统计周期</div>
            <el-switch v-model="derivedUsePeriod" size="small" />
          </div>
          <template v-if="derivedUsePeriod">
            <div class="grid grid-cols-2 gap-3">
              <div>
                <div class="text-xs text-gray-600 mb-1">周期类型</div>
                <el-select v-model="derivedPeriodKind" placeholder="请选择周期类型">
                  <el-option
                    v-for="option in PERIOD_KIND_OPTIONS"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
              </div>
              <div>
                <div class="text-xs text-gray-600 mb-1">时间维度</div>
                <el-select v-model="derivedTimeDimension" filterable placeholder="请选择时间维度">
                  <el-option
                    v-for="dimension in timeDimensions"
                    :key="dimension.name"
                    :label="dimension.name"
                    :value="dimension.name"
                  />
                  <template #empty>
                    <div class="px-3 py-2 text-xs text-gray-400">当前模型没有日期/时间类型的维度，请先添加（如 created_at）</div>
                  </template>
                </el-select>
              </div>
            </div>
            <div v-if="isPeriodKindWithN" class="w-1/2">
              <div class="text-xs text-gray-600 mb-1">N（天数）</div>
              <el-input-number v-model="derivedPeriodN" :min="1" :max="3650" class="!w-full" />
            </div>
            <div v-if="isPeriodKindBetween" class="grid grid-cols-2 gap-3">
              <div>
                <div class="text-xs text-gray-600 mb-1">开始日期</div>
                <el-input v-model="derivedPeriodStart" placeholder="2026-08-01" />
              </div>
              <div>
                <div class="text-xs text-gray-600 mb-1">结束日期</div>
                <el-input v-model="derivedPeriodEnd" placeholder="2026-08-31" />
              </div>
            </div>
          </template>
        </div>
        <div class="rounded-md border border-gray-200 p-3 space-y-2">
          <div class="flex items-center justify-between">
            <div class="text-xs font-medium text-gray-600">修饰词过滤</div>
            <el-button size="small" @click="addFilterRow">
              <i class="fa-solid fa-plus mr-1"></i>添加过滤
            </el-button>
          </div>
          <div v-if="derivedFilters.length === 0" class="text-xs text-gray-400">暂无过滤条件。</div>
          <div v-for="(item, index) in derivedFilters" :key="index" class="flex items-center gap-2">
            <el-select v-model="item.dimension" filterable placeholder="维度" class="!w-40">
              <el-option
                v-for="dimension in filterDimensions"
                :key="dimension.name"
                :label="dimension.name"
                :value="dimension.name"
              />
            </el-select>
            <el-select v-model="item.operator" class="!w-28">
              <el-option
                v-for="operator in FILTER_OPERATOR_OPTIONS"
                :key="operator"
                :label="operator"
                :value="operator"
              />
            </el-select>
            <el-input
              v-if="!NO_VALUE_OPERATORS.has(item.operator)"
              v-model="item.value"
              class="flex-1"
              :placeholder="filterValuePlaceholder(item.operator)"
            />
            <el-button size="small" type="danger" plain @click="removeFilterRow(index)">
              <i class="fa-solid fa-trash-can"></i>
            </el-button>
          </div>
        </div>
      </template>
      <template v-else>
        <div class="rounded-md border border-gray-200 p-3 space-y-2">
          <div class="text-xs font-medium text-gray-600">公式</div>
          <div class="relative">
            <el-input
              ref="formulaInputRef"
              v-model="compositeFormula"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 4 }"
              placeholder="{销售额} / {订单数}，输入 @ 引用指标"
              @input="handleFormulaInput"
              @keydown="handleFormulaKeydown"
              @blur="closeMention"
            />
            <div
              v-if="mentionOpen"
              class="absolute left-0 right-0 top-full z-10 mt-1 max-h-56 overflow-auto rounded-md border border-gray-200 bg-white shadow-lg"
            >
              <button
                v-for="(metric, index) in mentionCandidates"
                :key="metric.name"
                type="button"
                class="w-full px-3 py-1.5 text-left text-xs"
                :class="index === mentionIndex ? 'bg-blue-50 text-blue-600' : 'text-gray-700 hover:bg-blue-50'"
                @mousedown.prevent
                @click="applyMention(metric.name)"
              >
                {{ metric.name }}
              </button>
              <div v-if="mentionCandidates.length === 0" class="px-3 py-2 text-xs text-gray-400">无匹配指标</div>
            </div>
          </div>
          <div class="text-xs text-gray-400">输入 @ 引用指标；支持 + - * / ( ) 和数字；除法自动加除零保护</div>
          <div v-if="formulaRefs.length" class="flex flex-wrap gap-1.5">
            <el-tag v-for="ref in formulaRefs" :key="ref" size="small" :type="formulaRefMissing(ref) ? 'danger' : 'info'">
              {{ ref }}{{ formulaRefMissing(ref) ? '（不存在）' : '' }}
            </el-tag>
          </div>
        </div>
      </template>
      <!-- ③ 通用信息（归底） -->
      <div>
        <div class="text-xs text-gray-600 mb-1">显示名称</div>
        <el-input v-model="form.displayName" placeholder="请输入显示名称（同语义模型内唯一）" />
      </div>
      <div v-if="isDerivedMode || isCompositeMode">
        <div class="text-xs text-gray-600 mb-1">字段备注</div>
        <el-input v-if="isDerivedMode" v-model="derivedDescription" placeholder="补充业务含义" />
        <el-input v-else v-model="compositeDescription" placeholder="补充业务含义" />
      </div>
      <div v-if="isDerivedMode && definitionPreview" class="rounded bg-blue-50 px-3 py-2 text-xs text-blue-700">
        口径预览：{{ definitionPreview }}
      </div>
      <div v-if="isCompositeMode && compositePreview" class="rounded bg-blue-50 px-3 py-2 text-xs text-blue-700">
        <div class="mb-0.5 text-blue-600/70">展开预览</div>
        {{ compositePreview }}
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="handleConfirm">确定</el-button>
      </div>
    </template>
  </el-dialog>
</template>
