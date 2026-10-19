<script setup lang="ts">
/**
 * 模型选择 pill：两级筛选结构（参考 DeepSeek 官网）——
 * 主面板两行入口「模型 / 推理等级」，点进二级列表选择。
 * 思考三态与主对话口径一致：enableDeepThinking null 未触碰（展示回退模型
 * default_on）、true/false 用户显式选择；档位 null 跟随模型 default_effort。
 * off 映射 enableDeepThinking=false，其余档映射 true + thinkingEffort。
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
  /** 提供商；有值时展示为 provider/label，区分同名模型 */
  provider?: string
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

/** off/on 是无 effort 声明时的哨兵档；其余键为模型元数据 effort 值（off/none 声明档后端按关形态发）。 */
const LEVEL_LABELS: Record<string, string> = { off: '关闭', on: '开启', none: '关闭', low: '低', medium: '中', high: '高', minimal: '最低', xhigh: '超高', ultra: '极高', max: '最大' }

const menuRef = ref<HTMLElement | null>(null)
const menuOpen = ref(false)
/** 两级筛选：null=主面板；'models'/'levels'=对应二级列表。打开与关闭时重置。 */
const subView = ref<'models' | 'levels' | null>(null)

/** 展示名：有提供商时用 provider/label 前缀，让用户知道模型来自哪个供应商。 */
const displayLabel = (model: Pick<ModelOption, 'label' | 'provider'>) =>
  model.provider ? `${model.provider}/${model.label}` : model.label

const selectedModelLabel = computed(() => {
  if (!props.selectedModel) return '系统默认'
  const model = props.modelOptions.find(model => model.id === props.selectedModel)
  return model ? displayLabel(model) : props.selectedModel
})

/** 当前选中模型的思考能力声明。 */
const selectedModelThinking = computed(
  () => props.modelOptions.find(item => item.id === props.selectedModel)?.thinking ?? null
)

// ---- 档位形态（主对话/SQL 助手）----
/** 展示态开关：forced 恒开、none 恒关；optional 用户显式选择优先，未触碰回退 default_on。 */
const thinkingDisplayOn = computed(() => {
  const mode = selectedModelThinking.value?.mode
  if (mode === 'forced') return true
  if (mode === 'none') return false
  return props.enableDeepThinking ?? !!selectedModelThinking.value?.default_on
})
/** 档位选项：none 无档（不支持）；forced 恒开只列 efforts；optional 首列「关闭」（声明档已含 off 时去重）。 */
const thinkingLevelOptions = computed(() => {
  const thinking = selectedModelThinking.value
  if (!thinking || thinking.mode === 'none') return []
  if (thinking.mode === 'forced') {
    return thinking.efforts.length > 0 ? [...thinking.efforts] : ['on']
  }
  return [...new Set(['off', ...(thinking.efforts.length > 0 ? thinking.efforts : ['on'])])]
})
const thinkingEffortDisplay = computed(
  () => props.thinkingEffort ?? selectedModelThinking.value?.default_effort ?? null
)
/** 当前生效档位：关 → off；开 → 生效 effort（无声明归 on）。 */
const currentThinkingLevel = computed(() => {
  if (!thinkingDisplayOn.value) return 'off'
  const efforts = selectedModelThinking.value?.efforts ?? []
  if (efforts.length === 0) return 'on'
  return thinkingEffortDisplay.value ?? efforts[0]!
})
/** 触发器内联档位文案；模型不支持思考时不展示。 */
const thinkingLevelLabel = computed(() => {
  if (thinkingLevelOptions.value.length === 0) return ''
  return LEVEL_LABELS[currentThinkingLevel.value] ?? currentThinkingLevel.value
})

/** 推理等级行副值：不支持时占位文案。 */
const levelEntryValue = computed(() => {
  if (thinkingLevelOptions.value.length === 0) return '不支持'
  return LEVEL_LABELS[currentThinkingLevel.value] ?? currentThinkingLevel.value
})

const openMenuPanel = () => {
  if (props.isGenerating) return
  subView.value = null
  menuOpen.value = !menuOpen.value
}

const closeMenu = () => {
  menuOpen.value = false
  subView.value = null
}

const selectModel = (modelId: string) => {
  emit('update:selectedModel', modelId)
  subView.value = null
}

const selectThinkingLevel = (level: string) => {
  if (level === 'off') {
    emit('update:enableDeepThinking', false)
    return
  }
  emit('update:enableDeepThinking', true)
  if (level !== 'on') emit('update:thinkingEffort', level)
  subView.value = null
}

const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target
  if (!(target instanceof Node)) return
  if (menuRef.value?.contains(target)) return
  closeMenu()
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
        <i class="fa-solid fa-microchip text-[10px] text-gray-400"></i>
      </slot>
      <button
        type="button"
        :disabled="isGenerating"
        class="flex min-w-0 items-center gap-1.5 border-none bg-transparent p-0 text-xs text-gray-500 cursor-pointer"
        title="模型设置"
        @click="openMenuPanel"
      >
        <span class="model-trigger-label max-w-[200px] truncate text-gray-500">
          {{ selectedModelLabel }}
        </span>
        <span v-if="thinkingLevelLabel" class="shrink-0 text-gray-400">
          {{ thinkingLevelLabel }}
        </span>
        <i class="fa-solid fa-chevron-down text-[8px] text-gray-400"></i>
      </button>
    </div>
    <div
      v-if="menuOpen"
      data-model-picker-menu
      class="absolute right-0 bottom-8 z-50 w-[280px] max-w-[calc(100vw-24px)] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
      @click.stop
    >
      <!-- 主面板：两行入口（模型 / 推理等级） -->
      <template v-if="subView === null">
        <button
          type="button"
          class="flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-2 text-left text-xs text-gray-600 cursor-pointer hover:bg-gray-50"
          @click="subView = 'models'"
        >
          <span class="shrink-0 whitespace-nowrap">模型</span>
          <span class="flex min-w-0 items-center gap-1.5 text-gray-400">
            <span class="max-w-[200px] truncate">{{ selectedModelLabel }}</span>
            <i class="fa-solid fa-chevron-right text-[8px]"></i>
          </span>
        </button>
        <button
          type="button"
          :disabled="thinkingLevelOptions.length === 0"
          :class="[
            'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-2 text-left text-xs text-gray-600',
            thinkingLevelOptions.length === 0 ? 'cursor-not-allowed opacity-50' : 'cursor-pointer hover:bg-gray-50'
          ]"
          @click="thinkingLevelOptions.length > 0 && (subView = 'levels')"
        >
          <span class="shrink-0 whitespace-nowrap">推理等级</span>
          <span class="flex items-center gap-1.5 text-gray-400">
            <span>{{ levelEntryValue }}</span>
            <i class="fa-solid fa-chevron-right text-[8px]"></i>
          </span>
        </button>
      </template>

      <!-- 二级：模型列表 -->
      <template v-else-if="subView === 'models'">
        <button
          type="button"
          class="flex w-full items-center gap-1.5 border-none bg-transparent px-3 py-2 text-left text-[11px] text-gray-400 cursor-pointer hover:text-gray-600"
          @click="subView = null"
        >
          <i class="fa-solid fa-chevron-left text-[8px]"></i>
          <span>模型</span>
        </button>
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
          <span class="truncate">{{ displayLabel(m) }}</span>
          <i v-if="selectedModel === m.id" class="fa-solid fa-check text-[10px]"></i>
        </button>
        <div v-if="modelOptions.length === 0" class="px-3 py-2 text-xs text-gray-400">暂无模型</div>
      </template>

      <!-- 二级：推理等级列表 -->
      <template v-else>
        <button
          type="button"
          class="flex w-full items-center gap-1.5 border-none bg-transparent px-3 py-2 text-left text-[11px] text-gray-400 cursor-pointer hover:text-gray-600"
          @click="subView = null"
        >
          <i class="fa-solid fa-chevron-left text-[8px]"></i>
          <span class="shrink-0 whitespace-nowrap">推理等级</span>
        </button>
        <button
          v-for="level in thinkingLevelOptions"
          :key="level"
          type="button"
          :class="[
            'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-1.5 text-left text-xs cursor-pointer',
            currentThinkingLevel === level ? 'text-blue-600 bg-blue-50' : 'text-gray-600 hover:bg-gray-50'
          ]"
          :title="`思考档位：${LEVEL_LABELS[level] ?? level}`"
          @click="selectThinkingLevel(level)"
        >
          <span>{{ LEVEL_LABELS[level] ?? level }}</span>
          <i v-if="currentThinkingLevel === level" class="fa-solid fa-check text-[10px]"></i>
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 窄容器下收紧触发文案宽度；容器由外层 ChatInputBox 声明 */
@container (max-width: 560px) {
  .model-trigger-label {
    max-width: 130px;
  }
}

@container (max-width: 430px) {
  .model-trigger-label {
    max-width: 0;
  }
}
</style>
