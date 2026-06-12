<!--
  思考步骤组件
  迁移自: web/src/pages/GeneralAssistant/components/unified/conversation/ThinkingBlock.tsx

  功能：
  - 显示AI思考过程
  - 支持折叠/展开
  - 支持嵌套子步骤
  - 支持详细输出（detail和detailLines）
-->

<script setup lang="ts">
import { ref, computed, onUnmounted, nextTick, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import Lottie from 'lottie-web'
import type { ThinkingItem } from '../types'
// 导入图标和动画资源
import checkIcon from '@/assets/images/check.svg'
import chevronRightIcon from '@/assets/images/chevron-right.svg'
import chevronDownIcon from '@/assets/images/chevron-down.svg'
import thinkingAnimation from '@/assets/images/thinking.json'

const md = new MarkdownIt({
  html: false,
  xhtmlOut: false,
  breaks: true,
  linkify: true,
  typographer: true,
})

// Props
interface Props {
  items: ThinkingItem[]
  indent?: number
}

const props = withDefaults(defineProps<Props>(), {
  indent: 0,
})

// Emits
const emit = defineEmits<{
  (e: 'itemToggle', itemId: string, collapsed: boolean): void
}>()

// State
const collapsedMap = ref<Record<string, boolean>>({})
// Lottie 实例映射：stepId -> lottieInstance
const lottieInstances = ref<Map<string, any>>(new Map())
// Lottie 容器 refs 映射
const lottieContainerRefs = ref<Map<string, HTMLDivElement>>(new Map())

// Computed
const detailClassName = computed(() =>
  'prose prose-slate max-w-none text-[14px] font-[350] leading-[25px] text-[rgba(31,58,86,1)] prose-p:my-2 prose-ul:my-2 prose-li:my-1 prose-li:text-[14px] prose-li:leading-[25px]'
)

/**
 * 扁平化步骤树
 */
function flattenSteps(steps: ThinkingItem[]): ThinkingItem[] {
  return steps.flatMap((step) => {
    const normalizedChildren = step.children ? flattenSteps(step.children) : step.children
    if (step.flatten_children) {
      return normalizedChildren ?? []
    }
    if (normalizedChildren && normalizedChildren !== step.children) {
      return [{ ...step, children: normalizedChildren }]
    }
    return [step]
  })
}

const flattenedItems = computed(() => flattenSteps(props.items))

/**
 * 切换步骤折叠状态
 */
function toggleItem(itemId: string, current: boolean) {
  collapsedMap.value[itemId] = !current
  emit('itemToggle', itemId, !current)
}

/**
 * 渲染详细内容
 */
function renderDetail(step: ThinkingItem) {
  if (step.detailLines && step.detailLines.length > 0) {
    return step.detailLines.map((line, index) => {
      const content = md.render(line.content)
      return {
        key: line.lineId ?? index,
        content,
      }
    })
  }

  if (step.detail) {
    const content = md.render(step.detail)
    return [{ key: 'detail', content }]
  }

  return []
}

/**
 * 渲染步骤标签
 */
function renderStepLabel(label: string) {
  const match = label.match(/[（(]/)
  if (!match || match.index === undefined) {
    return { prefix: '', suffix: label, hasMatch: false }
  }

  const splitIndex = match.index
  const prefix = label.slice(0, splitIndex)
  const suffix = label.slice(splitIndex)

  return { prefix, suffix, hasMatch: true }
}

/**
 * 初始化 Lottie 动画
 */
function initLottieAnimation(stepId: string) {
  const container = lottieContainerRefs.value.get(stepId)
  if (!container) return

  // 如果已有实例，先销毁
  if (lottieInstances.value.has(stepId)) {
    lottieInstances.value.get(stepId)?.destroy()
    lottieInstances.value.delete(stepId)
  }

  // 创建新实例
  const instance = Lottie.loadAnimation({
    container,
    renderer: 'svg',
    loop: true,
    autoplay: true,
    animationData: thinkingAnimation,
    rendererSettings: {
      preserveAspectRatio: 'xMidYMid slice'
    }
  })
  lottieInstances.value.set(stepId, instance)
}

/**
 * 销毁 Lottie 动画
 */
function destroyLottieAnimation(stepId: string) {
  const instance = lottieInstances.value.get(stepId)
  if (instance) {
    instance.destroy()
    lottieInstances.value.delete(stepId)
  }
}

/**
 * 设置 Lottie 容器 ref
 */
function setLottieContainerRef(stepId: string, el: HTMLDivElement | null) {
  if (el) {
    lottieContainerRefs.value.set(stepId, el)
  } else {
    lottieContainerRefs.value.delete(stepId)
    destroyLottieAnimation(stepId)
  }
}

/**
 * 判断是否应该折叠
 */
function shouldCollapse(step: ThinkingItem): boolean {
  const hasContent = Boolean(step.detail || (step.detailLines && step.detailLines.length > 0) || (step.children && step.children.length > 0))
  return (collapsedMap.value.get(step.id) as boolean) ?? (step.status === 'done' && hasContent)
}

/**
 * 判断是否可以点击
 */
function isClickable(step: ThinkingItem): boolean {
  return Boolean(step.detail || (step.detailLines && step.detailLines.length > 0) || (step.children && step.children.length > 0))
}

// 监听步骤变化，初始化动画
watch(flattenedItems, (newItems, oldItems) => {
  // 获取旧的 stepIds
  const oldStepIds = oldItems ? new Set(oldItems.map(s => s.id)) : new Set()
  // 获取新的 stepIds
  const newStepIds = new Set(newItems.map(s => s.id))

  // 初始化新步骤或更新状态的步骤
  for (const step of newItems) {
    if (step.status === 'thinking') {
      // 延迟初始化，等待 DOM 更新
      nextTick(() => {
        if (step.status === 'thinking') {
          initLottieAnimation(step.id)
        }
      })
    } else {
      destroyLottieAnimation(step.id)
    }
  }

  // 清理已删除的步骤
  for (const stepId of oldStepIds) {
    if (!newStepIds.has(stepId)) {
      destroyLottieAnimation(stepId)
    }
  }
}, { deep: true })

// 组件卸载时清理所有动画
onUnmounted(() => {
  for (const [stepId] of lottieInstances.value.entries()) {
    destroyLottieAnimation(stepId)
  }
})
</script>

<template>
  <div class="relative">
    <div class="flex flex-col gap-3">
      <div
        v-for="step in flattenedItems"
        :key="step.id"
        class="flex flex-col gap-0"
      >
        <!-- 隐藏标签的步骤只显示内容 -->
        <template v-if="step.label_hidden">
          <div v-if="step.detail || (step.detailLines && step.detailLines.length > 0)" class="flex flex-col gap-0">
            <div
              v-for="item in renderDetail(step)"
              :key="item.key"
              v-html="item.content"
              class="thinking-detail"
            />
          </div>
          <!-- 递归渲染子步骤 -->
          <div v-if="step.children && step.children.length > 0" class="pt-2">
            <ThinkingBlock
              :items="step.children"
              @item-toggle="toggleItem"
            />
          </div>
        </template>

        <!-- 正常步骤 -->
        <template v-else>
          <div
            :class="[
              'flex items-center gap-2 text-[14px] text-black',
              isClickable(step) ? 'cursor-pointer' : ''
            ]"
            @click="() => {
              if (isClickable(step)) {
                toggleItem(step.id, shouldCollapse(step))
              }
            }"
            :role="isClickable(step) ? 'button' : undefined"
            :tabindex="isClickable(step) ? 0 : undefined"
            @keydown="(e) => {
              if (!isClickable(step)) return
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                toggleItem(step.id, shouldCollapse(step))
              }
            }"
          >
            <!-- 思考中状态 - Lottie 动画 -->
            <div
              v-if="step.status === 'thinking'"
              :ref="(el) => setLottieContainerRef(step.id, el as HTMLDivElement | null)"
              class="h-[19.26px] w-[20px]"
            ></div>
            <!-- 完成状态 - SVG 图标 -->
            <div
              v-else
              class="flex h-5 w-5 items-center justify-center rounded-full bg-[#31C48D] text-white"
            >
              <img :src="checkIcon" alt="" class="h-3 w-3" />
            </div>

            <!-- 标签和展开按钮 -->
            <span class="inline-flex items-center gap-1.5">
              <template v-if="renderStepLabel(step.label).hasMatch">
                <span class="font-semibold">{{ renderStepLabel(step.label).prefix }}</span>
                <span>{{ renderStepLabel(step.label).suffix }}</span>
              </template>
              <template v-else>
                {{ step.label }}
              </template>

              <button
                type="button"
                :class="[
                  'inline-flex h-4 w-4 items-center justify-center text-black',
                  isClickable(step) && !shouldCollapse(step) ? '' : 'opacity-40 cursor-default'
                ]"
                @click="(e) => {
                  e.stopPropagation()
                  if (isClickable(step)) {
                    toggleItem(step.id, shouldCollapse(step))
                  }
                }"
                :aria-label="shouldCollapse(step) ? '展开' : '收起'"
              >
                <img
                  v-if="shouldCollapse(step)"
                  :src="chevronRightIcon"
                  alt=""
                  class="h-3 w-3"
                />
                <img
                  v-else
                  :src="chevronDownIcon"
                  alt=""
                  class="h-3 w-3"
                />
              </button>
            </span>
          </div>

          <!-- 详细内容（展开时显示） -->
          <div
            v-if="step.detail || (step.detailLines && step.detailLines.length > 0)"
            v-show="!shouldCollapse(step)"
            class="pl-8 mt-[11px]"
          >
            <div
              v-for="item in renderDetail(step)"
              :key="item.key"
              v-html="item.content"
              class="thinking-detail"
            />
          </div>

          <!-- 子步骤（递归） -->
          <div
            v-if="step.children && step.children.length > 0"
            v-show="!shouldCollapse(step)"
            class="pt-2"
          >
            <ThinkingBlock
              :items="step.children"
              @item-toggle="toggleItem"
            />
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.thinking-detail :deep(p) {
  margin: 0.5rem 0;
}
</style>
