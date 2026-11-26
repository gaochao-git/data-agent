<script setup lang="ts">
/**
 * 只读代码文件预览（CodeMirror 语法高亮），接入工作区文件与写入工具的内容预览。
 * 与 SqlEditor.vue 共用 CodeMirror 6 底座；语法高亮直接用 vue-codemirror 注入的
 * basicSetup 内置 defaultHighlightStyle（与 SQL 编辑器同一套默认配色），
 * 不另挂自定义 HighlightStyle。纯展示：不可编辑、不自动聚焦、不挂 updateListener。
 * 后续 .json/.sql 等只读预览在此扩展语言包即可，不要另起新组件。
 * 长文件由父级 capWorkspaceTextPreview（1MB 上限）控制，此处不重复截断。
 */
import { computed } from 'vue'
import { Codemirror } from 'vue-codemirror'
import { EditorState, type Extension } from '@codemirror/state'
import { EditorView, lineNumbers } from '@codemirror/view'
import { yaml } from '@codemirror/lang-yaml'
import { sql } from '@codemirror/lang-sql'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
  /** 已安装语言包：yaml/sql；其余按纯文本展示，行号与只读能力保持一致。 */
  language: {
    type: String,
    default: 'yaml',
  },
})

// 结构性主题（背景/行号/选中态），不含 token 配色——语法配色由 basicSetup 的
// defaultHighlightStyle 提供，避免两套高亮样式叠加后优先级互相踩。
const codeViewTheme = EditorView.theme({
  '&': {
    backgroundColor: '#FFFFFF',
    color: '#1D1D1F',
    height: '100%',
    fontSize: '12px',
  },
  '&.cm-editor': {
    height: '100%',
  },
  '&.cm-editor.cm-focused': {
    outline: 'none',
  },
  '.cm-scroller': {
    overflow: 'auto',
    height: '100%',
    fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
    lineHeight: '1.6',
  },
  '.cm-content': {
    padding: '8px 0',
  },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': {
    backgroundColor: 'rgba(22, 119, 255, 0.12)',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(22, 119, 255, 0.04)',
  },
  '.cm-gutters': {
    backgroundColor: '#FAFAFA',
    color: '#AEAEB2',
    border: 'none',
    borderRight: '1px solid rgba(0, 0, 0, 0.06)',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 10px 0 12px',
    minWidth: '32px',
    fontSize: '11px',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'transparent',
    color: '#1677FF',
  },
})

const languageExtension = (): Extension => {
  if (props.language === 'sql') return sql()
  if (props.language === 'yaml') return yaml()
  return []
}

const extensions = computed<Extension[]>(() => [
  languageExtension(),
  lineNumbers(),
  EditorState.readOnly.of(true),
  EditorView.editable.of(false),
  codeViewTheme,
])
</script>

<template>
  <div class="code-file-preview min-w-0 overflow-hidden rounded-md border border-slate-100 bg-white">
    <Codemirror
      :model-value="props.content"
      :extensions="extensions"
      :editable="false"
      :autofocus="false"
      :style="{ height: '100%' }"
    />
  </div>
</template>

<style scoped>
.code-file-preview :deep(.cm-editor) {
  min-width: 0;
}
</style>
