<script setup lang="ts">
/**
 * 对话输入壳：容器样式、多行自适应 textarea、发送/停止按钮与工具栏插槽。
 * 主对话（经 ConversationInputBar）、SQL 助手、定时任务指令共用；
 * 能力入口（隔离模式、技能等）通过 slot 注入，发送按钮可通过 showSubmit 关闭。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'

interface Props {
  input: string
  placeholder?: string
  isGenerating?: boolean
  canSubmit?: boolean
  submitTitle?: string
  /** 是否展示发送/停止按钮；表单场景（如定时任务）由外部按钮提交时传 false。 */
  showSubmit?: boolean
  /** Enter 是否触发提交；false 时 Enter 换行（多行指令场景）。 */
  submitOnEnter?: boolean
  /** 最小行数（自适应起始高度）。 */
  minRows?: number
  /** 最大高度 px，超出后内部滚动。 */
  maxHeight?: number
  /** 最大字符数；设置后展示字数统计。 */
  maxlength?: number
  inputFocused?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  placeholder: '',
  isGenerating: false,
  canSubmit: true,
  submitTitle: '发送 (Enter)',
  showSubmit: true,
  submitOnEnter: true,
  minRows: 1,
  maxHeight: 200,
  inputFocused: false,
})

const emit = defineEmits<{
  submit: []
  stop: []
  keydown: [event: KeyboardEvent]
  focus: []
  blur: []
  'update:input': [value: string]
}>()

const textareaRef = ref<HTMLTextAreaElement | null>(null)

const boxClass = computed(() => [
  'chat-input-box rounded-2xl bg-white border border-black/[0.08] shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_1px_rgba(0,0,0,0.02)] transition-all duration-200',
  props.inputFocused && 'border-blue-400/40 shadow-[0_2px_12px_rgba(0,0,0,0.06),0_0_0_3px_rgba(0,122,255,0.1)]'
])

// 首行 26px，其余按 leading-relaxed(24px) 递增
const textareaStyle = computed(() => ({
  minHeight: `${26 + (props.minRows - 1) * 24}px`,
  maxHeight: `${props.maxHeight}px`,
}))

const resizeTextarea = () => {
  const textarea = textareaRef.value
  if (!textarea) return
  textarea.style.height = 'auto'
  textarea.style.height = `${Math.min(textarea.scrollHeight, props.maxHeight)}px`
}

const handleKeydown = (event: KeyboardEvent) => {
  emit('keydown', event)
  // 消费者（技能命令菜单等）已处理时不再兜底提交
  if (event.defaultPrevented) return
  if (!props.submitOnEnter) return
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || props.isGenerating) return
  event.preventDefault()
  emit('submit')
}

const focus = () => {
  nextTick(() => {
    const textarea = textareaRef.value
    if (!textarea) return
    textarea.focus()
    // 光标钉到文本末尾，插入推荐问题后可直接回车发送
    const end = textarea.value.length
    textarea.setSelectionRange(end, end)
  })
}

defineExpose({ focus, resizeTextarea })

onMounted(resizeTextarea)

watch(() => props.input, async () => {
  await nextTick()
  resizeTextarea()
})
</script>

<template>
  <form class="relative" @submit.prevent="emit('submit')">
    <div :class="boxClass">
      <div class="relative min-h-[54px] px-4 pt-4 pb-2">
        <span v-if="maxlength" class="absolute right-4 top-2 text-[11px] tabular-nums text-gray-400">
          {{ input.length }}/{{ maxlength }}
        </span>
        <slot name="prepend" />
        <textarea
          ref="textareaRef"
          :value="input"
          :placeholder="placeholder"
          :maxlength="maxlength"
          :style="textareaStyle"
          class="block w-full border-none bg-transparent text-[15px] leading-relaxed text-gray-700 resize-none outline-none overflow-y-auto placeholder:text-gray-400"
          @input="emit('update:input', ($event.target as HTMLTextAreaElement).value)"
          @keydown="handleKeydown"
          @focus="emit('focus')"
          @blur="emit('blur')"
        ></textarea>
      </div>
      <div class="flex items-center justify-between gap-2 px-4 py-2 pb-3">
        <div class="chat-input-toolbar flex min-w-0 flex-1 items-center gap-2">
          <slot name="toolbar" />
        </div>

        <div class="flex items-center gap-2">
          <slot name="actions" />
          <button
            v-if="showSubmit && isGenerating"
            type="button"
            class="relative w-8 h-8 flex items-center justify-center border-none rounded-full bg-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200 animate-breathe hover:scale-105 active:scale-95"
            title="停止生成"
            @click="emit('stop')"
          >
            <i class="fa-solid fa-stop relative z-10"></i>
            <span class="absolute inset-0 rounded-full bg-[#1677FF] opacity-40 animate-ping"></span>
          </button>
          <button
            v-else-if="showSubmit"
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
    <slot name="footer" />
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
