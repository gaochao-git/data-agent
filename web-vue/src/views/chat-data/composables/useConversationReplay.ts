/**
 * 历史会话回放（演示模式）的宿主接线层：入口门禁、全量预拉、pending 自动开演、
 * 柔性滚动跟随与浮标控制。回放引擎在 useReplayPlayer；渲染复用 ConversationMessageList
 * （与直播/历史同一套组件，footer、工作过程卡等走真实渲染逻辑）。
 * 纯 view 层：不写任何会话状态。
 */
import { computed, onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { collectFullReplay, REPLAY_SPEED_OPTIONS, useReplayPlayer } from './useReplayPlayer'
import type { Message } from '../types'

/** 全量预拉的翻页护栏：防 has_more 异常时死循环（200 页 × 每页 5 轮 = 1000 轮上限） */
const MAX_PREFETCH_PAGES = 200

export interface UseConversationReplayOptions {
  messages: Ref<Message[]>
  chatContainerRef: Ref<HTMLDivElement | null>
  /** 用户是否本就在底部（宿主 handleScroll 维护）：仅底部时播放跟随，上翻即停 */
  isNearBottom: Ref<boolean>
  isGenerating: Ref<boolean>
  hasActiveRun: Ref<boolean>
  isLoadingHistory: Ref<boolean>
  hasMoreHistory: Ref<boolean>
  loadEarlierHistory: () => Promise<void>
}

export function useConversationReplay(options: UseConversationReplayOptions) {
  const replayGroup = ref<Message[] | null>(null)
  const replayPlayer = useReplayPlayer()
  /** 跨会话入口：历史加载完成后自动开演的待办标记 */
  const pendingAutoReplay = ref(false)
  /** 全量预拉进行中：长会话分页拉取，期间不重复触发 */
  const replayPreparing = ref(false)
  /**
   * 待启动（armed）态：requestReplay 通过门禁后先置位，宿主把第一条提问预填进输入框，
   * 用户回车（handleSubmit → startReplay）才真正开演——演示起点与真实发送完全一致。
   */
  const replayArmed = ref(false)
  /** 预填进输入框的种子文本：回放组第一条用户提问 */
  const replaySeedText = computed(() => {
    const first = collectFullReplay(options.messages.value)[0]
    return first && first.role === 'user' ? String(first.content ?? '') : ''
  })

  // 回放可见消息：把渐进填充的克隆消息按揭示顺序交给 ConversationMessageList
  const replayRevealedMessages = computed(() => {
    if (!replayGroup.value) return []
    return replayPlayer.messages.value.filter((_, index) => replayPlayer.revealed.value[index])
  })

  const openReplay = async () => {
    if (replayPreparing.value) return
    replayPreparing.value = true
    try {
      // 历史按轮次分页加载：开演前预拉全量，避免长会话只演「最近一页」
      let pages = 0
      while (options.hasMoreHistory.value && pages < MAX_PREFETCH_PAGES) {
        await options.loadEarlierHistory()
        pages += 1
      }
    } finally {
      replayPreparing.value = false
    }
    const group = collectFullReplay(options.messages.value)
    if (!group.length) {
      ElMessage.info('暂无可回放的历史消息')
      return
    }
    replayGroup.value = group
    replayPlayer.start(group)
  }

  /** 回车启动：仅 armed 态生效；消费 armed 标记并开演 */
  const startReplay = () => {
    if (!replayArmed.value) return
    replayArmed.value = false
    void openReplay()
  }

  const exitReplay = () => {
    replayGroup.value = null
    replayPlayer.close()
  }

  /** 从头重播：用同一组终态消息重建脚本、从第一条提问重新开演 */
  const restartReplay = () => {
    if (!replayGroup.value) return
    replayPlayer.start(replayGroup.value)
  }

  const requestReplay = () => {
    if (options.isGenerating.value || options.hasActiveRun.value) {
      ElMessage.info('当前任务运行中，结束后可回放')
      return
    }
    if (options.isLoadingHistory.value) {
      pendingAutoReplay.value = true
      return
    }
    if (!collectFullReplay(options.messages.value).length) {
      ElMessage.info('暂无可回放的历史消息')
      return
    }
    replayArmed.value = true
  }

  /** 倍速循环切换：1x → 2x → 4x → 8x → 16x → 1x（浮标上单按钮） */
  const cycleReplaySpeed = () => {
    const speeds = REPLAY_SPEED_OPTIONS
    const idx = speeds.findIndex(value => value === replayPlayer.speed.value)
    const next = speeds[(idx + 1) % speeds.length]
    replayPlayer.setSpeed(next ?? 1)
  }

  // pending 消费：任何一次历史加载完成都消费掉标记（含空会话，此处统一提示），杜绝 flag 泄漏后突然自动开演；
  // 有可回放消息时进入 armed 态（等用户回车启动），不自动开演
  watch(options.isLoadingHistory, (loading) => {
    if (loading || !pendingAutoReplay.value) return
    pendingAutoReplay.value = false
    if (!collectFullReplay(options.messages.value).length) {
      ElMessage.info('暂无可回放的历史消息')
      return
    }
    replayArmed.value = true
  })

  // 柔性跟随：仅当用户本就在底部时随播放拽底；上翻即停跟随（与直播 isNearBottom 同口径，回到最新按钮可恢复）
  watch(() => replayPlayer.stepIndex.value, () => {
    if (!replayGroup.value || !options.isNearBottom.value) return
    const el = options.chatContainerRef.value
    if (el) el.scrollTop = el.scrollHeight
  })

  // Esc 取消待启动 / 退出回放；卸载时停掉播放器定时器
  const handleReplayGlobalKeydown = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && (replayGroup.value || replayArmed.value)) {
      event.preventDefault()
      exitReplay()
      replayArmed.value = false
    }
  }
  onMounted(() => window.addEventListener('keydown', handleReplayGlobalKeydown))
  onUnmounted(() => {
    window.removeEventListener('keydown', handleReplayGlobalKeydown)
    replayPlayer.close()
  })

  return {
    replayGroup,
    replayPlayer,
    replayRevealedMessages,
    replayArmed,
    replaySeedText,
    replayPreparing,
    requestReplay,
    startReplay,
    exitReplay,
    restartReplay,
    cycleReplaySpeed,
  }
}
