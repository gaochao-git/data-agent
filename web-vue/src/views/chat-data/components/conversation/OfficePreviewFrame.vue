<script setup lang="ts">
/**
 * Office 预览 iframe + 划选标注桥。
 *
 * OfficeCLI 输出的 HTML 每个段落/表格块带 data-path（如 /body/p[4]），
 * 这是比 Word 页码更稳定的定位锚点。iframe 是 sandbox=allow-scripts 的
 * 异源文档，父页面读不到选区，因此在 srcdoc 里注入一小段桥接脚本：
 * 选区落定后 postMessage 回父页面，父页面再浮出「引用选中内容」入口。
 *
 * 本组件只负责选区与评论采集，不直接改文件；点击“添加到对话”后由上层把
 * 结构化标注塞进输入框，随消息透传后端。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  html: { type: String, default: '' },
  /** 会话工作区相对路径（outputs/示例文档.docx）。 */
  filePath: { type: String, default: '' },
  /** 展示用文件名。 */
  fileName: { type: String, default: '' },
  fullscreen: { type: Boolean, default: false },
  /** 与下方对话输入框共享的草稿；浮层内编辑会直接更新同一条消息正文。 */
  draftText: { type: String, default: '' },
})

const emit = defineEmits<{
  add: [payload: { docPath: string; quote: string; comment: string }]
  'update:draftText': [value: string]
}>()

interface PreviewSelection {
  text: string
  docPath: string
  rect: { left: number; top: number; width: number; height: number }
}

const iframeRef = ref<HTMLIFrameElement | null>(null)
const selection = ref<PreviewSelection | null>(null)
const panelOpen = ref(false)
const position = ref({ left: 0, top: 0, above: false })

const draftTextValue = computed({
  get: () => String(props.draftText || ''),
  set: (value: string) => emit('update:draftText', value),
})

const BRIDGE_BODY = `
(function () {
  if (window.__chatdataAnnotationBridge) return;
  window.__chatdataAnnotationBridge = true;
  function post(payload) {
    try { parent.postMessage(Object.assign({ source: 'chatdata-office-preview' }, payload), '*'); }
    catch (error) { /* 父页面已卸载时忽略 */ }
  }
  function clear() { post({ type: 'selection-cleared' }); }
  document.addEventListener('mouseup', function (event) {
    var target = event.target;
    if (target && target.closest && target.closest('[data-chatdata-ignore]')) return;
    setTimeout(function () {
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) { clear(); return; }
      var text = String(sel.toString() || '').trim();
      if (!text) { clear(); return; }
      var node = sel.anchorNode;
      var el = node ? (node.nodeType === 1 ? node : node.parentElement) : null;
      var block = el && el.closest ? el.closest('[data-path]') : null;
      var rect = sel.getRangeAt(0).getBoundingClientRect();
      post({
        type: 'selection',
        text: text.slice(0, 1000),
        docPath: block ? (block.getAttribute('data-path') || '') : '',
        rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height }
      });
    }, 0);
  }, true);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') clear();
  });
  document.addEventListener('scroll', clear, true);
})();
`

const safeHtml = computed(() => {
  const html = String(props.html || '')
  if (!html || html.includes('data-chatdata-annotation-bridge')) return html
  const script = `<script data-chatdata-annotation-bridge="true">${BRIDGE_BODY}<${'/script'}>`
  return html.includes('</body>') ? html.replace('</body>', `${script}</body>`) : `${html}${script}`
})

const previewClass = computed(() => (
  props.fullscreen
    ? 'h-[calc(100vh-48px)] min-w-0 overflow-auto rounded-md border border-slate-100 bg-white'
    : 'min-h-0 flex-1 min-w-0 overflow-auto rounded-md border border-slate-100 bg-white'
))

const canSubmit = computed(() => Boolean(
  selection.value?.docPath && draftTextValue.value.trim(),
))

const reset = () => {
  selection.value = null
  panelOpen.value = false
}

const placeToolbar = (rect: PreviewSelection['rect']) => {
  const frame = iframeRef.value
  if (!frame) return
  const frameRect = frame.getBoundingClientRect()
  const left = Math.min(
    Math.max(frameRect.left + rect.left + rect.width / 2, 180),
    window.innerWidth - 180,
  )
  const top = frameRect.top + rect.top + rect.height + 10
  position.value = {
    left,
    top,
    above: top > window.innerHeight - 260,
  }
}

const handleMessage = (event: MessageEvent) => {
  const frame = iframeRef.value
  if (!frame || event.source !== frame.contentWindow) return
  const data = event.data as Record<string, unknown> | null
  if (!data || data.source !== 'chatdata-office-preview') return
  if (data.type === 'selection-cleared') {
    if (!panelOpen.value) reset()
    return
  }
  if (data.type !== 'selection') return
  const text = String(data.text || '').trim()
  if (!text) return
  const rect = (data.rect || {}) as Record<string, unknown>
  const next: PreviewSelection = {
    text,
    docPath: String(data.docPath || ''),
    rect: {
      left: Number(rect.left || 0),
      top: Number(rect.top || 0),
      width: Number(rect.width || 0),
      height: Number(rect.height || 0),
    },
  }
  selection.value = next
  panelOpen.value = false
  placeToolbar(next.rect)
}

const openPanel = () => {
  if (!selection.value?.docPath) return
  panelOpen.value = true
}

const submit = () => {
  const current = selection.value
  if (!current || !current.docPath) return
  const text = draftTextValue.value.trim()
  if (!text) return
  emit('add', {
    docPath: current.docPath,
    quote: current.text,
    comment: text,
  })
  reset()
}

const handleKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') reset()
}

// 切换文件或预览刷新后旧选区失效，避免把上一份文档的定位带到新文件
watch(() => [props.html, props.filePath], reset)

onMounted(() => {
  window.addEventListener('message', handleMessage)
  window.addEventListener('keydown', handleKeydown)
  window.addEventListener('resize', reset)
})

onBeforeUnmount(() => {
  window.removeEventListener('message', handleMessage)
  window.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('resize', reset)
})
</script>

<template>
  <div :class="previewClass">
    <iframe
      ref="iframeRef"
      :srcdoc="safeHtml"
      sandbox="allow-scripts"
      class="h-full min-w-0 w-full border-0 bg-white"
      title="Office 文件预览"
    ></iframe>

    <button
      v-if="selection && !panelOpen"
      type="button"
      class="fixed z-[var(--z-floating-tip)] flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 shadow-lg transition-colors hover:border-[#1677FF]/40 hover:text-[#1677FF]"
      :style="{ left: `${position.left}px`, top: `${position.top}px`, transform: 'translate(-50%, 0)' }"
      :title="selection.docPath ? `定位：${selection.docPath}` : '该位置没有可编辑的段落定位'"
      :disabled="!selection.docPath"
      @click="openPanel"
    >
      <i class="fa-solid fa-highlighter text-[11px]"></i>
      引用选中内容
    </button>

    <div
      v-if="selection && panelOpen"
      class="fixed z-[var(--z-floating-tip)] w-[340px] rounded-lg border border-slate-200 bg-white p-3 shadow-xl"
      :style="{
        left: `${position.left}px`,
        top: `${position.top}px`,
        transform: position.above ? 'translate(-50%, -100%) translateY(-8px)' : 'translate(-50%, 0)',
      }"
      data-doc-annotation-panel
    >
      <div class="mb-2 flex items-start justify-between gap-2">
        <div class="min-w-0">
          <div class="truncate text-xs font-medium text-slate-700">{{ fileName || filePath }}</div>
          <div class="mt-0.5 text-[10px] text-slate-400">{{ selection.docPath }}</div>
        </div>
        <button type="button" class="shrink-0 text-slate-400 hover:text-slate-600" title="关闭" @click="reset">
          <i class="fa-solid fa-xmark text-xs"></i>
        </button>
      </div>
      <div class="mb-2 max-h-[72px] overflow-y-auto rounded bg-slate-50 px-2 py-1.5 text-[11px] leading-relaxed text-slate-500">
        {{ selection.text }}
      </div>
      <textarea
        v-model="draftTextValue"
        rows="3"
        class="w-full resize-none rounded border border-slate-200 px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-[#1677FF]/50"
        placeholder="输入问题或修改要求，例如：这段话的依据是什么？/ 改成季度口径"
        @keydown.stop
      ></textarea>
      <div class="mt-1 text-[10px] text-slate-400">内容与下方对话输入框同步</div>
      <div class="mt-2 flex items-center justify-end gap-2">
        <button type="button" class="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-50" @click="reset">取消</button>
        <button
          type="button"
          class="rounded bg-[#1677FF] px-2.5 py-1 text-xs text-white disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!canSubmit"
          @click="submit"
        >添加到对话</button>
      </div>
    </div>
  </div>
</template>
