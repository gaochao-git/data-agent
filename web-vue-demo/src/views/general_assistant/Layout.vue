<template>
  <div
    :style="backgroundStyle"
    class="flex h-screen overflow-hidden"
  >
    <!-- 主内容区域 -->
    <div class="flex-1 flex flex-col overflow-hidden">
      <!-- 公共顶部导航 -->
      <TopNavBar
        :title="currentConversationTitle"
        :is-collapsed="isHistoryCollapsed"
        :show-back-button="true"
        @back="handleBackToHome"
        @new-conversation="handleNewConversation"
        @toggle-collapse="isHistoryCollapsed = !isHistoryCollapsed"
        @toggle-history="toggleHistoryView"
      />

      <!-- 内容区域 -->
      <div class="flex-1 overflow-hidden">
        <!-- 历史列表视图（内嵌平铺） -->
        <div v-if="viewMode === 'history'" class="h-full bg-[#F7F9FA] overflow-y-auto">
          <HistoryPanel
            :history="props.conversationHistory"
            :is-loading="props.isHistoryLoading"
            @select-history="handleSelectHistory"
            @toggle-favorite="handleToggleFavorite"
            @delete-conversation="handleDeleteConversation"
            @new-conversation="handleNewConversation"
          />
        </div>

        <!-- 首页视图 -->
        <div v-else-if="viewMode === 'home'" class="h-full">
          <Conversation
            :messages="chatMessages"
            :input="chatInput"
            :is-streaming="isChatStreaming"
            :panel-height-class="h-full"
            :uploaded-files="uploadedFiles"
            :upload-enabled="true"
            :show-agent-grid="true"
            placeholder="请先选择智能体，或直接输入内容（支持粘贴图片）"
            @send-message="sendChatMessage"
            @stop-streaming="stopChatStreaming"
            @agent-select="handleAgentSelect"
            @file-upload="handleFileUpload"
            @remove-file="removeUploadedFile"
            @update-input="setChatInput"
            @start-chat="handleStartChat"
          />
        </div>

        <!-- 纯聊天视图（智能体入口点击后，无智能体卡片） -->
        <div v-else-if="viewMode === 'chat-only'" class="h-full">
          <Conversation
            :messages="chatMessages"
            :input="chatInput"
            :is-streaming="isChatStreaming"
            :panel-height-class="h-full"
            :uploaded-files="uploadedFiles"
            :upload-enabled="true"
            :show-agent-grid="false"
            placeholder="请输入问题"
            @send-message="sendChatMessage"
            @stop-streaming="stopChatStreaming"
            @file-upload="handleFileUpload"
            @remove-file="removeUploadedFile"
            @update-input="setChatInput"
          />
        </div>

        <!-- 专家视图 -->
        <div v-else-if="viewMode === 'agent'" class="flex items-stretch h-full">
          <!-- 对话区域 -->
          <div class="flex-1 overflow-hidden">
            <Conversation
              :messages="chatMessages"
              :input="chatInput"
              :is-streaming="isChatStreaming"
              :panel-height-class="h-full"
              :uploaded-files="uploadedFiles"
              :upload-enabled="activeAgent?.input_controls?.upload?.enabled ?? true"
              :show-agent-grid="false"
              :placeholder="activeAgent?.input_placeholder || '请输入问题'"
              :footer-tag="activeAgent?.footer_tag"
              @send-message="sendChatMessage"
              @stop-streaming="stopChatStreaming"
              @file-upload="handleFileUpload"
              @remove-file="removeUploadedFile"
              @update-input="setChatInput"
            />
          </div>

          <!-- 工作台区域（如果启用） -->
          <Workbench
            v-if="activeAgent?.workbench?.enabled && showWorkbench"
            :title="activeAgent?.name"
            :icon="activeAgent?.icon"
            :references="currentReferences"
            :trace-enabled="tracePanelEnabled"
            :steps="currentSteps"
            :suggestions="currentSuggestions"
            @action="handleWorkbenchAction"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { ChatMessage, UploadedFile, ConversationHistoryItem, Agent, ViewMode, ActiveAgentState } from './types'
import TopNavBar from './components/TopNavBar.vue'
import Conversation from './components/Conversation.vue'
import Workbench from './components/Workbench.vue'
import HistoryPanel from './components/HistoryPanel.vue'
import { AGENT_CONFIG } from './config'

interface Props {
  // 聊天相关
  chatMessages?: ChatMessage[]
  chatInput?: string
  isChatStreaming: boolean
  // 历史相关
  conversationHistory?: ConversationHistoryItem[]
  isHistoryLoading: boolean
  authError?: string | null
  // 文件上传
  uploadedFiles: UploadedFile[]
  // 会话相关
  chatThreadId?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  chatMessages: () => [],
  chatInput: '',
  isChatStreaming: false,
  conversationHistory: () => [],
  isHistoryLoading: false,
  uploadedFiles: () => []
})

const router = useRouter()

const emit = defineEmits<{
  'send-message': [message: string]
  'stop-streaming': []
  'set-chat-input': [value: string]
  'load-conversations': []
  'update-title': [id: string, title: string]
  'toggle-favorite': [id: string]
  'delete-conversation': [id: string]
  'reset-chat': []
  'switch-thread': [id: string]
  'file-upload': [files: FileList]
  'remove-file': [fileId: string]
  'agent-select': [agent: Agent]
  'conversation-id-change': [conversationId: string]
  'title-change': [title: string]
}>()

// 状态
const viewMode = ref<ViewMode>('home')
const activeAgent = ref<ActiveAgentState | null>(null)
const agentTitle = ref<string | null>(null)
const isHistoryCollapsed = ref(false)
const activeHistoryId = ref<string | null>(null)
const showWorkbench = ref(false)
const tracePanelEnabled = ref(false)
const currentReferences = ref<Array<{ id: string; title: string; summary: string }>>([])
const currentSteps = ref<Array<{ id: string; label: string; status: 'done' | 'thinking'; label_hidden?: boolean }>>([])
const currentSuggestions = ref<Array<{ tool_id: string; name: string }>>([])

// 根据路由设置视图模式
watch(() => router.currentRoute.value.path, (pathname) => {
  const routeMatches = [
    // 执法检查类路由
    { pattern: /^\/psoss\/ai\/(fact_analysis|report_jcbg|report_zgtzs|report_zjm|report_xxjb|inspection)/, agentId: 'inspection' },
    // 投诉举报路由
    { pattern: /^\/psoss\/ai\/complaint/, agentId: 'complaint_agent' },
    // 专家系统路由
    { pattern: /^\/psoss\/ai\/expert/, agentId: 'expert_agent' },
    // 智能问数路由
    { pattern: /^\/psoss\/ai\/general/, agentId: 'general' },
  ]

  for (const { pattern, agentId } of routeMatches) {
    if (pattern.test(pathname)) {
      viewMode.value = 'agent'
      return
    }
  }

  viewMode.value = 'home'
}, { immediate: true })

// 背景样式
const backgroundStyle = computed(() => {
  return viewMode.value === 'home'
    ? { background: 'linear-gradient(61deg, #F7EDEC 0%, #CCE6FF 100%)' }
    : { background: '#F7F9FA' }
})

// 当前会话标题
const currentConversationTitle = computed(() => {
  if (viewMode.value === 'agent' || viewMode.value === 'chat-only') {
    return agentTitle.value || activeAgent?.agentName || '新会话'
  }
  if (viewMode.value === 'history') {
    return '历史会话'
  }
  return props.chatThreadId
    ? props.conversationHistory.find(h => h.id === props.chatThreadId)?.title || '新会话'
    : '新会话'
})

// 处理切换历史列表视图
function toggleHistoryView() {
  if (viewMode.value === 'history') {
    viewMode.value = 'home'
  } else {
    viewMode.value = 'history'
  }
}

// 处理返回首页
function handleBackToHome() {
  viewMode.value = 'home'
  activeAgent.value = null
  agentTitle.value = null
  activeHistoryId.value = null
  emit('reset-chat')
  // 不跳转路由，保持当前路由不变
}

// 处理新建会话
function handleNewConversation() {
  if (viewMode.value === 'home') {
    emit('reset-chat')
  } else if (activeAgent.value) {
    activeAgent.value = {
      ...activeAgent.value,
      key: Date.now(),
      componentProps: {
        ...activeAgent.value.componentProps,
        initialThreadId: undefined
      }
    }
    agentTitle.value = null
    activeHistoryId.value = null
    // 更新 URL
    const agentId = activeAgent.value.componentProps?.agentId
    const config = AGENT_CONFIG[agentId]
    if (config?.route) {
      router.push(config.route)
    }
  }
}

// 处理选择历史
function handleSelectHistory(id: string) {
  const conversation = props.conversationHistory.find(h => h.id === id)
  const agentId = conversation?.agent_id
  const config = agentId ? AGENT_CONFIG[agentId] : null

  if (config?.route && agentId !== 'general') {
    viewMode.value = 'agent'
    activeAgent.value = {
      agentId: agentId,
      agentName: config.name,
      category: agentId.startsWith('report') ? 'inspection' : agentId.startsWith('complaint') ? 'complaint' : agentId === 'expert_agent' ? 'expert' : 'risk',
      componentProps: {
        agentId: agentId,
        initialThreadId: id
      }
    }
    activeHistoryId.value = id
    agentTitle.value = conversation?.title || null
    router.push(`${config.route}/${id}`)
  } else {
    viewMode.value = 'home'
    activeAgent.value = null
    emit('switch-thread', id)
  }

  emit('load-conversations')
}

// 处理智能体选择
function handleAgentSelect(agent: Agent) {
  if (agent.displayMode === 'inline') {
    activeAgent.value = {
      agentId: agent.id,
      agentName: agent.name,
      category: agent.category,
      componentProps: agent.componentProps,
      key: Date.now()
    }
    viewMode.value = 'agent'
    agentTitle.value = null
    activeHistoryId.value = null

    if (agent.navigatePath) {
      router.push(agent.navigatePath)
    }

    emit('agent-select', agent)
  } else if (agent.displayMode === 'navigate' && agent.navigatePath) {
    router.push(agent.navigatePath)
  }
}

// 处理开始新对话
function handleStartChat(agent: Agent) {
  // 在当前页面切换为纯聊天模式
  viewMode.value = 'chat-only'
  agentTitle.value = agent.name
  activeAgent.value = {
    agentId: agent.id,
    agentName: agent.name,
    category: agent.category,
    componentProps: { agentId: agent.id },
    key: Date.now()
  }
  emit('reset-chat')
}

// 处理更新标题
function handleUpdateTitle(id: string, newTitle: string) {
  emit('update-title', id, newTitle)
  emit('load-conversations')
}

// 处理切换收藏
function handleToggleFavorite(id: string) {
  emit('toggle-favorite', id)
  emit('load-conversations')
}

// 处理删除会话
function handleDeleteConversation(id: string) {
  emit('delete-conversation', id)
  emit('load-conversations')

  if (id === props.chatThreadId) {
    emit('reset-chat')
  }
}

// 处理发送消息
function sendChatMessage(message: string) {
  emit('send-message', message)
}

// 处理停止流式
function stopChatStreaming() {
  emit('stop-streaming')
}

// 处理设置输入
function setChatInput(value: string) {
  emit('set-chat-input', value)
}

// 处理文件上传
function handleFileUpload(files: FileList) {
  emit('file-upload', files)
}

// 处理移除文件
function removeUploadedFile(fileId: string) {
  emit('remove-file', fileId)
}

// 处理工作台操作
function handleWorkbenchAction(toolId: string) {
  // 这里可以根据 toolId 执行相应的操作
  console.log('Workbench action:', toolId)
}

// 初始加载历史记录
watch(() => props.conversationHistory.length, () => {
  // 可以在这里添加额外的逻辑
})
</script>

<style scoped>
/* 无需额外样式，使用 Tailwind CSS */
</style>