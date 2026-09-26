import { computed, ref, unref, watch, type ComputedRef, type Ref } from 'vue'

type ReactiveValue<T> = Ref<T> | ComputedRef<T>

/** 输入框 @ 引用共用的工作区文件展示子集；ConversationWorkspaceFile 是其超集。 */
export interface WorkspaceFilePick {
  /** 会话工作区相对路径（uploads/xxx.pdf、workspace/semantic/a__1b2.yaml）。 */
  relativePath: string
  /** 文件名（末级路径段），chip 与菜单主标题。 */
  name: string
}

/** 文件名后面展示的目录路径（含末尾斜杠），如 uploads/、workspace/semantic/。 */
export const workspaceFileDirPath = (file: WorkspaceFilePick): string => (
  file.relativePath.slice(0, file.relativePath.length - file.name.length)
)

interface UseWorkspaceFileMentionOptions {
  input: Ref<string>
  /** 候选文件清单（会话工作区可见文件，菜单打开时由宿主刷新）。 */
  files: ReactiveValue<WorkspaceFilePick[]>
}

/**
 * 输入框 @ 引用工作区文件（多选）：输入 `@` 触发文件弹层（平铺列表：文件名 + 目录
 * 路径），选中后 @token 从文本移除、文件进 chip 列表；再次输入 `@` 可继续追加，
 * 每个 chip 可单独移除。选中信息随消息走 file_selections（相对路径列表）通道，
 * 后端校验白名单后在正文追加「[引用文件：…]」。工作区有什么就能引用什么，空工作区即无可引用项。
 */
export const useWorkspaceFileMention = (options: UseWorkspaceFileMentionOptions) => {
  const mentionedFiles = ref<WorkspaceFilePick[]>([])
  const isMentionOpen = ref(false)
  const highlightedFileIndex = ref(0)

  const mentionKeyword = computed(() => {
    // 光标语义上永远在文本末尾：匹配最后一个未完成的 @token（允许出现在消息中部）
    const match = options.input.value.match(/(^|\s)@([^\s@]*)$/)
    return match ? (match[2] || '').toLowerCase() : null
  })

  const filterFile = (file: WorkspaceFilePick, keyword: string): boolean => {
    const haystack = [file.name, file.relativePath]
      .map(item => String(item || '').toLowerCase()).join(' ')
    return haystack.includes(keyword)
  }

  const displayedMentionFiles = computed(() => {
    const keyword = mentionKeyword.value
    if (keyword === null) return []
    const files = unref(options.files)
    if (!keyword) return files
    return files.filter(file => filterFile(file, keyword))
  })

  const showMentionMenu = computed(() => isMentionOpen.value && mentionKeyword.value !== null)

  const closeMentionMenu = () => {
    isMentionOpen.value = false
    highlightedFileIndex.value = 0
  }

  const selectMentionFile = (file: WorkspaceFilePick) => {
    if (!mentionedFiles.value.some(item => item.relativePath === file.relativePath)) {
      mentionedFiles.value = [...mentionedFiles.value, file]
    }
    options.input.value = options.input.value.replace(/(^|\s)@[^\s@]*$/, '$1')
    closeMentionMenu()
  }

  const removeMentionFile = (relativePath: string) => {
    mentionedFiles.value = mentionedFiles.value.filter(item => item.relativePath !== relativePath)
  }

  const clearMentionFiles = () => {
    mentionedFiles.value = []
  }

  const handleMentionMenuKeyDown = (event: KeyboardEvent): boolean => {
    if (!showMentionMenu.value) return false
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      highlightedFileIndex.value = Math.min(
        highlightedFileIndex.value + 1,
        Math.max(displayedMentionFiles.value.length - 1, 0),
      )
      return true
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      highlightedFileIndex.value = Math.max(highlightedFileIndex.value - 1, 0)
      return true
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      const file = displayedMentionFiles.value[highlightedFileIndex.value]
      if (file) {
        event.preventDefault()
        selectMentionFile(file)
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
    highlightedFileIndex.value = 0
  })

  return {
    mentionedFiles,
    mentionKeyword,
    displayedMentionFiles,
    showMentionMenu,
    highlightedFileIndex,
    closeMentionMenu,
    selectMentionFile,
    removeMentionFile,
    clearMentionFiles,
    handleMentionMenuKeyDown,
  }
}
