import { computed, ref, unref, watch, type ComputedRef, type Ref } from 'vue'
import { getSkills } from '../api'
import type { SkillInfo } from '../types'

type SkillAgent = 'query' | 'analyze' | ''
type ReactiveValue<T> = Ref<T> | ComputedRef<T>

interface UseSkillCommandMenuOptions {
  input: Ref<string>
  enabled: ReactiveValue<boolean>
  agent?: SkillAgent
  maxItems?: number
  loadErrorText?: string
}

const normalizeSkillSearchText = (skill: SkillInfo) => [
  skill.name,
  skill.title,
  skill.description,
  skill.category,
].map(item => String(item || '').toLowerCase()).join(' ')

export const useSkillCommandMenu = (options: UseSkillCommandMenuOptions) => {
  const selectedSkill = ref<SkillInfo | null>(null)
  const commandSkills = ref<SkillInfo[]>([])
  const isCommandSkillLoading = ref(false)
  const isSkillCommandOpen = ref(false)
  const highlightedSkillIndex = ref(0)

  const skillCommandKeyword = computed(() => {
    if (!unref(options.enabled)) return null
    const match = options.input.value.match(/^\/([^\s/]*)/)
    return match ? (match[1] || '').toLowerCase() : null
  })

  const displayedCommandSkills = computed(() => {
    const keyword = skillCommandKeyword.value
    if (keyword === null) return []
    const result = keyword
      ? commandSkills.value.filter(skill => normalizeSkillSearchText(skill).includes(keyword))
      : commandSkills.value
    return result.slice(0, options.maxItems || 8)
  })

  const showSkillCommand = computed(() => isSkillCommandOpen.value && skillCommandKeyword.value !== null)
  const selectedSkillTitle = computed(() => selectedSkill.value?.title || selectedSkill.value?.name || '')

  const loadCommandSkills = async () => {
    if (commandSkills.value.length > 0 || isCommandSkillLoading.value) return
    isCommandSkillLoading.value = true
    try {
      const response = await getSkills(options.agent ? { agent: options.agent } : undefined)
      commandSkills.value = response.data?.skills || []
    } catch (err) {
      commandSkills.value = []
      console.error(options.loadErrorText || '加载技能列表失败:', err)
    } finally {
      isCommandSkillLoading.value = false
    }
  }

  const refreshCommandSkills = async () => {
    commandSkills.value = []
    await loadCommandSkills()
  }

  const closeSkillCommand = () => {
    isSkillCommandOpen.value = false
    highlightedSkillIndex.value = 0
  }

  const selectCommandSkill = (skill: SkillInfo) => {
    selectedSkill.value = skill
    options.input.value = options.input.value.replace(/^\/[^\s/]*\s?/, '')
    closeSkillCommand()
  }

  const clearSelectedSkill = () => {
    selectedSkill.value = null
  }

  const handleSkillCommandKeyDown = (
    event: KeyboardEvent,
    onSelect?: (skill: SkillInfo) => void,
  ): boolean => {
    if (!showSkillCommand.value) return false
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      highlightedSkillIndex.value = Math.min(
        highlightedSkillIndex.value + 1,
        Math.max(displayedCommandSkills.value.length - 1, 0),
      )
      return true
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      highlightedSkillIndex.value = Math.max(highlightedSkillIndex.value - 1, 0)
      return true
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      const skill = displayedCommandSkills.value[highlightedSkillIndex.value]
      if (skill) {
        event.preventDefault()
        if (onSelect) onSelect(skill)
        else selectCommandSkill(skill)
        return true
      }
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      closeSkillCommand()
      return true
    }
    return false
  }

  watch(skillCommandKeyword, (keyword) => {
    if (keyword === null) {
      closeSkillCommand()
      return
    }
    isSkillCommandOpen.value = true
    highlightedSkillIndex.value = 0
    loadCommandSkills()
  })

  return {
    selectedSkill,
    selectedSkillTitle,
    commandSkills,
    displayedCommandSkills,
    isCommandSkillLoading,
    isSkillCommandOpen,
    highlightedSkillIndex,
    skillCommandKeyword,
    showSkillCommand,
    loadCommandSkills,
    refreshCommandSkills,
    closeSkillCommand,
    selectCommandSkill,
    clearSelectedSkill,
    handleSkillCommandKeyDown,
  }
}
