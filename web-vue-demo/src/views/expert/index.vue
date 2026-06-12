<script setup lang="ts">
/**
 * 法律助手主页面
 * 提供法律咨询和法规查询功能
 *
 * 使用 UnifiedEmbeddedContent 组件，该组件已集成后端连接逻辑
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import UnifiedEmbeddedContent from './components/UnifiedEmbeddedContent.vue'
import { expertAgent } from './config'

const route = useRoute()

// 从路由参数中获取会话ID
const initialThreadId = computed(() => route.params.conversationId as string | undefined)
</script>

<template>
  <div class="expert-page">
    <UnifiedEmbeddedContent
      :agent-id="expertAgent.id"
      :initial-thread-id="initialThreadId"
      :input-actions="expertAgent.input_actions"
      :input-placeholder="expertAgent.input_placeholder"
      :footer-tag="expertAgent.footer_tag"
      :assistant-role-label="expertAgent.assistant_role_label"
      :opening-message="expertAgent.opening_message"
      :workbench="expertAgent.workbench"
    />
  </div>
</template>

<style scoped>
.expert-page {
  height: 100%;
  width: 100%;
}
</style>
