<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { Message } from '../../types'
import { getTeamMemberMessages, sendTeamMemberMessage } from '../../api'
import ConversationMessageList from './ConversationMessageList.vue'

const props = defineProps<{
  conversationId: string | null
  memberName: string
}>()
const emit = defineEmits<{ back: [] }>()

const messages = ref<Message[]>([])
const input = ref('')
const loading = ref(false)
const sending = ref(false)

const load = async () => {
  if (!props.conversationId || !props.memberName) return
  loading.value = true
  try {
    const response = await getTeamMemberMessages(props.conversationId, props.memberName)
    messages.value = (response.data?.messages || []).map((item: any) => ({
      id: String(item.id || ''),
      role: item.role,
      content: String(item.content || ''),
      toolCallId: item.tool_call_id,
      toolName: item.tool_name,
      toolCalls: item.tool_calls,
    })) as Message[]
  } catch (err) {
    console.error('读取 teammate 历史失败:', err)
    messages.value = []
  } finally {
    loading.value = false
  }
}

const send = async () => {
  const text = input.value.trim()
  if (!text || !props.conversationId || sending.value) return
  sending.value = true
  try {
    await sendTeamMemberMessage(props.conversationId, props.memberName, text)
    input.value = ''
    await load()
  } catch (err) {
    console.error('发送 teammate 消息失败:', err)
    ElMessage.error('发送失败')
  } finally {
    sending.value = false
  }
}

watch(() => [props.conversationId, props.memberName], load, { immediate: false })
onMounted(load)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="flex h-10 shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-4 text-sm">
      <button type="button" class="text-slate-500 hover:text-slate-900" @click="emit('back')">
        <i class="fa-solid fa-arrow-left mr-1"></i>返回主对话
      </button>
      <span class="font-medium text-slate-700">teammate · {{ memberName }}</span>
    </div>
    <div v-loading="loading" class="min-h-0 flex-1 overflow-auto px-4 py-4">
      <ConversationMessageList :messages="messages" :conversation-id="conversationId" />
    </div>
    <div class="shrink-0 border-t border-slate-200 bg-white p-3">
      <div class="flex gap-2">
        <textarea
          v-model="input"
          rows="2"
          class="flex-1 resize-none rounded-lg border border-slate-200 p-2 text-sm outline-none focus:border-blue-400"
          placeholder="直接给 teammate 发消息"
          @keydown.enter.exact.prevent="send"
        ></textarea>
        <button
          type="button"
          class="rounded-lg bg-slate-900 px-4 text-sm text-white disabled:opacity-50"
          :disabled="sending"
          @click="send"
        >
          发送
        </button>
      </div>
    </div>
  </div>
</template>
