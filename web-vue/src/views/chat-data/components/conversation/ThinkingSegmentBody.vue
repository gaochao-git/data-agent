<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'

// 主对话思考分段正文：流式追加时自动粘底跟随（与 ToolCallList 的
// subagent-trail-scroll 同一套约定：近底 100px 视为粘底，用户上翻暂停跟随）。
const NEAR_BOTTOM_PX = 100

const props = defineProps<{
  content: string
  /** 该分段是否处于「思考中」（流式追加中）；结束后不再干预滚动位置 */
  active?: boolean
}>()

const bodyRef = ref<HTMLElement | null>(null)
const sticky = ref(true)

const scrollToBottom = () => {
  const el = bodyRef.value
  if (el) el.scrollTop = el.scrollHeight
}

const onScroll = () => {
  const el = bodyRef.value
  if (el) sticky.value = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
}

// 手动展开「思考中」分段时直接落到最新内容
onMounted(() => {
  if (props.active) scrollToBottom()
})

watch(
  () => props.content,
  async () => {
    if (!props.active || !sticky.value) return
    await nextTick()
    scrollToBottom()
  },
)
</script>

<template>
  <div
    ref="bodyRef"
    class="mb-1.5 max-h-[210px] overflow-y-auto whitespace-pre-wrap rounded-md bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-400"
    @scroll.passive="onScroll"
  >{{ content }}</div>
</template>
