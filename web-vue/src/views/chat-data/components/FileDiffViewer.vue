<script setup lang="ts">
/**
 * file_diff 结果查看器：独立弹层，不改动工具结果的通用展示。
 * 两种对比模式可切换：
 * - merge：CodeMirror MergeView 双栏对比（按路径拉取两个文件全文，A 左 B 右）；
 *   拉取失败（文件已删/超限/无会话）时自动降级为 simple 并提示。
 * - simple：后端 unified diff 的红删绿增简洁渲染，不依赖额外请求。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { PropType } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView, lineNumbers } from '@codemirror/view'
import { MergeView } from '@codemirror/merge'
import { yaml } from '@codemirror/lang-yaml'
import { sql } from '@codemirror/lang-sql'
import { getConversationWorkspaceFileContent } from '../api'
import { splitFileDiffLines } from '../utils/toolDisplay'
import type { FileDiffPayload, FileDiffLine } from '../utils/toolDisplay'

type DiffMode = 'merge' | 'simple'

const props = defineProps({
  visible: { type: Boolean, default: false },
  payload: { type: Object as PropType<FileDiffPayload | null>, default: null },
  conversationId: { type: String, default: '' },
})

const emit = defineEmits(['update:visible'])

const mode = ref<DiffMode>('simple')
const mergeLoading = ref(false)
const mergeError = ref('')
const fileContents = ref<{ a: string; b: string } | null>(null)
const containerRef = ref<HTMLDivElement | null>(null)
let mergeView: MergeView | null = null

const diffLines = computed<FileDiffLine[]>(() => splitFileDiffLines(props.payload?.diff))
const fileNameOf = (path: string) => String(path || '').split('/').pop() || path

const close = () => emit('update:visible', false)

// ============ CodeMirror MergeView（并排对比） ============

const langExtension = (path: string) => {
  const lower = String(path || '').toLowerCase()
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return yaml()
  if (lower.endsWith('.sql')) return sql()
  return []
}

const diffTheme = EditorView.theme({
  '&': { fontSize: '12px', height: '58vh' },
  '.cm-scroller': {
    overflow: 'auto',
    fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
    lineHeight: '1.55',
  },
  '.cm-changedLine': { backgroundColor: 'rgba(255, 149, 0, 0.10)' },
  '.cm-insertedLine': { backgroundColor: 'rgba(52, 199, 89, 0.12)' },
  '.cm-deletedLine': { backgroundColor: 'rgba(255, 59, 48, 0.10)' },
  '.cm-merge-revert button': { color: 'transparent' },
  '.cm-collapsedPages, .cm-collapsedLines': { color: '#8E8E93', backgroundColor: '#FAFAFA' },
})

const destroyMergeView = () => {
  mergeView?.destroy()
  mergeView = null
}

const renderMergeView = async () => {
  destroyMergeView()
  // 容器由 v-else 分支渲染：先等 DOM patch 完成（loading 刚结束时容器尚未挂载）
  await nextTick()
  if (!containerRef.value || !fileContents.value || !props.payload) return
  const extensionsOf = (path: string) => [
    langExtension(path),
    diffTheme,
    // MergeView 是手动拼 extensions 构建的（无 basicSetup），行号必须显式加
    lineNumbers(),
    EditorView.lineWrapping,
    EditorView.editable.of(false),
    EditorState.readOnly.of(true),
  ]
  mergeView = new MergeView({
    a: { doc: fileContents.value.a, extensions: extensionsOf(props.payload.file_a) },
    b: { doc: fileContents.value.b, extensions: extensionsOf(props.payload.file_b) },
    parent: containerRef.value,
    highlightChanges: true,
    gutter: true,
    collapseUnchanged: { margin: 3, minSize: 8 },
  })
}

const loadFileContents = async () => {
  const payload = props.payload
  if (!payload || !props.conversationId) {
    mergeError.value = '缺少会话信息，无法读取文件全文'
    mode.value = 'simple'
    return
  }
  mergeLoading.value = true
  mergeError.value = ''
  try {
    const [left, right] = await Promise.all([
      getConversationWorkspaceFileContent(props.conversationId, payload.file_a),
      getConversationWorkspaceFileContent(props.conversationId, payload.file_b),
    ])
    fileContents.value = { a: left.data?.content ?? '', b: right.data?.content ?? '' }
    // 先结束 loading 让容器挂载，再构造 MergeView（渲染前会再等一个 nextTick）
    mergeLoading.value = false
    await renderMergeView()
  } catch {
    mergeError.value = '文件全文读取失败，已切换为简洁对比'
    mode.value = 'simple'
  } finally {
    mergeLoading.value = false
  }
}

watch(mode, (value) => {
  if (!props.visible) return
  if (value === 'merge') {
    if (fileContents.value) void renderMergeView()
    // 拉取失败后允许重试：loadFileContents 会清掉旧错误重新拉取
    else if (!mergeLoading.value) void loadFileContents()
  } else {
    destroyMergeView()
  }
})

// 打开时重置为简洁模式（零请求即可看）；关闭时回收 CodeMirror 实例
watch(() => props.visible, (visible) => {
  if (visible) {
    mode.value = 'simple'
    mergeError.value = ''
    fileContents.value = null
  } else {
    destroyMergeView()
  }
})

watch(() => [props.payload?.file_a, props.payload?.file_b], () => {
  fileContents.value = null
  destroyMergeView()
})

onBeforeUnmount(destroyMergeView)
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="对比文件"
    width="min(1100px, 94vw)"
    append-to-body
    destroy-on-close
    @update:model-value="emit('update:visible', $event)"
  >
    <div v-if="payload" class="min-w-0">
      <!-- 头部：文件对 + 统计 + 模式切换 -->
      <div class="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span class="min-w-0 max-w-[32%] truncate font-mono text-slate-500" :title="payload.file_a">{{ fileNameOf(payload.file_a) }}</span>
        <i class="fa-solid fa-arrow-right text-[10px] shrink-0 text-slate-300"></i>
        <span class="min-w-0 max-w-[32%] truncate font-mono text-slate-500" :title="payload.file_b">{{ fileNameOf(payload.file_b) }}</span>
        <span v-if="payload.status === 'identical'" class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-400">完全一致</span>
        <template v-else>
          <span class="shrink-0 font-mono text-[11px] font-medium text-emerald-500">+{{ payload.added_lines }}</span>
          <span class="shrink-0 font-mono text-[11px] font-medium text-rose-500">-{{ payload.removed_lines }}</span>
        </template>
        <div class="ml-auto flex items-center rounded-md border border-slate-200 bg-white p-0.5">
          <!-- chat-data 的 Tailwind 不引 preflight：裸 button 必须显式压掉 UA 默认灰底/黑边框（border-none + 两分支各给 bg） -->
          <button
            type="button"
            class="rounded border-none px-2 py-1 text-xs transition-colors"
            :class="mode === 'merge' ? 'bg-slate-700 text-white' : 'bg-transparent text-slate-500 hover:bg-slate-100'"
            @click="mode = 'merge'"
          >并排对比</button>
          <button
            type="button"
            class="rounded border-none px-2 py-1 text-xs transition-colors"
            :class="mode === 'simple' ? 'bg-slate-700 text-white' : 'bg-transparent text-slate-500 hover:bg-slate-100'"
            @click="mode = 'simple'"
          >简洁对比</button>
        </div>
      </div>

      <!-- 读取失败提示在两种模式下都可见（降级 simple 时说明原因） -->
        <div v-if="mergeError" class="mb-2 rounded bg-amber-50 px-2 py-1.5 text-xs text-amber-600">{{ mergeError }}</div>

        <!-- 并排对比（CodeMirror MergeView，A 左 B 右） -->
        <div v-if="mode === 'merge'">
          <div v-if="mergeLoading" class="flex h-40 items-center justify-center text-sm text-slate-400">
            <i class="fa-solid fa-spinner fa-spin mr-2"></i>正在读取文件全文...
          </div>
          <div v-else ref="containerRef" class="overflow-hidden rounded-lg border border-slate-200 bg-white"></div>
        </div>

      <!-- 简洁对比（unified diff 红删绿增）；行容器用 div 树，pre 内嵌块级元素在各宿主样式下行为不一致 -->
      <template v-else>
        <div v-if="diffLines.length > 0" class="max-h-[58vh] overflow-auto rounded-lg border border-slate-200 bg-slate-50 px-2 py-2 text-xs font-mono leading-relaxed text-slate-700">
          <div
            v-for="(line, idx) in diffLines"
            :key="idx"
            class="flex whitespace-pre"
            :class="{ 'bg-emerald-50 text-emerald-700': line.type === 'add', 'bg-rose-50 text-rose-700': line.type === 'del', 'text-sky-600': line.type === 'hunk', 'text-slate-400': line.type === 'meta', 'text-slate-600': line.type === 'context' }"
          >
            <span class="w-9 shrink-0 select-none pr-2 text-right text-slate-300">{{ line.oldNo ?? '' }}</span>
            <span class="w-9 shrink-0 select-none pr-2 text-right text-slate-300">{{ line.newNo ?? '' }}</span>
            <span class="min-w-0 flex-1">{{ line.content || ' ' }}</span>
          </div>
        </div>
        <div v-else class="flex h-40 items-center justify-center text-sm text-slate-400">两个文件内容完全一致</div>
      </template>
    </div>
  </el-dialog>
</template>
