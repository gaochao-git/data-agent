import { describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import { useSemanticModelMention, type SemanticModelPick } from '../useSemanticModelMention'

const buildModels = (): SemanticModelPick[] => [
  { id: 'cfg_1', name: '交易域', description: '订单与支付主题' },
  { id: 'cfg_2', name: '用户域', description: '注册与留存分析' },
  { id: 'cfg_3', name: 'Trading metrics', description: '' },
]

const setup = () => {
  const input = ref('')
  const models = ref(buildModels())
  const mention = useSemanticModelMention({ input, models })
  return { input, models, ...mention }
}

describe('useSemanticModelMention', () => {
  it('输入 @ 触发弹层，@ 后文本作为过滤关键字（不区分大小写）', async () => {
    const m = setup()

    input: m.input.value = '查一下 @'
    await nextTick()
    expect(m.mentionKeyword.value).toBe('')
    expect(m.showMentionMenu.value).toBe(true)
    expect(m.displayedMentionModels.value.map(item => item.id)).toEqual([
      'cfg_1',
      'cfg_2',
      'cfg_3',
    ])

    m.input.value = '查一下 @交易'
    await nextTick()
    expect(m.mentionKeyword.value).toBe('交易')
    expect(m.displayedMentionModels.value.map(item => item.id)).toEqual(['cfg_1'])

    // description 也参与匹配
    m.input.value = '查一下 @留存'
    await nextTick()
    expect(m.displayedMentionModels.value.map(item => item.id)).toEqual(['cfg_2'])

    // 英文关键字大小写不敏感
    m.input.value = '查一下 @TRADING'
    await nextTick()
    expect(m.displayedMentionModels.value.map(item => item.id)).toEqual(['cfg_3'])
  })

  it('没有未完成的 @token 时不弹层', async () => {
    const m = setup()

    m.input.value = '@abc 后续文本'
    await nextTick()
    expect(m.showMentionMenu.value).toBe(false)
    expect(m.displayedMentionModels.value).toEqual([])

    // 空 @token 匹配失败后自动关层
    m.input.value = '查一下 @'
    await nextTick()
    expect(m.showMentionMenu.value).toBe(true)
    m.input.value = '查一下 @交易 已确认'
    await nextTick()
    expect(m.showMentionMenu.value).toBe(false)
  })

  it('选中模型后 @token 从输入移除、模型进 chip 列表并关层', async () => {
    const m = setup()

    m.input.value = '查一下 @交易'
    await nextTick()
    m.selectMentionModel(buildModels()[0]!)

    expect(m.input.value).toBe('查一下 ')
    expect(m.mentionedModels.value.map(item => item.id)).toEqual(['cfg_1'])
    expect(m.showMentionMenu.value).toBe(false)

    // 同一模型不会重复加入
    m.input.value = '再查 @'
    await nextTick()
    m.selectMentionModel({ id: 'cfg_1', name: '交易域' })
    expect(m.mentionedModels.value).toHaveLength(1)

    // 再 @ 一次可以追加第二个模型（多选）
    m.input.value = '再查 @用户'
    await nextTick()
    m.selectMentionModel(buildModels()[1]!)
    expect(m.mentionedModels.value.map(item => item.id)).toEqual(['cfg_1', 'cfg_2'])
    expect(m.input.value).toBe('再查 ')
  })

  it('removeMentionModel / clearMentionModels 维护 chip 列表', () => {
    const m = setup()

    m.selectMentionModel({ id: 'cfg_1', name: '交易域' })
    m.selectMentionModel({ id: 'cfg_2', name: '用户域' })

    m.removeMentionModel('cfg_1')
    expect(m.mentionedModels.value.map(item => item.id)).toEqual(['cfg_2'])

    m.clearMentionModels()
    expect(m.mentionedModels.value).toEqual([])
  })

  it('键盘导航：上下移动有边界，Enter/Tab 选中高亮项，Escape 关层', async () => {
    const m = setup()

    m.input.value = '@'
    await nextTick()

    const preventDefault = vi.fn()
    expect(m.handleMentionMenuKeyDown({ key: 'ArrowDown', preventDefault } as unknown as KeyboardEvent)).toBe(true)
    expect(m.highlightedModelIndex.value).toBe(1)
    // 到底部后不再下移
    m.handleMentionMenuKeyDown({ key: 'ArrowDown', preventDefault } as unknown as KeyboardEvent)
    expect(m.highlightedModelIndex.value).toBe(2)
    m.handleMentionMenuKeyDown({ key: 'ArrowUp', preventDefault } as unknown as KeyboardEvent)
    expect(m.highlightedModelIndex.value).toBe(1)
    // 到顶部后不再上移
    m.handleMentionMenuKeyDown({ key: 'ArrowUp', preventDefault } as unknown as KeyboardEvent)
    m.handleMentionMenuKeyDown({ key: 'ArrowUp', preventDefault } as unknown as KeyboardEvent)
    expect(m.highlightedModelIndex.value).toBe(0)

    // Enter 选中高亮项（第 0 个 = cfg_1）
    expect(m.handleMentionMenuKeyDown({ key: 'Enter', preventDefault } as unknown as KeyboardEvent)).toBe(true)
    expect(m.mentionedModels.value.map(item => item.id)).toEqual(['cfg_1'])
    expect(preventDefault).toHaveBeenCalled()
    expect(m.showMentionMenu.value).toBe(false)

    // Escape 关层不选中（先等 watcher flush，再重新输入 @）
    await nextTick()
    m.input.value = '@'
    await nextTick()
    expect(m.handleMentionMenuKeyDown({ key: 'Escape', preventDefault } as unknown as KeyboardEvent)).toBe(true)
    expect(m.showMentionMenu.value).toBe(false)
    expect(m.mentionedModels.value).toHaveLength(1)
  })

  it('弹层未打开时键盘事件不拦截，交给输入框自己处理', async () => {
    const m = setup()

    m.input.value = '普通文本'
    await nextTick()
    expect(m.showMentionMenu.value).toBe(false)
    expect(m.handleMentionMenuKeyDown({ key: 'Enter', preventDefault: vi.fn() } as unknown as KeyboardEvent)).toBe(false)
  })
})
