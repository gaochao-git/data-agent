import { computed, ref, unref, watch, type ComputedRef, type Ref } from 'vue'

type ReactiveValue<T> = Ref<T> | ComputedRef<T>

/** 输入框 @ 引用共用的语义模型展示子集；SemanticConfigItem 是其超集。 */
export interface SemanticModelPick {
  id: string
  name: string
  description?: string
  metricsCount?: number
  dimensionsCount?: number
}

interface UseSemanticModelMentionOptions {
  input: Ref<string>
  /** 候选模型清单（问数侧边栏同一份 enabled 列表）。 */
  models: ReactiveValue<SemanticModelPick[]>
}

const normalizeModelSearchText = (model: SemanticModelPick) => [
  model.name,
  model.description,
].map(item => String(item || '').toLowerCase()).join(' ')

/**
 * 输入框 @ 引用语义模型（多选）：输入 `@` 触发候选弹层，选中后 @token 从文本移除、
 * 模型进 chip 列表（形态对齐技能 chip / 文件附件）；再次输入 `@` 可继续追加，
 * 每个 chip 可单独移除。选中信息随消息走 semantic_config_selection 列表通道，
 * 后端在正文追加「[本轮指定语义模型：A、B]」。无 chip 即智能选择（默认，不需要任何点击）。
 */
export const useSemanticModelMention = (options: UseSemanticModelMentionOptions) => {
  const mentionedModels = ref<SemanticModelPick[]>([])
  const isMentionOpen = ref(false)
  const highlightedModelIndex = ref(0)

  const mentionKeyword = computed(() => {
    // 光标语义上永远在文本末尾：匹配最后一个未完成的 @token（允许出现在消息中部）
    const match = options.input.value.match(/(^|\s)@([^\s@]*)$/)
    return match ? (match[2] || '').toLowerCase() : null
  })

  const displayedMentionModels = computed(() => {
    const keyword = mentionKeyword.value
    if (keyword === null) return []
    const models = unref(options.models)
    if (!keyword) return models
    return models.filter(model => normalizeModelSearchText(model).includes(keyword))
  })

  const showMentionMenu = computed(() => isMentionOpen.value && mentionKeyword.value !== null)

  const closeMentionMenu = () => {
    isMentionOpen.value = false
    highlightedModelIndex.value = 0
  }

  const selectMentionModel = (model: SemanticModelPick) => {
    if (!mentionedModels.value.some(item => item.id === model.id)) {
      mentionedModels.value = [...mentionedModels.value, model]
    }
    options.input.value = options.input.value.replace(/(^|\s)@[^\s@]*$/, '$1')
    closeMentionMenu()
  }

  const removeMentionModel = (modelId: string) => {
    mentionedModels.value = mentionedModels.value.filter(item => item.id !== modelId)
  }

  const clearMentionModels = () => {
    mentionedModels.value = []
  }

  const handleMentionMenuKeyDown = (event: KeyboardEvent): boolean => {
    if (!showMentionMenu.value) return false
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      highlightedModelIndex.value = Math.min(
        highlightedModelIndex.value + 1,
        Math.max(displayedMentionModels.value.length - 1, 0),
      )
      return true
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      highlightedModelIndex.value = Math.max(highlightedModelIndex.value - 1, 0)
      return true
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      const model = displayedMentionModels.value[highlightedModelIndex.value]
      if (model) {
        event.preventDefault()
        selectMentionModel(model)
        return true
      }
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      closeMentionMenu()
      return true
    }
    return false
  }

  watch(mentionKeyword, (keyword) => {
    if (keyword === null) {
      closeMentionMenu()
      return
    }
    isMentionOpen.value = true
    highlightedModelIndex.value = 0
  })

  return {
    mentionedModels,
    mentionKeyword,
    displayedMentionModels,
    showMentionMenu,
    highlightedModelIndex,
    closeMentionMenu,
    selectMentionModel,
    removeMentionModel,
    clearMentionModels,
    handleMentionMenuKeyDown,
  }
}
