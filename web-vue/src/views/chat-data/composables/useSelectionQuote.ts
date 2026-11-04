import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { copyTextToClipboard } from '../utils/clipboard'

/** 划选引用浮层状态：文本 + 视口坐标 + 锚定方向。 */
export interface QuoteToolbarState {
  text: string
  x: number
  y: number
  /** 浮层锚在选区上方（translate(-50%,-100%)）还是下方（translate(-50%,0)）。 */
  placement: 'top' | 'bottom'
}

const QUOTE_TOOLBAR_MARGIN = 12
const QUOTE_TOOLBAR_HALF_WIDTH = 96
/** 选区顶部低于该距离时浮层改锚到选区下方。 */
const QUOTE_TOOLBAR_TOP_SAFE_AREA = 56

const isNodeWithin = (node: Node | null, container: Element | null): boolean => (
  Boolean(node && container && container.contains(node))
)

/**
 * 助手气泡划选引用：监听列表容器上的 mouseup，命中 `[data-assistant-bubble]`
 * 且非流式时浮出操作条；复制只复制选中片段，「添加到对话」把片段抛给父组件。
 *
 * 监听器只在组件生命周期内挂一份；浮层是否可点通过 `[data-quote-toolbar]` 判断，
 * 不依赖组件内部 ref，便于把浮层拆成独立组件。
 */
export const useSelectionQuote = (options: { onAddToConversation: (text: string) => void }) => {
  const quoteToolbar = ref<QuoteToolbarState | null>(null)

  const hideQuoteToolbar = () => {
    quoteToolbar.value = null
  }

  const handleMouseUp = (event: MouseEvent) => {
    if (event.button !== 0) return
    const target = event.target
    // 浮层自身的 mouseup 会冒泡到列表根节点：必须忽略，否则会先把浮层收起，
    // 随后的 click 落在已卸载的按钮上——表现为「复制/添加到对话」点了没反应。
    if (target instanceof Element && target.closest('[data-quote-toolbar]')) return
    const bubble = target instanceof Element
      ? target.closest<HTMLElement>('[data-assistant-bubble]')
      : null
    if (!bubble || bubble.dataset.streaming === 'true') {
      hideQuoteToolbar()
      return
    }
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      hideQuoteToolbar()
      return
    }
    // 选区两端都必须落在当前助手气泡内，避免把气泡外的文字一起带进引用
    if (
      !isNodeWithin(selection.anchorNode, bubble)
      || !isNodeWithin(selection.focusNode, bubble)
    ) {
      hideQuoteToolbar()
      return
    }
    const text = selection.toString().trim()
    if (!text) {
      hideQuoteToolbar()
      return
    }
    const rect = selection.getRangeAt(0).getBoundingClientRect()
    if (!rect.width && !rect.height) {
      hideQuoteToolbar()
      return
    }
    const x = Math.min(
      Math.max(rect.left + rect.width / 2, QUOTE_TOOLBAR_MARGIN + QUOTE_TOOLBAR_HALF_WIDTH),
      window.innerWidth - QUOTE_TOOLBAR_MARGIN - QUOTE_TOOLBAR_HALF_WIDTH,
    )
    const placement: QuoteToolbarState['placement'] = rect.top > QUOTE_TOOLBAR_TOP_SAFE_AREA ? 'top' : 'bottom'
    quoteToolbar.value = { text, x, y: placement === 'top' ? rect.top - 6 : rect.bottom + 6, placement }
  }

  const toolbarStyle = computed(() => {
    const toolbar = quoteToolbar.value
    if (!toolbar) return {}
    return {
      left: `${toolbar.x}px`,
      top: `${toolbar.y}px`,
      transform: toolbar.placement === 'top' ? 'translate(-50%, -100%)' : 'translate(-50%, 0)',
    }
  })

  const handleCopyQuoteSelection = async () => {
    const text = quoteToolbar.value?.text
    if (!text) return
    await copyTextToClipboard(text)
    hideQuoteToolbar()
  }

  const handleQuoteSelectionToConversation = () => {
    const text = quoteToolbar.value?.text
    if (!text) return
    options.onAddToConversation(text)
    window.getSelection()?.removeAllRanges()
    hideQuoteToolbar()
  }

  // 选中范围塌缩 / 滚动 / 视口变化时收起浮层，避免位置漂移或残留
  const handleDocumentSelectionChange = () => {
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed) hideQuoteToolbar()
  }

  const handleViewportChange = () => hideQuoteToolbar()

  const handleDocumentMouseDown = (event: MouseEvent) => {
    const target = event.target
    if (target instanceof Element && target.closest('[data-quote-toolbar]')) return
    hideQuoteToolbar()
  }

  onMounted(() => {
    document.addEventListener('selectionchange', handleDocumentSelectionChange)
    document.addEventListener('mousedown', handleDocumentMouseDown)
    document.addEventListener('scroll', handleViewportChange, true)
    window.addEventListener('resize', handleViewportChange)
  })

  onBeforeUnmount(() => {
    document.removeEventListener('selectionchange', handleDocumentSelectionChange)
    document.removeEventListener('mousedown', handleDocumentMouseDown)
    document.removeEventListener('scroll', handleViewportChange, true)
    window.removeEventListener('resize', handleViewportChange)
  })

  return {
    quoteToolbar,
    toolbarStyle,
    handleMouseUp,
    handleCopyQuoteSelection,
    handleQuoteSelectionToConversation,
  }
}
