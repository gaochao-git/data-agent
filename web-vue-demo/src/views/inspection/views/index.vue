<template>
  <div class="inspection-page">
    <EmbeddedLayout
      :agent-id="agentId"
      :initial-thread-id="threadId"
      @conversation-id-change="handleConversationIdChange"
      @history-refresh="handleHistoryRefresh"
      @title-change="handleTitleChange"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import EmbeddedLayout from '../EmbeddedLayout.vue'

const route = useRoute()
const router = useRouter()

// 从路由参数或查询参数获取配置
const agentId = computed(() => {
  const routeAgentId = route.params.agentId as string
  if (routeAgentId) return routeAgentId

  // 从查询参数获取
  const queryAgentId = route.query.agentId as string
  if (queryAgentId) return queryAgentId

  // 默认值
  return 'report_jcbg'
})

const threadId = computed(() => {
  return (route.query.threadId as string) || ''
})

// 处理会话ID变化
const handleConversationIdChange = (id: string) => {
  // 更新URL查询参数，但不刷新页面
  router.replace({
    query: {
      ...route.query,
      threadId: id || undefined
    }
  })
}

// 处理历史记录刷新
const handleHistoryRefresh = () => {
  // 可以在这里触发父组件的历史记录刷新
  console.log('History refresh triggered')
}

// 处理标题变化
const handleTitleChange = (title: string) => {
  // 可以更新页面标题或其他状态
  document.title = title ? `${title} - 执法检查` : '执法检查'
}

onMounted(() => {
  // 初始化页面标题
  document.title = '执法检查'
})
</script>

<style scoped>
.inspection-page {
  height: 100%;
  width: 100%;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
</style>
