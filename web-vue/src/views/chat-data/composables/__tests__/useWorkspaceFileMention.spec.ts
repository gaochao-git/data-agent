import { describe, expect, it } from 'vitest'
import { nextTick, ref } from 'vue'
import { useWorkspaceFileMention, workspaceFileDirPath, type WorkspaceFilePick } from '../useWorkspaceFileMention'

const buildFiles = (): WorkspaceFilePick[] => [
  { relativePath: 'uploads/用户上传.pdf', name: '用户上传.pdf' },
  { relativePath: 'outputs/分析报告.md', name: '分析报告.md' },
  { relativePath: 'workspace/semantic/订单指标集__a1b2.yaml', name: '订单指标集__a1b2.yaml' },
]

const keydown = (key: string): KeyboardEvent => new KeyboardEvent('keydown', { key, cancelable: true })

describe('useWorkspaceFileMention', () => {
  it('输入 @ 打开菜单、无 @ 关闭菜单', async () => {
    const input = ref('')
    const mention = useWorkspaceFileMention({ input, files: ref(buildFiles()) })

    input.value = '查一下 @'
    await nextTick()
    expect(mention.showMentionMenu.value).toBe(true)
    expect(mention.displayedMentionFiles.value).toHaveLength(3)

    input.value = '查一下'
    await nextTick()
    expect(mention.showMentionMenu.value).toBe(false)
    expect(mention.displayedMentionFiles.value).toHaveLength(0)
  })

  it('关键词按文件名/路径/语义模型展示名过滤', () => {
    const input = ref('@订单')
    const mention = useWorkspaceFileMention({ input, files: ref(buildFiles()) })
    // 语义模型按工作区文件名（订单指标集__a1b2.yaml）参与匹配，不特殊化
    expect(mention.displayedMentionFiles.value.map(file => file.relativePath))
      .toEqual(['workspace/semantic/订单指标集__a1b2.yaml'])

    input.value = '@uploads'
    expect(mention.displayedMentionFiles.value.map(file => file.relativePath))
      .toEqual(['uploads/用户上传.pdf'])
  })

  it('选中后 @token 从文本移除、进 chip 列表，重复选中不重复追加', () => {
    const input = ref('分析 @')
    const files = ref(buildFiles())
    const mention = useWorkspaceFileMention({ input, files })

    mention.selectMentionFile(files.value[0]!)
    expect(input.value).toBe('分析 ')
    expect(mention.mentionedFiles.value.map(file => file.relativePath)).toEqual(['uploads/用户上传.pdf'])

    input.value = '再看 @'
    mention.selectMentionFile(files.value[0]!)
    expect(mention.mentionedFiles.value).toHaveLength(1)
  })

  it('平铺展示：文件名后跟目录路径（含末尾斜杠）', () => {
    const [pdf, , yaml] = buildFiles()
    expect(workspaceFileDirPath(pdf!)).toBe('uploads/')
    expect(workspaceFileDirPath(yaml!)).toBe('workspace/semantic/')
  })

  it('removeMentionFile 按 relativePath 移除、clearMentionFiles 清空', () => {
    const input = ref('')
    const files = ref(buildFiles())
    const mention = useWorkspaceFileMention({ input, files })

    mention.selectMentionFile(files.value[0]!)
    mention.selectMentionFile(files.value[1]!)
    mention.removeMentionFile('uploads/用户上传.pdf')
    expect(mention.mentionedFiles.value.map(file => file.relativePath)).toEqual(['outputs/分析报告.md'])
    mention.clearMentionFiles()
    expect(mention.mentionedFiles.value).toHaveLength(0)
  })

  it('键盘导航：Enter 选中高亮项并阻止默认提交', async () => {
    const input = ref('')
    const files = ref(buildFiles())
    const mention = useWorkspaceFileMention({ input, files })

    input.value = '@'
    await nextTick()

    const down = keydown('ArrowDown')
    mention.handleMentionMenuKeyDown(down)
    const enter = keydown('Enter')
    const handled = mention.handleMentionMenuKeyDown(enter)
    expect(handled).toBe(true)
    expect(enter.defaultPrevented).toBe(true)
    expect(mention.mentionedFiles.value.map(file => file.relativePath)).toEqual(['outputs/分析报告.md'])
  })
})
