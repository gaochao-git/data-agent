<script setup lang="ts">
/**
 * 模型选择 pill：一个下拉内聚合「模型列表 + 深度思考开关 + 思考档位」。
 * 思考三态与主对话口径一致：enableDeepThinking null 未触碰（展示回退模型
 * default_on）、true/false 用户显式开关；档位 null 跟随模型 default_effort。
 * allowEmpty=true 时模型可空（列表含「系统默认」项），用于定时任务。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

// 本地结构化类型：与 types 的 ChatDataModelOption 同构（外部类型不得进入 defineProps）
interface ModelThinkingOption {
  mode: 'none' | 'optional' | 'forced'
  efforts: string[]
  default_on: boolean
  default_effort: string | null
}

interface ModelOption {
  id: string
  label: string
  is_default?: boolean
  /** 思考能力声明（/config 下发）；缺省按 optional 处理。 */
  thinking?: ModelThinkingOption
}

interface Props {
  selectedModel: string
  modelOptions: ModelOption[]
  /** 三态：true/false 用户显式选择；null 未触碰（展示回退模型 default_on）。 */
  enableDeepThinking?: boolean | null
  /** 思考档位；null 未选择（展示回退模型 default_effort）。 */
  thinkingEffort?: string | null
  isGenerating?: boolean
  /** 允许不选模型（列表含「系统默认」项）。 */
  allowEmpty?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  enableDeepThinking: null,
  thinkingEffort: null,
  isGenerating: false,
  allowEmpty: false,
})

const emit = defineEmits<{
  'update:selectedModel': [value: string]
  'update:enableDeepThinking': [value: boolean]
  'update:thinkingEffort': [value: string]
}>()

const EFFORT_LABELS: Record<string, string> = { low: '低', medium: '中', high: '高' }

const menuRef = ref<HTMLElement | null>(null)
const menuOpen = ref(false)

const selectedModelLabel = computed(() => {
  if (!props.selectedModel) return '系统默认'
  return props.modelOptions.find(model => model.id === props.selectedModel)?.label || props.selectedModel
})

/** 当前选中模型的思考能力声明。 */
const selectedModelThinking = computed(
  () => props.modelOptions.find(item => item.id === props.selectedModel)?.thinking ?? null
)

// ---- 开关形态（主对话/SQL 助手）----
/** forced / none 模型锁定开关：思考与否由注册表决定，用户切换无意义。 */
const thinkingLocked = computed(() => {
  const mode = selectedModelThinking.value?.mode
  return mode === 'forced' || mode === 'none'
})
/** 开关展示态：forced 恒亮、none 恒灭；optional 用户显式选择优先，未触碰回退 default_on。 */
const thinkingDisplayOn = computed(() => {
  const mode = selectedModelThinking.value?.mode
  if (mode === 'forced') return true
  if (mode === 'none') return false
  return props.enableDeepThinking ?? !!selectedModelThinking.value?.default_on
})
const deepThinkingTitle = computed(() => {
  const mode = selectedModelThinking.value?.mode
  if (mode === 'forced') return '当前模型始终深度思考'
  if (mode === 'none') return '当前模型不支持深度思考'
  return thinkingDisplayOn.value ? '关闭深度思考' : '开启深度思考'
})
/** 开关形态档位：仅模型声明了 efforts 且思考展示为开时出现。 */
const toggleEffortOptions = computed(() => selectedModelThinking.value?.efforts ?? [])
const thinkingEffortDisplay = computed(
  () => props.thinkingEffort ?? selectedModelThinking.value?.default_effort ?? null
)
const showEffortSelector = computed(
  () => thinkingDisplayOn.value && toggleEffortOptions.value.length > 0
)

const selectModel = (modelId: string) => {
  emit('update:selectedModel', modelId)
  menuOpen.value = false
}

const toggleDeepThinking = () => {
  if (thinkingLocked.value) return
  emit('update:enableDeepThinking', !thinkingDisplayOn.value)
}

const selectThinkingEffort = (effort: string) => {
  emit('update:thinkingEffort', effort)
}

const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target
  if (!(target instanceof Node)) return
  if (menuRef.value?.contains(target)) return
  menuOpen.value = false
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<template>
  <div ref="menuRef" class="relative">
    <div class="flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-1 hover:bg-black/[0.05]">
      <slot name="prefix" :thinking-on="thinkingDisplayOn">
        <i
          :class="[
            'fa-solid fa-microchip text-[10px]',
            thinkingDisplayOn ? 'text-blue-500' : 'text-gray-400'
          ]"
        ></i>
      </slot>
      <button
        type="button"
        :disabled="isGenerating"
        class="flex min-w-0 items-center gap-1.5 border-none bg-transparent p-0 text-xs text-gray-500 cursor-pointer"
        title="模型设置"
        @click="!isGenerating && (menuOpen = !menuOpen)"
      >
        <span
          :class="[
            'model-trigger-label max-w-[150px] truncate',
            thinkingDisplayOn ? 'text-blue-600' : 'text-gray-500'
          ]"
        >
          {{ selectedModelLabel }}
        </span>
        <i class="fa-solid fa-chevron-down text-[8px] text-gray-400"></i>
      </button>
    </div>
    <div
      v-if="menuOpen"
      class="absolute right-0 bottom-8 z-50 w-[220px] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
    >
      <div class="py-1">
        <button
          v-if="allowEmpty"
          type="button"
          :class="[
            'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-1.5 text-left text-xs cursor-pointer',
            !selectedModel ? 'text-blue-600 bg-blue-50' : 'text-gray-600 hover:bg-gray-50'
          ]"
          @click="selectModel('')"
        >
          <span>系统默认</span>
          <i v-if="!selectedModel" class="fa-solid fa-check text-[10px]"></i>
        </button>
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
          :class="[
            'flex w-full items-center justify-between gap-3 rounded-md border-none bg-transparent px-2 py-1.5 text-left text-xs text-gray-600 cursor-pointer hover:bg-gray-50',
            thinkingLocked && 'cursor-not-allowed opacity-60 hover:bg-transparent'
          ]"
          role="switch"
          :aria-checked="thinkingDisplayOn"
          :aria-disabled="thinkingLocked"
          :title="deepThinkingTitle"
          @click="toggleDeepThinking"
        >
          <span class="flex min-w-0 items-center gap-1.5">
            <i
              :class="[
                'fa-solid fa-brain text-[10px]',
                thinkingDisplayOn ? 'text-blue-500' : 'text-gray-400'
              ]"
            ></i>
            <span class="truncate">深度思考</span>
          </span>
          <span :class="['deep-thinking-switch', thinkingDisplayOn && 'deep-thinking-switch-on', thinkingLocked && 'opacity-50']">
            <span class="deep-thinking-switch-thumb"></span>
          </span>
        </button>
        <!-- 思考档位：模型声明了 efforts 且思考开启时展示 -->
        <div v-if="showEffortSelector" class="mt-1.5 flex items-center gap-1.5 px-2 pb-0.5">
          <span class="shrink-0 text-[10px] text-gray-400">思考等级</span>
          <div class="flex flex-1 items-center gap-0.5 rounded-md bg-gray-100 p-0.5">
            <button
              v-for="effort in toggleEffortOptions"
              :key="effort"
              type="button"
              class="flex-1 rounded-[5px] border-none px-1.5 py-1 text-[11px] leading-none cursor-pointer transition-colors duration-150"
              :class="
                thinkingEffortDisplay === effort
                  ? 'bg-white text-blue-600 shadow-[0_1px_3px_rgba(0,0,0,0.12)] font-medium'
                  : 'bg-transparent text-gray-500 hover:text-gray-700'
              "
              :title="`思考等级：${EFFORT_LABELS[effort] ?? effort}`"
              @click="selectThinkingEffort(effort)"
            >
              {{ EFFORT_LABELS[effort] ?? effort }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
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

/* 窄容器下收紧触发文案宽度；容器由外层 ChatInputBox 声明 */
@container (max-width: 560px) {
  .model-trigger-label {
    max-width: 92px;
  }
}

@container (max-width: 430px) {
  .model-trigger-label {
    max-width: 0;
  }
}
</style>
