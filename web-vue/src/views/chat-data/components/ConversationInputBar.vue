<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import ConversationAgentSelector from './ConversationAgentSelector.vue'

type AgentId = 'chat_data' | 'chat_data_data_analyze'

interface AgentOption {
  id: AgentId
  label: string
  disabled?: boolean
}

interface ModelOption {
  id: string
  label: string
}

interface Props {
  input: string
  placeholder: string
  selectedSkillTitle?: string
  selectedAgent: AgentId
  agentOptions: readonly AgentOption[]
  selectedModel: string
  modelOptions: ModelOption[]
  enableDeepThinking: boolean
  isGenerating: boolean
  canSubmit: boolean
  inputFocused?: boolean
  showSkillButton?: boolean
  submitTitle?: string
}

const props = withDefaults(defineProps<Props>(), {
  selectedSkillTitle: '',
  inputFocused: false,
  showSkillButton: true,
  submitTitle: '发送 (Enter)',
})
const textareaRef = ref<HTMLTextAreaElement | null>(null)

const emit = defineEmits<{
  submit: []
  stop: []
  clearSkill: []
  openSkillManager: []
  disabledAgentClick: [option: AgentOption]
  keydown: [event: KeyboardEvent]
  focus: []
  blur: []
  'update:input': [value: string]
  'update:selectedAgent': [value: AgentId]
  'update:selectedModel': [value: string]
  'update:enableDeepThinking': [value: boolean]
}>()

const boxClass = computed(() => [
  'chat-input-box rounded-2xl bg-white border border-black/[0.08] shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_1px_rgba(0,0,0,0.02)] transition-all duration-200',
  props.inputFocused && 'border-blue-400/40 shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_3px_rgba(0,122,255,0.1)]'
])

const resizeTextarea = () => {
  const textarea = textareaRef.value
  if (!textarea) return
  textarea.style.height = 'auto'
  textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
}

const focus = () => {
  nextTick(() => textareaRef.value?.focus())
}

defineExpose({
  focus,
  resizeTextarea,
})
</script>

<template>
  <form class="relative" @submit.prevent="emit('submit')">
    <div :class="boxClass">
      <div class="min-h-[54px] px-4 pt-4 pb-2">
        <div
          v-if="selectedSkillTitle"
          class="mb-2 flex w-fit items-center gap-1.5 rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700"
        >
          <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
          <span class="max-w-[140px] truncate">{{ selectedSkillTitle }}</span>
          <button type="button" class="text-blue-400 hover:text-blue-700" @click="emit('clearSkill')">
            <i class="fa-solid fa-xmark text-[10px]"></i>
          </button>
        </div>
        <textarea
          ref="textareaRef"
          :value="input"
          :placeholder="placeholder"
          class="block w-full min-h-[26px] max-h-[200px] border-none bg-transparent text-[15px] leading-relaxed text-gray-700 resize-none outline-none overflow-y-hidden placeholder:text-gray-400"
          @input="emit('update:input', ($event.target as HTMLTextAreaElement).value)"
          @keydown="emit('keydown', $event)"
          @focus="emit('focus')"
          @blur="emit('blur')"
        ></textarea>
      </div>
      <div class="flex items-center justify-between gap-2 px-4 py-2 pb-3">
        <div class="chat-input-toolbar flex min-w-0 flex-1 items-center gap-2">
          <button
            v-if="showSkillButton"
            type="button"
            class="flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700"
            title="技能管理"
            @click="emit('openSkillManager')"
          >
            <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
            <span class="toolbar-label">技能</span>
          </button>
          <ConversationAgentSelector
            :model-value="selectedAgent"
            :options="agentOptions"
            @update:model-value="emit('update:selectedAgent', $event)"
            @disabled-click="emit('disabledAgentClick', $event)"
          />
          <div class="flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full hover:bg-black/[0.05]">
            <i class="fa-solid fa-microchip text-[10px] text-gray-400"></i>
            <select
              :value="selectedModel"
              class="model-select max-w-[150px] truncate border-none bg-transparent text-xs text-gray-500 outline-none cursor-pointer appearance-none pr-3"
              @change="emit('update:selectedModel', ($event.target as HTMLSelectElement).value)"
            >
              <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
            </select>
            <i class="fa-solid fa-chevron-down text-[8px] text-gray-400 -ml-2"></i>
          </div>
          <button
            type="button"
            :class="[
              'flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-full border-none text-xs outline-none cursor-pointer transition-colors',
              enableDeepThinking ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:bg-black/[0.05]'
            ]"
            :title="enableDeepThinking ? '已开启深度思考' : '开启深度思考'"
            :aria-pressed="enableDeepThinking"
            @click="emit('update:enableDeepThinking', !enableDeepThinking)"
          >
            <i class="fa-solid fa-brain text-[10px]"></i>
            <span class="toolbar-label">思考</span>
          </button>
          <slot name="toolbar-extra" />
        </div>

        <button
          v-if="isGenerating"
          type="button"
          class="relative w-8 h-8 flex items-center justify-center border-none rounded-full bg-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200 animate-breathe hover:scale-105 active:scale-95"
          title="停止生成"
          @click="emit('stop')"
        >
          <i class="fa-solid fa-stop relative z-10"></i>
          <span class="absolute inset-0 rounded-full bg-[#1677FF] opacity-40 animate-ping"></span>
        </button>
        <button
          v-else
          type="submit"
          :disabled="!canSubmit"
          class="w-8 h-8 flex items-center justify-center border-none rounded-full bg-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200 hover:scale-105 active:scale-95 disabled:bg-gray-200 disabled:text-gray-400 disabled:shadow-none disabled:hover:scale-100 disabled:cursor-not-allowed"
          :title="submitTitle"
        >
          <i class="fa-solid fa-arrow-up"></i>
        </button>
      </div>
    </div>
    <slot name="command-menu" />
  </form>
</template>

<style scoped>
.chat-input-box {
  container-type: inline-size;
}

@container (max-width: 560px) {
  .chat-input-toolbar {
    gap: 4px;
  }

  .chat-input-toolbar .toolbar-label,
  .chat-input-toolbar :deep(.toolbar-label) {
    display: none;
  }

  .chat-input-toolbar .model-select {
    max-width: 92px;
  }
}

@container (max-width: 430px) {
  .chat-input-toolbar .model-select {
    max-width: 0;
    padding-right: 0;
  }
}
</style>
