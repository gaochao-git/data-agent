/**
 * SSE(Server-Sent Events)报文解析的唯一共享实现。
 *
 * 历史 useSqlGenerator、SqlEditor、api/index、api/dashboard 各自手写了一份
 * 块内解析,多行 data 的语义互相不一致:有的"最后一行覆盖"会静默丢掉前几行,
 * 有的无分隔符累加会黏行。这里统一为 W3C SSE 规范行为:
 * - 同一事件块内多行 data 按出现顺序用 "\n" 连接;
 * - event 字段取第一条 event: 行;
 * - id 字段取第一条非空 id: 行;
 * - 既无 data 又无 event 的块(空块/纯注释块)不构成事件,返回 null。
 *
 * 分帧(按空行切块、末尾半包留待下一网络分片)由 parseSseChunk 统一处理,
 * 与四处既有实现的 "\n\n" 切块策略保持一致;事件名到各业务类型的映射、
 * [DONE] 处理仍由调用方自己完成,避免收敛字节层解析时改变业务语义。
 */

export interface SseBlock {
  /** event: 字段值;未携带时为 undefined,默认事件类型由调用方决定 */
  event?: string
  /** id: 字段值;取第一条非空 id: 行 */
  id?: string
  /** data: 字段;多行 data 按规范用 "\n" 连接 */
  data: string
}

/**
 * 解析单个 SSE 事件块(不含块间空行分隔符)。
 * 返回 null 表示该块不构成有效事件。
 */
export function parseSseBlock(block: string): SseBlock | null {
  const dataLines: string[] = []
  let event: string | undefined
  let id: string | undefined

  for (const line of block.split('\n')) {
    if (line.startsWith('data:')) {
      // 字段值两侧空白按 trim 处理,与四处既有实现的取值方式保持一致
      dataLines.push(line.substring(5).trim())
    } else if (line.startsWith('event:')) {
      // 规范上后出现的 event 行会覆盖前面的,这里按统一语义只取第一条
      if (event === undefined) event = line.substring(6).trim()
    } else if (line.startsWith('id:')) {
      const parsedId = line.substring(3).trim()
      if (parsedId && id === undefined) id = parsedId
    }
  }

  const data = dataLines.join('\n')
  if (!data && event === undefined) return null
  return { event, id, data }
}

export interface SseChunkResult {
  /** 缓冲区中已完整成帧的事件块(按顺序) */
  events: SseBlock[]
  /** 末尾未成帧的半包,调用方需拼回缓冲区等待下一个网络分片 */
  rest: string
}

/**
 * 按空行 "\n\n" 分帧解析 SSE 缓冲区,处理跨 chunk 半包。
 * 只分发完整块;末尾可能被网络分片截断的半包原样返回。
 */
export function parseSseChunk(buffer: string): SseChunkResult {
  const blocks = buffer.split('\n\n')
  const rest = blocks.pop() || ''
  const events: SseBlock[] = []
  for (const block of blocks) {
    const parsed = parseSseBlock(block)
    if (parsed) events.push(parsed)
  }
  return { events, rest }
}
