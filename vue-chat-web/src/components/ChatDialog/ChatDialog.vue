<template>
  <div class="chat-dialog-container">
    <ChatButton @open-chat="openChatDialog" :is-open="showChat" />
    <ChatDrawer
      v-model="showChat"
      :messages="messages"
      @send-message="handleSendMessage"
      @clear-messages="clearMessages"
    />
  </div>
</template>

<script setup>
import { ref, reactive, nextTick } from 'vue'
import ChatButton from './ChatButton.vue'
import ChatDrawer from './ChatDrawer.vue'

function getCurrentTime() {
  const now = new Date()
  return `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`
}

const showChat = ref(false)
const STREAM_DEBUG = false
const messages = ref([
  {
    id: 1,
    type: 'system',
    content: '欢迎使用智能助手，有什么可以帮您的？',
    time: getCurrentTime(),
    sender: '系统'
  },
  {
    id: 2,
    type: 'system',
    content: '您可以随时向我提问，我会尽快为您解答。',
    time: getCurrentTime(),
    sender: '系统'
  }
])

let messageIdCounter = 3

const openChatDialog = () => {
  showChat.value = true
}

const handleSendMessage = async (content) => {
  if (!content.trim()) return

  const userMessage = {
    id: messageIdCounter++,
    type: 'user',
    content: content,
    time: getCurrentTime(),
    sender: '用户'
  }
  messages.value.push(userMessage)

  const botMessage = reactive({
    id: messageIdCounter++,
    type: 'bot',
    content: '正在响应...',
    time: getCurrentTime(),
    sender: '智能体'
  })
  messages.value.push(botMessage)

  try {
    const res = await fetch('/api/chat-assistant/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: content, user: 'stream-tester' })
    })
    if (!res.ok) {
      botMessage.content = '接口错误：' + res.status + ' ' + res.statusText
      return
    }

    const ct = (res.headers.get('content-type') || '').toLowerCase()
    const reader = res.body?.getReader?.()
    if (!reader) {
      const text = await res.text()
      botMessage.content = text
      return
    }

    const decoder = new TextDecoder('utf-8')
    let lineBuffer = ''
    let appended = false
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value, { stream: true })
      if (ct.includes('text/event-stream')) {
        lineBuffer += chunk
        const lines = lineBuffer.split(/\r?\n/)
        lineBuffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('data:')) {
            const data = line.replace(/^data:\s?/, '')
            try {
              const obj = JSON.parse(data)
              if (obj?.event === 'message' && typeof obj?.answer === 'string') {
                botMessage.content = (botMessage.content === '正在响应...' ? '' : botMessage.content) + obj.answer
                appended = true
              }
              // ignore non-message events like message_end, do not append raw JSON
            } catch {
              // ignore parse failure for SSE; do not append raw
            }
          }
        }
      } else {
        botMessage.content = (botMessage.content === '正在响应...' ? '' : botMessage.content) + chunk
        appended = true
        // no UI debug append when STREAM_DEBUG is false
        messages.value = [...messages.value]
      }
    }
    if (lineBuffer && ct.includes('text/event-stream')) {
      const tailLines = lineBuffer.split(/\r?\n/)
      for (const line of tailLines) {
        if (line.startsWith('data:')) {
          const data = line.replace(/^data:\s?/, '')
          try {
            const obj = JSON.parse(data)
            if (obj?.event === 'message' && typeof obj?.answer === 'string') {
              botMessage.content = (botMessage.content === '正在响应...' ? '' : botMessage.content) + obj.answer
              appended = true
            }
          } catch {
            // ignore parse failure for SSE tail; do not append raw
          }
        }
      }
    }
    if (!appended && botMessage.content === '正在响应...') {
      botMessage.content = '接口无有效返回'
    }
  } catch (e) {
    botMessage.content = '接口调用失败，请稍后重试'
  }
}

const clearMessages = () => {
  messages.value = [{
    id: messageIdCounter++,
    type: 'system',
    content: '聊天记录已清空',
    time: getCurrentTime(),
    sender: '系统'
  }]
}

</script>

<style scoped>
.chat-dialog-container {
  position: fixed;
  right: 0;
  bottom: 0;
  z-index: 9999;
}
</style>
