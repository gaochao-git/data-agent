<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
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
const modelMenuRef = ref<HTMLElement | null>(null)
const modelMenuOpen = ref(false)

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

const selectedModelLabel = computed(() =>
  props.modelOptions.find(model => model.id === props.selectedModel)?.label || props.selectedModel || '模型'
)

const resizeTextarea = () => {
  const textarea = textareaRef.value
  if (!textarea) return
  textarea.style.height = 'auto'
  textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
}

const selectModel = (modelId: string) => {
  emit('update:selectedModel', modelId)
  modelMenuOpen.value = false
}

const toggleDeepThinking = () => {
  emit('update:enableDeepThinking', !props.enableDeepThinking)
}

const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target
  if (!(target instanceof Node)) return
  if (modelMenuRef.value?.contains(target)) return
  modelMenuOpen.value = false
}

const focus = () => {
  nextTick(() => textareaRef.value?.focus())
}

defineExpose({
  focus,
  resizeTextarea,
})

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
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
          <ConversationAgentSelector
            :model-value="selectedAgent"
            :options="agentOptions"
            @update:model-value="emit('update:selectedAgent', $event)"
            @disabled-click="emit('disabledAgentClick', $event)"
          />
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
          <slot name="toolbar-extra" />
        </div>

        <div class="flex items-center gap-2">
          <div ref="modelMenuRef" class="relative">
            <div class="flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-1 hover:bg-black/[0.05]">
              <slot name="model-prefix">
                <i
                  :class="[
                    'fa-solid fa-microchip text-[10px]',
                    enableDeepThinking ? 'text-blue-500' : 'text-gray-400'
                  ]"
                ></i>
              </slot>
              <button
                type="button"
                class="flex min-w-0 items-center gap-1.5 border-none bg-transparent p-0 text-xs text-gray-500 cursor-pointer"
                title="模型设置"
                @click="modelMenuOpen = !modelMenuOpen"
              >
                <span
                  :class="[
                    'model-trigger-label max-w-[150px] truncate',
                    enableDeepThinking ? 'text-blue-600' : 'text-gray-500'
                  ]"
                >
                  {{ selectedModelLabel }}
                </span>
                <i class="fa-solid fa-chevron-down text-[8px] text-gray-400"></i>
              </button>
            </div>
            <div
              v-if="modelMenuOpen"
              class="absolute right-0 bottom-8 z-50 w-[220px] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
            >
              <div class="py-1">
                <button
                  v-for="m in modelOptions"
                  :key="m.id"
                  type="button"
                  :class="[
                    'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-1.5 text-left text-xs cursor-pointer',
                    selectedModel === m.id ? 'text-blue-600 bg-blue-50' : 'text-gray-600 hover:bg-gray-50'
                  ]"
                  @click="selectModel(m.id)"
                >
                  <span class="truncate">{{ m.label }}</span>
                  <i v-if="selectedModel === m.id" class="fa-solid fa-check text-[10px]"></i>
                </button>
                <div v-if="modelOptions.length === 0" class="px-3 py-2 text-xs text-gray-400">暂无模型</div>
              </div>
              <div class="border-t border-gray-100 p-1.5">
                <button
                  type="button"
                  class="flex w-full items-center justify-between gap-3 rounded-md border-none bg-transparent px-2 py-1.5 text-left text-xs text-gray-600 cursor-pointer hover:bg-gray-50"
                  role="switch"
                  :aria-checked="enableDeepThinking"
                  :title="enableDeepThinking ? '关闭深度思考' : '开启深度思考'"
                  @click="toggleDeepThinking"
                >
                  <span class="flex min-w-0 items-center gap-1.5">
                    <i
                      :class="[
                        'fa-solid fa-brain text-[10px]',
                        enableDeepThinking ? 'text-blue-500' : 'text-gray-400'
                      ]"
                    ></i>
                    <span class="truncate">深度思考</span>
                  </span>
                  <span :class="['deep-thinking-switch', enableDeepThinking && 'deep-thinking-switch-on']">
                    <span class="deep-thinking-switch-thumb"></span>
                  </span>
                </button>
              </div>
            </div>
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
    </div>
    <slot name="command-menu" />
  </form>
</template>

<style scoped>
.chat-input-box {
  container-type: inline-size;
}

.deep-thinking-switch {
  position: relative;
  display: inline-block;
  width: 26px;
  height: 14px;
  flex: 0 0 auto;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: #e5e7eb;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.deep-thinking-switch:hover {
  background: #d1d5db;
}

.deep-thinking-switch-on {
  background: #1677ff;
}

.deep-thinking-switch-on:hover {
  background: #0f6be8;
}

.deep-thinking-switch-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.22);
  transition: transform 0.15s ease;
}

.deep-thinking-switch-on .deep-thinking-switch-thumb {
  transform: translateX(12px);
}

@container (max-width: 560px) {
  .chat-input-toolbar {
    gap: 4px;
  }

  .chat-input-toolbar .toolbar-label,
  .chat-input-toolbar :deep(.toolbar-label) {
    display: none;
  }

  .model-trigger-label {
    max-width: 92px;
  }
}

@container (max-width: 430px) {
  .model-trigger-label {
    max-width: 0;
  }
}

@keyframes breathe {
  0%, 100% {
    box-shadow: 0 2px 8px rgba(0, 122, 255, 0.35), 0 0 0 0 rgba(22, 119, 255, 0.28);
  }
  50% {
    box-shadow: 0 4px 14px rgba(0, 122, 255, 0.45), 0 0 0 6px rgba(22, 119, 255, 0.08);
  }
}

.animate-breathe {
  animation: breathe 1.4s ease-in-out infinite;
}
</style>
