import { computed, ref, unref, watch, type ComputedRef, type Ref } from 'vue'
import { getSkills } from '../api'
import type { SkillInfo } from '../types'

type ReactiveValue<T> = Ref<T> | ComputedRef<T>

/** 消息/选择器共用的技能展示子集；SkillInfo 是其超集。 */
export type SkillPick = Pick<SkillInfo, 'name' | 'title' | 'description'>

interface UseSkillCommandMenuOptions {
  input: Ref<string>
  enabled: ReactiveValue<boolean>
  loadErrorText?: string
}

const normalizeSkillSearchText = (skill: SkillInfo) => [
  skill.name,
  skill.title,
  skill.description,
  skill.category,
].map(item => String(item || '').toLowerCase()).join(' ')

export const useSkillCommandMenu = (options: UseSkillCommandMenuOptions) => {
  const selectedSkills = ref<SkillPick[]>([])
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
    return result
  })

  const showSkillCommand = computed(() => isSkillCommandOpen.value && skillCommandKeyword.value !== null)
  const selectedSkillTitles = computed(() => selectedSkills.value.map(skill => skill.title || skill.name))

  const loadCommandSkills = async () => {
    if (commandSkills.value.length > 0 || isCommandSkillLoading.value) return
    isCommandSkillLoading.value = true
    try {
      const response = await getSkills({ scope: 'runtime' })
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
    if (!selectedSkills.value.some(item => item.name === skill.name)) {
      selectedSkills.value = [...selectedSkills.value, { name: skill.name, title: skill.title, description: skill.description }]
    }
    options.input.value = options.input.value.replace(/^\/[^\s/]*\s?/, '')
    closeSkillCommand()
  }

  /** 管理面板/引导场景的"钦定单个技能"：替换当前全部选择。 */
  const replaceSelectedSkill = (skill: SkillPick) => {
    selectedSkills.value = [{ name: skill.name, title: skill.title, description: skill.description }]
  }

  const removeSelectedSkill = (name: string) => {
    selectedSkills.value = selectedSkills.value.filter(item => item.name !== name)
  }

  const clearSelectedSkills = () => {
    selectedSkills.value = []
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
    selectedSkills,
    selectedSkillTitles,
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
    replaceSelectedSkill,
    removeSelectedSkill,
    clearSelectedSkills,
    handleSkillCommandKeyDown,
  }
}
