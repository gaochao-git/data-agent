<template>
  <el-drawer
    v-model="drawerVisible"
    title="在线聊天"
    direction="rtl"
    size="23%"
    class="chat-drawer"
    :with-header="true"
    :modal="true"
    :lock-scroll="false"
    :trap-focus="false"
    :teleported="true"
    :close-on-click-modal="true"
    modal-class="chat-drawer-overlay"
    @close="handleClose"
  >
    <template #header>
      <div class="drawer-header">
        <div class="header-left">
          <el-avatar
            size="small"
            :src="avatarUrl"
            class="chat-avatar"
          />
          <span class="chat-title">智能助手</span>
        </div>
        <div class="header-right">
          <el-tag size="small" type="success" effect="plain">在线</el-tag>
          <el-tag size="small" type="info" effect="plain">{{ currentTime }}</el-tag>
        </div>
      </div>
    </template>

    <div class="chat-container">
      <!-- 消息列表 -->
      <div
        class="message-list"
        ref="messageListRef"
      >
        <!-- 时间分隔线 -->
        <div class="time-divider">
          <span>{{ currentDate }}</span>
        </div>

        <!-- 消息项 -->
        <div
          v-for="message in messages"
          :key="message.id"
          :class="['message-item', message.type]"
        >
          <!-- 系统消息 -->
          <div v-if="message.type === 'system'" class="system-message">
            <div class="system-content">
              <el-icon class="system-icon"><InfoFilled /></el-icon>
              <span>{{ message.content }}</span>
            </div>
            <div class="message-time">{{ message.time }}</div>
          </div>

          <!-- 用户消息 -->
          <div v-else-if="message.type === 'user'" class="user-message">
            <div class="message-content-wrapper">
              <div class="message-content user-content">
                {{ message.content }}
              </div>
              <div class="message-info">
                <span class="message-time">{{ message.time }}</span>
                <el-avatar
                  size="small"
                  src="https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png"
                  class="user-avatar"
                />
              </div>
            </div>
          </div>

          <!-- 客服消息 -->
          <div v-else class="bot-message">
            <div class="message-content-wrapper">
              <el-avatar
                size="small"
                :src="avatarUrl"
                class="bot-avatar"
              />
              <div class="message-content bot-content">
                <div class="message-header">
                  <span class="sender-name">{{ message.sender }}</span>
                </div>
                <div class="message-text">{{ message.content }}</div>
              </div>
            </div>
            <div class="message-time bot-time">{{ message.time }}</div>
          </div>
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="input-area">
        <el-input
          v-model="inputMessage"
          type="textarea"
          :rows="3"
          :autosize="{ minRows: 3, maxRows: 5 }"
          placeholder="请输入您的问题..."
          resize="none"
          class="message-input"
          @keydown="handleKeydown"
        />

        <div class="input-actions">
          <div class="actions-left">
          </div>
          <div class="actions-right">
            <el-button
              @click="handleClear"
              size="small"
              plain
              :icon="Delete"
            >
              清空
            </el-button>
            <el-button
              @click="handleSend"
              type="primary"
              size="small"
              :loading="sending"
              :disabled="!inputMessage.trim()"
              :icon="Promotion"
            >
              {{ sending ? '发送中...' : '发送' }}
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import {
  InfoFilled,
  Picture,
  Folder,
  DataLine,
  Delete,
  Promotion
} from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  messages: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['update:modelValue', 'send-message', 'clear-messages'])

const drawerVisible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const inputMessage = ref('')
const sending = ref(false)
const autoReply = ref(true)
const soundEnabled = ref(true)
const messageListRef = ref(null)
const avatarUrl = 'https://cube.elemecdn.com/0/88/03b0d39583f48206768a7534e55bcpng.png'

// 当前时间
const currentTime = computed(() => {
  const now = new Date()
  return `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`
})

const currentDate = computed(() => {
  const now = new Date()
  const month = now.getMonth() + 1
  const day = now.getDate()
  return `${month}月${day}日`
})

const handleClose = () => {
  drawerVisible.value = false
}

const handleSend = () => {
  if (!inputMessage.value.trim()) return

  sending.value = true
  const message = inputMessage.value
  inputMessage.value = ''

  emit('send-message', message)

  setTimeout(() => {
    sending.value = false
    scrollToBottom()
  }, 300)
}

const handleClear = () => {
  emit('clear-messages')
}

const handleKeydown = (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    if (!event.ctrlKey && !event.altKey) {
      handleSend()
    }
  }
}

const handleToolClick = (type) => {
  console.log(`点击工具: ${type}`)
  // 这里可以添加具体的工具功能
}

const scrollToBottom = () => {
  nextTick(() => {
    if (messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight
    }
  })
}

// 监听消息变化，自动滚动到底部
watch(() => props.messages, () => {
  if (drawerVisible.value) {
    scrollToBottom()
  }
}, { deep: true })

// 监听抽屉打开
watch(drawerVisible, (value) => {
  if (value) {
    nextTick(() => {
      scrollToBottom()
    })
  }
})

onMounted(() => {
  // 如果需要，可以在这里添加初始化逻辑
})
</script>

<style scoped>
.chat-container {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 10px;
  width: 100%;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.chat-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.chat-avatar {
  background-color: #409eff;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background-color: #f8f9fa;
  border-radius: 8px;
  margin: 10px;
  scroll-behavior: smooth;
}

.time-divider {
  text-align: center;
  margin: 20px 0;
  position: relative;
}

.time-divider span {
  display: inline-block;
  padding: 4px 12px;
  background-color: #e4e7ed;
  border-radius: 12px;
  font-size: 12px;
  color: #909399;
  position: relative;
  z-index: 1;
}

.time-divider::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 1px;
  background-color: #e4e7ed;
  z-index: 0;
}

.message-item {
  margin-bottom: 20px;
  transition: all 0.3s ease;
}

.message-item:hover {
  transform: translateY(-2px);
}

/* 系统消息样式 */
.system-message {
  text-align: center;
  margin: 10px 0;
}

.system-content {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background-color: rgba(64, 158, 255, 0.1);
  border-radius: 4px;
  font-size: 12px;
  color: #409eff;
}

.system-icon {
  font-size: 14px;
}

/* 用户消息样式 */
.user-message {
  display: flex;
  justify-content: flex-end;
  margin: 10px 0;
}

.message-content-wrapper {
  max-width: 70%;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.user-content {
  background: linear-gradient(135deg, #409eff 0%, #337ecc 100%);
  color: white;
  border-radius: 12px 12px 0 12px;
  padding: 10px 14px;
  line-height: 1.4;
  word-break: break-word;
  box-shadow: 0 2px 4px rgba(64, 158, 255, 0.2);
  position: relative;
}

.user-content::after {
  content: '';
  position: absolute;
  bottom: 0;
  right: -10px;
  width: 0;
  height: 0;
  border-style: solid;
  border-width: 0 0 10px 10px;
  border-color: transparent transparent #409eff transparent;
}

.message-info {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}

.message-time {
  font-size: 12px;
  color: #909399;
}

.user-avatar {
  background: linear-gradient(135deg, #67c23a 0%, #5daf34 100%);
}

/* 客服消息样式 */
.bot-message {
  display: flex;
  flex-direction: column;
  margin: 10px 0;
}

.bot-message .message-content-wrapper {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 100%;
}

.bot-avatar {
  flex-shrink: 0;
  margin-top: 4px;
}

.bot-content {
  background-color: white;
  border: 1px solid #ebeef5;
  border-radius: 0 12px 12px 12px;
  padding: 12px;
  max-width: calc(100% - 50px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  position: relative;
}

.bot-content::before {
  content: '';
  position: absolute;
  top: 0;
  left: -10px;
  width: 0;
  height: 0;
  border-style: solid;
  border-width: 0 10px 10px 0;
  border-color: transparent #fff transparent transparent;
}

.bot-content::after {
  content: '';
  position: absolute;
  top: 0;
  left: -11px;
  width: 0;
  height: 0;
  border-style: solid;
  border-width: 0 11px 11px 0;
  border-color: transparent #ebeef5 transparent transparent;
}

.message-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.sender-name {
  font-size: 12px;
  font-weight: 600;
  color: #606266;
}

.message-text {
  font-size: 14px;
  line-height: 1.5;
  color: #303133;
  white-space: pre-wrap;
}

.bot-time {
  margin-left: 42px;
  margin-top: 4px;
}

/* 输入区域样式 */
.input-area {
  padding: 16px;
  background-color: white;
  border-top: 1px solid #ebeef5;
}

.input-tools {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #f2f6fc;
}

.input-tools .el-button {
  padding: 4px;
  color: #909399;
}

.input-tools .el-button:hover {
  color: #409eff;
  background-color: #ecf5ff;
}

.message-input {
  margin-bottom: 12px;
}

.message-input :deep(.el-textarea__inner) {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 12px;
  font-size: 14px;
  transition: all 0.3s ease;
  font-family: inherit;
}

.message-input :deep(.el-textarea__inner:focus) {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.1);
}

.input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.actions-left {
  display: flex;
  align-items: center;
  gap: 15px;
}

.actions-right {
  display: flex;
  gap: 8px;
}

/* 滚动条样式 */
.message-list::-webkit-scrollbar {
  width: 6px;
}

.message-list::-webkit-scrollbar-track {
  background: #f1f1f1;
  border-radius: 3px;
}

.message-list::-webkit-scrollbar-thumb {
  background: #c1c1c1;
  border-radius: 3px;
}

.message-list::-webkit-scrollbar-thumb:hover {
  background: #a8a8a8;
}
</style>

<style>
/* 全局抽屉样式 */
.chat-drawer {
  top: auto !important;
  height: 600px !important;
  bottom: 80px !important;
  border-radius: 12px 0 0 12px !important;
  box-shadow: -2px 0 20px rgba(0, 0, 0, 0.1) !important;
  border: 1px solid #ebeef5 !important;
}

.chat-drawer .el-drawer__header {
  margin-bottom: 0 !important;
  padding: 16px 20px !important;
  border-bottom: 1px solid #ebeef5;
  background-color: #fafafa;
  border-radius: 12px 0 0 0 !important;
}

.chat-drawer .el-drawer__body {
  padding: 0 !important;
  overflow: auto !important;
}

.chat-drawer-overlay {
  background-color: transparent !important;
}

/* 响应式调整 */
@media (max-width: 768px) {
  .chat-drawer {
    width: 100% !important;
    max-width: 100% !important;
    height: 70vh !important;
    bottom: 0 !important;
    border-radius: 12px 12px 0 0 !important;
  }

  .chat-container {
    height: calc(70vh - 60px);
  }

  .message-content-wrapper {
    max-width: 85% !important;
  }

  .input-actions {
    flex-direction: column;
    align-items: stretch;
  }

  .actions-left,
  .actions-right {
    justify-content: center;
    width: 100%;
  }
}
</style>
