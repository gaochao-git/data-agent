<script setup lang="ts">
/**
 * Ossie 导入冲突差异视图：CodeMirror MergeView 双栏对比（左=现有配置，右=导入内容），只读。
 * 两侧内容用确定性 YAML 序列化成文本后交给 MergeView 逐行对比，
 * 长段未变更区域自动折叠（collapseUnchanged），便于聚焦真正的差异。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { MergeView } from '@codemirror/merge'
import { yaml } from '@codemirror/lang-yaml'
import { toYaml } from '../../utils/yamlDump'
import type { OssieImportModelPreview } from '../../api'
import type { SemanticConfigItem } from '../../types'

const props = defineProps<{
  existing: SemanticConfigItem
  imported: OssieImportModelPreview
}>()

const containerRef = ref<HTMLDivElement | null>(null)
let mergeView: MergeView | null = null

/** 键输出顺序：两侧配置的存储键序不同，按此统一排序让 diff 逐行对齐 */
const KEY_ORDER = [
  'name', 'field_name', 'metric_kind', 'table', 'table_alias', 'column',
  'expression', 'formula', 'base_metric', 'period', 'filters',
  'left_table', 'left_column', 'right_table', 'right_column', 'join_type', 'relationship_type',
  'logical_type', 'data_type', 'description', 'business_notes',
]

/** 单条目归一化：剔除空值与「等于默认值的冗余键」（field_name==column、table_alias==table），按 KEY_ORDER 排键对齐 diff */
const normalizeItem = (item: Record<string, any>): Record<string, any> => {
  const column = item.column != null ? String(item.column) : ''
  const table = item.table != null ? String(item.table) : ''
  const entries = Object.entries(item)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .filter(([key, v]) => {
      if (key === 'field_name') return String(v) !== column
      if (key === 'table_alias') return String(v) !== table
      return true
    })
    .sort((a, b) => {
      const ia = KEY_ORDER.indexOf(a[0])
      const ib = KEY_ORDER.indexOf(b[0])
      return (ia === -1 ? KEY_ORDER.length : ia) - (ib === -1 ? KEY_ORDER.length : ib)
    })
  return Object.fromEntries(entries)
}

const normalizeList = (list: unknown) =>
  Array.isArray(list) ? list.map(item => (item && typeof item === 'object' ? normalizeItem(item) : item)) : list

/** 只取内容字段参与对比：id/启用状态/创建人等运行时字段不进入 diff */
const contentOf = (item: Record<string, any>) => ({
  name: item.name,
  description: item.description || '',
  business_notes: item.business_notes || '',
  dimensions: normalizeList(item.dimensions || []),
  metrics: normalizeList(item.metrics || []),
  relationships: normalizeList(item.relationships || []),
})

const diffTheme = EditorView.theme({
  '&': {
    fontSize: '11px',
    height: '260px',
  },
  '.cm-scroller': {
    overflow: 'auto',
    fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
    lineHeight: '1.5',
  },
  '.cm-changedLine': {
    backgroundColor: 'rgba(255, 149, 0, 0.10)',
  },
  '.cm-insertedLine': {
    backgroundColor: 'rgba(52, 199, 89, 0.10)',
  },
  '.cm-deletedLine': {
    backgroundColor: 'rgba(255, 59, 48, 0.10)',
  },
  '.cm-merge-revert button': {
    color: 'transparent',
  },
  '.cm-collapsedPages, .cm-collapsedLines': {
    color: '#8E8E93',
    backgroundColor: '#FAFAFA',
  },
})

onMounted(() => {
  if (!containerRef.value) return
  const baseExtensions = [
    yaml(),
    diffTheme,
    EditorView.lineWrapping,
    EditorView.editable.of(false),
    EditorState.readOnly.of(true),
  ]
  mergeView = new MergeView({
    a: { doc: toYaml(contentOf(props.existing)), extensions: baseExtensions },
    b: { doc: toYaml(contentOf(props.imported)), extensions: baseExtensions },
    parent: containerRef.value,
    highlightChanges: true,
    gutter: true,
    collapseUnchanged: { margin: 3, minSize: 8 },
  })
})

onBeforeUnmount(() => {
  mergeView?.destroy()
  mergeView = null
})
</script>

<template>
  <div class="mt-1 overflow-hidden rounded border border-gray-100">
    <div class="flex items-center border-b border-gray-100 bg-gray-50 px-2 py-1 text-[10px] text-gray-400">
      <span class="flex-1">现有配置（{{ existing.id }}）</span>
      <span class="flex-1">导入内容</span>
    </div>
    <div ref="containerRef"></div>
  </div>
</template>
