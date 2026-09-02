<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../utils'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
})

const renderedContent = computed(() => renderMarkdown(props.content))
</script>

<template>
  <div class="markdown-file-preview" v-html="renderedContent"></div>
</template>

<style scoped>
.markdown-file-preview {
  max-width: 100%;
  padding: 16px 18px;
  overflow: auto;
  color: #334155;
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.markdown-file-preview :deep(p) { margin: 0 0 10px; }
.markdown-file-preview :deep(p:last-child) { margin-bottom: 0; }

.markdown-file-preview :deep(h1),
.markdown-file-preview :deep(h2),
.markdown-file-preview :deep(h3),
.markdown-file-preview :deep(h4) {
  margin: 14px 0 8px;
  color: #1e293b;
  font-weight: 700;
  line-height: 1.35;
}

.markdown-file-preview :deep(h1) { font-size: 20px; }
.markdown-file-preview :deep(h2) { font-size: 17px; }
.markdown-file-preview :deep(h3) { font-size: 15px; }
.markdown-file-preview :deep(h4) { font-size: 14px; }

.markdown-file-preview :deep(ul),
.markdown-file-preview :deep(ol) {
  margin: 6px 0 10px;
  padding-left: 22px;
}

.markdown-file-preview :deep(li) { margin: 2px 0; }

.markdown-file-preview :deep(blockquote) {
  margin: 10px 0;
  padding: 6px 12px;
  border-left: 3px solid #cbd5e1;
  background: #f8fafc;
  color: #64748b;
}

.markdown-file-preview :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: #f1f5f9;
  color: #be123c;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}

.markdown-file-preview :deep(pre) {
  margin: 10px 0;
  padding: 12px;
  overflow-x: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
}

.markdown-file-preview :deep(pre code) {
  display: block;
  min-width: max-content;
  padding: 0;
  background: transparent;
  color: #334155;
  white-space: pre;
}

.markdown-file-preview :deep(table) {
  width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  table-layout: fixed;
}

.markdown-file-preview :deep(th),
.markdown-file-preview :deep(td) {
  padding: 7px 9px;
  border: 1px solid #d8dee8;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.markdown-file-preview :deep(th) {
  background: #f8fafc;
  color: #334155;
  font-weight: 700;
}

.markdown-file-preview :deep(hr) {
  margin: 14px 0;
  border: 0;
  border-top: 1px solid #e2e8f0;
}
</style>
