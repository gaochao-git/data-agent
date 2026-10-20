<script setup lang="ts">
/**
 * 等待提示：智能体生成/思考期间轮播产品小知识，把「等待时间」用来告诉用户怎么用得更好。
 * 内联在「正在生成 / 速率 / 用时」状态行末尾，不额外占一行；
 * 仅 active 时渲染与计时，每次挂载随机起点，避免所有会话都从同一条开始。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ active: boolean; intervalMs?: number }>(), {
  intervalMs: 8000
})

/** 等待期知识库：每条一句话，点出「怎么用更好」，不打断阅读 */
const WAITING_TIPS = [
  '输入 @ 可以把工作区文件或上下文加进提问，不用重复描述背景',
  '划选回复里的关键结论可一键作为引用加入提问，追问不用复制粘贴',
  '输入 / 可以调用已安装的技能与指令，复杂分析可沉淀成技能复用',
  '问题里带上数据源或语义模型名，取数口径会更准',
  '简单问题关掉深度思考更快，复杂分析再打开思考过程',
  '长文档先传到工作区，智能体会按需读取，不占对话上下文',
  '多个数据源可以在同一次提问里一起分析，结果会并列展示',
  '追问时不用重复背景，智能体会沿用当前会话上下文',
  '不确定字段口径时，先让它解释字段含义再取数更稳',
  '生成中可随时中断，已生成的内容会保留',
  '技能按需开启即可，无关能力不进入任务，输出更好哦',
  '划选回复任意片段可直接复制，引用追问时智能体会看到原文',
  '表格结果可以直接导出或保存到看板继续分析',
  '历史会话可随时回到任意一轮继续追问'
] as const

const tipIndex = ref(Math.floor(Math.random() * WAITING_TIPS.length))
let timer: ReturnType<typeof setInterval> | null = null

const tip = computed(() => WAITING_TIPS[tipIndex.value])

const stopTimer = () => {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

const startTimer = () => {
  stopTimer()
  timer = setInterval(() => {
    tipIndex.value = (tipIndex.value + 1) % WAITING_TIPS.length
  }, props.intervalMs)
}

onMounted(() => {
  if (props.active) startTimer()
})

watch(
  () => props.active,
  active => {
    if (active) startTimer()
    else stopTimer()
  }
)

onBeforeUnmount(stopTimer)
</script>

<template>
  <span
    v-if="active"
    class="inline-flex min-w-0 items-center gap-1 border-l border-slate-200 pl-2 text-[11px] text-slate-400"
    data-waiting-tip
  >
    <i class="fa-regular fa-lightbulb shrink-0 text-[10px] text-amber-400/80"></i>
    <span class="truncate" :title="tip">{{ tip }}</span>
  </span>
</template>
