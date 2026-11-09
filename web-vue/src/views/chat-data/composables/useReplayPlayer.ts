/**
 * 历史消息「演示模式」回放引擎（纯前端，view 层，不写任何会话状态）。
 *
 * 原理：历史 timeline 的终态 Message（segments/thinkingSegments/toolCalls）与直播
 * 渲染汇聚到同一套组件；把终态合成一条伪事件脚本，按节奏渐进地填充到深克隆的
 * Message 上，即可用直播同款组件模拟整个流的过程。
 *
 * 脚本动作（按源消息 segments 顺序展开）：
 * - reveal        用户消息整卡出现
 * - start         助手消息外壳（isStreaming，渲染「正在处理...」）
 * - thinking      思考段分块打字
 * - toolStart/toolDone 工具卡先 running 后 completed（时长按真实耗时压缩）
 * - text          正文分块打字
 * - finish        该消息收尾（isStreaming=false，补 dashboardSpec/error）
 */
import { ref, type Ref } from 'vue'
import type { Message, MessageSegment, ThinkingSegment, ToolCallInfo } from '../types'

/** 打字节奏基线（毫秒，1x 速度；实际 delay = base / speed） */
const PACE = {
  userReveal: 500,
  assistantStart: 400,
  thinkingFirst: 600,
  thinkingChunk: 30,
  thinkingChunkChars: 40,
  toolStart: 200,
  toolMin: 500,
  toolMax: 2000,
  textChunk: 26,
  textChunkChars: 16,
  newlineGap: 220,
  minTick: 16,
} as const

export const REPLAY_SPEED_OPTIONS = [1, 2, 4, 8, 16] as const

type ReplayAction =
  | { kind: 'reveal'; index: number; delay: number }
  | { kind: 'start'; index: number; delay: number }
  | { kind: 'thinking'; index: number; segment: ThinkingSegment; upto: number; delay: number }
  | { kind: 'toolStart'; index: number; toolCallId: string; delay: number }
  | { kind: 'toolDone'; index: number; toolCallId: string; delay: number }
  | { kind: 'text'; index: number; ordinal: number; upto: number; delay: number }
  | { kind: 'finish'; index: number; delay: number }

interface BuiltScript {
  actions: ReplayAction[]
  /** 深克隆后的源消息（终态，用于收尾与结果回填） */
  source: Message[]
  /** 源助手消息按 index 的 segments（text 段按 ordinal 对齐） */
  assistantSegments: Map<number, MessageSegment[]>
}

const clampDuration = (ms: number | null | undefined): number => {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return 900
  return Math.min(Math.max(ms, PACE.toolMin), PACE.toolMax)
}

const cloneMessage = (message: Message): Message => {
  try {
    return structuredClone(message)
  } catch {
    return JSON.parse(JSON.stringify(message)) as Message
  }
}

/** 工具卡 running 期剥离结果类字段，toolDone 时由终态回填 */
const runningToolCall = (final: ToolCallInfo): ToolCallInfo => {
  const rest = { ...final }
  delete rest.result
  delete rest.subagentTrail
  delete rest.subagentContextUsage
  delete rest.subagentTokenUsage
  delete rest.subagentTokenUsageDetail
  return { ...rest, status: 'running' }
}

const collectChunks = (content: string, size: number, base: number, gapChars: string): Array<{ upto: number; delay: number }> => {
  const chunks: Array<{ upto: number; delay: number }> = []
  for (let i = size; i < content.length; i += size) {
    // 不把代理对等宽字符切成两半
    if (/[^\u0000-\u00ff]/.test(content.charAt(i))) i += 1
    chunks.push({ upto: i, delay: base + (content.slice(0, i).includes(gapChars) ? PACE.newlineGap : 0) })
  }
  chunks.push({ upto: content.length, delay: base })
  return chunks
}

const buildScript = (group: Message[]): BuiltScript => {
  const source = group.map(cloneMessage)
  const actions: ReplayAction[] = []
  const assistantSegments = new Map<number, MessageSegment[]>()
  source.forEach((message, index) => {
    if (message.role === 'user' || message.role === 'context_compression') {
      // 用户提问与压缩分隔卡整卡出现（压缩卡是纯展示分隔，无需渐进）
      actions.push({ kind: 'reveal', index, delay: PACE.userReveal })
      return
    }
    if (message.role !== 'assistant') {
      // tool 消息的信息已并入助手 toolCalls，演示中整卡跳过
      return
    }
    actions.push({ kind: 'start', index, delay: PACE.assistantStart })
    const list: MessageSegment[] = message.segments?.length
      ? message.segments
      : (message.content ? [{ type: 'text', content: message.content }] : [])
    assistantSegments.set(index, list)
    const textCounter = { n: 0 }
    list.forEach((segment) => {
      appendSegmentActions(actions, message, index, segment, textCounter)
    })
    actions.push({ kind: 'finish', index, delay: 350 })
  })
  return { actions, source, assistantSegments }
}

const appendSegmentActions = (actions: ReplayAction[], message: Message, index: number, segment: MessageSegment, textCounter: { n: number }) => {
  if (segment.type === 'text') {
    const ordinal = textCounter.n++
    collectChunks(segment.content, PACE.textChunkChars, PACE.textChunk, '\n').forEach(({ upto, delay }) => {
      actions.push({ kind: 'text', index, ordinal, upto, delay })
    })
    return
  }
  if (segment.type === 'thinkingSegment') {
    const thinking = (message.thinkingSegments || []).find(item => item.id === segment.thinkingSegmentId)
    if (!thinking) return
    collectChunks(thinking.content, PACE.thinkingChunkChars, PACE.thinkingChunk, '').forEach(({ upto, delay }) => {
      actions.push({ kind: 'thinking', index, segment: thinking, upto, delay })
    })
    return
  }
  const final = (message.toolCalls || []).find(item => item.tool_call_id === segment.toolCallId)
  if (!final) return
  actions.push({ kind: 'toolStart', index, toolCallId: final.tool_call_id, delay: PACE.toolStart })
  actions.push({
    kind: 'toolDone',
    index,
    toolCallId: final.tool_call_id,
    delay: clampDuration(final.ended_at_ms && final.started_at_ms ? final.ended_at_ms - final.started_at_ms : null),
  })
}

export interface ReplayPlayerState {
  messages: Ref<Message[]>
  revealed: Ref<boolean[]>
  status: Ref<'idle' | 'playing' | 'paused' | 'finished'>
  speed: Ref<number>
  stepIndex: Ref<number>
  totalSteps: Ref<number>
  start: (group: Message[]) => void
  pause: () => void
  resume: () => void
  toggle: () => void
  setSpeed: (value: number) => void
  skipToEnd: () => void
  close: () => void
}

export function useReplayPlayer(): ReplayPlayerState {
  const messages = ref<Message[]>([])
  const revealed = ref<boolean[]>([])
  const status = ref<'idle' | 'playing' | 'paused' | 'finished'>('idle')
  const speed = ref<number>(1)
  const stepIndex = ref(0)
  const totalSteps = ref(0)

  let script: BuiltScript | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  /** index → ordinal → 已创建的 text 段（引用克隆消息里的 segment，原地追加） */
  let liveTextSegments: Map<number, Map<number, { type: 'text'; content: string }>> = new Map()
  let liveThinking: Map<number, Map<string, ThinkingSegment>> = new Map()

  const stopTimer = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  const getLive = (action: ReplayAction): { target: Message; live: Message } | null => {
    if (!script) return null
    const target = script.source[action.index]
    const live = messages.value[action.index]
    if (!target || !live) return null
    return { target, live }
  }

  const applyReveal = (action: ReplayAction & { kind: 'reveal' }, { target }: { target: Message; live: Message }) => {
    messages.value.splice(action.index, 1, cloneMessage(target))
    revealed.value[action.index] = true
  }

  const applyThinking = (action: ReplayAction & { kind: 'thinking' }, { live }: { target: Message; live: Message }) => {
    const store = liveThinking.get(action.index) ?? new Map<string, ThinkingSegment>()
    liveThinking.set(action.index, store)
    let segment = store.get(action.segment.id)
    if (!segment) {
      segment = { ...action.segment, content: '' }
      store.set(action.segment.id, segment)
      live.thinkingSegments = [...(live.thinkingSegments || []), segment]
      live.segments = [...(live.segments || []), { type: 'thinkingSegment', thinkingSegmentId: action.segment.id }]
    }
    segment.content = action.segment.content.slice(0, action.upto)
  }

  const applyToolStart = (action: ReplayAction & { kind: 'toolStart' }, { live, target }: { target: Message; live: Message }) => {
    const final = (target.toolCalls || []).find(item => item.tool_call_id === action.toolCallId)
    if (!final) return
    live.toolCalls = [...(live.toolCalls || []), runningToolCall(final)]
    live.segments = [...(live.segments || []), { type: 'toolCall', toolCallId: action.toolCallId }]
  }

  const applyToolDone = (action: ReplayAction & { kind: 'toolDone' }, { live, target }: { target: Message; live: Message }) => {
    const final = (target.toolCalls || []).find(item => item.tool_call_id === action.toolCallId)
    const running = (live.toolCalls || []).find(item => item.tool_call_id === action.toolCallId)
    if (!final || !running) return
    Object.assign(running, final, { status: final.status === 'generating' ? 'completed' : final.status })
  }

  const applyText = (action: ReplayAction & { kind: 'text' }, { live }: { target: Message; live: Message }) => {
    const sourceSegments = (script?.assistantSegments.get(action.index) || [])
      .filter(item => item.type === 'text')
    const sourceSegment = sourceSegments[action.ordinal]
    if (!sourceSegment || sourceSegment.type !== 'text') return
    const store = liveTextSegments.get(action.index) ?? new Map<number, { type: 'text'; content: string }>()
    liveTextSegments.set(action.index, store)
    let segment = store.get(action.ordinal)
    if (!segment) {
      segment = { type: 'text', content: '' }
      store.set(action.ordinal, segment)
      live.segments = [...(live.segments || []), segment]
    }
    segment.content = sourceSegment.content.slice(0, action.upto)
    live.content = (live.segments || []).reduce((acc, item) => item.type === 'text' ? acc + item.content : acc, '')
  }

  const applyFinish = (action: ReplayAction & { kind: 'finish' }, { target }: { target: Message; live: Message }) => {
    // 终态整卡替换：回放结束的渲染结果与一次性加载的历史消息完全一致（含未渐进模拟的段型/字段）
    messages.value.splice(action.index, 1, cloneMessage(target))
    revealed.value[action.index] = true
  }

  const handlers = {
    reveal: applyReveal,
    start: (action: ReplayAction & { kind: 'start' }, { live }: { target: Message; live: Message }) => {
      live.isStreaming = true
      revealed.value[action.index] = true
    },
    thinking: (action: ReplayAction & { kind: 'thinking' }, pair: { target: Message; live: Message }) => {
      applyThinking(action, pair)
    },
    toolStart: (action: ReplayAction & { kind: 'toolStart' }, pair: { target: Message; live: Message }) => {
      applyToolStart(action, pair)
    },
    toolDone: (action: ReplayAction & { kind: 'toolDone' }, pair: { target: Message; live: Message }) => {
      applyToolDone(action, pair)
    },
    text: (action: ReplayAction & { kind: 'text' }, pair: { target: Message; live: Message }) => {
      applyText(action, pair)
    },
    finish: (action: ReplayAction & { kind: 'finish' }, pair: { target: Message; live: Message }) => {
      applyFinish(action, pair)
    },
  }

  const apply = (action: ReplayAction) => {
    const pair = getLive(action)
    if (!pair) return
    const handler = handlers[action.kind] as (a: ReplayAction, pair: { target: Message; live: Message }) => void
    handler(action, pair)
  }

  const schedule = (delayMs: number) => {
    stopTimer()
    const wait = Math.max(PACE.minTick, delayMs / Math.max(speed.value, 1))
    timer = setTimeout(() => {
      if (status.value !== 'playing' || !script) return
      const actions = script.actions
      const current = actions[stepIndex.value]
      if (!current) {
        skipToEnd()
        return
      }
      apply(current)
      stepIndex.value += 1
      const next = actions[stepIndex.value]
      if (!next) {
        status.value = 'finished'
        stopTimer()
      } else {
        schedule(next.delay)
      }
    }, wait)
  }

  const start = (group: Message[]) => {
    close()
    script = buildScript(group)
    liveTextSegments = new Map()
    liveThinking = new Map()
    // 全量占位保证动作索引稳定；全部消息（含用户提问）都由动作按节奏揭示，模拟从第一条提问开始
    messages.value = script.source.map(message => ({
      id: message.id,
      role: message.role,
      content: '',
    }) as Message)
    revealed.value = script.source.map(() => false)
    totalSteps.value = script.actions.length
    stepIndex.value = 0
    status.value = 'playing'
    const first = script.actions[0]
    if (first) {
      schedule(first.delay)
    } else {
      status.value = 'finished'
    }
  }

  const pause = () => {
    if (status.value === 'playing') {
      stopTimer()
      status.value = 'paused'
    }
  }

  const resume = () => {
    if (status.value === 'paused' && script) {
      const action = script.actions[stepIndex.value]
      if (!action) return
      status.value = 'playing'
      schedule(action.delay)
    }
  }

  const toggle = () => {
    if (status.value === 'playing') pause()
    else resume()
  }

  const setSpeed = (value: number) => {
    speed.value = value
    if (status.value === 'playing' && script) {
      const action = script.actions[stepIndex.value]
      if (action) schedule(action.delay)
    }
  }

  const skipToEnd = () => {
    stopTimer()
    if (!script) return
    const actions = script.actions
    while (stepIndex.value < actions.length) {
      const current = actions[stepIndex.value]
      if (!current) break
      apply(current)
      stepIndex.value += 1
    }
    // 终态整组替换：与历史一次性渲染完全一致
    messages.value = script.source.map(cloneMessage)
    revealed.value = script.source.map(() => true)
    status.value = 'finished'
  }

  const close = () => {
    stopTimer()
    script = null
    messages.value = []
    revealed.value = []
    stepIndex.value = 0
    totalSteps.value = 0
    status.value = 'idle'
  }

  return { messages, revealed, status, speed, stepIndex, totalSteps, start, pause, resume, toggle, setSpeed, skipToEnd, close }
}

/** 整个会话回放的消息集：从第一条用户提问到最后一条回复；含压缩分隔卡（整卡出现）；tool 的信息已并入助手 toolCalls，不参与回放 */
export const collectFullReplay = (messages: Message[]): Message[] =>
  messages.filter(item => item.role === 'user' || item.role === 'assistant' || item.role === 'context_compression')
