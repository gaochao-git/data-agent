<script setup lang="ts">
import { computed } from 'vue'
import { buildSandboxHtmlDocument } from '../utils/sandboxHtml'

/**
 * 自包含 HTML 页面的沙箱渲染容器（唯一权威入口）。
 *
 * iframe 只授予 allow-scripts：页面脚本无同源权限（opaque origin），
 * 配合 sandboxHtml.ts 注入的 CSP 实现无网络、无父域访问的纯展示沙箱。
 */
const props = defineProps({
  html: {
    type: String,
    default: '',
  },
})

const sandboxDocument = computed(() => buildSandboxHtmlDocument(props.html))
</script>

<template>
  <iframe
    v-if="sandboxDocument"
    class="block w-full border-0 bg-white"
    :srcdoc="sandboxDocument"
    sandbox="allow-scripts"
    referrerpolicy="no-referrer"
    title="HTML 页面预览"
  ></iframe>
</template>
